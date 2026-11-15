"""시험 설정(여러 시험 관리): 만들기/검증/전환/결과 분리/구조 잠금/삭제/영속성/통계·엑셀 반영. (실제 시험과 전혀 다른 구성으로 검증)"""
import io, os, random, subprocess, sys, tempfile, zipfile
os.environ["OMR_DATA_DIR"] = tempfile.mkdtemp()
import cv2, pandas as pd
from test_synthetic import make_sheet_exam
from app import app

c = app.test_client()
assert c.post("/api/setup", json={"password": "testpass123"}).status_code == 200
lst = c.get("/api/exams").get_json()
assert lst["active"] == "default" and len(lst["exams"]) == 1 and lst["exams"][0]["questions"] == 100
assert lst["layouts"][0]["capacity"] == 100 and lst["exam_info"]["is_default_structure"] is True

# --- 검증: 잘못된 시험 구성은 한글 메시지와 함께 거부
base = {"name": "디지털 역량 검정", "title": "2026 디지털 역량 검정 답안지", "layout": "nia_a4_100", "choices": 4, "id_digits": 8,
        "forms": ["A"], "form_rule": "none",
        "subjects": [{"name": "기초", "short": "기초", "count": 30}, {"name": "실무", "short": "실무", "count": 30}, {"name": "응용", "short": "응용", "count": 20}]}
for bad, kw in [({**base, "subjects": [{"name": "a", "count": 101}]}, "용량"), ({**base, "name": " "}, "시험명"),
                ({**base, "choices": 6}, "선택지"), ({**base, "id_digits": 12}, "수험번호"),
                ({**base, "forms": ["A", "B"], "form_rule": "half_swap", "subjects": [{"name": "a", "count": 21}]}, "짝수"),
                ({**base, "forms": ["C"]}, "유형"), ({**base, "layout": "nope"}, "양식")]:
    r = c.post("/api/exams", json={"exam": bad}); assert r.status_code == 400 and kw in r.get_json()["message"], (kw, r.get_json())

# --- 새 시험 만들고 전환
eid = c.post("/api/exams", json={"exam": base}).get_json()["id"]
assert len(c.get("/api/exams").get_json()["exams"]) == 2
assert c.post(f"/api/exams/{eid}/activate").status_code == 200
cfg = c.get("/api/config").get_json()
assert cfg["exam"]["name"] == "디지털 역량 검정" and cfg["exam_info"]["total_questions"] == 80 and cfg["exam_info"]["codes"] == ["A", "B", "C"]
assert list(cfg["answer_keys"]) == ["A"] and len(cfg["answer_keys"]["A"]) == 80 and max(cfg["answer_keys"]["A"]) <= 4
assert c.get("/api/test/info").get_json()["available"] is False and c.post("/api/test/run").status_code == 400   # 테스트 샘플은 기본 구성 전용
ex = cfg["exam"]

# --- 정답 등록 검증(선택지 4개: 5는 불가, 길이 부족 불가)
random.seed(4); key = [random.randint(1, 4) for _ in range(80)]
assert c.post("/api/config", json={"answer_keys": {"A": key[:50]}}).status_code == 400
assert c.post("/api/config", json={"answer_keys": {"A": key[:79] + [5]}}).status_code == 400
assert c.post("/api/config", json={"answer_keys": {"A": key}}).status_code == 200
assert c.get("/api/config").get_json()["answer_keys"]["A"] == key and c.get("/api/config").get_json()["keys_set"] == {"A": True}

# --- 가상 답안지 채점: 과목별 점수가 새 과목 구성(30/30/20)으로 계산되는지
d = tempfile.mkdtemp()
def put(name, img): p = os.path.join(d, name); cv2.imwrite(p, img); return p
ans1 = list(key)                                              # 만점
ans2 = list(key)
for q in range(1, 11): ans2[q - 1] = key[q - 1] % 4 + 1         # 1과목(1~30) 10문항 오답
for q in range(61, 66): ans2[q - 1] = key[q - 1] % 4 + 1        # 3과목(61~80) 5문항 오답
files = [(open(put("s1.png", make_sheet_exam(ex, "A", "20260001", ans1)), "rb"), "s1.png"),
         (open(put("s2.png", make_sheet_exam(ex, "A", "20260002", ans2, blanks=(40,), doubles=(41,))), "rb"), "s2.png")]
