import os
import numpy as np
import cv2

try:
    import fitz
    HAS_FITZ = True
except ImportError:
    HAS_FITZ = False

DEFAULT_CONFIG = {
    'exam_title': '2026년 (제12회) 인터넷중독전문상담사 자격검정 답안지',
    'canonical_size': [2337, 1653],
    'regions': {
        'answer_box': [700, 237, 1542, 1293],
        'id_box': [98, 778, 564, 748],
        'type_box': [98, 606, 564, 148],
        'name_box': [98, 441, 564, 142]
    },
    'bubble_radius': 13,
    'choices_per_block': 5,
    'type_bubbles': {'A': [520.0, 650.0], 'B': [520.0, 712.0]},
    'id_grid': {
        'x_cols': [162.0, 217.0, 271.5, 326.0, 381.0, 435.5, 490.0, 545.0, 599.5],
        'y_rows': [1001.0, 1054.0, 1107.0, 1160.0, 1213.0, 1266.0, 1319.0, 1372.0, 1425.0, 1477.0]
    },
    'answer_grid': {
        'x_cols': [
            808.0, 848.5, 889.0, 930.0, 970.5,
            1113.5, 1154.0, 1194.5, 1235.0, 1275.5,
            1419.0, 1459.0, 1500.0, 1540.0, 1581.0,
            1732.0, 1772.0, 1813.0, 1853.5, 1894.0,
            2045.0, 2085.0, 2126.0, 2166.5, 2207.0
        ],
        'y_rows': [
            543.0, 588.0, 632.0, 677.0, 721.0,
            800.0, 844.0, 889.0, 933.0, 978.0,
            1056.0, 1101.0, 1145.0, 1190.0, 1234.0,
            1313.0, 1357.0, 1402.0, 1446.0, 1491.0
        ]
    },
    'subjects': [
        {'code': 'A', 'name': '인터넷중독의 이해', 'start': 1, 'end': 20},
        {'code': 'B', 'name': '인터넷중독의 진단과 평가', 'start': 21, 'end': 40},
        {'code': 'C', 'name': '인터넷중독 상담기법', 'start': 41, 'end': 60},
        {'code': 'D', 'name': '인터넷중독 상담의 이론과 실제', 'start': 61, 'end': 80},
        {'code': 'E', 'name': '인터넷중독의 정책과 법', 'start': 81, 'end': 100}
    ]
}

def imread_korean(path):
    stream = np.fromfile(path, dtype=np.uint8)
    return cv2.imdecode(stream, cv2.IMREAD_COLOR)

def load_pages_from_file(filepath):
    ext = os.path.splitext(filepath)[1].lower()
    pages = []
    if ext == '.pdf':
        if not HAS_FITZ:
            raise RuntimeError('pymupdf 패키지가 필요합니다.')
        doc = fitz.open(filepath)
        for i, page in enumerate(doc):
            pix = page.get_pixmap(dpi=200)
            img_data = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
            img_bgr = cv2.cvtColor(img_data, cv2.COLOR_RGBA2BGR if pix.n == 4 else cv2.COLOR_RGB2BGR)
            pages.append((f'{os.path.basename(filepath)} [P.{i+1}]', img_bgr))
    else:
        img_bgr = imread_korean(filepath)
        if img_bgr is not None:
            pages.append((os.path.basename(filepath), img_bgr))
    return pages

