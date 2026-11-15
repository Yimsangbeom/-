"""데이터 저장소 (SQL). 설정과 채점 결과를 DB 에 저장한다.

접속 정보(우선순위):
  1) 환경변수 OMR_DATABASE_URL
  2) data/db.json  ({"url": "postgresql+psycopg2://사용자:비밀번호@127.0.0.1:5432/omr_score"})  <- set_db.py 로 생성
  3) 기본값: SQLite 파일 data/omr.db (설치 불필요)

예) PostgreSQL : postgresql+psycopg2://omr_user:비밀번호@127.0.0.1:5432/omr_score
    SQL Server : mssql+pyodbc://사용자:비밀번호@서버/DB?driver=ODBC+Driver+17+for+SQL+Server  (pyodbc 필요)
"""
import json
import os

from sqlalchemy import (Boolean, Column, Integer, MetaData, String, Table, Text, create_engine, delete, func,
                        inspect, insert, select, text)

metadata = MetaData()
settings_t = Table("settings", metadata,
                   Column("key", String(64), primary_key=True),
                   Column("value", Text, nullable=False))
results_t = Table("results", metadata,
                  Column("id", Integer, primary_key=True, autoincrement=False),
                  Column("exam_id", String(32), nullable=False, server_default="default"),   # 시험(시험 설정) 구분
                  Column("is_test", Boolean, nullable=False, default=False),
                  Column("exam_type", String(4)),
                  Column("student_id", String(64)),
                  Column("data", Text, nullable=False))      # 결과 전체(JSON): 파일명, 문항별 답안, 맞힌 개수, 채점에 쓴 정답 등


def resolve_url(data_dir):
    url = os.environ.get("OMR_DATABASE_URL")
    if url:
        return url
    cfg = os.path.join(data_dir, "db.json")
    if os.path.exists(cfg):
        with open(cfg, encoding="utf-8") as f:
            return json.load(f)["url"]
    return "sqlite:///" + os.path.join(data_dir, "omr.db").replace("\\", "/")


