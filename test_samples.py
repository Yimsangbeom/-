"""가상 스캔 답안지로 전체 흐름(마스터 등록 → 업로드 채점)과 인식 정확도를 검증한다.
사용: python test_samples.py <양식.pdf>"""
import io, os, sys, tempfile
os.environ["OMR_DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools"))
from make_samples import make_all
from app import app

form = sys.argv[1]
out = sys.argv[2] if len(sys.argv) > 2 else tempfile.mkdtemp()
truth, keys = make_all(form, out, seed=int(os.environ.get("SAMPLE_SEED", "7")))
c = app.test_client()
assert c.post("/api/setup", json={"password": "testpass123"}).status_code == 200

for t in "AB":   # 정답지 인식
    j = c.post("/api/grade/master", data={"file": (open(f"{out}/master_{t}.jpg", "rb"), f"master_{t}.jpg")}).get_json()
    assert j["status"] == "success" and j["exam_type"] == t, j
    if t == "A":
        assert j["derived_other"] is True and j["conflicts"] == [], j     # A형만 등록해도 B형 정답이 자동 생성됨
    else:
        assert j["derived_other"] is False and j["conflicts"] == [], j    # B형 정답지도 올리면 A형과 일치 검증(불일치 없음)
    ok = sum(a == b for a, b in zip(j["answers"], keys[t]))
    print(f"마스터 {t}형 인식: {ok}/100 일치, 문제문항={j['problems']}")
    assert ok == 100

files = [(open(f"{out}/{n}", "rb"), n) for n in ("student1.jpg", "student2.jpg", "student5.jpg", "batch_student3_4.pdf")]
j = c.post("/api/grade/students", data={"files": files}).get_json()
print("오류:", j["errors"]); assert not j["errors"]
res = {r["student_id"]: r for r in j["results"]}
bad = 0
for name, tr in truth.items():
    r = res.get(tr["student_id"])
    if not r:
        print(f"{name}: 수험번호 인식 실패"); bad += 1; continue
    exp_ans, exp_score = {}, 0
    for q in range(1, 101):
        m = tr["marks"][q]
        exp_ans[str(q)] = "무표기" if not m else ("이중" if len(m) > 1 else str(m[0]))
        exp_score += len(m) == 1 and m[0] == tr["key"][q - 1]
    wrong_q = [q for q in exp_ans if r["student_answers"].get(q) != exp_ans[q]]
    st = "OK " if (not wrong_q and r["total_score"] / 5 == exp_score and r["exam_type"] == tr["exam_type"]) else "NG "
    bad += st == "NG "
    print(f"{st}{name}: 유형 {r['exam_type']}(기대 {tr['exam_type']}) 맞힌 개수 {r['total_score'] / 5:.0f}(기대 {exp_score}) 점수 {r['total_score']:.0f} "
          f"무표기 {r['blank_count']} 이중 {r['multi_count']} 검수필요={r['needs_review']} 오인식문항={wrong_q}")
print("RESULT:", "ALL OK" if not bad else f"{bad}건 불일치")
sys.exit(1 if bad else 0)