class OMREngine:
    def __init__(self, config=None):
        self.cfg = config or DEFAULT_CONFIG
        self.cw, self.ch = self.cfg['canonical_size']
        self.fill_threshold = 0.65
        self.qpos = self._build_question_positions(self.cfg)

    @staticmethod
    def _build_question_positions(cfg):
        """문항 번호(1부터) -> 선택지별 마킹칸 중심 [(x, y), ...].
        answer_blocks(블록마다 x/y 따로) 가 있으면 그것을, 없으면 기존 방식(블록 모두 같은 y 행)을 쓴다. 문항은 블록 순서대로 이어서 번호가 붙는다."""
        blocks = cfg.get('answer_blocks')
        if not blocks:
            per = cfg.get('choices_per_block', 5)
            xs, ys = cfg['answer_grid']['x_cols'], cfg['answer_grid']['y_rows']
            blocks = [{'x_cols': xs[i * per:(i + 1) * per], 'y_rows': ys} for i in range(len(xs) // per)]
        return [[(x, y) for x in b['x_cols']] for b in blocks for y in b['y_rows']]

    @staticmethod
    def _order_corners(pts):
        """4점을 좌상, 우상, 우하, 좌하 순으로 정렬."""
        pts = np.asarray(pts, dtype=np.float32).reshape(4, 2)
        s, d = pts.sum(axis=1), pts[:, 0] - pts[:, 1]
        return np.float32([pts[s.argmin()], pts[d.argmax()], pts[s.argmax()], pts[d.argmin()]])

    def _box_corners(self, contour):
        """외곽선의 실제 네 모서리(기울어진 상자도 정확히). 사각형 근사 실패 시 최소외접사각형."""
        peri = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.02 * peri, True)
        pts = approx.reshape(-1, 2) if len(approx) == 4 else cv2.boxPoints(cv2.minAreaRect(contour))
        return self._order_corners(pts)

    def _align_by_anchors(self, resized, contours):
        """자동 인식된 양식용: 기준 사각 프레임(anchors)을 스캔에서 찾아 호모그래피로 보정."""
        src, dst = [], []
        for a in self.cfg['anchors']:
            ref = np.float32(a['corners'])
            rw, rh = a['size']
            rc = ref.mean(axis=0)
            best = None
            for c in contours:
                x, y, bw, bh = cv2.boundingRect(c)
                if abs(bw - rw) > 0.12 * rw or abs(bh - rh) > 0.12 * rh:
                    continue
                dist = float(np.hypot(x + bw / 2 - rc[0], y + bh / 2 - rc[1]))
                if dist > 0.15 * max(self.cw, self.ch):
                    continue
                if best is None or dist < best[0]:
                    best = (dist, c)
            if best:
                src.append(self._box_corners(best[1]))
                dst.append(ref)
        if not src:
            return resized
        src, dst = np.vstack(src), np.vstack(dst)
        H, _ = cv2.findHomography(src, dst, 0)
        if H is None:
            return resized
        mapped = cv2.perspectiveTransform(src.reshape(-1, 1, 2), H).reshape(-1, 2)
        if np.abs(mapped - dst).max() > 80:
            return resized
        return cv2.warpPerspective(resized, H, (self.cw, self.ch), borderValue=(255, 255, 255))

    def align_image(self, img_bgr):
        """답안 상자와 수험번호 상자의 8개 모서리로 호모그래피를 구해 기울기/이동/원근을 보정한다."""
        resized = cv2.resize(img_bgr, (self.cw, self.ch), interpolation=cv2.INTER_CUBIC)
        gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
        _, thresh = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY_INV)
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if self.cfg.get('anchors'):
            return self._align_by_anchors(resized, contours)
        ans_c, id_c = None, None
        for c in contours:
            x, y, bw, bh = cv2.boundingRect(c)
            area = cv2.contourArea(c)
            if 1250 < bw < 1800 and 1050 < bh < 1500 and x > 450:
                if ans_c is None or area > cv2.contourArea(ans_c):
                    ans_c = c
            elif 430 < bw < 720 and 560 < bh < 950 and x < 350 and y > 550:
                if id_c is None or area > cv2.contourArea(id_c):
                    id_c = c
        if ans_c is None or id_c is None:
            return resized

        def ref_corners(box):
            x, y, w, h = box
            return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]

        src = np.vstack([self._box_corners(ans_c), self._box_corners(id_c)])
        dst = np.float32(ref_corners(self.cfg['regions']['answer_box']) + ref_corners(self.cfg['regions']['id_box']))
        H, _ = cv2.findHomography(src, dst, 0)
        if H is None:
            return resized
        # 비정상 변환(상자를 잘못 잡은 경우) 방지: 모서리가 기준 위치에서 80px 넘게 벗어나면 변환 포기
        mapped = cv2.perspectiveTransform(src.reshape(-1, 1, 2), H).reshape(-1, 2)
        if np.abs(mapped - dst).max() > 80:
            return resized
        return cv2.warpPerspective(resized, H, (self.cw, self.ch), borderValue=(255, 255, 255))

    def get_bubble_ratio(self, binary_img, cx, cy, r):
        x1, y1 = max(0, int(cx - r)), max(0, int(cy - r))
        x2, y2 = min(self.cw, int(cx + r)), min(self.ch, int(cy + r))
        roi = binary_img[y1:y2, x1:x2]
        if roi.size == 0: return 0.0
        mask = np.zeros(roi.shape, dtype=np.uint8)
        cv2.circle(mask, (int(cx - x1), int(cy - y1)), r - 2, 255, -1)
        total_pixels = cv2.countNonZero(mask)
        if total_pixels == 0: return 0.0
        red = getattr(self, '_red', None)
        if red is not None:      # 빨간 표시에 가려진 부분은 분모에서 제외 (가려진 정도가 크면 판단 불가 -> 0)
            rr = red[y1:y2, x1:x2]
            mask = cv2.bitwise_and(mask, cv2.bitwise_not(rr))
            left = cv2.countNonZero(mask)
            if left < total_pixels * 0.3:
                return 0.0
            total_pixels = left
        return cv2.countNonZero(cv2.bitwise_and(roi, roi, mask=mask)) / float(total_pixels)

    def pick_marked(self, ratios, th):
        """한 줄(문항/수험번호 한 자리/유형)에서 마킹된 칸의 인덱스.
        절대 기준(th) 이상이면서, 같은 줄의 가장 빈 칸보다 0.25 이상 진해야 마킹으로 인정한다.
        (인쇄된 숫자·원 테두리 때문에 빈 칸도 스캔에서는 0.3~0.55가 나오므로 이중 조건 사용)"""
        base = min(ratios)
        return [i for i, v in enumerate(ratios) if v >= th and v >= base + 0.25]

    def grade_sheet(self, img_bgr, answer_key_dict, score_per_q=1.0, exam=None):
        """exam: 시험 설정(exam.py). 없으면 기본 시험(5과목x20문항, 선택지 5, 수험번호 9자리, A/B형)."""
        import exam as exam_mod
        exam = exam or exam_mod.default_exam()
        aligned = self.align_image(img_bgr)
        vis_img = aligned.copy()
        gray = cv2.cvtColor(aligned, cv2.COLOR_BGR2GRAY)
        _, binary = cv2.threshold(gray, 175, 255, cv2.THRESH_BINARY_INV)
        # 규칙 1: 빨간색 표시는 검토용이므로 답안(먹)으로 보지 않는다 -> 빨간 픽셀(과 번진 가장자리)은 잉크에서 제외
        b_, g_, r_ = [c.astype(np.int16) for c in cv2.split(aligned)]
        red = (((r_ - np.maximum(g_, b_)) > 45) & (r_ > 90)).astype(np.uint8) * 255
        red = cv2.dilate(red, np.ones((3, 3), np.uint8))
        binary[red > 0] = 0
        self._red = red
        r, th = self.cfg['bubble_radius'], self.fill_threshold

        # 문제 유형 표시칸: 시험에서 쓰는 유형만 인식 (그 외 칸은 무시)
        forms = [f for f in exam['forms'] if f in self.cfg['type_bubbles']]
        if len(exam['forms']) == 1 or len(forms) < len(exam['forms']):     # 유형이 하나(또는 양식에 표시칸이 없음)면 그 유형
            forms = [exam['forms'][0]]
        type_ratios = [self.get_bubble_ratio(binary, *self.cfg['type_bubbles'][k], r) for k in forms] if len(forms) > 1 else []
        marked_types = [forms[i] for i in self.pick_marked(type_ratios, th)] if len(forms) > 1 else []
        if len(forms) == 1:                      # 유형이 하나면 표시 여부와 관계없이 그 유형
            exam_type, type_status = forms[0], '정상'
        elif len(marked_types) == 1:
            exam_type, type_status = marked_types[0], '정상'
        else:
            exam_type, type_status = forms[0], '유형오류'
        for t_code in (forms if len(forms) > 1 else []):
            tx, ty = self.cfg['type_bubbles'][t_code]
            color = (0, 180, 0) if t_code == exam_type and type_status == '정상' else (0, 165, 255)
            if t_code in marked_types or type_status != '정상':
                cv2.circle(vis_img, (int(tx), int(ty)), r + 3, color, 3)

        id_x_cols, id_y_rows = self.cfg['id_grid']['x_cols'][:exam['id_digits']], self.cfg['id_grid']['y_rows']
        student_id_chars, id_warnings = [], 0
        for cx in id_x_cols:
            col_ratios = [self.get_bubble_ratio(binary, cx, cy, r) for cy in id_y_rows]
            col_marked = [(d, cx, id_y_rows[d]) for d in self.pick_marked(col_ratios, th)]
            if len(col_marked) == 1:
                student_id_chars.append(str(col_marked[0][0]))
                cv2.circle(vis_img, (int(col_marked[0][1]), int(col_marked[0][2])), r + 2, (255, 120, 0), 2)
            else:
                student_id_chars.append('?')
                id_warnings += 1
        student_id = ''.join(student_id_chars)

        n_q, n_ch = exam_mod.total_questions(exam), exam['choices']
        ans_key = answer_key_dict.get(exam_type, answer_key_dict.get(forms[0], [1] * n_q))
        if n_q > len(self.qpos):
            raise ValueError(f"시험 문항 수({n_q})가 답안지 양식 용량({len(self.qpos)})을 넘습니다.")
        codes = exam_mod.codes(exam)
        student_answers, subject_scores = {}, {c: 0.0 for c in codes}
        multi_count, blank_count, correct_count = 0, 0, 0

        for q_num in range(1, n_q + 1):
            centers = self.qpos[q_num - 1][:n_ch]
            code = codes[exam_mod.subject_index(exam, q_num)]
            correct_choice = int(ans_key[q_num - 1]) if q_num - 1 < len(ans_key) else 1
            row_ratios = [self.get_bubble_ratio(binary, cx, cy, r) for cx, cy in centers]
            marked = [(c_i + 1, centers[c_i][0], centers[c_i][1]) for c_i in self.pick_marked(row_ratios, th)]
            if len(marked) == 1:
                chosen, m_cx, m_cy = marked[0]
                student_answers[q_num] = str(chosen)
                if chosen == correct_choice:
                    subject_scores[code] += score_per_q
                    correct_count += 1
                    cv2.circle(vis_img, (int(m_cx), int(m_cy)), r + 3, (0, 190, 0), 3)
                else:
                    cv2.circle(vis_img, (int(m_cx), int(m_cy)), r + 3, (0, 0, 255), 3)
                    if 1 <= correct_choice <= n_ch:
                        cv2.circle(vis_img, (int(centers[correct_choice - 1][0]), int(centers[correct_choice - 1][1])), r, (255, 100, 0), 2)
            elif len(marked) == 0:
                student_answers[q_num] = '무표기'
                blank_count += 1
            else:
                # 규칙 2: 한 문항에 2개 이상 표시(정답 포함)하면 이중마킹 = 오답 (점수 없음). 판독 화면에 보라색으로 표시
                student_answers[q_num] = '이중'
                multi_count += 1
                for _, m_cx, m_cy in marked:
                    cv2.circle(vis_img, (int(m_cx), int(m_cy)), r + 3, (200, 0, 200), 3)
                if 1 <= correct_choice <= n_ch:
                    cv2.circle(vis_img, (int(centers[correct_choice - 1][0]), int(centers[correct_choice - 1][1])), r, (255, 100, 0), 2)

        total_score = sum(subject_scores.values())
        needs_review = (type_status != '정상') or (id_warnings > 0) or (multi_count > 0) or (blank_count > 0)
        return {
            'exam_type': exam_type, 'student_id': student_id, 'student_answers': student_answers,
            'subject_scores': subject_scores, 'total_score': total_score, 'correct_count': correct_count,
            'multi_count': multi_count, 'blank_count': blank_count, 'needs_review': needs_review, 'vis_img': vis_img
        }
