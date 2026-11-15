"""비밀번호 로그인 (세션 쿠키). 비밀번호는 해시로만 data/auth.json 에 저장된다."""
import json
import os
import secrets
import time
from datetime import timedelta

from flask import jsonify, redirect, request, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash

MAX_FAILS = 5          # 연속 실패 허용 횟수
LOCK_SECONDS = 300     # 초과 시 해당 IP 잠금(초)
_fails = {}            # ip -> [횟수, 잠금해제시각]
PUBLIC_PATHS = {"/login", "/api/login"}


def _paths(data_dir):
    return os.path.join(data_dir, "auth.json"), os.path.join(data_dir, "secret.key")


def set_password(data_dir, password):
    if len(password) < 8:
        raise ValueError("비밀번호는 8자 이상이어야 합니다.")
    auth_file, _ = _paths(data_dir)
    os.makedirs(data_dir, exist_ok=True)
    with open(auth_file, "w", encoding="utf-8") as f:
        json.dump({"hash": generate_password_hash(password)}, f)


def is_configured(data_dir):
    auth_file, _ = _paths(data_dir)
    return os.path.exists(auth_file)


def _load_hash(data_dir):
    with open(_paths(data_dir)[0], encoding="utf-8") as f:
        return json.load(f)["hash"]


def _client_ip():
    # 외부 접속은 nginx 가 X-Real-IP 를 덮어써서 전달한다(채점 서버는 127.0.0.1 에서만 대기).
    return request.headers.get("X-Real-IP") or request.remote_addr or "?"


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

    @app.before_request
    def require_login():
        if request.path in PUBLIC_PATHS or session.get("auth"):
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

    @app.route("/api/login", methods=["POST"])
    def api_login():
        ip, now = _client_ip(), time.time()
        count, locked_until = _fails.get(ip, [0, 0])
        if locked_until > now:
            wait = int(locked_until - now) + 1
            return jsonify({"status": "error", "message": f"시도 횟수 초과. {wait}초 후 다시 시도하세요."}), 429
        pw = (request.get_json(silent=True) or {}).get("password", "")
        if check_password_hash(_load_hash(data_dir), pw):
            _fails.pop(ip, None)
            session.clear()
            session.permanent = True
            session["auth"] = True
            return jsonify({"status": "success"})
        count += 1
        _fails[ip] = [count, now + LOCK_SECONDS if count >= MAX_FAILS else 0]
        if count >= MAX_FAILS:
            _fails[ip][0] = 0
        return jsonify({"status": "error", "message": "비밀번호가 올바르지 않습니다."}), 401

    @app.route("/api/logout", methods=["POST"])
    def api_logout():
        session.clear()
        return jsonify({"status": "success"})
