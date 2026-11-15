"""빈 답안지(이미지/PDF 1쪽)에서 마킹칸(원)을 찾아 답안지 양식(좌표)을 자동으로 만든다.

순서: 크기 정규화 -> 원 검출 -> 기울기 보정 -> 이웃 원끼리 묶기 -> 격자 블록 구성 -> 역할 판별
      (정답 영역 / 수험번호 / 문제 유형 표시칸) -> 기준 프레임(보정용) 검출 -> 양식 설정(dict) + 미리보기 이미지.

가정(미리보기로 사람이 확인해야 함):
  - 정답 영역은 같은 모양의 격자 블록이며, 문항 번호는 블록을 왼쪽에서 오른쪽으로, 각 블록 안은 위에서 아래로 이어서 붙는다.
  - 수험번호는 '10행(숫자 0~9, 위에서 아래로)'인 가장 넓은 격자. 문제 유형 표시칸은 2~4개짜리 한 줄 원 묶음(위/왼쪽부터 A, B, C...).
"""
import cv2
import numpy as np

TARGET_LONG = 2339            # 긴 변 기준 크기 (A4를 200dpi로 렌더링한 크기와 같음)


class DetectError(Exception):
    def __init__(self, message, info=None):
        super().__init__(message)
        self.info = info or {}


def normalize_image(img):
    h, w = img.shape[:2]
    s = TARGET_LONG / max(h, w)
    if abs(s - 1) > 0.02:
        img = cv2.resize(img, (round(w * s), round(h * s)), interpolation=cv2.INTER_CUBIC if s > 1 else cv2.INTER_AREA)
    return img


def _hough(gray):
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    c = cv2.HoughCircles(blur, cv2.HOUGH_GRADIENT, dp=1, minDist=16, param1=100, param2=22, minRadius=8, maxRadius=26)
    return np.zeros((0, 3), np.float32) if c is None else c[0]


def find_circles(gray):
    """원 검출 후 대표 반지름과 비슷한 것만 남기고 중복을 제거한다. -> (원 배열[x, y, r], 대표 반지름)"""
    c = _hough(gray)
    if len(c) < 8:
        raise DetectError("마킹칸(원)을 충분히 찾지 못했습니다. 선명한 빈 답안지 PDF나 이미지를 올려 주세요.")
    hist, edges = np.histogram(c[:, 2], bins=np.arange(7.5, 27.5, 1))
    mode = edges[hist.argmax()] + 0.5
    c = c[np.abs(c[:, 2] - mode) <= max(2.5, 0.22 * mode)]
    r0 = float(np.median(c[:, 2]))
    kept = []
    for x, y, r in c[np.argsort(c[:, 1] * 10000 + c[:, 0])]:
        if all((x - kx) ** 2 + (y - ky) ** 2 > (0.8 * r0) ** 2 for kx, ky, _ in kept):
            kept.append((x, y, r))
    return np.array(kept, np.float32), r0


def _skew_angle(pts, r0):
    """가로로 나란한 이웃 원들의 기울기 중앙값(도)."""
    ang = []
    d = np.hypot(pts[:, None, 0] - pts[None, :, 0], pts[:, None, 1] - pts[None, :, 1])
    for i in range(len(pts)):
        d[i, i] = np.inf
        j = int(d[i].argmin())
        dx, dy = pts[j, 0] - pts[i, 0], pts[j, 1] - pts[i, 1]
        if dx < 0:
            dx, dy = -dx, -dy
        a = np.degrees(np.arctan2(dy, dx))
        if abs(a) < 8:
            ang.append(a)
    return float(np.median(ang)) if len(ang) > 20 else 0.0


def _cluster1d(vals, tol):
    """1차원 값을 tol 이내로 묶어 (중심 목록, 각 값의 묶음 번호) 를 돌려준다."""
    order = np.argsort(vals)
    groups, cur = [], [order[0]]
    for i in order[1:]:
        if vals[i] - vals[cur[-1]] <= tol:
            cur.append(i)
        else:
            groups.append(cur)
            cur = [i]
    groups.append(cur)
    centers = [float(np.mean([vals[k] for k in g])) for g in groups]
    idx = np.zeros(len(vals), int)
    for gi, g in enumerate(groups):
        idx[g] = gi
    return centers, idx


def _components(pts, r0):
    """가까운 원끼리 연결해 덩어리로 묶는다. 연결 거리는 이웃 간격의 분포에서 자동 결정."""
    n = len(pts)
    d = np.hypot(pts[:, None, 0] - pts[None, :, 0], pts[:, None, 1] - pts[None, :, 1])
    np.fill_diagonal(d, np.inf)
    t = 1.5 * float(np.percentile(d.min(axis=1), 80))
    parent = list(range(n))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    for i, j in zip(*np.where(d < t)):
        parent[find(i)] = find(j)
    comps = {}
    for i in range(n):
        comps.setdefault(find(i), []).append(i)
    return list(comps.values())