j = c.post("/api/grade/students", data={"files": files}).get_json()
assert j["status"] == "success" and not j["errors"], j
r1, r2 = j["results"]
assert r1["student_id"] == "20260001" and len(r2["student_id"]) == 8 and r1["exam_type"] == "A"          # 수험번호 8자리, 유형 1개
assert r1["subject_scores"] == {"A": 150, "B": 150, "C": 100} and r1["total_score"] == 400 and r1["average"] == round(400 / 3, 1)
assert r2["subject_scores"] == {"A": 100, "B": 145, "C": 75} or r2["subject_scores"]["A"] == 100, r2["subject_scores"]   # B과목: 40번 무표기, 41번 이중 -> 2문항 감점
assert r2["subject_scores"]["B"] == 140 and r2["subject_scores"]["C"] == 75 and r2["blank_count"] == 1 and r2["multi_count"] == 1 and r2["needs_review"]
assert len(r1["student_answers"]) == 80 and r1["key_used"] == key
# 판정: 합격 평균(과목 평균) / 과락(과목 점수 < 40)
assert r1["result"] == "합격"
c.post("/api/config", json={"subject_fail_cutoff": 80}); assert {x["result"] for x in c.get("/api/results").get_json()} == {"합격", "과락"}
c.post("/api/config", json={"subject_fail_cutoff": 40, "pass_average": 130})       # 1번 평균 133.3 합격, 2번 평균 105 불합격
rs = {x["student_id"]: x for x in c.get("/api/results").get_json()}
assert rs["20260001"]["result"] == "합격" and rs["20260002"]["result"] == "불합격"
c.post("/api/config", json={"pass_average": 60})

# --- 통계: 과목/문항/선택지가 새 구성으로
st = c.get("/api/stats?scope=real").get_json()
assert [s["name"] for s in st["subjects"]] == ["기초", "실무", "응용"] and [s["max_score"] for s in st["subjects"]] == [150, 150, 100]
assert st["avg_full"] == round(400 / 3, 2) and st["distribution"][0]["label"] == "0~10%"        # 평균점수 만점 133.3 기준 % 구간
assert [d["count"] for d in st["distribution"]] == [0, 0, 0, 0, 0, 0, 0, 1, 0, 1], st["distribution"]    # 평균 105(78.8%) / 133.3(100%)
assert st["paired"] is False and st["blocks"] == [] and len(st["forms"]) == 1 and len(st["forms"][0]["items"]) == 80
assert set(st["forms"][0]["items"][0]["choices"]) == {"1", "2", "3", "4", "무표기", "이중"} and st["forms"][0]["items"][79]["subject_name"] == "응용"
# --- 엑셀: 열 이름/문항 수가 새 구성, 통계 시트에 차트
xr = c.get("/api/export/excel?scope=real"); xl = pd.ExcelFile(io.BytesIO(xr.data))
cols = list(pd.read_excel(xl, "성적총괄표").columns)
assert "A.기초(150)" in cols and "C.응용(100)" in cols and "총점(400)" in cols and "D.이론(100)" not in " ".join(cols), cols
assert pd.read_excel(xl, "문항별답안").shape[1] == 2 + 80
assert sum(1 for n in zipfile.ZipFile(io.BytesIO(xr.data)).namelist() if n.startswith("xl/charts/chart")) == 6
only = c.get("/api/export/excel?scope=real&part=stats"); xs = pd.ExcelFile(io.BytesIO(only.data)); assert "성적총괄표" not in xs.sheet_names and "과목별통계" in xs.sheet_names

# --- 결과 분리: 기본 시험으로 돌아가면 이 결과가 보이지 않고, 다시 오면 그대로
assert c.post("/api/exams/default/activate").status_code == 200
assert c.get("/api/results").get_json() == [] and c.get("/api/config").get_json()["exam"]["name"] == "인터넷중독전문상담사 자격검정"
c.post("/api/test/run"); assert len(c.get("/api/results").get_json()) == 5
assert c.post(f"/api/exams/{eid}/activate").status_code == 200 and len(c.get("/api/results").get_json()) == 2
sums = {e["id"]: e for e in c.get("/api/exams").get_json()["exams"]}
assert sums[eid]["counts"] == {"real": 2, "test": 0} and sums["default"]["counts"] == {"real": 0, "test": 5}
c.delete("/api/results")                                    # 활성 시험(eid)의 결과만 삭제
assert c.get("/api/results").get_json() == [] and sum(c.get("/api/exams").get_json()["exams"][0]["counts"].values()) == 5

