"""사용자 계정 추가/삭제, 권한(관리자/일반/조회 전용), 비밀번호 변경·재설정, 세션 무효화."""
import os, tempfile
os.environ["OMR_DATA_DIR"] = tempfile.mkdtemp()
from app import app

admin = app.test_client()
assert admin.post("/api/setup", json={"password": "adminpass123"}).status_code == 200
st = admin.get("/api/auth/status").get_json(); assert st["role"] == "admin" and st["username"] == "hancom"
def login(u, p):
    c = app.test_client(); r = c.post("/api/login", json={"username": u, "password": p}); return c, r.status_code

# 추가: 검증
assert admin.post("/api/users", json={"username": "ab", "password": "password123", "role": "user"}).status_code == 400      # 아이디 너무 짧음
assert admin.post("/api/users", json={"username": "kim01", "password": "short", "role": "user"}).status_code == 400          # 비밀번호 짧음
assert admin.post("/api/users", json={"username": "kim01", "password": "password123", "role": "boss"}).status_code == 400    # 없는 권한
assert admin.post("/api/users", json={"username": "hancom", "password": "password123", "role": "user"}).status_code == 409    # 기본 관리자와 중복
for u, role in (("kim01", "user"), ("lee02", "viewer"), ("boss03", "admin")):
    assert admin.post("/api/users", json={"username": u, "password": "password123", "role": role}).status_code == 200
assert admin.post("/api/users", json={"username": "kim01", "password": "password123", "role": "user"}).status_code == 409
lst = admin.get("/api/users").get_json()["users"]; assert [x["username"] for x in lst] == ["hancom", "kim01", "lee02", "boss03"] and lst[0]["primary"]
assert "hash" not in str(lst)
assert os.path.exists(os.path.join(os.environ["OMR_DATA_DIR"], "users.json")) and "password123" not in open(os.path.join(os.environ["OMR_DATA_DIR"], "users.json"), encoding="utf-8").read()

# 로그인/권한
user, code = login("kim01", "password123"); assert code == 200
viewer, code = login("lee02", "password123"); assert code == 200
sub, code = login("boss03", "password123"); assert code == 200
assert login("kim01", "wrongpass")[1] == 401 and login("nobody", "password123")[1] == 401
assert user.get("/api/results").status_code == 200 and user.get("/api/users").status_code == 403      # 일반 사용자: 사용 가능, 사용자 관리 불가
assert user.post("/api/users", json={"username": "x" * 5, "password": "password123"}).status_code == 403
assert user.post("/api/config", json={"pass_average": 61}).status_code == 200
assert viewer.get("/api/results").status_code == 200 and viewer.get("/api/export/excel?scope=test").status_code in (200, 400)
assert viewer.post("/api/config", json={"pass_average": 70}).status_code == 403 and viewer.delete("/api/results").status_code == 403
assert viewer.get("/api/users").status_code == 403
assert sub.get("/api/users").status_code == 200       # 관리자 권한 사용자는 사용자 관리 가능
v = viewer.get("/api/auth/status").get_json(); assert v["role"] == "viewer" and v["role_name"] == "조회 전용"

# 내 비밀번호 변경 (일반 사용자)
assert user.post("/api/password", json={"current": "bad", "new": "newpassword1"}).status_code == 401
assert user.post("/api/password", json={"current": "password123", "new": "newpassword1"}).status_code == 200
assert user.get("/api/results").status_code == 200                                  # 변경한 기기는 로그인 유지
assert login("kim01", "password123")[1] == 401 and login("kim01", "newpassword1")[1] == 200
assert viewer.post("/api/password", json={"current": "password123", "new": "viewerpass12"}).status_code == 200     # 조회 전용도 자기 비밀번호는 변경 가능

# 관리자 재설정 -> 기존 로그인 무효
other, _ = login("kim01", "newpassword1")
assert admin.post("/api/users/kim01/password", json={"new": "reset123456"}).status_code == 200
assert other.get("/api/results").status_code == 401 and login("kim01", "reset123456")[1] == 200
assert admin.post("/api/users/hancom/password", json={"new": "reset123456"}).status_code == 404       # 기본 관리자는 이 경로로 못 바꿈
# 권한 변경
assert admin.post("/api/users/kim01/role", json={"role": "viewer"}).status_code == 200
k, _ = login("kim01", "reset123456"); assert k.post("/api/config", json={"pass_average": 1}).status_code == 403
# 삭제 -> 세션 무효, 로그인 불가
assert admin.delete("/api/users/kim01").status_code == 200 and k.get("/api/results").status_code == 401 and login("kim01", "reset123456")[1] == 401
assert admin.delete("/api/users/hancom").status_code == 400 and sub.delete("/api/users/boss03").status_code == 400      # 본인/기본 관리자 삭제 불가
assert admin.delete("/api/users/nobody").status_code == 404
# 기본 관리자가 아이디를 추가 사용자 아이디로 바꾸려 하면 거부
assert admin.post("/api/password", json={"current": "adminpass123", "new": "adminpass456", "username": "lee02"}).status_code == 400
assert admin.post("/api/password", json={"current": "adminpass123", "new": "adminpass456"}).status_code == 200
assert login("hancom", "adminpass456")[1] == 200
print("ALL PASS")
