"""새 시험지(답안지 양식) 자동 인식 검증.
1) 합성 답안지(기존과 전혀 다른 배치: 4블록x25행x4지선다, 수험번호 8자리, 유형 3개)를 인식 -> 등록 -> 새 시험으로 스캔본 채점
2) (선택) 실제 NIA 빈 양식 PDF 를 인식해 기존 좌표와 비교하고 스캔 샘플까지 채점:  python test_layout_detect.py <양식.pdf>"""
import io, os, random, sys, tempfile
os.environ["OMR_DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools"))
import cv2, numpy as np
from make_samples import scan, fill
from app import app
import layout_detect

FONT = cv2.FONT_HERSHEY_SIMPLEX
W, H = 2339, 1654

def design():
    """합성 양식의 정답 좌표."""
    blocks = []
    for b in range(4):
        x0 = 580 + b * 430
        blocks.append(([x0 + c * 50 for c in range(4)], [300 + r * 45 + (r // 5) * 30 for r in range(25)]))
    idg = ([110 + i * 40 for i in range(8)], [700 + d * 55 for d in range(10)])
    types = {"A": (150, 300), "B": (150, 365), "C": (150, 430)}
    return blocks, idg, types

def blank_generic():
    img = np.full((H, W, 3), 255, np.uint8)
    blocks, (idx, idy), types = design()
    def ring(x, y, t):
        cv2.circle(img, (int(x), int(y)), 13, (0, 0, 0), 2); cv2.putText(img, t, (int(x) - 5, int(y) + 5), FONT, 0.45, (0, 0, 0), 1, cv2.LINE_AA)
    cv2.putText(img, "TEST EXAM ANSWER SHEET", (600, 120), FONT, 1.4, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.rectangle(img, (500, 200), (2250, 1500), (0, 0, 0), 3)
    for b, (xs, ys) in enumerate(blocks):
        for r, y in enumerate(ys):
            cv2.putText(img, str(b * 25 + r + 1), (xs[0] - 70, int(y) + 6), FONT, 0.6, (0, 0, 0), 1, cv2.LINE_AA)
            for c, x in enumerate(xs): ring(x, y, str(c + 1))
    cv2.rectangle(img, (60, 600), (440, 1300), (0, 0, 0), 3)
    for i, x in enumerate(idx):
        for d, y in enumerate(idy): ring(x, y, str(d))
    cv2.rectangle(img, (60, 250), (440, 500), (0, 0, 0), 3)
    for k, (x, y) in types.items():
        ring(x, y, k); cv2.putText(img, k + " type", (x + 40, y + 6), FONT, 0.7, (0, 0, 0), 2, cv2.LINE_AA)
    cv2.rectangle(img, (60, 60), (440, 200), (0, 0, 0), 2)               # 작성 유의사항(●, ◐ 같은 기호가 원으로 검출됨)
    cv2.circle(img, (120, 110), 11, (0, 0, 0), -1); cv2.circle(img, (165, 110), 11, (0, 0, 0), 2); cv2.ellipse(img, (165, 110), (11, 11), 0, -90, 90, (0, 0, 0), -1)
    cv2.circle(img, (210, 110), 11, (0, 0, 0), 2); cv2.circle(img, (210, 110), 3, (0, 0, 0), -1)
    return img

def filled(form, sid, answers, rng, wrong=(), blanks=()):
    img = blank_generic()
    blocks, (idx, idy), types = design()
    fill(img, *types[form], rng)
    for i, ch in enumerate(sid): fill(img, idx[i], idy[int(ch)], rng)
    q = 0
    for xs, ys in blocks:
        for y in ys:
            q += 1
            if q in blanks: continue
            a = answers[q - 1] if q not in wrong else answers[q - 1] % 4 + 1
            fill(img, xs[a - 1], y, rng)
    return img

def png(img):
    ok, buf = cv2.imencode(".png", img); return buf.tobytes()

# ---------------- 1) 합성 양식 ----------------
c = app.test_client()
assert c.post("/api/setup", json={"password": "testpass123"}).status_code == 200
blank = blank_generic()
cfg, sm, prev = layout_detect.detect(blank)
assert (sm["blocks"], sm["rows"], sm["choices"], sm["questions"], sm["id_digits"], sm["forms"]) == (4, [25] * 4, 4, 100, 8, ["A", "B", "C"]), sm
assert sm["anchors"] >= 2 and not sm["warnings"], sm
blocks, (idx, idy), types = design()
for b, (xs, ys) in zip(cfg["answer_blocks"], blocks):
    assert max(abs(np.array(b["x_cols"]) - xs)) <= 1.5 and max(abs(np.array(b["y_rows"]) - ys)) <= 1.5      # 좌표 오차 1.5px 이내
assert max(abs(np.array(cfg["id_grid"]["x_cols"]) - idx)) <= 1.5 and max(abs(np.array(cfg["id_grid"]["y_rows"]) - idy)) <= 1.5
assert all(abs(cfg["type_bubbles"][k][0] - v[0]) <= 1.5 and abs(cfg["type_bubbles"][k][1] - v[1]) <= 1.5 for k, v in types.items())
print("합성 양식 인식 OK:", sm["blocks"], "블록", sm["rows"], "행", sm["choices"], "지선다 ID", sm["id_digits"], "유형", sm["forms"])

# 인식 API: 미리보기/등록/오류
r = c.post("/api/layouts/detect", data={"file": (io.BytesIO(png(blank)), "테스트 시험지.png")}); j = r.get_json()
assert r.status_code == 200 and j["status"] == "success" and j["name"] == "테스트 시험지" and j["summary"]["questions"] == 100, j
pv = c.get(j["preview_url"]); assert pv.status_code == 200 and pv.mimetype == "image/jpeg" and len(pv.data) > 20000
assert c.get("/api/layouts/preview/..%2F..%2Fetc.jpg").status_code in (400, 404)
blank_white = png(np.full((H, W, 3), 255, np.uint8))
e = c.post("/api/layouts/detect", data={"file": (io.BytesIO(blank_white), "빈종이.png")}); assert e.status_code == 422 and "찾" in e.get_json()["message"]
assert app.test_client().post("/api/layouts/detect", data={"file": (io.BytesIO(b"x"), "a.png")}).status_code == 401      # 로그인 필요
assert c.post("/api/layouts/register", json={"token": "000000000000"}).status_code == 404
rg = c.post("/api/layouts/register", json={"token": j["token"], "name": "디지털 시험 (자동 인식)"}).get_json()
assert rg["status"] == "success"
info = c.get("/api/exams").get_json()
lay = next(l for l in info["layouts"] if l["id"] == rg["layout"])
assert lay["capacity"] == 100 and lay["choices_max"] == 4 and lay["id_digits_max"] == 8 and lay["forms"] == ["A", "B", "C"] and "자동 인식" in lay["label"]
newx = next(x for x in info["exams"] if x["id"] == rg["id"])
assert [s["count"] for s in newx["exam"]["subjects"]] == [25] * 4 and newx["exam"]["choices"] == 4 and newx["exam"]["id_digits"] == 8 and newx["exam"]["forms"] == ["A", "B", "C"]
assert os.path.exists(os.path.join(os.environ["OMR_DATA_DIR"], "layouts", rg["layout"] + ".json"))     # 양식이 파일로 저장됨(재시작 후에도 유지)

# 새 시험으로 전환 -> 정답 등록 -> 스캔본 채점
assert c.post(f"/api/exams/{rg['id']}/activate").status_code == 200
rng = random.Random(21); key = [rng.randint(1, 4) for _ in range(100)]
assert c.post("/api/config", json={"answer_keys": {"C": key}}).status_code == 200
sheets = [("s1.png", "C", "20260001", set(), set()), ("s2.png", "C", "20260002", {3, 40, 77}, {5, 90})]
files = []
for name, f, sid, wrong, blanks in sheets:
    img = filled(f, sid, key, rng, wrong=wrong, blanks=blanks)
    img, _ = scan(img, rng, rot=1.0, shift=(14, -10), persp=3)
    files.append((io.BytesIO(png(img)), name))
j2 = c.post("/api/grade/students", data={"files": files}).get_json()
assert j2["status"] == "success" and not j2["errors"], j2
r1, r2 = j2["results"]
assert r1["student_id"] == "20260001" and r1["exam_type"] == "C" and r1["correct_count"] == 100, (r1["student_id"], r1["exam_type"], r1["correct_count"])
assert r1["student_answers"] == {str(i + 1): str(key[i]) for i in range(100)}
assert r2["student_id"] == "20260002" and r2["correct_count"] == 95 and r2["blank_count"] == 2 and r2["needs_review"], (r2["correct_count"], r2["blank_count"])
print("합성 양식 등록/채점 OK: 스캔본(기울기 1도, 이동) 100/100, 오답3+무표기2 정확히 판독")

# ---------------- 2) 실제 NIA 양식 (선택) ----------------
if len(sys.argv) > 1:
    from omr_engine import load_pages_from_file, DEFAULT_CONFIG as D
    from make_samples import make_all
    (_, nia), = load_pages_from_file(sys.argv[1])
    ncfg, nsm, _ = layout_detect.detect(nia)
    assert (nsm["blocks"], nsm["rows"], nsm["choices"], nsm["id_digits"], nsm["forms"], nsm["anchors"]) == (5, [20] * 5, 5, 9, ["A", "B"], 2), nsm
    old = np.array([(D["answer_grid"]["x_cols"][s * 5 + k], y) for s in range(5) for y in D["answer_grid"]["y_rows"] for k in range(5)])
    new = np.array([(x, y) for b in ncfg["answer_blocks"] for y in b["y_rows"] for x in b["x_cols"]])
    assert np.abs(old - new).max() <= 1.0, np.abs(old - new).max()
    print("NIA 양식 인식 OK: 기존 좌표와 최대 오차 %.1fpx" % np.abs(old - new).max())
    out = tempfile.mkdtemp(); truth, keys = make_all(sys.argv[1], out)
    jn = c.post("/api/layouts/detect", data={"file": (open(sys.argv[1], "rb"), "NIA.pdf")}).get_json()
    rn = c.post("/api/layouts/register", json={"token": jn["token"], "name": "NIA 자동 인식"}).get_json()
    # 과목/유형 관계는 자동으로 알 수 없으므로 시험 설정에서 지정 (여기서는 기본 시험과 같게)
    nx = next(x for x in c.get("/api/exams").get_json()["exams"] if x["id"] == rn["id"])["exam"]
    nx["form_rule"] = "half_swap"
    assert c.put(f"/api/exams/{rn['id']}", json={"exam": nx}).status_code == 200
    assert c.post(f"/api/exams/{rn['id']}/activate").status_code == 200
    for t in "AB":
        m = c.post("/api/grade/master", data={"file": (open(f"{out}/master_{t}.jpg", "rb"), f"master_{t}.jpg")}).get_json()
        assert m["status"] == "success" and m["answers"] == keys[t], (t, m.get("status"), m.get("problems"))
    files = [(open(f"{out}/{n}", "rb"), n) for n in ("student1.jpg", "student2.jpg", "student5.jpg", "batch_student3_4.pdf")]
    res = {r["student_id"]: r for r in c.post("/api/grade/students", data={"files": files}).get_json()["results"]}
    bad = 0
    for name, tr in truth.items():
        r = res[tr["student_id"]]
        exp = {str(q): ("무표기" if not m else ("이중" if len(m) > 1 else str(m[0]))) for q, m in tr["marks"].items()}
        wrong = [q for q in exp if r["student_answers"].get(q) != exp[q]]
        bad += bool(wrong) or r["exam_type"] != tr["exam_type"]
        print(("OK " if not wrong else "NG ") + name, "오인식:", wrong)
    assert not bad
    print("NIA 자동 인식 양식으로 스캔 샘플 5장 채점 OK")
print("ALL PASS")
