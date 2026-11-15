"""내장 가상 답안지(static/samples)로 테스트 실행/판정/과락/삭제 기능을 검증한다. (양식 PDF 불필요)"""
import os, sys, tempfile
os.environ["OMR_DATA_DIR"] = tempfile.mkdtemp()
from app import app, state

c = app.test_client()
assert c.post("/api/setup", json={"password": "testpass123"}).status_code == 200
keys_before = {k: list(v) for k, v in state["answer_keys"].items()}

info = c.get("/api/test/info").get_json()
assert len(info["files"]) == 5 and len(info["students"]) == 4
assert c.get("/samples/student1.jpg").status_code == 200

d = c.post("/api/test/run").get_json()
assert d["status"] == "success" and len(d["results"]) == 4
for r in d["results"]:
    assert r["is_test"] and r["verify"]["match"] == 100, r["verify"]
    assert r["total_score"] == r["verify"]["expected_score"]
assert state["answer_keys"] == keys_before, "테스트가 실제 정답 설정을 바꿨음"
print("테스트 실행 OK:", {r["student_id"]: (r["total_score"], r["result"]) for r in d["results"]})

# 재실행하면 테스트 결과는 교체(중복 없음)
c.post("/api/test/run"); assert len(c.get("/api/results").get_json()) == 4

# 판정(총점 기준): 기본(합격 60 / 과락 40) → 점수 100/90/82/94 전원 합격
res = {r["student_id"]: r for r in c.get("/api/results").get_json()}
assert all(r["result"] == "합격" for r in res.values()), [r["result"] for r in res.values()]
# 합격 총점 92 → 90, 82 는 불합격(과락 아님)
c.post("/api/config", json={"pass_cutoff": 92}); res = {r["student_id"]: r for r in c.get("/api/results").get_json()}
assert [res[k]["result"] for k in ("202600001", "202600002", "202600003", "202600004")] == ["합격", "불합격", "불합격", "합격"]
# 과락 점수 85 → 82 는 과락, 90 은 불합격(합격 총점 92 미만, 과락 85 이상) — 과락이 불합격보다 우선
c.post("/api/config", json={"fail_cutoff": 85}); res = {r["student_id"]: r for r in c.get("/api/results").get_json()}
assert [res[k]["result"] for k in ("202600001", "202600002", "202600003", "202600004")] == ["합격", "불합격", "과락", "합격"]
# 경계: 총점이 과락 점수와 같으면 과락 아님(미만일 때만 과락), 합격 총점과 같으면 합격(이상)
c.post("/api/config", json={"fail_cutoff": 82, "pass_cutoff": 90}); res = {r["student_id"]: r for r in c.get("/api/results").get_json()}
assert res["202600003"]["result"] == "불합격" and res["202600002"]["result"] == "합격"
# 0 = 과락 사용 안 함
c.post("/api/config", json={"pass_cutoff": 100, "fail_cutoff": 0}); assert {r["result"] for r in c.get("/api/results").get_json()} == {"합격", "불합격"}
c.post("/api/config", json={"pass_cutoff": 60, "fail_cutoff": 40})
print("판정/과락 OK")

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
assert len(c.get("/api/results").get_json()) == 5
c.delete("/api/results?scope=test")
left = c.get("/api/results").get_json(); assert len(left) == 1 and not left[0]["is_test"]
print("테스트만 삭제 OK"); print("ALL PASS")
