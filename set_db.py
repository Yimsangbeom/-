"""데이터 저장소(SQL) 접속 설정:  venv\\Scripts\\python set_db.py
비밀번호는 이 창에서만 입력하며 data\\db.json 에만 저장됩니다(저장소/채팅에 올리지 마세요). 설정 후 서버를 다시 시작하세요."""
import getpass
import json
import os
import sys
from urllib.parse import quote_plus

import db

DATA = os.environ.get("OMR_DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))


def ask(label, default=""):
    v = input(f"{label}" + (f" [{default}]" if default else "") + ": ").strip()
    return v or default


def main():
    os.makedirs(DATA, exist_ok=True)
    cur = db.Store(DATA)
    print(f"현재 저장소: {cur.describe()['kind']} - {cur.describe()['where']}  (결과 {sum(cur.count().values())}건)")
    print("\n1) PostgreSQL 에 연결\n2) 접속 주소(URL) 직접 입력\n3) 기본 SQLite 파일로 되돌리기")
    mode = ask("선택", "1")
    cfg = os.path.join(DATA, "db.json")
    if mode == "3":
        if os.path.exists(cfg):
            os.remove(cfg)
        print("db.json 을 삭제했습니다. 서버를 다시 시작하면 SQLite 파일(data\\omr.db)을 사용합니다.")
        return
    if mode == "2":
        url = ask("접속 주소(URL)")
    else:
        host, port = ask("서버 주소", "127.0.0.1"), ask("포트", "5432")
        name, user = ask("DB 이름", "omr_score"), ask("사용자 ID")
        pw = getpass.getpass("비밀번호: ")
        url = f"postgresql+psycopg2://{quote_plus(user)}:{quote_plus(pw)}@{host}:{port}/{name}"
    try:
        new = db.Store(DATA, url=url)       # 접속 + 표(table) 생성
        new.ping()
    except Exception as e:
        sys.exit(f"연결 실패: {e}")
    d = new.describe()
    print(f"연결 성공: {d['kind']} - {d['where']}")
    if cur.url != new.url and sum(cur.count().values()) + (cur.load_settings() is not None) > 0:
        if input("현재 저장된 설정/채점 결과를 새 DB 로 복사할까요? (y/N): ").strip().lower() == "y":
            try:
                print(f"{db.copy_all(cur, new)}건을 복사했습니다.")
            except ValueError as e:
                print(e)
    with open(cfg, "w", encoding="utf-8") as f:
        json.dump({"url": url}, f)
    try:
        os.chmod(cfg, 0o600)
    except OSError:
        pass
    print("저장했습니다. 서버를 다시 시작하세요.")


if __name__ == "__main__":
    main()
