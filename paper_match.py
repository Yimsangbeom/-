"""문제지(A형/B형 시험 문제 파일)를 읽어 문제를 나누고, 두 문제지에서 서로 대응되는 문제(와 선택지 순서)를 찾는다.

- 글자가 들어 있는 PDF, Word(.docx), 텍스트(.txt)를 읽는다. (사진/스캔 PDF는 글자를 읽는 기능이 없어 지원하지 않는다)
- 문제 나누기: 1, 2, 3 ... 순서대로 번호가 붙은 줄을 문제 시작으로 본다(과목마다 1번부터 다시 시작해도 된다).
- 대응: 문제 본문과 선택지 글자의 유사도(2글자 조각 Dice)로 점수를 만들고, 전체가 가장 잘 맞도록(헝가리안 알고리즘) 한 문제씩 짝짓는다.
"""
import io
import re
import zipfile

CIRC = "①②③④⑤⑥⑦⑧"
Q_START_ANY = re.compile(r"^\s*(?:문\s*)?(\d{1,3})\s*[\.\)번]\s*(.*)$")
Q_START_DOT = re.compile(r"^\s*(?:문\s*)?(\d{1,3})\s*[\.번]\s*(.*)$")


# ------------------------------------------------------------ 글자 읽기
def _pdf_text(data):
    import fitz
    doc = fitz.open(stream=data, filetype="pdf")
    pages = []
    for page in doc:
        W = page.rect.width
        bl = [b for b in page.get_text("blocks") if b[6] == 0 and b[4].strip()]
        narrow = [b for b in bl if (b[2] - b[0]) < 0.55 * W]
        left = [b for b in narrow if (b[0] + b[2]) / 2 < W / 2]
        right = [b for b in narrow if (b[0] + b[2]) / 2 >= W / 2]
        two_col = len(left) >= 3 and len(right) >= 3                  # 2단 편집이면 왼쪽 단 -> 오른쪽 단 순서로 읽는다
        if two_col:
            bl.sort(key=lambda b: (1 if (b[2] - b[0]) < 0.55 * W and (b[0] + b[2]) / 2 >= W / 2 else 0, b[1], b[0]))
        else:
            bl.sort(key=lambda b: (round(b[1] / 3), b[0]))
        pages.append("\n".join(b[4].rstrip() for b in bl))
    return "\n".join(pages)


