"""문제지(A/B) 분석으로 대응표 만들기: 문제 나누기, 대응/선택지 순서 찾기, 저장, 정답 자동 생성, 합산 통계."""
import io, os, random, tempfile, zipfile
os.environ["OMR_DATA_DIR"] = tempfile.mkdtemp()
import fitz
import pandas as pd
import exam as exam_mod
import paper_match, stats
from app import app, state

rng = random.Random(11)
WORDS = [w for w in ("중독 상담 청소년 스마트폰 게임 과의존 진단 척도 평가 면담 동기 강화 인지 행동 치료 가족 학교 예방 정책 법률 "
                     "개입 위기 보호 요인 위험 상담자 윤리 비밀 보장 사례 개념화 목표 설정 단계 기법 질문 반영 공감 저항 다루기 재발 "
                     "방지 프로그램 집단 놀이 미술 음악 부모 교육 센터 지원 체계 연계 통계 조사 결과 해석 기준 점수 도구 개발 타당도").split()]
SUBJ = ["인터넷중독의 이해", "인터넷중독의 진단과 평가", "인터넷중독 상담기법", "인터넷중독 상담의 이론과 실제", "인터넷중독의 정책과 법"]
N, NCH = 100, 5
def phrase(k): return " ".join(rng.sample(WORDS, k))
base = []        # 문제 원본: (과목, 본문, 선택지 5개)  (A 순서)
seen = set()
while len(base) < N:
    stem = phrase(9) + " 에 대한 설명으로 옳은 것은?"
    if stem in seen: continue
    seen.add(stem); base.append((len(base) // 20, stem, [phrase(4) for _ in range(NCH)]))
# B형: 과목 안에서 문제 순서를 섞고, 일부 문제는 선택지 순서도 섞는다
b_order, b_perm = [], {}          # b_order[j] = B형 j+1번이 되는 A형 문제 번호(0부터), b_perm[a] = A 선택지 i -> B 선택지
for s in range(5):
    ids = list(range(s * 20, s * 20 + 20)); rng.shuffle(ids); b_order += ids
for a in range(N):
    p = list(range(1, NCH + 1))
    if rng.random() < 0.3: rng.shuffle(p)
    b_perm[a] = p                 # A 선택지 i+1 은 B 에서 p[i]번
CIRC = "①②③④⑤"

def paper_lines(order, perm):
    lines, n = [], 0
    for s in range(5):
        lines.append(f"제{s + 1}과목 {SUBJ[s]}")
        for k in range(20):
            a = order[n]; n += 1
            _, stem, ch = base[a]
            lines.append(f"{k + 1}. {stem}")
            slots = [None] * NCH
            for i in range(NCH): slots[perm[a][i] - 1] = ch[i]        # perm 이 가리키는 위치에 A 선택지 i 배치
            lines.append("  ".join(f"{CIRC[j]} {slots[j]}" for j in range(NCH)))
    return lines

def make_pdf(lines):
    doc = fitz.open(); page = doc.new_page(); y = 60
    for ln in lines:
        for k in range(0, len(ln), 48):                     # 쪽 너비 안에서 줄바꿈 (실제 문제지처럼 한 문제가 여러 줄)
            if y > 800:
                page = doc.new_page(); y = 60
            page.insert_text((50, y), ln[k:k + 48], fontname="korea", fontsize=9)
            y += 13
        y += 4
    return doc.tobytes()

def make_pdf_2col(lines):
    """2단 편집 문제지: 왼쪽 단을 위에서 아래로 다 읽은 뒤 오른쪽 단."""
    doc = fitz.open(); page = doc.new_page(); y, col = 60, 0
    for ln in lines:
        for k in range(0, len(ln), 26):
            if y > 800:
                if col == 0: col, y = 1, 60
                else: page = doc.new_page(); col, y = 0, 60
            page.insert_text((40 + col * 290, y), ln[k:k + 26], fontname="korea", fontsize=9)
            y += 13
        y += 4
    return doc.tobytes()


def make_docx(lines):
    paras = "".join(f"<w:p><w:r><w:t>{ln.replace('&', '&amp;')}</w:t></w:r></w:p>" for ln in lines)
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("word/document.xml", f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>{paras}</w:body></w:document>')
    return b.getvalue()

a_lines = paper_lines(list(range(N)), {a: list(range(1, NCH + 1)) for a in range(N)})
# A형은 원래 순서/선택지 그대로, B형은 섞음. (B 선택지: A 선택지 i+1 -> B 선택지 b_perm 이므로 위 함수는 같은 규칙으로 배치)
b_lines = paper_lines(b_order, b_perm)
exp = {}                                       # 정답 대응: A번호(1~) -> (B번호, perm)
for j, a in enumerate(b_order): exp[a + 1] = (j + 1, b_perm[a])

# --- 분석 (A: PDF, B: docx)
r = paper_match.analyze(make_pdf(a_lines), "A.pdf", make_docx(b_lines), "B.docx", N, NCH)
assert r["n_a"] == N and r["n_b"] == N, r
ok = sum(1 for p in r["pairs"] if (p["b"], p["perm"]) == exp[p["a"]])
assert ok == N, f"대응 {ok}/{N}"
print("분석 OK: 문제 대응 100/100, 선택지 순서 포함")
r2 = paper_match.analyze(make_pdf_2col(a_lines), "A.pdf", make_pdf_2col(b_lines), "B.pdf", N, NCH)
assert sum(1 for p in r2["pairs"] if (p["b"], p["perm"]) == exp[p["a"]]) == N, "2단 PDF 분석 실패"
print("2단 편집 PDF OK")
# 같은 과목 안에서만 짝짓기: 다른 과목에 거의 똑같은 문제가 있어도 같은 과목의 문제를 고른다
import copy as _copy
qa_ = [{"stem": q[1], "choices": q[2]} for q in base]
qb_ = [{"stem": base[a][1], "choices": [x for x in base[a][2]]} for a in b_order]
dup = _copy.deepcopy(qa_); dup[0] = {"stem": qa_[25]["stem"], "choices": qa_[25]["choices"]}          # A 1번(과목1)이 과목2의 26번과 똑같은 문제
groups = [i // 20 for i in range(N)]
mm = paper_match.match(dup, qb_, NCH, groups)
assert groups[mm[0]["a"] - 1] == groups[mm[0]["b"] - 1], "다른 과목과 짝지어짐"
print("같은 과목 제한 OK")
# 파일 형식/오류
try: paper_match.extract_text(b"x" * 10, "a.hwp"); raise AssertionError
except ValueError as e: assert "PDF" in e.args[0]
try: paper_match.extract_text(make_pdf(["1. 짧다"]), "a.pdf"); raise AssertionError
except ValueError as e: assert "글자" in e.args[0]
try: paper_match.analyze(make_pdf(a_lines[:60]), "A.pdf", make_pdf(b_lines), "B.pdf", N, NCH); raise AssertionError
except ValueError as e: assert "A형 문제지" in e.args[0] and "문제를" in e.args[0]

# --- API: 분석 -> 저장 -> 정답 자동 생성/일치
c = app.test_client()
assert c.post("/api/setup", json={"password": "testpass123"}).status_code == 200
pa = c.post("/api/paper/A", data={"file": (io.BytesIO(make_pdf(a_lines)), "A형.pdf")}, content_type="multipart/form-data").get_json()
pb = c.post("/api/paper/B", data={"file": (io.BytesIO(make_docx(b_lines)), "B형.docx")}, content_type="multipart/form-data").get_json()
assert pa["status"] == "success" and pa["count"] == N and pb["count"] == N and pa["form"] == "A"
pp = c.get("/api/paper").get_json()["papers"]; assert pp["A"]["filename"] == "A형.pdf" and pp["B"]["count"] == N
assert c.post("/api/paper/Z", data={"file": (io.BytesIO(b"x"), "a.pdf")}, content_type="multipart/form-data").status_code == 400
assert c.post("/api/paper/A", data={"file": (io.BytesIO(make_pdf(a_lines[:60])), "A.pdf")}, content_type="multipart/form-data").status_code == 422      # 문제 수 부족
assert c.get("/api/paper").get_json()["papers"]["A"]["filename"] == "A형.pdf"                                          # 실패해도 기존 등록 유지
j = c.post("/api/formmap/analyze").get_json()
assert j["status"] == "success" and len(j["pairs"]) == N and j["forms"] == ["A", "B"] and len(j["groups"]) == N
assert sum(1 for p in j["pairs"] if (p["b"], p["perm"]) == exp[p["a"]]) == N
assert all(j["groups"][p["a"] - 1] == j["groups"][p["b"] - 1] for p in j["pairs"]), "다른 과목끼리 짝지어짐"
assert c.delete("/api/paper/B").status_code == 200 and c.post("/api/formmap/analyze").status_code == 400            # B 미등록이면 분석 불가
c.post("/api/paper/B", data={"file": (io.BytesIO(make_docx(b_lines)), "B형.docx")}, content_type="multipart/form-data")
keyA = [rng.randint(1, NCH) for _ in range(N)]
assert c.post("/api/config", json={"answer_keys": {"A": keyA}}).status_code == 200            # 아직 대응표 없음(half_swap)
sv = c.post("/api/formmap", json={"pairs": [{"a": p["a"], "b": p["b"], "perm": p["perm"]} for p in j["pairs"]]}).get_json()
assert sv["status"] == "success" and state["exam"]["form_rule"] == "custom" and len(state["exam"]["form_map"]) == N
keyB = state["answer_keys"]["B"]
for a in range(1, N + 1):                      # B 정답 = A 정답을 대응표(문제/선택지 순서)로 옮긴 것
    b, perm = exp[a]
    assert keyB[b - 1] == perm[keyA[a - 1] - 1], a
assert exam_mod.key_conflicts(state["exam"], keyA, keyB) == [] and sv["key_conflicts"] == []
assert c.post("/api/formmap", json={"pairs": [{"a": 1, "b": 1}] * N}).status_code == 400          # 중복 대응 거부
assert c.post("/api/formmap", json={"pairs": []}).status_code == 400
# 시험 설정을 다시 저장해도 대응표 유지
ex0 = c.get("/api/exams").get_json()
eid = ex0["active"] if "active" in ex0 else state and None
cfg = c.get("/api/config").get_json(); assert cfg["exam"]["form_rule"] == "custom" and len(cfg["exam"]["form_map"]) == N
# 엑셀 대응표 내려받기/올리기
x = c.get("/api/formmap/template"); assert x.status_code == 200
df = pd.read_excel(io.BytesIO(x.data)); assert len(df) == N
back = c.post("/api/formmap/excel", data={"file": (io.BytesIO(x.data), "대응.xlsx")}, content_type="multipart/form-data").get_json()
assert back["status"] == "success" and len(back["pairs"]) == N and back["pairs"][0]["perm"]

# --- 합산 통계: 대응표를 쓰면 같은 문제끼리 합쳐져 정상 문항은 역변별/정답키 의심이 거의 없고, 대응표 없이(같은 번호) 합치면 망가진다
ex = state["exam"]
def make_items(n_each):
    items = []
    for form in ("A", "B"):
        key = keyA if form == "A" else keyB
        for i in range(n_each):
            ab = rng.random()                                  # 능력
            sa, total = {}, 0
            for q in range(1, N + 1):
                a = q if form == "A" else exam_mod.other_q(ex, q, "B")
                hard = (a % 10) / 12                         # 문제 난이도(A 번호 기준: 같은 문제는 양쪽에서 같은 난이도)
                right = rng.random() < max(0.05, min(0.97, ab * 1.1 - hard + 0.3))
                if right: sa[str(q)] = str(key[q - 1])
                else: sa[str(q)] = str(rng.choice([c_ for c_ in range(1, NCH + 1) if c_ != key[q - 1]]))
                total += right
            subj_sc = {cd: 0 for cd in exam_mod.codes(ex)}
            for q in range(1, N + 1):
                if sa[str(q)] == str(key[q - 1]): subj_sc[exam_mod.codes(ex)[exam_mod.subject_index(ex, q)]] += 5
            tot = sum(subj_sc.values())
            items.append({"exam_type": form, "student_answers": sa, "subject_scores": subj_sc, "total_score": tot, "average": tot / 5,
                          "result": "합격", "fail_subjects": [], "needs_review": False, "blank_count": 0, "multi_count": 0, "_key": key})
    return items
items = make_items(40)
st = stats.compute(items, 5.0, ex)
m = st["forms"][0]
assert m["label"].startswith("통합") and m["n"] == 80 and st["paired"] and not st["pair_invalid"] and st["blocks"] == []
bad_flags = [x["q"] for x in m["items"] if "revdisc" in x["flags"] or "key" in x["flags"]]
assert len(bad_flags) <= 3, bad_flags
assert all(x["alt_q"] == exam_mod.other_q(ex, x["q"], "A") for x in m["items"])
fb = {f["label"][:2]: f for f in st["forms"]}["B형"]
assert fb["items"][0]["alt_q"] == exam_mod.other_q(ex, 1, "B") and exam_mod.other_q(ex, exam_mod.other_q(ex, 7, "A"), "B") == 7
# 대응표 없이 같은 번호끼리 합치면(옛 가정) 잘못된 문제를 섞어 오류 표시가 늘어난다 = 대응표의 효과
ex_none = {**ex, "form_rule": "none"}
st2 = stats.compute(items, 5.0, ex_none)
assert st2["paired"] is False and not st2["forms"][0]["label"].startswith("통합")
print(f"합산 통계 OK: 대응표 적용 시 문제 표시 {len(bad_flags)}건")
# 대응 규칙을 없애면 '없음'
assert c.delete("/api/formmap").get_json()["rule"] == "none" and state["exam"]["form_rule"] == "none" and "form_map" not in state["exam"]
print("ALL PASS")
