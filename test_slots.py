"""정답 답안지 A/B 칸 등록, 유형 불일치 거부, '시험지 새로 설정 저장'(reset) 검증."""
import io, os, tempfile
os.environ["OMR_DATA_DIR"] = tempfile.mkdtemp()
from app import app

c = app.test_client()
assert c.post("/api/setup", json={"password": "testpass123"}).status_code == 200
def up(fn, form):
    return c.post("/api/grade/master", data={"file": (open(f"static/samples/{fn}", "rb"), fn), "form": form}, content_type="multipart/form-data")
cfg = lambda: c.get("/api/config").get_json()
assert cfg()["keys_set"] == {"A": False, "B": False}
bad = up("master_A.jpg", "B"); assert bad.status_code == 422 and "A형" in bad.get_json()["message"], bad.get_json()      # B칸에 A형 답안지
assert cfg()["keys_set"] == {"A": False, "B": False}
r = up("master_A.jpg", "A").get_json(); assert r["status"] == "success" and r["derived_other"]
k = cfg(); assert k["keys_set"] == {"A": True, "B": True} and k["key_source"] == {"A": "sheet", "B": "derived"}, k["key_source"]
r = up("master_B.jpg", "B").get_json(); assert r["status"] == "success" and not r["conflicts"], r
assert cfg()["key_source"] == {"A": "sheet", "B": "sheet"}
# 새로 설정 저장: 정답 등록이 모두 지워지고 값은 새로 저장
assert c.post("/api/config", json={"reset": True, "score_per_q": 4, "pass_average": 55, "subject_fail_cutoff": 30, "threshold": 0.6}).get_json()["status"] == "success"
k = cfg(); assert k["keys_set"] == {"A": False, "B": False} and k["key_source"] == {} and (k["score_per_q"], k["pass_average"], k["subject_fail_cutoff"], k["threshold"]) == (4, 55, 30, 0.6)
assert k["answer_keys"]["A"] == [1] * 100 or len(k["answer_keys"]["A"]) == 100
print("ALL PASS")
