"""내장 가상 답안지(static/samples)로 테스트 실행/판정/과락/삭제 기능을 검증한다. (양식 PDF 불필요)"""
import os, sys, tempfile
os.environ["OMR_DATA_DIR"] = tempfile.mkdtemp()
from app import app, state

c = app.test_client()
assert c.post("/api/setup", json={"password": "testpass123"}).status_code == 200
keys_before = {k: list(v) for k, v in state["answer_keys"].items()}

info = c.get("/api/test/info").get_json()
assert len(info["files"]) == 6 and len(info["students"]) == 5
assert c.get("/samples/student1.jpg").status_code == 200

d = c.post("/api/test/run").get_json()
assert d["status"] == "success" and len(d["results"]) == 5
for r in d["results"]:
    assert r["is_test"] and r["verify"]["match"] == 100, r["verify"]
    assert r["total_score"] == r["verify"]["expected_correct"] * 5, r  # 기본 문항당 5점
assert state["answer_keys"] == keys_before, "테스트가 실제 정답 설정을 바꿨음"
print("테스트 실행 OK:", {r["student_id"]: (r["total_score"], r["result"]) for r in d["results"]})

# 재실행하면 테스트 결과는 교체(중복 없음)
c.post("/api/test/run"); assert len(c.get("/api/results").get_json()) == 5

# 기본값: 문항당 5점(과목 100/총 500), 합격 총점 300, 과락 40점(한 과목이라도 미만이면 과락)
assert (state["score_per_q"], state["pass_cutoff"], state["subject_fail_cutoff"]) == (5.0, 300.0, 40.0)
IDS = ("202600001", "202600002", "202600003", "202600004", "202600005")
def results():
    r = {x["student_id"]: x for x in c.get("/api/results").get_json()}
    return [r[i] for i in IDS]
rs = results()
assert [r["total_score"] for r in rs] == [500, 450, 410, 470, 435]
assert [r["result"] for r in rs] == ["합격", "합격", "합격", "합격", "과락"]
assert rs[4]["fail_subjects"] == ["C"] and rs[4]["subject_scores"]["C"] == 35 and rs[4]["total_score"] >= 300  # 총점이 높아도 한 과목 과락이면 과락
# 합격 총점 460 → 450·410 불합격(과락 아님), 470 합격
c.post("/api/config", json={"pass_cutoff": 460}); assert [r["result"] for r in results()] == ["합격", "불합격", "불합격", "합격", "과락"]
c.post("/api/config", json={"pass_cutoff": 300})
# 과락 점수 80 → 3번 학생(C,E=75점)도 과락, 2번(최저 80점)은 경계값이라 과락 아님(미만일 때만)
c.post("/api/config", json={"subject_fail_cutoff": 80}); rs = results()
assert [r["result"] for r in rs] == ["합격", "합격", "과락", "합격", "과락"] and set(rs[2]["fail_subjects"]) == {"C", "E"}, [(r["result"], r["fail_subjects"]) for r in rs]
# 0 = 과락 사용 안 함 → 모두 총점으로만 판정
c.post("/api/config", json={"subject_fail_cutoff": 0}); assert {r["result"] for r in results()} == {"합격"}
c.post("/api/config", json={"subject_fail_cutoff": 40})
# 문항당 배점을 바꾸면 이미 채점된 결과의 점수도 즉시 재계산
c.post("/api/config", json={"score_per_q": 2.5}); assert results()[0]["total_score"] == 250
c.post("/api/config", json={"score_per_q": 5})
print("판정/과락/배점 OK")

# 엑셀에 판정·과락 컬럼 / 테스트 구분
import io, pandas as pd
x = pd.read_excel(io.BytesIO(c.get("/api/export/excel").data), sheet_name="성적총괄표")
assert {"구분", "판정"} <= set(x.columns) and (x["구분"] == "테스트").all()

# 테스트 결과만 삭제 → 실제 결과(직접 채점한 것)는 유지
import cv2
from test_synthetic import make_sheet
import random; random.seed(1); key = [random.randint(1, 5) for _ in range(100)]
p = os.path.join(tempfile.mkdtemp(), "real.png"); cv2.imwrite(p, make_sheet("A", "111111111", key))
c.post("/api/grade/students", data={"files": [(open(p, "rb"), "real.png")]})
assert len(c.get("/api/results").get_json()) == 6
c.delete("/api/results?scope=test")
left = c.get("/api/results").get_json(); assert len(left) == 1 and not left[0]["is_test"]
print("테스트만 삭제 OK"); print("ALL PASS")