class _Block:
    def __init__(self, pts, r0):
        self.pts = np.asarray(pts, np.float32)
        self.r0 = r0
        self.xs, xi = _cluster1d(self.pts[:, 0], 0.6 * r0)
        self.ys, yi = _cluster1d(self.pts[:, 1], 0.6 * r0)
        self.C, self.R, self.n = len(self.xs), len(self.ys), len(self.pts)
        self.regular = self.n >= 0.9 * self.C * self.R

    @property
    def left(self):
        return self.xs[0]

    @property
    def top(self):
        return self.ys[0]


def _merge_vertical(blocks, r0):
    """같은 열 위치를 가진 위아래 덩어리(문항 5줄씩 끊어진 표 등)를 하나의 블록으로 합친다."""
    merged = []
    for b in sorted(blocks, key=lambda b: (b.left, b.top)):
        for m in merged:
            if (b.C >= 2 and m.C == b.C and b.regular and m.regular
                    and max(abs(np.array(m.xs) - np.array(b.xs))) <= 0.8 * r0
                    and (b.top > m.ys[-1] or b.ys[-1] < m.top)):
                m.__init__(np.vstack([m.pts, b.pts]), r0)
                break
        else:
            merged.append(b)
    return merged


def _frames(gray, blocks, shape):
    """블록을 감싸는 큰 사각 프레임(표 테두리) -> 스캔 보정용 기준(anchors)."""
    h, w = shape
    _, th = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY_INV)
    cnts, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in cnts:
        x, y, bw, bh = cv2.boundingRect(c)
        if not (0.02 * w * h <= bw * bh <= 0.6 * w * h):
            continue
        if cv2.contourArea(c) < 0.85 * bw * bh:
            continue
        ap = cv2.approxPolyDP(c, 0.02 * cv2.arcLength(c, True), True)
        if len(ap) != 4:
            continue
        if not any(x <= px <= x + bw and y <= py <= y + bh for b in blocks for px, py in b.pts):
            continue
        out.append((bw * bh, c, (bw, bh)))
    out.sort(key=lambda t: -t[0])
    res = []
    for _, c, size in out[:4]:
        pts = cv2.approxPolyDP(c, 0.02 * cv2.arcLength(c, True), True).reshape(4, 2)
        s, d = pts.sum(axis=1), pts[:, 0] - pts[:, 1]
        corners = np.float32([pts[s.argmin()], pts[d.argmax()], pts[s.argmax()], pts[d.argmin()]])
        res.append({"corners": corners.round(1).tolist(), "size": [int(size[0]), int(size[1])]})
    return res


