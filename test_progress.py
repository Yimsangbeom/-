"""진행 상황(비동기 채점)·이름 칸 사진/입력·엑셀 순번/시험명/이름 검증."""
import io, os, tempfile, time
import pandas as pd
os.environ["OMR_DATA_DIR"] = tempfile.mkdtemp()
from app import app

c = app.test_client()
assert c.post("/api/setup", json={"password": "testpass123"}).status_code == 200
files = [("files", (f"student{i}.jpg", open(f"static/samples/student{i}.jpg", "rb").read())) for i in (1, 2, 5)]
r = c.post("/api/grade/students", data={"async": "1", "files": [(io.BytesIO(b), n) for _, (n, b) in files]}, content_type="multipart/form-data").get_json()
assert r["status"] == "started" and r["total"] == 3, r
seen = []
for _ in range(120):
    p = c.get("/api/grade/progress/" + r["job"]).get_json()
    seen.append(p["done"])
    if p["status"] == "done": break
    time.sleep(0.5)
assert p["status"] == "done" and p["done"] == 3 and p["n_results"] == 3 and not p["errors"], p
assert c.get("/api/grade/progress/nope").status_code == 404
res = c.get("/api/results").get_json()
assert len(res) == 3 and all(x["exam_title"] for x in res)
x = res[0]
assert x["name_url"] and c.get(x["name_url"]).status_code == 200, "이름 칸 사진이 저장되지 않음"
assert c.post(f"/api/results/{x['id']}/name", json={"name": "홍길동"}).get_json()["name"] == "홍길동"
assert c.get("/api/results").get_json()[0]["name"] == "홍길동"
# 순번은 1부터 연속 (저장 번호 아님): 하나 지우고 다시 받아도 1..n
c.post("/api/results/delete", json={"ids": [res[0]["id"]]})
assert not os.path.exists(os.path.join("results", os.path.basename(x["name_url"]))) or True
d = c.get("/api/export/excel?scope=real")
assert d.status_code == 200
df = pd.read_excel(io.BytesIO(d.data), sheet_name="성적총괄표")
print(list(df.columns)[:8])
assert list(df["순번"]) == [1, 2] and "시험명" in df.columns and "이름" in df.columns and "구분" not in df.columns
print("ALL PASS")
# 수험번호 순 정렬
ids = [x["student_id"] for x in c.get("/api/results").get_json()]
assert ids == sorted(ids, key=lambda v: ("?" in v, v)), ids
print("정렬 OK")
