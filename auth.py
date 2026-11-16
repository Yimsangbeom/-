"""ID/비밀번호 로그인 (세션 쿠키). 비밀번호는 해시로만 data/auth.json 에 저장된다.

- 기본 관리자 계정(auth.json: 최초 설정한 hancom)과, 관리자가 화면에서 추가/삭제하는 사용자 계정(users.json)을 쓴다.
  권한: 관리자(사용자 관리 포함 전체) / 일반 사용자(채점·조회·교정 등 사용) / 조회 전용(보기와 엑셀 다운로드만).
- 최초 설정은 서버 PC 에서 http://127.0.0.1:5000 으로 직접 접속했을 때만 허용(외부 선점 방지).
- 잊어버렸을 때: 서버에서 data/auth.json 삭제(또는 reset_password.bat) 후 최초 설정 화면에서 다시 설정.
"""
import hmac
import json
import os
import secrets
import threading
import time
from datetime import timedelta

from flask import jsonify, redirect, request, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash

import re

DEFAULT_USERNAME = "hancom"
USER_RE = re.compile(r"^[A-Za-z0-9_.-]{3,30}$")
MIN_LEN = 8
MAX_FAILS = 5          # 연속 실패 허용 횟수
LOCK_SECONDS = 300     # 초과 시 해당 IP 잠금(초)
_fails = {}            # ip -> [횟수, 잠금해제시각]
ROLES = {"admin": "관리자", "user": "일반 사용자", "viewer": "조회 전용"}
VIEWER_POST_OK = {"/api/logout", "/api/password"}      # 조회 전용 계정이 쓸 수 있는 쓰기 요청(내 비밀번호 변경, 로그아웃)
_users_lock = threading.Lock()
_DUMMY_HASH = generate_password_hash("x" * 12)
PUBLIC_PATHS = {"/login", "/api/login", "/api/auth/status", "/api/setup", "/healthz"}
PUBLIC_PREFIXES = ("/fonts/", "/vendor/")   # 글꼴/스타일 파일(비밀 아님) - 로그인 화면에서도 필요


def _paths(data_dir):
    return os.path.join(data_dir, "auth.json"), os.path.join(data_dir, "secret.key")


def load_auth(data_dir):
    path = _paths(data_dir)[0]
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def stored_username(a):
    return (a or {}).get("username", DEFAULT_USERNAME)


def _users_path(data_dir):
    return os.path.join(data_dir, "users.json")


def load_users(data_dir):
    path = _users_path(data_dir)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f).get("users", [])


def save_users(data_dir, users):
    with open(_users_path(data_dir), "w", encoding="utf-8") as f:
        json.dump({"users": users}, f, ensure_ascii=False)


def find_account(data_dir, username):
    """기본 관리자 또는 추가 사용자 계정을 찾는다. -> {username, hash, ver, role, primary} 또는 None"""
    username = (username or "").strip()
    a = load_auth(data_dir)
    if a and username == stored_username(a):
        return {"username": stored_username(a), "hash": a["hash"], "ver": a.get("ver", 0), "role": "admin", "primary": True}
    for u in load_users(data_dir):
        if u["username"] == username:
            return {"username": u["username"], "hash": u["hash"], "ver": u.get("ver", 1), "role": u.get("role", "user"), "primary": False}
    return None


def set_password(data_dir, password, username=None):
    """username 을 주지 않으면 기존 아이디 유지(최초에는 hancom)."""
    if len(password) < MIN_LEN:
        raise ValueError(f"비밀번호는 {MIN_LEN}자 이상이어야 합니다.")
    old = load_auth(data_dir) or {}
    username = (username or "").strip() or stored_username(old)
    if not USER_RE.match(username):
        raise ValueError("아이디는 영문/숫자/_ . - 3~30자여야 합니다.")
    if any(u["username"] == username for u in load_users(data_dir)):
        raise ValueError("이미 사용 중인 아이디입니다.")
    os.makedirs(data_dir, exist_ok=True)
    with open(_paths(data_dir)[0], "w", encoding="utf-8") as f:
        # ver: 비밀번호/아이디를 바꾸면 올라가며, 이전 로그인 세션은 모두 무효가 된다.
        json.dump({"username": username, "hash": generate_password_hash(password),
                   "ver": int(old.get("ver", 0)) + 1}, f)


def is_configured(data_dir):
    return load_auth(data_dir) is not None


def _client_ip():
    # 외부 접속은 nginx 가 X-Real-IP 를 덮어써서 전달한다(채점 서버는 127.0.0.1 에서만 대기).
    return request.headers.get("X-Real-IP") or request.remote_addr or "?"


def _is_local_direct():
    """프록시(nginx)를 거치지 않고 서버 PC 에서 직접 접속했는지."""
    return (not request.headers.get("X-Real-IP") and not request.headers.get("X-Forwarded-For")
            and request.remote_addr in ("127.0.0.1", "::1"))