def detect(img_bgr):
    """-> (layout_config, summary, 미리보기 이미지(BGR)). 실패하면 DetectError."""
    img = normalize_image(img_bgr)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    circles, r0 = find_circles(gray)
    ang = _skew_angle(circles[:, :2], r0)
    if abs(ang) > 0.15:                                   # 기울어진 빈 양식은 바로잡고 다시 검출
        h, w = img.shape[:2]
        M = cv2.getRotationMatrix2D((w / 2, h / 2), ang, 1.0)
        img = cv2.warpAffine(img, M, (w, h), borderValue=(255, 255, 255))
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        circles, r0 = find_circles(gray)
    ch, cw = img.shape[:2]
    pts = circles[:, :2]

    comps = _components(pts, r0)
    blocks = [_Block(pts[c], r0) for c in comps if len(c) >= 2]
    small = [b for b in blocks if b.n <= 4 and (b.C == 1 or b.R == 1)]
    big = [b for b in blocks if b not in small and b.n >= 6]
    big = _merge_vertical(big, r0)
    big = [b for b in big if b.regular]

    id_cands = [b for b in big if b.R == 10 and b.C >= 4]
    if not id_cands:
        raise DetectError("수험번호 칸(숫자 0~9 열 10줄짜리 격자)을 찾지 못했습니다. 이 답안지는 자동 인식이 어렵습니다.", {"circles": int(len(circles))})
    idb = max(id_cands, key=lambda b: b.C)
    ans = [b for b in big if b is not idb and 2 <= b.C <= 6]
    if not ans:
        raise DetectError("정답 영역(문항별 선택지 격자)을 찾지 못했습니다.", {"circles": int(len(circles))})
    choices = int(np.bincount([b.C for b in ans]).argmax())
    dropped = [b for b in ans if b.C != choices]
    ans = [b for b in ans if b.C == choices]
    # 문항 순서: 왼쪽 블록부터, 같은 열이면 위쪽부터
    ans.sort(key=lambda b: (round(b.left / (2 * r0)), b.top))
    warnings = []
    if dropped:
        warnings.append(f"선택지 수가 다른 격자 {len(dropped)}개는 제외했습니다.")
    # 유형 표시칸: 2~4개짜리 한 줄 원 묶음. 작성 유의사항의 기호(● ◐ ⊙ 등)도 원으로 검출되므로
    # (1) 속이 많이 채워진 원은 제외, (2) 마킹칸은 옆의 글자 때문에 간격이 넓다는 점(>=3.8r)으로 가려낸다.
    def ink(x, y):
        m = np.zeros(gray.shape, np.uint8)
        cv2.circle(m, (int(x), int(y)), max(2, int(r0) - 3), 255, -1)
        return float((gray[m > 0] < 175).mean())
    cands = []
    for b in small:
        if b.n < 2:
            continue
        pp = sorted([tuple(p) for p in b.pts], key=(lambda p: p[1]) if b.C == 1 else (lambda p: p[0]))
        gaps = [float(np.hypot(pp[i + 1][0] - pp[i][0], pp[i + 1][1] - pp[i][1])) for i in range(len(pp) - 1)]
        cands.append({"pts": pp, "gap": min(gaps), "ink": float(np.mean([ink(x, y) for x, y in pp]))})
    good = [c for c in cands if c["ink"] < 0.45 and c["gap"] >= 3.8 * r0]
    if not good:
        good = [c for c in cands if c["ink"] < 0.45 and c["gap"] >= 2.8 * r0]
        good = good if len(good) == 1 else []
    types = {}
    if len(good) >= 1:
        best = max(good, key=lambda c: (c["gap"] >= 3.8 * r0, -abs(len(c["pts"]) - 2)))
        types = {chr(65 + i): [round(float(x), 1), round(float(y), 1)] for i, (x, y) in enumerate(best["pts"][:4])}
    else:
        warnings.append("문제 유형(A/B형) 표시칸을 찾지 못했습니다. 유형이 하나인 시험으로 설정되며, 유형이 여러 개면 직접 좌표 등록이 필요합니다.")

    anchors = _frames(gray, ans + [idb], (ch, cw))
    if not anchors:
        warnings.append("보정용 기준 프레임(표 테두리)을 찾지 못했습니다. 기울어진 스캔은 정확도가 떨어질 수 있습니다.")

    cfg = {
        "canonical_size": [cw, ch], "bubble_radius": int(round(r0)), "choices_per_block": choices,
        "answer_blocks": [{"x_cols": [round(x, 1) for x in b.xs], "y_rows": [round(y, 1) for y in b.ys]} for b in ans],
        "id_grid": {"x_cols": [round(x, 1) for x in idb.xs], "y_rows": [round(y, 1) for y in idb.ys]},
        "type_bubbles": types, "anchors": anchors,
    }
    n_q = sum(b.R for b in ans)
    summary = {
        "circles": int(len(circles)), "blocks": len(ans), "rows": [b.R for b in ans], "choices": choices, "questions": n_q,
        "id_digits": idb.C, "forms": list(types), "anchors": len(anchors), "radius": cfg["bubble_radius"],
        "size": [cw, ch], "warnings": warnings,
    }
    return cfg, summary, _preview(img, cfg, ans, idb, small)


def _preview(img, cfg, ans, idb, small):
    """인식 결과를 그림으로: 정답 영역(파랑)+문항 번호, 수험번호(주황), 유형(초록), 기준 프레임(자홍)."""
    v = img.copy()
    r = cfg["bubble_radius"]
    q = 1
    for b in ans:
        for y in cfg["answer_blocks"][ans.index(b)]["y_rows"]:
            for x in cfg["answer_blocks"][ans.index(b)]["x_cols"]:
                cv2.circle(v, (int(x), int(y)), r, (230, 120, 0), 2)
            if (q - 1) % 5 == 0 or q == sum(bb.R for bb in ans):
                cv2.putText(v, str(q), (int(cfg["answer_blocks"][ans.index(b)]["x_cols"][0] - r - 36), int(y + 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 0, 0), 1, cv2.LINE_AA)
            q += 1
    for x in cfg["id_grid"]["x_cols"]:
        for y in cfg["id_grid"]["y_rows"]:
            cv2.circle(v, (int(x), int(y)), r, (0, 140, 255), 2)
    for i, (k, (x, y)) in enumerate(cfg["type_bubbles"].items()):
        cv2.circle(v, (int(x), int(y)), r + 3, (0, 170, 0), 3)
        cv2.putText(v, k, (int(x + r + 6), int(y + 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 140, 0), 2, cv2.LINE_AA)
    for a in cfg["anchors"]:
        cv2.polylines(v, [np.int32(a["corners"])], True, (200, 0, 200), 3)
    return v
