"""수험생이 A/B 유형을 잘못 표시했을 때: 양쪽 정답으로 대조해 맞는 유형으로 채점하고 결과에 표시한다."""
import io, os, random, sys, tempfile
os.environ["OMR_DATA_DIR"] = tempfile.mkdtemp()
import cv2, numpy as np
sys.path.insert(0, "tools")
exec(open("test_marks.py").read().split("key = [")[0])      # blank(), pos(), C
from make_samples import fill, scan
import exam as exam_mod
from app import app, state

c = app.test_client()
assert c.post("/api/setup", json={"password": "testpass123"}).status_code == 200
keyA = [(i * 7 + i // 3) % 5 + 1 for i in range(100)]
assert c.post("/api/config", json={"answer_keys": {"A": keyA}}).status_code == 200          # B 는 자동 생성
keyB = state["answer_keys"]["B"]; assert keyA != keyB
rng = random.Random(5)

def sheet(answers, mark_type, sid="202600001"):
    img = blank()
    ix, iy = C["id_grid"]["x_cols"], C["id_grid"]["y_rows"]
    for i, ch in enumerate(sid): fill(img, ix[i], iy[int(ch)], rng)
    if mark_type: fill(img, *C["type_bubbles"][mark_type], rng)
    for q in range(1, 101): fill(img, *pos(q, answers[q - 1]), rng)
    img, _ = scan(img, rng, rot=0.8, shift=(8, -6), persp=2, blur=0.7, q=75)
    ok, buf = cv2.imencode(".jpg", img); return io.BytesIO(buf.tobytes())

def grade(answers, mark_type, sid):
    r = c.post("/api/grade/students", data={"files": [(sheet(answers, mark_type, sid), "s.jpg")]}, content_type="multipart/form-data").get_json()
    assert r["status"] == "success" and not r["errors"], r
    return r["results"][0]

r = grade(keyA, "A", "202600001"); assert r["exam_type"] == "A" and r["type_status"] == "정상" and r["total_score"] == 500 and not r["needs_review"], r
r = grade(keyB, "B", "202600002"); assert r["exam_type"] == "B" and r["type_status"] == "정상" and r["total_score"] == 500, r
# B형 답안인데 A로 표시 -> B 로 채점 + 표시
r = grade(keyB, "A", "202600003")
assert r["exam_type"] == "B" and r["marked_type"] == "A" and r["type_status"] == "유형불일치" and r["total_score"] == 500 and r["needs_review"] and "B형으로 채점" in r["type_note"], r
# A형 답안인데 B로 표시
r = grade(keyA, "B", "202600004"); assert r["exam_type"] == "A" and r["type_status"] == "유형불일치" and r["total_score"] == 500, r
# 유형 표시 없음 -> 답안에 맞는 유형으로
r = grade(keyB, None, "202600005"); assert r["exam_type"] == "B" and r["type_status"] == "유형오류" and r["total_score"] == 500 and r["needs_review"], r
# 엑셀에 표시
import pandas as pd
df = pd.read_excel(io.BytesIO(c.get("/api/export/excel?scope=real").data), sheet_name="성적총괄표")
assert "표시한 유형" in df.columns and "유형 확인" in df.columns and df["유형 확인"].notna().sum() == 3
print("ALL PASS")
