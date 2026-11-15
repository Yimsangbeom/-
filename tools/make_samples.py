"""실제 양식(PDF)에 가상 마킹을 그리고 '스캔한 것처럼' 열화시켜 테스트용 답안지를 만든다.
사용: python tools/make_samples.py <양식.pdf> <출력폴더>"""
import os, random, sys
import cv2
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from omr_engine import DEFAULT_CONFIG as C, load_pages_from_file  # noqa: E402
from form_map import derive_key  # noqa: E402


def fill(img, x, y, rng, gray=35, r=11.5):
    """펜으로 칠한 듯한 불규칙 타원 (위치/크기/각도/농도 편차)."""
    h, w = img.shape[:2]
    m = np.zeros((h, w), np.float32)
    cv2.ellipse(m, (int(x + rng.uniform(-2.5, 2.5)), int(y + rng.uniform(-2.5, 2.5))),
                (int(r + rng.uniform(-1.5, 1.5)), int(r + rng.uniform(-1.5, 1.5))),
                rng.uniform(0, 180), 0, 360, 1.0, -1, cv2.LINE_AA)
    noise = np.random.default_rng(rng.randint(0, 10**6)).normal(0, 14, (h, w)).astype(np.float32)
    ink = np.clip(gray + noise, 0, 255)
    for c in range(3):
        img[:, :, c] = (img[:, :, c] * (1 - m) + ink * m).astype(np.uint8)


def draw_sheet(blank, exam_type, sid, marks, rng, gray_map=None):
    """marks: {문항번호: [선택,...]} (빈 리스트/없음=무표기, 2개=이중마킹)."""
    img = blank.copy()
    fill(img, *C["type_bubbles"][exam_type], rng)
    for i, ch in enumerate(sid):
        fill(img, C["id_grid"]["x_cols"][i], C["id_grid"]["y_rows"][int(ch)], rng)
    xs, ys = C["answer_grid"]["x_cols"], C["answer_grid"]["y_rows"]
    for q in range(1, 101):
        s, row = divmod(q - 1, 20)
        for ch in marks.get(q, []):
            fill(img, xs[s * 5 + ch - 1], ys[row], rng, gray=(gray_map or {}).get(q, 35))
    return img


def scan(img, rng, rot=0.0, shift=(0, 0), persp=4, blur=0.7, noise=4, bright=0.97, shadow=12, q=78):
    """스캔 열화: 기울어짐/이동/원근/그림자/블러/노이즈/JPEG."""
    h, w = img.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2, h / 2), rot, 1.0)
    M[:, 2] += shift
    out = cv2.warpAffine(img, M, (w, h), borderValue=(244, 244, 244))
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    dst = src + np.float32([[rng.uniform(-persp, persp), rng.uniform(-persp, persp)] for _ in range(4)])
    out = cv2.warpPerspective(out, cv2.getPerspectiveTransform(src, dst), (w, h), borderValue=(244, 244, 244))
    grad = np.linspace(0, shadow, w, dtype=np.float32)[None, :, None]       # 좌→우 조명 불균일
    out = np.clip(out.astype(np.float32) * bright - grad, 0, 255)
    out = cv2.GaussianBlur(out, (0, 0), blur)
    out = np.clip(out + np.random.default_rng(rng.randint(0, 10**6)).normal(0, noise, out.shape), 0, 255).astype(np.uint8)
    ok, buf = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, q])
    return cv2.imdecode(buf, cv2.IMREAD_COLOR), buf.tobytes()


