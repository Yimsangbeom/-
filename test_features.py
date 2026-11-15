"""내장 가상 답안지(static/samples)로 테스트 실행/판정/과락/삭제 기능을 검증한다. (양식 PDF 불필요)"""
import io, os, sys, tempfile
import pandas as pd
os.environ["OMR_DATA_DIR"] = tempfile.mkdtemp()
from app import app, state

c = app.test_client()
assert c.post("/api/setup", json={"password": "testpass123"}).status_code == 200
keys_before = {k: list(v) for k, v in state["answer_keys"].items()}

info = c.get("/api/test/info").get_json()
assert len(info["files"]) == 6 and len(info["students"]) == 5
assert c.get("/samples/student1.jpg").status_code == 200

d = c.post("/api/test/run").get_json()
assert d["status"] == "success" and len(d["results"]) == 5
for r in d["results"]:
    assert r["is_test"] and r["verify"]["match"] == 100, r["verify"]
    assert r["total_score"] == r["verify"]["expected_correct"] * 5, r  # 기본 문항당 5점
assert state["answer_keys"] == keys_before, "테스트가 실제 정답 설정을 바꿨음"
print("테스트 실행 OK:", {r["student_id"]: (r["total_score"], r["result"]) for r in d["results"]})

# 재실행하면 테스트 결과는 교체(중복 없음)
c.post("/api/test/run"); assert len(c.get("/api/results").get_json()) == 5

# 기본값: 문항당 5점(과목 100/총 500), 합격 평균 60, 과락 40점(한 과목이라도 미만이면 과락)
assert (state["score_per_q"], state["pass_average"], state["subject_fail_cutoff"]) == (5.0, 60.0, 40.0)
IDS = ("202600001", "202600002", "202600003", "202600004", "202600005")
def results():
    r = {x["student_id"]: x for x in c.get("/api/results").get_json()}
    return [r[i] for i in IDS]
rs = results()
assert [r["total_score"] for r in rs] == [500, 450, 410, 470, 435]
assert [r["average"] for r in rs] == [100, 90, 82, 94, 87]
assert [r["result"] for r in rs] == ["합격", "합격", "합격", "합격", "과락"]
assert rs[4]["fail_subjects"] == ["C"] and rs[4]["subject_scores"]["C"] == 35 and rs[4]["average"] >= 60  # 평균이 높아도 한 과목 과락이면 과락
# 합격 평균 92 → 평균 90·82 불합격(과락 아님), 94 합격, 100 합격
c.post("/api/config", json={"pass_average": 92}); assert [r["result"] for r in results()] == ["합격", "불합격", "불합격", "합격", "과락"]
c.post("/api/config", json={"pass_average": 60})
# 과락 점수 80 → 3번 학생(C,E=75점)도 과락, 2번(최저 80점)은 경계값이라 과락 아님(미만일 때만)
c.post("/api/config", json={"subject_fail_cutoff": 80}); rs = results()
assert [r["result"] for r in rs] == ["합격", "합격", "과락", "합격", "과락"] and set(rs[2]["fail_subjects"]) == {"C", "E"}, [(r["result"], r["fail_subjects"]) for r in rs]
# 0 = 과락 사용 안 함 → 모두 총점으로만 판정
c.post("/api/config", json={"subject_fail_cutoff": 0}); assert {r["result"] for r in results()} == {"합격"}
c.post("/api/config", json={"subject_fail_cutoff": 40})
# 문항당 배점을 바꾸면 이미 채점된 결과의 점수도 즉시 재계산
c.post("/api/config", json={"score_per_q": 2.5}); assert results()[0]["total_score"] == 250
c.post("/api/config", json={"score_per_q": 5})
print("판정/과락/배점 OK")

# 통계 API: 범위(scope) 구분, 문항 분석은 유형별, 엑셀에 통계 시트 포함
st = c.get("/api/stats?scope=test").get_json()
assert st["n"] == 5 and st["counts"] == {"real": 0, "test": 5} and st["summary"]["cut"] == 1
assert [f["label"][:2] for f in st["forms"]] == ["통합", "A형", "B형"] and all(not f["enough"] for f in st["forms"])  # 소수 인원: 판정 보류
assert st["key_conflicts"] == [] and len(st["blocks"]) == 10 and st["avg_full"] == 100 and st["distribution"][0]["label"] == "0~10"
assert c.get("/api/stats?scope=real").get_json() == {"n": 0, "scope": "real", "counts": {"real": 0, "test": 5}, "score_per_q": 5.0}
xl = pd.ExcelFile(io.BytesIO(c.get("/api/export/excel?scope=test").data))
assert {"성적총괄표", "문항별답안", "통계요약", "점수분포", "과목별통계", "유형별통계"} <= set(xl.sheet_names) and any(n.startswith("문항분석_") for n in xl.sheet_names), xl.sheet_names
assert "석차" in pd.read_excel(xl, "성적총괄표").columns
assert st["legacy_n"] == 0 and st["blocks"][0]["subject_name"] == "인터넷중독의 이해" and st["blocks"][0]["n_a"] == 4 and st["blocks"][0]["n_b"] == 1
assert st["forms"][0]["items"][20]["subject_name"] == "인터넷중독의 진단과 평가"
x_items = pd.read_excel(xl, [n for n in xl.sheet_names if n.startswith("문항분석_")][0])
assert x_items.loc[0, "과목"] == "인터넷중독의 이해" and x_items.loc[99, "과목"] == "인터넷중독의 정책과 법"
# 이전 버전 결과(정답 미저장) 표시
import app as _app
for r in _app.graded_results[:2]:
    r.pop("key_used", None)
