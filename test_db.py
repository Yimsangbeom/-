"""저장소(db.py) 검증: 영속성, 삭제 범위, 이전 JSON 파일 이전, DB 간 복사. PG_TEST_URL(빈 PostgreSQL DB)을 주면 PostgreSQL 도 검증."""
import json, os, subprocess, sys, tempfile
import db

def item(i, test=False, key=True):
    d = {"id": i, "filename": f"수험생_{i}.png", "exam_type": "A", "student_id": f"2026{i:05d}", "is_test": test,
         "student_answers": {"1": "3"}, "subject_correct": {c: 10 for c in "ABCDE"}, "total_score": 50}
    if key: d["key_used"] = [1] * 100
    return d

def check(store):
    store.save_settings({"score_per_q": 5.0, "answer_keys": {"A": [1] * 100}})
    store.add_results([item(0), item(1), item(2, test=True), item(3, test=True)])
    again = db.Store(store.data_dir, url=store.url)                      # 새로 접속해도 그대로 (영속성)
    assert again.load_settings()["score_per_q"] == 5.0
    got = again.load_results(); assert [r["id"] for r in got] == [0, 1, 2, 3] and got[0]["filename"] == "수험생_0.png"
    assert again.count() == {"real": 2, "test": 2}
    again.delete_results(is_test=True); assert again.count() == {"real": 2, "test": 0}      # 테스트만 삭제
    again.delete_results(ids=[0]); assert [r["id"] for r in again.load_results()] == [1]       # 번호 지정 삭제
    again.delete_results(); assert again.count() == {"real": 0, "test": 0}                     # 전체 삭제
    again.save_settings({"x": 1}); assert again.load_settings() == {"x": 1}                    # 설정 덮어쓰기
    return again

d = tempfile.mkdtemp(); s = db.Store(d); assert s.kind == "sqlite" and s.describe()["kind"] == "SQLite"; check(s)
print("SQLite 영속성/삭제 OK")

# 이전 버전(JSON 파일) 데이터 자동 이전
d2 = tempfile.mkdtemp()
json.dump({"score_per_q": 5.0, "schema": 3}, open(os.path.join(d2, "state.json"), "w"))
json.dump({"next_id": 2, "results": [item(0), item(1)]}, open(os.path.join(d2, "results.json"), "w"))
m = db.Store(d2); assert m.load_settings()["schema"] == 3 and m.count()["real"] == 2
assert os.path.exists(os.path.join(d2, "state.json.migrated")) and not os.path.exists(os.path.join(d2, "results.json"))
assert db.Store(d2).count()["real"] == 2          # 다시 열어도 중복 이전 없음
print("JSON 파일 이전 OK")

# 앱 시작 시: 정답이 저장되지 않은 이전 버전 '테스트' 결과만 정리 (실제 결과는 보존)
d3 = tempfile.mkdtemp(); s3 = db.Store(d3)
s3.add_results([item(0, key=False), item(1, test=True, key=False), item(2, test=True, key=True)])
out = subprocess.run([sys.executable, "-c", "import app; print([ (r['id'], r['is_test']) for r in app.graded_results ], app.next_id)"],
                     env={**os.environ, "OMR_DATA_DIR": d3, "OMR_DATABASE_URL": ""}, capture_output=True, text=True).stdout.strip().splitlines()[-1]
assert out == "[(0, False), (2, True)] 3", out
print("이전 테스트 결과 정리 OK")

# DB 간 복사 (set_db.py 의 '현재 데이터를 새 DB 로 복사')
src = db.Store(tempfile.mkdtemp()); src.save_settings({"a": 1}); src.add_results([item(0), item(1, test=True)])
dst = db.Store(tempfile.mkdtemp()); assert db.copy_all(src, dst) == 2 and dst.load_settings() == {"a": 1} and dst.count() == {"real": 1, "test": 1}
try:
    db.copy_all(src, dst); raise SystemExit("비어 있지 않은 DB 로 복사되면 안 됨")
except ValueError:
    pass
print("DB 복사 OK")

pg = os.environ.get("PG_TEST_URL")
if pg:
    p = db.Store(tempfile.mkdtemp(), url=pg); p.delete_results(); check(p)
    q = db.Store(tempfile.mkdtemp(), url=pg); q.delete_results(); print("PostgreSQL 영속성/삭제 OK", p.describe()["kind"], "|", p.describe()["where"])
    src2 = db.Store(tempfile.mkdtemp()); src2.save_settings({"b": 2}); src2.add_results([item(0), item(1, test=True)])
    assert db.copy_all(src2, q) == 2 and q.count() == {"real": 1, "test": 1}; q.delete_results()
    print("SQLite -> PostgreSQL 복사 OK")
print("ALL PASS")