def make_all(form_pdf, outdir, seed=7):
    import fitz
    os.makedirs(outdir, exist_ok=True)
    rng = random.Random(seed)
    (_, blank), = load_pages_from_file(form_pdf)
    keys = {"A": [rng.randint(1, 5) for _ in range(100)]}
    [rng.randint(1, 5) for _ in range(100)]          # (이전 샘플과 같은 난수 흐름을 유지하기 위한 소비)
    keys["B"] = derive_key(keys["A"])                # B형 정답 = A형 정답을 블록 교체 규칙으로 변환

    def marks_from(key, wrong=0, doubles=(), blanks=()):
        mk = {q: [key[q - 1]] for q in range(1, 101)}
        for q in rng.sample(range(1, 101), wrong):
            mk[q] = [key[q - 1] % 5 + 1]
        for q in doubles:
            mk[q] = [key[q - 1], key[q - 1] % 5 + 1]
        for q in blanks:
            mk[q] = []
        return mk

    def marks_from_rng(key, r, wrong_qs):
        mk = {q: [key[q - 1]] for q in range(1, 101)}
        for q in wrong_qs:
            mk[q] = [key[q - 1] % 5 + 1]
        return mk

    truth, files = {}, {}
    # 마스터(정답) 답안지 A/B
    for t in "AB":
        _, jpg = scan(draw_sheet(blank, t, "000000000", marks_from(keys[t]), rng), rng, rot=0.4, shift=(5, 4))
        files[f"master_{t}.jpg"] = jpg
    specs = [  # 이름, 형, 수험번호, marks, 스캔옵션, 연한마킹
        ("student1", "A", "202600001", marks_from(keys["A"]), dict(rot=0.0, shift=(0, 0)), None),
        ("student2", "A", "202600002", marks_from(keys["A"], wrong=10), dict(rot=1.0, shift=(12, -9)), None),
        ("student3", "B", "202600003", marks_from(keys["B"], wrong=15, doubles=(12, 57), blanks=(3, 44, 90)),
         dict(rot=-1.5, shift=(-14, 10), bright=0.93), None),
        ("student4", "A", "202600004", marks_from(keys["A"], wrong=6), dict(rot=0.7, shift=(20, 15), blur=1.0, noise=6),
         {q: 120 for q in range(1, 9)}),   # 연필처럼 연하게 마킹한 8문항
    ]
    # student5: 3과목(C)만 13문항 오답 → 과목 점수 낮음(과락 확인용). 앞 학생들의 난수 흐름을 바꾸지 않도록 별도 난수 사용
    rng5 = random.Random(seed * 13 + 5)
    mk5 = marks_from_rng(keys["A"], rng5, rng5.sample(range(41, 61), 13))
    specs.append(("student5", "A", "202600005", mk5, dict(rot=-0.6, shift=(-8, 6)), None))
    imgs = {}
    for name, t, sid, mk, opt, gm in specs:
        r_ = rng5 if name == "student5" else rng
        sheet, jpg = scan(draw_sheet(blank, t, sid, mk, r_, gm), r_, **opt)
        imgs[name] = (sheet, jpg)
        truth[name] = dict(exam_type=t, student_id=sid, marks=mk, key=keys[t])
    files["student1.jpg"] = imgs["student1"][1]
    files["student2.jpg"] = imgs["student2"][1]
    files["student5.jpg"] = imgs["student5"][1]
    doc = fitz.open()                      # student3, student4 는 2쪽짜리 PDF 한 파일
    for n in ("student3", "student4"):
        h, w = imgs[n][0].shape[:2]
        page = doc.new_page(width=w * 72 / 200, height=h * 72 / 200)
        page.insert_image(page.rect, stream=imgs[n][1])
    files["batch_student3_4.pdf"] = doc.tobytes()
    for fn, data in files.items():
        with open(os.path.join(outdir, fn), "wb") as f:
            f.write(data)
    # 프로그램의 [테스트 실행]이 사용하는 정답 기록 (인식 결과와 대조해 일치율 표시)
    import json
    desc = {
        "student1": "A형 · 만점 (깨끗한 스캔)",
        "student2": "A형 · 10문항 오답, 약간 기울어진 스캔",
        "student3": "B형 · 15문항 오답 + 무표기 3 + 이중마킹 2 (검수필요 표시 확인용), 기울어짐",
        "student4": "A형 · 6문항 오답, 연필처럼 연한 마킹 8문항, 흐릿한 스캔",
        "student5": "A형 · 3과목(C) 13문항 오답, 해당 과목 35점(과락 확인용), 나머지는 만점",
    }
    students = {}
    for name, tr in truth.items():
        ans, score = {}, 0
        for q in range(1, 101):
            m = tr["marks"][q]
            ans[str(q)] = "무표기" if not m else ("이중" if len(m) > 1 else str(m[0]))
            score += len(m) == 1 and m[0] == tr["key"][q - 1]
        students[tr["student_id"]] = {"name": name, "exam_type": tr["exam_type"], "answers": ans,
                                      "score": score, "desc": desc[name]}
    meta = {"keys": keys, "students": students,
            "files": [{"file": "master_A.jpg", "desc": "A형 정답 답안지 (정답 자동 인식용)"},
                      {"file": "master_B.jpg", "desc": "B형 정답 답안지 (정답 자동 인식용)"},
                      {"file": "student1.jpg", "desc": desc["student1"]},
                      {"file": "student2.jpg", "desc": desc["student2"]},
                      {"file": "student5.jpg", "desc": desc["student5"]},
                      {"file": "batch_student3_4.pdf", "desc": "PDF 한 파일에 2쪽: " + desc["student3"].split(" (")[0] + " / " + desc["student4"].split(",")[0]}],
            "run_files": ["student1.jpg", "student2.jpg", "student5.jpg", "batch_student3_4.pdf"]}
    with open(os.path.join(outdir, "truth.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)
    return truth, keys


if __name__ == "__main__":
    t, _ = make_all(sys.argv[1], sys.argv[2])
    print("생성:", sorted(os.listdir(sys.argv[2])))
