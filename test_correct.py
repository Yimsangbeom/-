"""답안지 수정(교정): 유형/수험번호/문항 답을 고치면 그 값으로 재채점되고 결과·통계·엑셀에 반영, 되돌리기, 다시 채점에서 제외."""
import io, os, tempfile, time
import pandas as pd
os.environ["OMR_DATA_DIR"] = tempfile.mkdtemp()
from app import app, state

c = app.test_client()
assert c.post("/api/setup", json={"password": "testpass123"}).status_code == 200
keyA = [(i * 7 + i // 3) % 5 + 1 for i in range(100)]
assert c.post("/api/config", json={"answer_keys": {"A": keyA}}).status_code == 200
keyB = state["answer_keys"]["B"]
files = [(io.BytesIO(open(f"static/samples/student{i}.jpg", "rb").read()), f"student{i}.jpg") for i in (1, 2)]
c.post("/api/grade/students", data={"files": files}, content_type="multipart/form-data")
res = c.get("/api/results").get_json(); x = res[0]; rid = x["id"]
assert "orig" not in x
# 모든 문항을 B형 정답으로 교정 + 수험번호/이름
ans = {str(q): str(keyB[q - 1]) for q in range(1, 101)}
r = c.post(f"/api/results/{rid}/correct", json={"answers": ans, "exam_type": "B", "student_id": "261220001", "name": "김교정"}).get_json()
assert r["status"] == "success"; y = r["result"]
assert y["exam_type"] == "B" and y["total_score"] == 500 and y["result"] == "합격" and y["corrected"] and not y["needs_review"] and y["name"] == "김교정" and y["student_id"] == "261220001" and "orig" not in y
assert y["multi_count"] == 0 and y["blank_count"] == 0 and y["key_used"] == [int(v) for v in keyB]
# 일부를 무표기/이중으로 교정하면 점수 반영
ans2 = dict(ans); ans2["1"] = "무표기"; ans2["2"] = "이중"; ans2["3"] = str(keyB[2] % 5 + 1)
y = c.post(f"/api/results/{rid}/correct", json={"answers": ans2, "exam_type": "B", "student_id": "261220001", "name": "김교정"}).get_json()["result"]
assert y["blank_count"] == 1 and y["multi_count"] == 1 and y["correct_count"] == 97 and y["total_score"] == 485, y
# 검증 오류
for bad in ({"answers": ans, "exam_type": "Z", "student_id": "1"}, {"answers": ans, "exam_type": "A", "student_id": "abc"}, {"answers": {**ans, "5": "9"}, "exam_type": "A", "student_id": "1"}):
    assert c.post(f"/api/results/{rid}/correct", json=bad).status_code == 400
# 통계/엑셀 반영
st = c.get("/api/stats?scope=real").get_json(); assert st["n"] == 2
df = pd.read_excel(io.BytesIO(c.get("/api/export/excel?scope=real").data), sheet_name="성적총괄표")
assert (df["교정"] == "교정됨").sum() == 1
# 다시 채점은 교정 건을 건드리지 않음
j = c.post("/api/regrade", json={}).get_json(); assert j["total"] == 1 and j["corrected"] == 1, j
for _ in range(120):
    p = c.get("/api/grade/progress/" + j["job"]).get_json()
    if p["status"] == "done": break
    time.sleep(0.5)
now = {z["id"]: z for z in c.get("/api/results").get_json()}
assert now[rid]["corrected"] and now[rid]["total_score"] == 485
# 되돌리기
u = c.post(f"/api/results/{rid}/uncorrect").get_json(); assert u["status"] == "success" and not u["result"]["corrected"]
assert u["result"]["student_answers"] == x["student_answers"] and u["result"]["total_score"] == x["total_score"] and u["result"]["student_id"] == x["student_id"]
assert c.post(f"/api/results/{rid}/uncorrect").status_code == 400
print("ALL PASS")