assert c.get("/api/stats?scope=test").get_json()["legacy_n"] == 2
c.post("/api/test/run"); assert c.get("/api/stats?scope=test").get_json()["legacy_n"] == 0     # 테스트 재실행하면 새 기준으로 교체
print("통계 API/엑셀 OK")

# 정답 등록: 한쪽만 주면 다른 쪽은 블록 교체 규칙으로 자동 생성, 둘 다 주면 불일치 검증
from form_map import derive_key
import random as _r
_r.seed(5); kA = [_r.randint(1, 5) for _ in range(100)]
c.post("/api/config", json={"answer_keys": {"A": kA}}); cfg = c.get("/api/config").get_json()
assert cfg["answer_keys"]["A"] == kA and cfg["answer_keys"]["B"] == derive_key(kA) and cfg["key_conflicts"] == []
assert cfg["answer_keys"]["B"][0] == kA[10] and cfg["answer_keys"]["B"][10] == kA[0] and cfg["answer_keys"]["B"][20] == kA[30]   # A 11번=B 1번, A 1번=B 11번, 과목 경계 유지
kB = derive_key(kA); kB[4] = kB[4] % 5 + 1                                  # B형 5번(=A형 15번)만 틀리게
r = c.post("/api/config", json={"answer_keys": {"A": kA, "B": kB}}).get_json()
assert r["key_conflicts"] == [15] and c.get("/api/config").get_json()["key_conflicts"] == [15]
c.post("/api/config", json={"answer_keys": {"B": derive_key(kA)}}); assert c.get("/api/config").get_json()["key_conflicts"] == []
print("정답 자동 연결/교차 검증 OK")

# 엑셀에 판정·과락 컬럼 / 테스트 구분
x = pd.read_excel(io.BytesIO(c.get("/api/export/excel").data), sheet_name="성적총괄표")
assert {"구분", "판정"} <= set(x.columns) and (x["구분"] == "테스트").all()

# 테스트 결과만 삭제 → 실제 결과(직접 채점한 것)는 유지
import cv2
from test_synthetic import make_sheet
import random; random.seed(1); key = [random.randint(1, 5) for _ in range(100)]
p = os.path.join(tempfile.mkdtemp(), "real.png"); cv2.imwrite(p, make_sheet("A", "111111111", key))
c.post("/api/grade/students", data={"files": [(open(p, "rb"), "real.png")]})
assert len(c.get("/api/results").get_json()) == 6
c.delete("/api/results?scope=test")
left = c.get("/api/results").get_json(); assert len(left) == 1 and not left[0]["is_test"]
# 선택 삭제: 지정한 답안지만 삭제(이미지 포함), 나머지는 유지
c.post("/api/test/run"); allr = c.get("/api/results").get_json(); assert len(allr) == 6
tests_ = [x for x in allr if x["is_test"]]; victim = [tests_[0]["id"], tests_[2]["id"]]
r = c.post("/api/results/delete", json={"ids": victim}).get_json(); assert r["deleted"] == 2
left = c.get("/api/results").get_json(); assert len(left) == 4 and not (set(victim) & {x["id"] for x in left})
assert c.get(tests_[0]["vis_url"]).status_code == 404 and c.get(tests_[1]["vis_url"]).status_code == 200
assert c.post("/api/results/delete", json={"ids": [99999]}).get_json()["deleted"] == 0
assert app.test_client().post("/api/results/delete", json={"ids": [1]}).status_code == 401     # 로그인 없이는 불가
c.delete("/api/results?scope=test")
hz = app.test_client().get("/healthz")                      # 감시 작업이 쓰는 상태 확인 주소: 로그인 없이 200, 내부 정보 없음
assert hz.status_code == 200 and hz.get_json() == {"status": "ok"}
sysinfo = c.get("/api/system").get_json()
assert sysinfo["kind"] in ("SQLite", "PostgreSQL") and sysinfo["counts"] == {"real": 1, "test": 0}, sysinfo
c.delete("/api/results"); assert c.get("/api/system").get_json()["counts"] == {"real": 0, "test": 0} and c.get("/api/results").get_json() == []   # 전체 삭제
print("테스트만 삭제 OK")

# 이전 버전 설정(schema 2, 합격 총점 300) → 합격 평균 60 으로 자동 이전
import json, subprocess
d2 = tempfile.mkdtemp()
json.dump({"answer_keys": {"A": [1]*100, "B": [2]*100}, "score_per_q": 1.0, "pass_cutoff": 300.0, "subject_fail_cutoff": 40.0, "threshold": 0.5, "schema": 2},
          open(os.path.join(d2, "state.json"), "w"))
out = subprocess.run([sys.executable, "-c", "import app; s = app.state; print(s['pass_average'], s['score_per_q'], s['threshold'], s['schema'], 'pass_cutoff' in s)"],
                     env={**os.environ, "OMR_DATA_DIR": d2, "OMR_DATABASE_URL": ""}, capture_output=True, text=True).stdout.split()[-5:]
assert out == ["60.0", "1.0", "0.65", "4", "False"], out   # 합격 총점 300 -> 평균 60, 배점은 유지
print("설정 이전 OK"); print("ALL PASS")