def _docx_text(data):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        xml = z.read("word/document.xml").decode("utf-8", "ignore")
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<w:(?:tab|br)\s*/>", " ", xml)
    text = re.sub(r"<[^>]+>", "", xml)
    return (text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&apos;", "'"))


def extract_text(data, filename):
    name = (filename or "").lower()
    if name.endswith(".pdf"):
        text = _pdf_text(data)
    elif name.endswith(".docx"):
        text = _docx_text(data)
    elif name.endswith(".txt"):
        for enc in ("utf-8-sig", "cp949"):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise ValueError("텍스트 파일의 문자 인코딩을 읽을 수 없습니다.")
    else:
        raise ValueError("PDF, Word(.docx), 텍스트(.txt) 파일만 읽을 수 있습니다.")
    if len(re.sub(r"\s", "", text)) < 200:
        raise ValueError("파일에서 글자를 거의 읽지 못했습니다. 사진/스캔으로 만든 PDF는 분석할 수 없습니다. 글자가 들어 있는 PDF나 Word 파일을 올려 주세요.")
    return text


# ------------------------------------------------------------ 문제 나누기
def parse_questions(text):
    """-> [{'num': 문제지에 적힌 번호, 'stem': 본문, 'choices': [선택지 글자...]}] (문제지에 나온 순서)"""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    has_circ = sum(1 for ch in text if ch in CIRC) >= 8
    start_re = Q_START_ANY if has_circ else Q_START_DOT
    qs, cur, last = [], None, 0
    for ln in lines:
        m = start_re.match(ln)
        if m:
            num = int(m.group(1))
            if num == last + 1 or (num == 1 and last >= 2):          # 순서대로 이어지거나 새 과목이 1번부터 다시 시작
                if cur:
                    qs.append(cur)
                cur, last = {"num": num, "lines": [m.group(2)]}, num
                continue
        if cur is not None:
            cur["lines"].append(ln.strip())
    if cur:
        qs.append(cur)
    out = []
    for q in qs:
        body = " ".join(x for x in q["lines"] if x).strip()
        stem, choices = _split_choices(body)
        out.append({"num": q["num"], "stem": stem, "choices": choices})
    return out


def _split_choices(body):
    marks = [(i, CIRC.index(ch)) for i, ch in enumerate(body) if ch in CIRC]
    if len(marks) >= 2:
        stem = body[:marks[0][0]].strip()
        parts = {}
        for k, (pos, idx) in enumerate(marks):
            end = marks[k + 1][0] if k + 1 < len(marks) else len(body)
            parts.setdefault(idx, body[pos + 1:end].strip())
        return stem, [parts.get(i, "") for i in range(max(parts) + 1)]
    ms = list(re.finditer(r"(?:^|\s)([1-5])\)\s", body))
    if len(ms) >= 3:
        stem = body[:ms[0].start()].strip()
        parts = {}
        for k, m in enumerate(ms):
            end = ms[k + 1].start() if k + 1 < len(ms) else len(body)
            parts.setdefault(int(m.group(1)) - 1, body[m.end():end].strip())
        return stem, [parts.get(i, "") for i in range(max(parts) + 1)]
    return body, []


# ------------------------------------------------------------ 유사도 / 짝짓기
def _grams(t):
    t = re.sub(r"[^0-9A-Za-z가-힣]", "", t or "")
    if len(t) < 2:
        return {t} if t else set()
    return {t[i:i + 2] for i in range(len(t) - 1)}


def _dice(a, b):
    if not a and not b:
        return 0.0
    return 2 * len(a & b) / (len(a) + len(b))


def hungarian(cost):
    """최소 비용 짝짓기 (정사각 행렬). 각 행에 대응하는 열 번호 목록을 돌려준다."""
    n = len(cost)
    INF = float("inf")
    u, v, p, way = [0.0] * (n + 1), [0.0] * (n + 1), [0] * (n + 1), [0] * (n + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [INF] * (n + 1)
        used = [False] * (n + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], INF, 0
            for j in range(1, n + 1):
                if not used[j]:
                    cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j], way[j] = cur, j0
                    if minv[j] < delta:
                        delta, j1 = minv[j], j
            for j in range(n + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    res = [0] * n
    for j in range(1, n + 1):
        if p[j]:
            res[p[j] - 1] = j - 1
    return res


def match(qa, qb, n_choices):
    """두 문제 목록을 짝짓는다. -> [{'a': A번호(1~), 'b': B번호(1~), 'score': 0~1, 'perm': [...]}] (A번호 순)"""
    n = max(len(qa), len(qb))
    ga = [(_grams(q["stem"]), _grams("".join(q["choices"]))) for q in qa]
    gb = [(_grams(q["stem"]), _grams("".join(q["choices"]))) for q in qb]

    def score(i, j):
        s_stem = _dice(ga[i][0], gb[j][0])
        if ga[i][1] and gb[j][1]:
            return 0.65 * s_stem + 0.35 * _dice(ga[i][1], gb[j][1])
        return s_stem

    sc = [[score(i, j) if i < len(qa) and j < len(qb) else 0.0 for j in range(n)] for i in range(n)]
    cols = hungarian([[1.0 - x for x in row] for row in sc])
    out = []
    for i in range(len(qa)):
        j = cols[i]
        if j >= len(qb):
            continue
        out.append({"a": i + 1, "b": j + 1, "score": round(sc[i][j], 3), "perm": _choice_perm(qa[i]["choices"], qb[j]["choices"], n_choices)})
    return out


def _choice_perm(ca, cb, n):
    """첫 유형 선택지 i+1 이 둘째 유형의 몇 번 선택지인지 (글자로 찾고, 못 찾으면 같은 번호)."""
    ident = list(range(1, n + 1))
    if len(ca) != n or len(cb) != n or not all(ca) or not all(cb):
        return ident
    ga, gb = [_grams(x) for x in ca], [_grams(x) for x in cb]
    cost = [[1.0 - _dice(ga[i], gb[j]) for j in range(n)] for i in range(n)]
    cols = hungarian(cost)
    if sorted(cols) != list(range(n)):
        return ident
    # 글자가 전혀 겹치지 않는 선택지(예: 숫자만 있는 선택지)는 신뢰할 수 없으므로 같은 번호로 둔다
    if any(_dice(ga[i], gb[cols[i]]) < 0.3 for i in range(n)):
        return ident
    return [c + 1 for c in cols]


def analyze(data_a, name_a, data_b, name_b, n_questions, n_choices):
    """두 문제지 파일을 분석한다. -> {'pairs': [...], 'n_a', 'n_b', 'warnings': [...]}"""
    ta, tb = extract_text(data_a, name_a), extract_text(data_b, name_b)
    qa, qb = parse_questions(ta), parse_questions(tb)
    warnings = []
    for label, qs, txt in (("A형", qa, ta), ("B형", qb, tb)):
        if len(qs) != n_questions:
            head = re.sub(r"\s+", " ", txt[:200])
            raise ValueError(f"{label} 문제지에서 문제를 {len(qs)}개 찾았습니다 (시험 설정은 {n_questions}문항). "
                             f"문제 번호가 '1.' 또는 '1)' 모양으로 시작하는지 확인하세요. 읽은 글자 앞부분: {head}")
    pairs = match(qa, qb, n_choices)
    low = [p["a"] for p in pairs if p["score"] < 0.55]
    if low:
        warnings.append(f"유사도가 낮은 문제 {len(low)}개(A형 {', '.join(map(str, low[:12]))}{' ...' if len(low) > 12 else ''})는 직접 확인하세요.")
    for p in pairs:
        p["a_text"] = qa[p["a"] - 1]["stem"][:70]
        p["b_text"] = qb[p["b"] - 1]["stem"][:70]
        p["perm_changed"] = p["perm"] != list(range(1, n_choices + 1))
    return {"pairs": pairs, "n_a": len(qa), "n_b": len(qb), "warnings": warnings,
            "a_texts": [q["stem"][:70] for q in qa], "b_texts": [q["stem"][:70] for q in qb]}
