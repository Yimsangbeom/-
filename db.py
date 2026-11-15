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
                        insert, select, text)

metadata = MetaData()
settings_t = Table("settings", metadata,
                   Column("key", String(64), primary_key=True),
                   Column("value", Text, nullable=False))
results_t = Table("results", metadata,
                  Column("id", Integer, primary_key=True, autoincrement=False),
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

    # ---------- 설정 ----------
    def load_settings(self):
        with self.engine.connect() as c:
            row = c.execute(select(settings_t.c.value).where(settings_t.c.key == "state")).first()
        return json.loads(row[0]) if row else None

    def save_settings(self, state):
        with self.engine.begin() as c:
            c.execute(delete(settings_t).where(settings_t.c.key == "state"))
            c.execute(insert(settings_t).values(key="state", value=json.dumps(state)))   # ensure_ascii=True: DB 인코딩과 무관하게 저장

    # ---------- 결과 ----------
    def load_results(self):
        with self.engine.connect() as c:
            rows = c.execute(select(results_t.c.data).order_by(results_t.c.id)).all()
        return [json.loads(r[0]) for r in rows]

    def add_results(self, items):
        if not items:
            return
        with self.engine.begin() as c:
            c.execute(insert(results_t), [{
                "id": it["id"], "is_test": bool(it.get("is_test")), "exam_type": it.get("exam_type"),
                "student_id": it.get("student_id"),
                "data": json.dumps(it)} for it in items])      # 한글은 \\uXXXX 로 저장되어 DB 인코딩(SQL_ASCII 등)에 영향받지 않음

    def delete_results(self, is_test=None, ids=None):
        """is_test=None 이면 전부, True/False 이면 해당 구분만, ids 가 있으면 그 번호만 삭제."""
        stmt = delete(results_t)
        if ids is not None:
            stmt = stmt.where(results_t.c.id.in_(list(ids)))
        elif is_test is not None:
            stmt = stmt.where(results_t.c.is_test == bool(is_test))
        with self.engine.begin() as c:
            c.execute(stmt)

    def count(self):
        with self.engine.connect() as c:
            real = c.execute(select(func.count()).select_from(results_t).where(results_t.c.is_test == False)).scalar()  # noqa: E712
            test = c.execute(select(func.count()).select_from(results_t).where(results_t.c.is_test == True)).scalar()    # noqa: E712
        return {"real": real, "test": test}

    def ping(self):
        with self.engine.connect() as c:
            c.execute(text("select 1"))

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
    st = src.load_settings()
    if st is not None:
        dst.save_settings(st)
    items = src.load_results()
    dst.add_results(items)
    return len(items)