class Store:
    def __init__(self, data_dir, url=None):
        self.data_dir = data_dir
        self.url = url or resolve_url(data_dir)
        kw = {"pool_pre_ping": True}
        if self.url.startswith("sqlite"):
            kw["connect_args"] = {"check_same_thread": False, "timeout": 30}
        elif self.url.startswith("postgresql"):
            kw["connect_args"] = {"client_encoding": "utf8"}
        self.engine = create_engine(self.url, **kw)
        metadata.create_all(self.engine)
        self._ensure_exam_column()
        self._migrate_legacy_files()

    # ---------- 표시용 ----------
    @property
    def kind(self):
        return self.engine.dialect.name

    def describe(self):
        """비밀번호를 가린 접속 정보 (화면 표시용)."""
        u = self.engine.url
        if u.get_backend_name() == "sqlite":
            return {"kind": "SQLite", "where": u.database}
        return {"kind": {"postgresql": "PostgreSQL", "mssql": "SQL Server", "mysql": "MySQL"}.get(u.get_backend_name(), u.get_backend_name()),
                "where": f"{u.host}:{u.port or ''}/{u.database}  (사용자 {u.username})"}

    # ---------- 설정 (키/값) ----------
    def get_setting(self, key):
        with self.engine.connect() as c:
            row = c.execute(select(settings_t.c.value).where(settings_t.c.key == key)).first()
        return json.loads(row[0]) if row else None

    def set_setting(self, key, value):
        with self.engine.begin() as c:
            c.execute(delete(settings_t).where(settings_t.c.key == key))
            c.execute(insert(settings_t).values(key=key, value=json.dumps(value)))   # ensure_ascii=True: DB 인코딩과 무관

    def delete_setting(self, key):
        with self.engine.begin() as c:
            c.execute(delete(settings_t).where(settings_t.c.key == key))

    def all_settings(self):
        with self.engine.connect() as c:
            return {k: json.loads(v) for k, v in c.execute(select(settings_t.c.key, settings_t.c.value)).all()}

    def load_settings(self):          # (이전 버전 호환) 단일 설정 'state'
        return self.get_setting("state")

    def save_settings(self, state):
        self.set_setting("state", state)

    # ---------- 결과 ----------
    def load_results(self, exam_id=None):
        stmt = select(results_t.c.data).order_by(results_t.c.id)
        if exam_id is not None:
            stmt = stmt.where(results_t.c.exam_id == exam_id)
        with self.engine.connect() as c:
            rows = c.execute(stmt).all()
        return [json.loads(r[0]) for r in rows]

    def add_results(self, items, exam_id=None):
        """items 각각의 exam_id(없으면 인자, 그것도 없으면 'default')로 저장."""
        if not items:
            return
        with self.engine.begin() as c:
            c.execute(insert(results_t), [{
                "id": it["id"], "exam_id": it.get("exam_id") or exam_id or "default",
                "is_test": bool(it.get("is_test")), "exam_type": it.get("exam_type"),
                "student_id": it.get("student_id"),
                "data": json.dumps(it)} for it in items])      # 한글은 \\uXXXX 로 저장되어 DB 인코딩(SQL_ASCII 등)에 영향받지 않음

    def delete_results(self, is_test=None, ids=None, exam_id=None):
        """조건을 모두 만족하는 결과를 삭제. 조건이 없으면 전부.
        exam_id: 해당 시험만 / is_test: True·False 구분만 / ids: 그 번호만."""
        stmt = delete(results_t)
        if exam_id is not None:
            stmt = stmt.where(results_t.c.exam_id == exam_id)
        if ids is not None:
            stmt = stmt.where(results_t.c.id.in_(list(ids)))
        if is_test is not None:
            stmt = stmt.where(results_t.c.is_test == bool(is_test))
        with self.engine.begin() as c:
            c.execute(stmt)

    def max_id(self):
        with self.engine.connect() as c:
            v = c.execute(select(func.max(results_t.c.id))).scalar()
        return -1 if v is None else int(v)

    def count(self, exam_id=None):
        def q(flag):
            stmt = select(func.count()).select_from(results_t).where(results_t.c.is_test == flag)  # noqa: E712
            if exam_id is not None:
                stmt = stmt.where(results_t.c.exam_id == exam_id)
            return stmt
        with self.engine.connect() as c:
            return {"real": c.execute(q(False)).scalar(), "test": c.execute(q(True)).scalar()}

    def ping(self):
        with self.engine.connect() as c:
            c.execute(text("select 1"))

    # ---------- 이전 버전 DB 에 exam_id 열이 없으면 추가 (기존 결과는 'default' 시험) ----------
    def _ensure_exam_column(self):
        cols = {c["name"] for c in inspect(self.engine).get_columns("results")}
        if "exam_id" not in cols:
            with self.engine.begin() as c:
                c.execute(text("ALTER TABLE results ADD COLUMN exam_id VARCHAR(32) NOT NULL DEFAULT 'default'"))

    # ---------- 이전 버전(JSON 파일) 데이터 이전 ----------
    def _migrate_legacy_files(self):
        state_f = os.path.join(self.data_dir, "state.json")
        res_f = os.path.join(self.data_dir, "results.json")
        if self.load_settings() is None and os.path.exists(state_f):
            with open(state_f, encoding="utf-8") as f:
                self.save_settings(json.load(f))
            os.replace(state_f, state_f + ".migrated")
        if os.path.exists(res_f):
            if self.count()["real"] + self.count()["test"] == 0:
                with open(res_f, encoding="utf-8") as f:
                    self.add_results(json.load(f).get("results", []))
            os.replace(res_f, res_f + ".migrated")


def copy_all(src, dst):
    """src Store 의 설정과 결과를 dst Store 로 복사한다 (dst 가 비어 있을 때만). 복사한 결과 건수를 돌려준다."""
    c = dst.count()
    if c["real"] + c["test"] > 0:
        raise ValueError("대상 DB 에 이미 채점 결과가 있어 복사하지 않았습니다.")
    for k, v in src.all_settings().items():
        dst.set_setting(k, v)
    items = src.load_results()
    dst.add_results(items)
    return len(items)
