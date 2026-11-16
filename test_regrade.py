"""다시 채점: 저장된 원본으로 현재 정답/설정을 적용해 다시 채점(번호·이름 유지), 삭제 시 원본도 삭제."""
import io, os, tempfile, time
os.environ["OMR_DATA_DIR"] = tempfile.mkdtemp()
from app import app, SOURCE_DIR

c = app.test_client()
assert c.post("/api/setup", json={"password": "testpass123"}).status_code == 200
files = [(io.BytesIO(open(f"static/samples/student{i}.jpg", "rb").read()), f"student{i}.jpg") for i in (1, 2)]
r = c.post("/api/grade/students", data={"files": files}, content_type="multipart/form-data").get_json()
assert len(r["results"]) == 2
before = {x["id"]: x for x in c.get("/api/results").get_json()}
assert all(x["src"] and os.path.exists(os.path.join(SOURCE_DIR, x["src"])) for x in before.values())
rid = list(before)[0]
c.post(f"/api/results/{rid}/name", json={"name": "홍길동"})
# 정답을 바꾸고(모두 1번) 다시 채점
assert c.post("/api/config", json={"answer_keys": {"A": [1] * 100, "B": [1] * 100}}).status_code == 200
j = c.post("/api/regrade", json={}).get_json(); assert j["status"] == "started" and j["total"] == 2, j
for _ in range(120):
    p = c.get("/api/grade/progress/" + j["job"]).get_json()
    if p["status"] == "done": break
    time.sleep(0.5)
assert p["done"] == 2 and p["n_results"] == 2 and not p["errors"], p
after = {x["id"]: x for x in c.get("/api/results").get_json()}
assert set(after) == set(before) and len(after) == 2
assert after[rid]["name"] == "홍길동" and after[rid]["filename"] == before[rid]["filename"]
assert all(after[i]["total_score"] != before[i]["total_score"] for i in before), "정답 변경이 재채점에 반영되지 않음"
# 일부만 다시 채점 / 삭제 시 원본 삭제
assert c.post("/api/regrade", json={"ids": [rid]}).get_json()["total"] == 1
assert c.post("/api/regrade", json={"ids": [99999]}).status_code == 400
src = os.path.join(SOURCE_DIR, after[rid]["src"])
c.post("/api/results/delete", json={"ids": [rid]})
time.sleep(0.5)
assert not os.path.exists(src)
print("ALL PASS")
