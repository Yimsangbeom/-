"""응시자 명단: 수험번호로 이름 채우기(엑셀 반영), 못 읽은 번호 복구, 명단에 없는 번호 표시, 미응시자, 직접 입력한 이름 유지."""
import io, os, tempfile
import pandas as pd
os.environ["OMR_DATA_DIR"] = tempfile.mkdtemp()
from app import app, state

c = app.test_client()
assert c.post("/api/setup", json={"password": "testpass123"}).status_code == 200
files = [(io.BytesIO(open(f"static/samples/student{i}.jpg", "rb").read()), f"student{i}.jpg") for i in (1, 2, 5)]
c.post("/api/grade/students", data={"files": files}, content_type="multipart/form-data")
res = {x["student_id"]: x for x in c.get("/api/results").get_json()}
ids = sorted(res); assert len(ids) == 3
c.post(f"/api/results/{res[ids[2]]['id']}/name", json={"name": "직접입력"})            # 사람이 입력한 이름

def xlsx(rows, header=("순번", "이름", "소속", "수험번호")):
    b = io.BytesIO(); pd.DataFrame(rows, columns=list(header)).to_excel(b, index=False); b.seek(0); return b
roster = [(1, "가나다", "A기관", int(ids[0])), (2, "라마바", "B기관", f"{ids[1]}.0"), (3, "사아자", "C기관", ids[2]), (4, "차카타", "D기관", "202699999")]
r = c.post("/api/roster", data={"file": (xlsx(roster), "명단.xlsx")}, content_type="multipart/form-data").get_json()
assert r["status"] == "success" and r["count"] == 4 and r["graded"] == 3 and [a["id"] for a in r["absent"]] == ["202699999"], r
now = {x["student_id"]: x for x in c.get("/api/results").get_json()}
assert now[ids[0]]["name"] == "가나다" and now[ids[0]]["name_src"] == "명단" and now[ids[1]]["name"] == "라마바"
assert now[ids[2]]["name"] == "직접입력" and now[ids[2]].get("name_src") is None, "직접 입력한 이름을 덮어씀"
df = pd.read_excel(io.BytesIO(c.get("/api/export/excel?scope=real").data), sheet_name=None, dtype=str)
t = df["성적총괄표"]; assert dict(zip(t["수험번호"], t["이름"].fillna(""))) == {ids[0]: "가나다", ids[1]: "라마바", ids[2]: "직접입력"}
assert "파일명" not in t.columns and list(df["미응시자"]["이름"]) == ["차카타"]
# 명단에 없는 번호 표시
r2 = c.post("/api/roster", data={"file": (xlsx([(1, "가나다", "A", int(ids[0]))]), "명단.xlsx")}, content_type="multipart/form-data").get_json()
now = {x["student_id"]: x for x in c.get("/api/results").get_json()}
assert now[ids[1]]["roster_flags"] == ["명단에 없는 수험번호"] and now[ids[1]]["needs_review"] and now[ids[1]]["name"] == "" and r2["unknown"] == 2, now[ids[1]]
# 못 읽은 자리(?) 복구: 유일하면 자동 복구, 여러 명이면 후보
import app as A
with A.lock:
    it = next(x for x in A.graded_results if x["student_id"] == ids[0])
    it["student_id"] = ids[0][:-2] + "??"
    A.apply_roster(it)
    assert it["student_id"] == ids[0] and it["id_read"].endswith("??") and "수험번호 자동 복구(확인 필요)" in it["roster_flags"], it
    it["student_id"] = ids[0][:-1] + "?"; it.pop("id_read")
    A.apply_roster(it, {"entries": {ids[0]: "가", ids[0][:-1] + "9": "나"}}); assert it["id_candidates"] and "후보 2명" in it["roster_flags"][0]
    A.apply_roster(it, {}); assert not it.get("roster_flags")
# 잘못된 파일
bad = c.post("/api/roster", data={"file": (xlsx([(1, "가", "A", "1")], ("a", "b", "c", "d")), "x.xlsx")}, content_type="multipart/form-data")
assert bad.status_code == 400 and "수험번호" in bad.get_json()["message"]
# 삭제하면 명단으로 채운 이름/표시 제거(직접 입력은 유지)
d = c.delete("/api/roster").get_json(); assert d["registered"] is False
now = {x["student_id"]: x for x in c.get("/api/results").get_json()}
assert all(not x.get("roster_flags") for x in now.values()) and all(x["name"] in ("", "직접입력") for x in now.values())
print("ALL PASS")
