"""ID/비밀번호 로그인 (세션 쿠키). 비밀번호는 해시로만 data/auth.json 에 저장된다.

- 아이디는 USERNAME 고정, 비밀번호는 화면에서 설정/변경한다.
- 최초 설정은 서버 PC 에서 http://127.0.0.1:5000 으로 직접 접속했을 때만 허용(외부 선점 방지).
- 잊어버렸을 때: 서버에서 data/auth.json 삭제(또는 reset_password.bat) 후 최초 설정 화면에서 다시 설정.
"""
import hmac
import json
import os
import secrets
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
PUBLIC_PATHS = {"/login", "/api/login", "/api/auth/status", "/api/setup"}
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


def set_password(data_dir, password, username=None):
    """username 을 주지 않으면 기존 아이디 유지(최초에는 hancom)."""
    if len(password) < MIN_LEN:
        raise ValueError(f"비밀번호는 {MIN_LEN}자 이상이어야 합니다.")
    old = load_auth(data_dir) or {}
    username = (username or "").strip() or stored_username(old)
    if not USER_RE.match(username):
        raise ValueError("아이디는 영문/숫자/_ . - 3~30자여야 합니다.")
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


def _login_session(ver):
    session.clear()
    session.permanent = True
    session["auth"] = True
    session["ver"] = ver


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

    def logged_in():
        a = load_auth(data_dir)
        return bool(a) and session.get("auth") and session.get("ver") == a.get("ver", 0)

    @app.before_request
    def require_login():
        if request.path in PUBLIC_PATHS or request.path.startswith(PUBLIC_PREFIXES) or logged_in():
            return None
        if request.path.startswith("/api/") or request.path.startswith("/results/"):
            return jsonify({"status": "error", "message": "로그인이 필요합니다."}), 401
        return redirect("/login")

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
        return jsonify({"configured": configured, "setup_allowed": (not configured) and _is_local_direct(),
                        "logged_in": bool(logged_in()),
                        "username": stored_username(load_auth(data_dir)) if logged_in() else None})

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
        _login_session(load_auth(data_dir)["ver"])
        return jsonify({"status": "success"})

    @app.route("/api/login", methods=["POST"])
    def api_login():
        blocked = _locked()
        if blocked:
            return blocked
        a = load_auth(data_dir)
        if not a:
            return jsonify({"status": "error", "message": "비밀번호가 아직 설정되지 않았습니다."}), 409
        d = request.get_json(silent=True) or {}
        user_ok = hmac.compare_digest(str(d.get("username", "")).strip().encode(), stored_username(a).encode())
        pw_ok = check_password_hash(a["hash"], str(d.get("password", "")))
        if user_ok and pw_ok:
            _fails.pop(_client_ip(), None)
            _login_session(a.get("ver", 0))
            return jsonify({"status": "success"})
        _record_fail()
        return jsonify({"status": "error", "message": "아이디 또는 비밀번호가 올바르지 않습니다."}), 401

    @app.route("/api/password", methods=["POST"])
    def api_change_password():
        blocked = _locked()
        if blocked:
            return blocked
        d = request.get_json(silent=True) or {}
        a = load_auth(data_dir)
        if not check_password_hash(a["hash"], str(d.get("current", ""))):
            _record_fail()
            return jsonify({"status": "error", "message": "현재 비밀번호가 올바르지 않습니다."}), 401
        try:
            set_password(data_dir, str(d.get("new", "")), d.get("username"))
        except ValueError as e:
            return jsonify({"status": "error", "message": str(e)}), 400
        _login_session(load_auth(data_dir)["ver"])  # 현재 기기만 로그인 유지, 다른 세션은 무효
        return jsonify({"status": "success"})

    @app.route("/api/logout", methods=["POST"])
    def api_logout():
        session.clear()
        return jsonify({"status": "success"})
