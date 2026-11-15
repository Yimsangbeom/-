"""채점(오래 걸리는 업로드) 중에도 상태 확인(/healthz)과 결과 조회가 막히지 않는지 검증한다.
감시 작업이 정상 서버를 멈춘 것으로 오판해 재시작시키는 일을 막기 위한 테스트. (실제 waitress 서버를 띄워 동시에 요청)"""
import io, os, sys, tempfile, threading, time
os.environ["OMR_DATA_DIR"] = tempfile.mkdtemp()
import cv2, random, requests, waitress
from test_synthetic import make_sheet
import omr_engine
_orig = omr_engine.OMREngine.grade_sheet
def _slow(self, *a, **k):          # 실제 스캔본처럼 한 장에 시간이 걸리는 상황을 흉내 (장당 0.6초)
    time.sleep(0.6)
    return _orig(self, *a, **k)
omr_engine.OMREngine.grade_sheet = _slow
from app import app

PORT = 18765
server = waitress.create_server(app, host="127.0.0.1", port=PORT, threads=4)      # 이전 운영 설정(4개)에서도 막히지 않아야 함
threading.Thread(target=server.run, daemon=True).start()
time.sleep(1.0)
U = f"http://127.0.0.1:{PORT}"
s = requests.Session()
assert s.post(U + "/api/setup", json={"password": "testpass123"}).status_code == 200

random.seed(2); key = [random.randint(1, 5) for _ in range(100)]
d = tempfile.mkdtemp(); files = []
for i in range(14):
    p = os.path.join(d, f"s{i}.png"); cv2.imwrite(p, make_sheet("A", f"2026{i:05d}", key)); files.append(("files", (f"s{i}.png", open(p, "rb"))))

done = {}
def upload():
    t = time.time(); r = s.post(U + "/api/grade/students", files=files); done["upload"] = (time.time() - t, r.status_code, len(r.json()["results"]))
up = threading.Thread(target=upload); up.start()
time.sleep(0.5)

lat = {"healthz": [], "results": [], "stats": []}
def poll(kind, path, n):
    for _ in range(n):
        t = time.time(); r = requests.get(U + path, timeout=30) if kind == "healthz" else s.get(U + path, timeout=30)
        assert r.status_code == 200, (path, r.status_code)
        lat[kind].append(time.time() - t); time.sleep(0.05)
ths = [threading.Thread(target=poll, args=("results", "/api/results", 15)) for _ in range(10)] \
    + [threading.Thread(target=poll, args=("stats", "/api/stats?scope=real", 6)) for _ in range(2)] \
    + [threading.Thread(target=poll, args=("healthz", "/healthz", 25))]
[t.start() for t in ths]; [t.join() for t in ths]; up.join()
dur, code, n = done["upload"]
print(f"업로드 {n}장 {dur:.1f}초 소요 중 / 상태확인 최대 {max(lat['healthz']):.2f}s, 결과조회 최대 {max(lat['results']):.2f}s, 통계 최대 {max(lat['stats']):.2f}s")
assert code == 200 and n == 14
assert max(lat["healthz"]) < 0.3, "채점 중 /healthz 응답이 느림 -> 감시 작업이 오판할 수 있음"
assert dur > 5.0, "채점이 너무 빨리 끝나 동시성 검증이 무의미함"
assert max(lat["results"]) < 0.3 and max(lat["stats"]) < 0.3, "채점 중 결과/통계 조회가 막힘"
server.close()
print("ALL PASS")