# --- 구조 잠금: 결과가 있으면 구조 변경 불가(이름 변경은 가능), 없으면 가능
c.post("/api/grade/students", data={"files": [(open(put("s3.png", make_sheet_exam(ex, "A", "20260003", ans1)), "rb"), "s3.png")]})
r = c.put(f"/api/exams/{eid}", json={"exam": {**base, "subjects": [{"name": "기초", "short": "기초", "count": 40}, {"name": "실무", "short": "실무", "count": 40}]}})
assert r.status_code == 409 and "채점된 결과" in r.get_json()["message"]
r = c.put(f"/api/exams/{eid}", json={"exam": {**base, "name": "디지털 역량 검정 (개정)", "subjects": [{"name": "기초지식", "short": "기초", "count": 30}, base["subjects"][1], base["subjects"][2]]}})
assert r.status_code == 200 and r.get_json()["structure_changed"] is False and c.get("/api/config").get_json()["exam"]["subjects"][0]["name"] == "기초지식"
assert c.get("/api/config").get_json()["answer_keys"]["A"] == key                          # 이름만 바꾸면 정답 유지
c.delete("/api/results")
r = c.put(f"/api/exams/{eid}", json={"exam": {**base, "subjects": [{"name": "기초", "short": "기초", "count": 40}, {"name": "실무", "short": "실무", "count": 40}]}})
assert r.status_code == 200 and r.get_json()["structure_changed"] is True
assert len(c.get("/api/config").get_json()["answer_keys"]["A"]) == 80 and c.get("/api/config").get_json()["keys_set"] == {"A": False}   # 구조가 바뀌면 정답 초기화
assert c.put("/api/exams/없는시험", json={"exam": base}).status_code == 404

# --- 삭제: 사용 중인 시험은 불가, 다른 시험으로 전환하면 삭제 가능(결과 포함)
assert c.delete(f"/api/exams/{eid}").status_code == 409
c.post("/api/grade/students", data={"files": [(open(put("s4.png", make_sheet_exam({**ex, "subjects": [{"name": "기초", "short": "기초", "count": 40}, {"name": "실무", "short": "실무", "count": 40}]}, "A", "20260004", [1] * 80)), "rb"), "s4.png")]})
c.post("/api/exams/default/activate")
assert c.delete(f"/api/exams/{eid}").status_code == 200 and [e["id"] for e in c.get("/api/exams").get_json()["exams"]] == ["default"]
import db
assert db.Store(os.environ["OMR_DATA_DIR"]).count(exam_id=eid) == {"real": 0, "test": 0} and db.Store(os.environ["OMR_DATA_DIR"]).get_setting("state:" + eid) is None

# --- 복제: 시험 구성 + 기준 설정 복사(정답/결과는 비어 있음)
c.post("/api/config", json={"pass_average": 55, "score_per_q": 2})
cid = c.post("/api/exams", json={"copy_from": "default", "name": "인터넷중독전문상담사 (다음 회차)"}).get_json()["id"]
c.post(f"/api/exams/{cid}/activate"); cfg = c.get("/api/config").get_json()
assert cfg["exam"]["name"].endswith("(다음 회차)") and cfg["exam_info"]["is_default_structure"] and cfg["pass_average"] == 55 and cfg["score_per_q"] == 2
assert cfg["keys_set"] == {"A": False, "B": False} and c.get("/api/results").get_json() == []

# --- 영속성: 서버를 다시 시작해도 마지막 활성 시험/설정 유지
out = subprocess.run([sys.executable, "-c", "import app; print(app.registry['active'], [e['name'] for e in app.registry['list']], app.state['pass_average'])"],
                     env={**os.environ, "OMR_DATABASE_URL": ""}, capture_output=True, text=True).stdout.strip().splitlines()[-1]
assert out.startswith(cid) and "다음 회차" in out and out.endswith("55.0"), out
print("ALL PASS")