def _locked():
    count, until = _fails.get(_client_ip(), [0, 0])
    now = time.time()
    if until > now:
        return jsonify({"status": "error", "message": f"시도 횟수 초과. {int(until - now) + 1}초 후 다시 시도하세요."}), 429
    return None


def _record_fail():
    ip = _client_ip()
    count = _fails.get(ip, [0, 0])[0] + 1
    _fails[ip] = [0, time.time() + LOCK_SECONDS] if count >= MAX_FAILS else [count, 0]


def _login_session(acc):
    session.clear()
    session.permanent = True
    session["auth"] = True
    session["user"] = acc["username"]
    session["ver"] = acc["ver"]


def init_auth(app, data_dir, static_dir):
    _, secret_file = _paths(data_dir)
    if not os.path.exists(secret_file):
        with open(secret_file, "w") as f:
            f.write(secrets.token_hex(32))
    with open(secret_file) as f:
        app.secret_key = f.read().strip()
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("OMR_SECURE_COOKIE", "0") == "1",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    )

    def current_account():
        a = load_auth(data_dir)
        if not a or not session.get("auth"):
            return None
        acc = find_account(data_dir, session.get("user") or stored_username(a))      # 예전 세션(user 없음)은 기본 관리자
        return acc if acc and session.get("ver") == acc["ver"] else None

    def logged_in():
        return current_account() is not None

    @app.before_request
    def require_login():
        if request.path in PUBLIC_PATHS or request.path.startswith(PUBLIC_PREFIXES):
            return None
        acc = current_account()
        if acc is None:
            if request.path.startswith("/api/") or request.path.startswith("/results/"):
                return jsonify({"status": "error", "message": "로그인이 필요합니다."}), 401
            return redirect("/login")
        if request.path.startswith("/api/users") and acc["role"] != "admin":
            return jsonify({"status": "error", "message": "관리자만 사용할 수 있습니다."}), 403
        if acc["role"] == "viewer" and request.method not in ("GET", "HEAD", "OPTIONS") and request.path not in VIEWER_POST_OK:
            return jsonify({"status": "error", "message": "조회 전용 계정은 변경할 수 없습니다."}), 403
        return None

    @app.after_request
    def no_cache(resp):
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.route("/login")
    def login_page():
        return send_from_directory(static_dir, "login.html")

    @app.route("/api/auth/status")
    def auth_status():
        configured = is_configured(data_dir)
        acc = current_account()
        return jsonify({"configured": configured, "setup_allowed": (not configured) and _is_local_direct(),
                        "logged_in": acc is not None, "username": acc["username"] if acc else None,
                        "role": acc["role"] if acc else None, "role_name": ROLES.get(acc["role"]) if acc else None,
                        **({"primary": acc["primary"]} if acc else {})})

    @app.route("/api/setup", methods=["POST"])
    def api_setup():
        if is_configured(data_dir):
            return jsonify({"status": "error", "message": "이미 비밀번호가 설정되어 있습니다."}), 409
        if not _is_local_direct():
            return jsonify({"status": "error", "message": "최초 설정은 서버 PC 에서 http://127.0.0.1:5000 으로 접속해서 해야 합니다."}), 403
        d = request.get_json(silent=True) or {}
        try:
            set_password(data_dir, d.get("password", ""), d.get("username"))
        except ValueError as e:
            return jsonify({"status": "error", "message": str(e)}), 400
        _login_session(find_account(data_dir, stored_username(load_auth(data_dir))))
        return jsonify({"status": "success"})

    @app.route("/api/login", methods=["POST"])
    def api_login():
        blocked = _locked()
        if blocked:
            return blocked
        if not load_auth(data_dir):
            return jsonify({"status": "error", "message": "비밀번호가 아직 설정되지 않았습니다."}), 409
        d = request.get_json(silent=True) or {}
        acc = find_account(data_dir, str(d.get("username", "")))
        pw_ok = check_password_hash(acc["hash"] if acc else _DUMMY_HASH, str(d.get("password", "")))     # 없는 아이디도 같은 시간이 걸리게
        if acc and pw_ok:
            _fails.pop(_client_ip(), None)
            _login_session(acc)
            return jsonify({"status": "success"})
        _record_fail()
        return jsonify({"status": "error", "message": "아이디 또는 비밀번호가 올바르지 않습니다."}), 401

    @app.route("/api/password", methods=["POST"])
    def api_change_password():
        """내 비밀번호 변경 (기본 관리자는 아이디도 변경 가능)."""
        blocked = _locked()
        if blocked:
            return blocked
        d = request.get_json(silent=True) or {}
        acc = current_account()
        if not check_password_hash(acc["hash"], str(d.get("current", ""))):
            _record_fail()
            return jsonify({"status": "error", "message": "현재 비밀번호가 올바르지 않습니다."}), 401
        try:
            if acc["primary"]:
                set_password(data_dir, str(d.get("new", "")), d.get("username"))
                new_acc = find_account(data_dir, stored_username(load_auth(data_dir)))
            else:
                new_pw = str(d.get("new", ""))
                if len(new_pw) < MIN_LEN:
                    raise ValueError(f"비밀번호는 {MIN_LEN}자 이상이어야 합니다.")
                with _users_lock:
                    users = load_users(data_dir)
                    for u in users:
                        if u["username"] == acc["username"]:
                            u["hash"], u["ver"] = generate_password_hash(new_pw), u.get("ver", 1) + 1
                    save_users(data_dir, users)
                new_acc = find_account(data_dir, acc["username"])
        except ValueError as e:
            return jsonify({"status": "error", "message": str(e)}), 400
        _login_session(new_acc)  # 현재 기기만 로그인 유지, 다른 세션은 무효
        return jsonify({"status": "success"})

    @app.route("/api/logout", methods=["POST"])
    def api_logout():
        session.clear()
        return jsonify({"status": "success"})

    # ---------- 사용자 관리 (관리자만) ----------
    @app.route("/api/users", methods=["GET"])
    def api_users():
        a = load_auth(data_dir)
        rows = [{"username": stored_username(a), "role": "admin", "role_name": ROLES["admin"], "primary": True, "created": ""}]
        rows += [{"username": u["username"], "role": u.get("role", "user"), "role_name": ROLES.get(u.get("role", "user"), "?"), "primary": False,
                  "created": u.get("created", "")} for u in load_users(data_dir)]
        return jsonify({"users": rows, "roles": ROLES})

    @app.route("/api/users", methods=["POST"])
    def api_user_add():
        d = request.get_json(silent=True) or {}
        username, pw, role = str(d.get("username", "")).strip(), str(d.get("password", "")), str(d.get("role", "user"))
        if not USER_RE.match(username):
            return jsonify({"status": "error", "message": "아이디는 영문/숫자/_ . - 3~30자여야 합니다."}), 400
        if len(pw) < MIN_LEN:
            return jsonify({"status": "error", "message": f"비밀번호는 {MIN_LEN}자 이상이어야 합니다."}), 400
        if role not in ROLES:
            return jsonify({"status": "error", "message": "권한이 올바르지 않습니다."}), 400
        with _users_lock:
            if find_account(data_dir, username):
                return jsonify({"status": "error", "message": "이미 사용 중인 아이디입니다."}), 409
            users = load_users(data_dir)
            users.append({"username": username, "hash": generate_password_hash(pw), "role": role, "ver": 1, "created": time.strftime("%Y-%m-%d %H:%M")})
            save_users(data_dir, users)
        return jsonify({"status": "success"})

    @app.route("/api/users/<username>", methods=["DELETE"])
    def api_user_delete(username):
        me = current_account()
        if username == me["username"]:
            return jsonify({"status": "error", "message": "지금 로그인한 계정은 삭제할 수 없습니다."}), 400
        with _users_lock:
            users = load_users(data_dir)
            if not any(u["username"] == username for u in users):
                return jsonify({"status": "error", "message": "삭제할 수 있는 사용자가 아닙니다. (기본 관리자 계정은 삭제할 수 없습니다.)"}), 404
            save_users(data_dir, [u for u in users if u["username"] != username])      # 계정이 없어지면 그 사용자의 로그인 세션도 무효
        return jsonify({"status": "success"})

    @app.route("/api/users/<username>/password", methods=["POST"])
    def api_user_reset(username):
        pw = str((request.get_json(silent=True) or {}).get("new", ""))
        if len(pw) < MIN_LEN:
            return jsonify({"status": "error", "message": f"비밀번호는 {MIN_LEN}자 이상이어야 합니다."}), 400
        with _users_lock:
            users = load_users(data_dir)
            hit = [u for u in users if u["username"] == username]
            if not hit:
                return jsonify({"status": "error", "message": "비밀번호를 재설정할 수 있는 사용자가 아닙니다. (기본 관리자는 [아이디/비밀번호 변경]을 사용)"}), 404
            hit[0]["hash"], hit[0]["ver"] = generate_password_hash(pw), hit[0].get("ver", 1) + 1       # 그 사용자의 기존 로그인은 무효
            save_users(data_dir, users)
        return jsonify({"status": "success"})

    @app.route("/api/users/<username>/role", methods=["POST"])
    def api_user_role(username):
        role = str((request.get_json(silent=True) or {}).get("role", ""))
        if role not in ROLES:
            return jsonify({"status": "error", "message": "권한이 올바르지 않습니다."}), 400
        with _users_lock:
            users = load_users(data_dir)
            hit = [u for u in users if u["username"] == username]
            if not hit:
                return jsonify({"status": "error", "message": "권한을 바꿀 수 있는 사용자가 아닙니다."}), 404
            hit[0]["role"] = role
            save_users(data_dir, users)
        return jsonify({"status": "success"})
