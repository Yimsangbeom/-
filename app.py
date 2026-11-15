import io
import json
import os
import re
import threading
import time
import uuid

import cv2
import pandas as pd
from flask import Flask, request, jsonify, send_file, send_from_directory

import auth
import db
import excel_export
import exam as exam_mod
import layout_detect
import layouts
import stats
from omr_engine import OMREngine, load_pages_from_file

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("OMR_DATA_DIR", os.path.join(BASE_DIR, "data"))
TEMP_DIR = os.path.join(DATA_DIR, "temp")
RESULT_DIR = os.path.join(DATA_DIR, "results")
for d in (DATA_DIR, TEMP_DIR, RESULT_DIR):
    os.makedirs(d, exist_ok=True)
layouts.load_user_layouts(DATA_DIR)   # 자동 인식으로 등록한 답안지 양식

# 소스코드(app.py 등)가 외부에 노출되지 않도록 static 폴더만 공개한다.
app = Flask(__name__, static_folder=os.path.join(BASE_DIR, "static"), static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024 * 1024  # 요청당 최대 1GB
auth.init_auth(app, DATA_DIR, app.static_folder)

lock = threading.RLock()
_engines = {}


def get_engine(layout_id):
    if layout_id not in _engines:
        _engines[layout_id] = OMREngine(layouts.LAYOUTS[layout_id]["config"])
    return _engines[layout_id]


def current_engine():
    e = get_engine(state["exam"]["layout"])
    e.fill_threshold = float(state["threshold"])
    return e


def new_state(exam):
    """새 시험의 기본 설정. 정답은 등록 전 임시값."""
    return {
        "exam": exam,
        "answer_keys": exam_mod.default_keys(exam),
        "keys_set": {f: False for f in exam["forms"]},   # 실제로 등록된 정답인지(임시값 구분)
        "score_per_q": 5.0,           # 문항당 배점 (기본 시험: 20문항 x 5점 = 과목 100점)
        "pass_average": 60.0,         # 합격 평균: 과락이 없고 과목 점수 평균(총점/과목 수)이 이 점수 이상이면 합격
        "subject_fail_cutoff": 40.0,  # 과락 점수: 한 과목이라도 이 점수 미만이면 과락(0=사용 안 함)
        "threshold": 0.65,            # 마킹 인식 민감도
        "schema": 4,
    }


state = {}                 # 현재(활성) 시험의 설정
registry = {"active": "default", "list": []}    # 시험 목록 [{"id", "name"}]
graded_results = []        # 활성 시험의 채점 결과 (실제 + 테스트)
next_id = 0

try:
    store = db.Store(DATA_DIR)
except Exception as e:      # DB 접속 실패 시 원인을 로그에 남기고 종료
    raise SystemExit(f"데이터베이스에 연결하지 못했습니다: {e}\n접속 정보(OMR_DATABASE_URL 또는 data/db.json)를 확인하세요.")


def save_state():
    store.set_setting("state:" + registry["active"], state)


def save_registry():
    store.set_setting("exams", registry)


def upgrade_state(st):
    """이전 버전 설정을 현재 방식으로 이전."""
    if st.get("schema") not in (3, 4):   # 이전 버전 설정을 문항당 5점/합격 평균 방식으로 이전
        if st.get("schema") == 2 and "pass_cutoff" in st:
            st["pass_average"] = round(float(st["pass_cutoff"]) / 5, 1)   # 합격 총점 -> 합격 평균
        else:
            st.update({"score_per_q": 5.0, "subject_fail_cutoff": 40.0, "pass_average": 60.0})
        for k in ("pass_cutoff", "fail_cutoff", "subject_cutoff"):
            st.pop(k, None)
        if float(st.get("threshold", 0)) < 0.6:
            st["threshold"] = 0.65
    if "exam" not in st:                  # 시험 설정이 없던 버전: 기본 시험(인터넷중독전문상담사)
        st["exam"] = exam_mod.default_exam()
    ex = st["exam"]
    st.setdefault("keys_set", {})
    defaults = exam_mod.default_keys(ex)
    for f in ex["forms"]:
        st["answer_keys"].setdefault(f, defaults[f])
        # 이전 버전에서 이미 등록해 둔 정답은 '등록됨'으로 간주
        st["keys_set"][f] = bool(st["keys_set"].get(f)) or st["answer_keys"][f] != defaults[f]
    st["schema"] = 4
    return st


def remove_result_files(items):
    for r in items:
        p = os.path.join(RESULT_DIR, os.path.basename(r.get("vis_url", "")))
        if os.path.isfile(p):
            os.remove(p)


def activate(exam_id):
    """exam_id 시험을 활성화: 설정과 채점 결과를 불러온다."""
    global graded_results, next_id
    st = store.get_setting("state:" + exam_id)
    if st is None:
        raise KeyError(exam_id)
    state.clear()
    state.update(upgrade_state(st))
    registry["active"] = exam_id
    graded_results = store.load_results(exam_id)
    # 채점 당시 정답이 저장되지 않은 이전 버전의 테스트 결과는 다시 만들 수 있으므로 정리 (통계가 어긋나 보이는 것을 방지)
    stale = [r for r in graded_results if r.get("is_test") and not r.get("key_used")]
    if stale:
        remove_result_files(stale)
        store.delete_results(ids=[r["id"] for r in stale])
        graded_results = [r for r in graded_results if r not in stale]
    next_id = store.max_id() + 1
    save_state()
    save_registry()


def load_all():
    reg = store.get_setting("exams")
    if reg is None:      # 최초 실행 또는 이전 버전(단일 시험) -> 'default' 시험으로 이전
        legacy = store.load_settings()
        st = upgrade_state(legacy) if legacy else new_state(exam_mod.default_exam())
        store.set_setting("state:default", st)
        reg = {"active": "default", "list": [{"id": "default", "name": st["exam"]["name"]}]}
        store.set_setting("exams", reg)
        if legacy is not None:
            store.delete_setting("state")
    registry.clear()
    registry.update(reg)
    if registry["active"] not in {e["id"] for e in registry["list"]}:
        registry["active"] = registry["list"][0]["id"]
    activate(registry["active"])


load_all()


def exam_forms():
    return state["exam"]["forms"]


def current_conflicts():
    """두 유형 정답이 모두 등록된 경우, 첫 번째 유형 번호 기준으로 서로 다른 문항 목록."""
    ex = state["exam"]
    if ex["form_rule"] == "half_swap" and len(ex["forms"]) == 2 and all(state["keys_set"][f] for f in ex["forms"]):
        a, b = ex["forms"]
        return exam_mod.key_conflicts(ex, state["answer_keys"][a], state["answer_keys"][b])
    return []


def register_key(form, key):
    """정답 등록. 대응 규칙이 있고 다른 유형이 미등록이면 자동 생성하고, 이미 등록돼 있으면 서로 일치하는지 검사."""
    ex = state["exam"]
    n_q = exam_mod.total_questions(ex)
    if form not in ex["forms"]:
        raise ValueError(f"이 시험에 없는 문제 유형입니다: {form}")
    key = [int(x) for x in key]
    if len(key) < n_q or any(not 1 <= v <= ex["choices"] for v in key[:n_q]):
        raise ValueError(f"정답은 {n_q}문항 모두 1~{ex['choices']} 사이 숫자여야 합니다.")
    state["answer_keys"][form] = key[:n_q]
    state["keys_set"][form] = True
    derived = False
    if ex["form_rule"] == "half_swap" and len(ex["forms"]) == 2:
        other = [f for f in ex["forms"] if f != form][0]
        if not state["keys_set"][other]:
            state["answer_keys"][other] = exam_mod.derive_key(ex, state["answer_keys"][form])
            state["keys_set"][other] = True
            derived = True
    return {"derived_other": derived, "conflicts": current_conflicts()}


def save_upload(file_storage):
    """원본 파일명을 쓰지 않고 uuid 이름으로 저장 (경로조작/이름충돌 방지)."""
    ext = os.path.splitext(file_storage.filename or "")[1].lower()
    path = os.path.join(TEMP_DIR, uuid.uuid4().hex + ext)
    file_storage.save(path)
    return path


def scaled(item):
    """저장된 '맞힌 개수'에 현재 문항당 배점을 곱해 점수를 계산한다(배점을 바꿔도 즉시 반영)."""
    spq = state["score_per_q"]
    counts = item.get("subject_correct") or item.get("subject_scores", {})
    scores = {c: counts.get(c, 0) * spq for c in exam_mod.codes(state["exam"])}
    return {**item, "subject_scores": scores, "total_score": sum(scores.values())}


def judge(item):
    """item 은 scaled() 결과. 한 과목이라도 과락 점수 미만 → 과락, 그 외 평균(총점/과목 수)이 합격 평균 이상 → 합격, 아니면 불합격."""
    cut = state["subject_fail_cutoff"]
    fails = [c for c in exam_mod.codes(state["exam"]) if cut > 0 and item["subject_scores"][c] < cut]
    if fails:
        return "과락", fails
    return ("불합격" if item["total_score"] / exam_mod.n_subjects(state["exam"]) < state["pass_average"] else "합격"), []


def make_result(res, page_label, is_test=False, verify=None, keys=None):
    """채점 결과를 저장용 dict 로 만들고 판독 이미지를 저장한다. (lock 안에서 호출)"""
    global next_id
    rid = next_id
    next_id += 1
    vis_name = f"vis_{rid}.jpg"
    cv2.imwrite(os.path.join(RESULT_DIR, vis_name), res["vis_img"])
    item = {
        "id": rid,
        "filename": page_label,
        "exam_type": res["exam_type"],
        "student_id": res["student_id"],
        "subject_correct": {c: int(round(v)) for c, v in res["subject_scores"].items()},  # 맞힌 개수(배점 1점 기준)
        "correct_count": res["correct_count"],
        "multi_count": res["multi_count"],
        "blank_count": res["blank_count"],
        "needs_review": res["needs_review"],
        "student_answers": {str(k): v for k, v in res["student_answers"].items()},
        "vis_url": f"/results/{vis_name}",
        "is_test": is_test,
        "exam_id": registry["active"],
    }
    if keys is not None:   # 채점에 쓰인 정답을 함께 저장 (나중에 정답을 바꿔도 문항 통계가 흔들리지 않게)
        n_q = exam_mod.total_questions(state["exam"])
        item["key_used"] = [int(x) for x in keys.get(res["exam_type"], keys[state["exam"]["forms"][0]])][:n_q]
    if verify is not None:
        item["verify"] = verify
    return item


def display_name(file_storage):
    return os.path.basename((file_storage.filename or "").replace("\\", "/"))


@app.route("/")
def index():
    return app.send_static_file("index.html")


@app.route("/results/<path:name>")
def result_image(name):
    return send_from_directory(RESULT_DIR, name)


def exam_info():
    ex = state["exam"]
    return {"total_questions": exam_mod.total_questions(ex), "codes": exam_mod.codes(ex),
            "ranges": [{"code": c, "name": n, "short": sh, "start": a, "end": b} for c, n, sh, a, b in exam_mod.subject_ranges(ex)],
            "is_default_structure": exam_mod.is_default_structure(ex), "id": registry["active"]}


@app.route("/api/config", methods=["GET", "POST"])
def handle_config():
    with lock:
        if request.method == "GET":
            return jsonify({**state, "key_conflicts": current_conflicts(), "exam_info": exam_info()})
        data = request.get_json(force=True) or {}
        try:
            if "answer_keys" in data:
                keys = data["answer_keys"]
                forms = [f for f in exam_forms() if f in keys]
                for t in forms:
                    register_key(t, keys[t])
                if len(forms) == 2:     # 둘 다 직접 준 경우에는 자동 생성 없이 입력값 그대로
                    n_q = exam_mod.total_questions(state["exam"])
                    for t in forms:
                        state["answer_keys"][t] = [int(x) for x in keys[t]][:n_q]
        except ValueError as e:
            return jsonify({"status": "error", "message": str(e)}), 400
        if "score_per_q" in data:
            state["score_per_q"] = float(data["score_per_q"])
        if "pass_average" in data:
            state["pass_average"] = float(data["pass_average"])
        if "subject_fail_cutoff" in data:
            state["subject_fail_cutoff"] = max(0.0, float(data["subject_fail_cutoff"]))
        if "threshold" in data:
            state["threshold"] = float(data["threshold"])
        save_state()
        return jsonify({"status": "success", "message": "설정이 업데이트되었습니다.", "key_conflicts": current_conflicts()})


@app.route("/api/grade/master", methods=["POST"])
def grade_master():
    if "file" not in request.files:
        return jsonify({"status": "error", "message": "파일이 없습니다."}), 400
    temp_path = save_upload(request.files["file"])
    try:
        pages = load_pages_from_file(temp_path)
        if not pages:
            return jsonify({"status": "error", "message": "이미지를 읽을 수 없습니다."}), 400
        _, img_bgr = pages[0]
        with lock:
            ex = state["exam"]
            res = current_engine().grade_sheet(img_bgr, state["answer_keys"], 1.0, exam=ex)
            t_code = res["exam_type"]
            extracted, problems = [], []
            for q in range(1, exam_mod.total_questions(ex) + 1):
                ans = res["student_answers"].get(q, "무표기")
                if ans.isdigit():
                    extracted.append(int(ans))
                else:
                    extracted.append(1)
                    problems.append(f"{q}번({ans})")
            if len(problems) > max(3, exam_mod.total_questions(ex) // 10):
                return jsonify({"status": "error",
                                "message": f"인식 실패 문항이 {len(problems)}개입니다. 스캔 품질/민감도를 확인하세요."}), 422
            try:
                info = register_key(t_code, extracted)
            except ValueError as e:
                return jsonify({"status": "error", "message": str(e)}), 422
            save_state()
        return jsonify({"status": "success", "exam_type": t_code, "answers": extracted,
                        "problems": problems, **info})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


@app.route("/api/grade/students", methods=["POST"])
def grade_students():
    files = request.files.getlist("files")
    if not files:
        return jsonify({"status": "error", "message": "파일이 없습니다."}), 400

    new_results, errors = [], []
    for file in files:
        shown = display_name(file)
        temp_path = save_upload(file)
        try:
            pages = load_pages_from_file(temp_path)
            if not pages:
                errors.append(f"{shown}: 이미지를 읽을 수 없습니다.")
            for label, img_bgr in pages:
                with lock:
                    res = current_engine().grade_sheet(img_bgr, state["answer_keys"], 1.0, exam=state["exam"])
                    # 페이지 라벨은 임시파일명이므로 원래 파일명으로 교체
                    new_results.append(make_result(res, label.replace(os.path.basename(temp_path), shown),
                                                   keys=state["answer_keys"]))
        except Exception as e:
            errors.append(f"{shown}: {e}")
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    with lock:
        graded_results.extend(new_results)
        store.add_results(new_results)
    return jsonify({"status": "success", "results": [with_judge(r) for r in new_results], "errors": errors})


def with_judge(item):
    sc = scaled(item)
    status, fails = judge(sc)
    return {**sc, "average": round(sc["total_score"] / exam_mod.n_subjects(state["exam"]), 1), "result": status, "fail_subjects": fails}


@app.route("/api/results", methods=["GET", "DELETE"])
def handle_results():
    global graded_results, next_id
    with lock:
        if request.method == "GET":
            return jsonify([with_judge(r) for r in graded_results])
        if request.args.get("scope") == "test":      # 테스트 결과만 삭제
            remove_result_files([r for r in graded_results if r.get("is_test")])
            graded_results = [r for r in graded_results if not r.get("is_test")]
            store.delete_results(is_test=True, exam_id=registry["active"])
            return jsonify({"status": "success"})
        remove_result_files(graded_results)           # 활성 시험의 결과만 삭제 (다른 시험은 유지)
        graded_results = []
        store.delete_results(exam_id=registry["active"])
        return jsonify({"status": "success"})


@app.route("/api/results/delete", methods=["POST"])
def delete_selected():
    """선택한 답안지 결과(번호 목록)만 삭제."""
    global graded_results
    ids = {int(x) for x in (request.get_json(silent=True) or {}).get("ids", [])}
    with lock:
        gone = [r for r in graded_results if r["id"] in ids]
        for r in gone:
            p = os.path.join(RESULT_DIR, os.path.basename(r.get("vis_url", "")))
            if os.path.isfile(p):
                os.remove(p)
        graded_results = [r for r in graded_results if r["id"] not in ids]
        store.delete_results(ids=[r["id"] for r in gone])
    return jsonify({"status": "success", "deleted": len(gone)})


@app.route("/api/system")
def api_system():
    """저장 위치/건수 (홈 화면 '데이터 관리'에 표시)."""
    with lock:
        info = store.describe()
        info["counts"] = {"real": len(scoped("real")), "test": len(scoped("test"))}
        info["images_dir"] = RESULT_DIR
        info["exam_name"] = state["exam"]["name"]
    return jsonify(info)


SAMPLE_DIR = os.path.join(app.static_folder, "samples")


@app.route("/api/test/info")
def test_info():
    with open(os.path.join(SAMPLE_DIR, "truth.json"), encoding="utf-8") as f:
        meta = json.load(f)
    return jsonify({"available": exam_mod.is_default_structure(state["exam"]), "files": meta["files"], "students": [
        {"student_id": k, "desc": v["desc"], "exam_type": v["exam_type"], "expected_correct": v["score"]}
        for k, v in meta["students"].items()]})


@app.route("/api/test/run", methods=["POST"])
def test_run():
    """내장된 가상 답안지를 채점한다. 테스트용 정답을 따로 쓰므로 실제 정답 설정은 바뀌지 않는다.
    결과에는 is_test 표시와 '인식 일치율'(가상 답안지를 만들 때의 정답 기록과 대조)이 붙는다."""
    global graded_results
    if not exam_mod.is_default_structure(state["exam"]):
        return jsonify({"status": "error", "message": "테스트용 가상 답안지는 기본 시험 구성(5과목 x 20문항, A/B형, 선택지 5, 수험번호 9자리)에서만 사용할 수 있습니다."}), 400
    with open(os.path.join(SAMPLE_DIR, "truth.json"), encoding="utf-8") as f:
        meta = json.load(f)
    new = []
    with lock:
        for fn in meta["run_files"]:
            for label, img in load_pages_from_file(os.path.join(SAMPLE_DIR, fn)):
                res = current_engine().grade_sheet(img, meta["keys"], 1.0, exam=state["exam"])
                exp = meta["students"].get(res["student_id"])
                verify = None
                if exp:
                    ok = sum(res["student_answers"].get(int(q)) == a for q, a in exp["answers"].items())
                    verify = {"match": ok, "total": 100, "expected_correct": exp["score"], "desc": exp["desc"]}
                new.append(make_result(res, "[테스트] " + label, is_test=True, verify=verify, keys=meta["keys"]))
        graded_results = [r for r in graded_results if not r.get("is_test")] + new   # 이전 테스트 결과는 교체
        store.delete_results(is_test=True, exam_id=registry["active"])
        store.add_results(new)
    return jsonify({"status": "success", "results": [with_judge(r) for r in new]})


def scoped(scope):
    if scope == "test":
        return [r for r in graded_results if r.get("is_test")]
    if scope == "real":
        return [r for r in graded_results if not r.get("is_test")]
    return list(graded_results)


_truth_keys = {}


def item_key(item):
    """문항 통계용 정답. 저장된 key_used 우선, 없으면(이전 버전 결과) 현재 정답 설정/테스트 정답으로 대체."""
    if item.get("key_used"):
        return item["key_used"]
    if item.get("is_test"):
        if not _truth_keys:
            with open(os.path.join(SAMPLE_DIR, "truth.json"), encoding="utf-8") as f:
                _truth_keys.update(json.load(f)["keys"])
        keys = _truth_keys
    else:
        keys = state["answer_keys"]
    return [int(x) for x in keys.get(item["exam_type"], keys[state["exam"]["forms"][0]])][:exam_mod.total_questions(state["exam"])]


def stats_items(scope):
    # _legacy: 채점 당시 정답이 저장되지 않은 이전 버전 결과 (현재 정답 설정으로 대신 계산하므로 부정확할 수 있음)
    return [{**with_judge(r), "_key": item_key(r), "_legacy": not r.get("key_used")} for r in scoped(scope)]


@app.route("/api/stats")
def api_stats():
    scope = request.args.get("scope", "real")
    with lock:
        data = stats.compute(stats_items(scope), state["score_per_q"], state["exam"])
        data["scope"] = scope
        data["counts"] = {"real": len(scoped("real")), "test": len(scoped("test"))}
        data["score_per_q"] = state["score_per_q"]
    return jsonify(data)


@app.route("/api/export/excel", methods=["GET"])
def export_excel():
    """엑셀 다운로드. part=stats 이면 통계(차트 포함)만, 아니면 결과 + 문항별 답안 + 통계(차트 포함)."""
    scope = request.args.get("scope", "all")
    part = request.args.get("part", "all")
    with lock:
        base = scoped(scope)
        if not base:
            return jsonify({"status": "error", "message": "채점 결과가 없습니다."}), 400
        judged = [with_judge(r) for r in base]
        # 통계 대상 (범위가 all 이면 실제 응시자 우선, 없으면 테스트)
        st_scope = scope if scope in ("real", "test") else ("real" if scoped("real") else "test")
        st = stats.compute(stats_items(st_scope), state["score_per_q"], state["exam"])
        out = excel_export.build(state["exam"], state["score_per_q"], judged, st, st_scope,
                                 state["pass_average"], state["subject_fail_cutoff"], part)
    name = state["exam"]["name"].replace("/", "_").replace("\\", "_")
    return send_file(
        out,
        download_name=f"{name}_{'통계' if part == 'stats' else '채점결과'}.xlsx",
        as_attachment=True,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ------------------------- 새 시험지(답안지 양식) 자동 인식 -------------------------
DETECT_DIR = os.path.join(TEMP_DIR, "layout_detect")
os.makedirs(DETECT_DIR, exist_ok=True)
TOKEN_RE = re.compile(r"^[0-9a-f]{12}$")


def _cleanup_detect_dir(max_age=86400):
    now = time.time()
    for f in os.listdir(DETECT_DIR):
        p = os.path.join(DETECT_DIR, f)
        if os.path.isfile(p) and now - os.path.getmtime(p) > max_age:
            os.remove(p)


@app.route("/api/layouts/detect", methods=["POST"])
def detect_layout():
    """빈 답안지(PDF/이미지 첫 쪽)에서 양식을 자동 인식한다. 아직 등록하지 않고 결과와 미리보기만 돌려준다."""
    f = request.files.get("file")
    if f is None:
        return jsonify({"status": "error", "message": "파일이 없습니다."}), 400
    temp_path = save_upload(f)
    try:
        pages = load_pages_from_file(temp_path)
        if not pages:
            return jsonify({"status": "error", "message": "이미지를 읽을 수 없습니다. PDF 또는 PNG/JPG 파일을 올려 주세요."}), 400
        _cleanup_detect_dir()
        token = uuid.uuid4().hex[:12]
        try:
            cfg, summary, preview = layout_detect.detect(pages[0][1])
        except layout_detect.DetectError as e:
            return jsonify({"status": "error", "message": str(e), "info": e.info}), 422
        summary["pages"] = len(pages)
        cv2.imwrite(os.path.join(DETECT_DIR, token + ".jpg"), preview, [cv2.IMWRITE_JPEG_QUALITY, 85])
        shown = os.path.splitext(display_name(f))[0] or "새 시험"
        with open(os.path.join(DETECT_DIR, token + ".json"), "w", encoding="utf-8") as fh:
            json.dump({"config": cfg, "summary": summary, "name": shown}, fh)
        return jsonify({"status": "success", "token": token, "summary": summary, "name": shown,
                        "preview_url": f"/api/layouts/preview/{token}.jpg"})
    except Exception as e:
        return jsonify({"status": "error", "message": f"인식 중 오류가 발생했습니다: {e}"}), 500
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


@app.route("/api/layouts/preview/<token>.jpg")
def layout_preview(token):
    if not TOKEN_RE.match(token):
        return jsonify({"status": "error", "message": "잘못된 요청입니다."}), 400
    return send_from_directory(DETECT_DIR, token + ".jpg")


@app.route("/api/layouts/register", methods=["POST"])
def register_layout():
    """인식한 양식을 등록하고, 그 양식을 쓰는 새 시험을 만든다. 과목은 정답 영역 블록 단위로 기본 구성(이후 시험 설정에서 수정)."""
    data = request.get_json(silent=True) or {}
    token = str(data.get("token", ""))
    path = os.path.join(DETECT_DIR, token + ".json")
    if not TOKEN_RE.match(token) or not os.path.isfile(path):
        return jsonify({"status": "error", "message": "인식 결과가 없거나 만료되었습니다. 다시 인식해 주세요."}), 404
    with open(path, encoding="utf-8") as fh:
        det = json.load(fh)
    cfg, sm = det["config"], det["summary"]
    name = str(data.get("name") or det["name"]).strip() or "새 시험"
    with lock:
        lid = "u" + token
        label = f"{name} 양식 - 자동 인식 ({sm['questions']}문항 {sm['choices']}지선다, 수험번호 {sm['id_digits']}자리" + (f", {'/'.join(sm['forms'])}형)" if sm["forms"] else ")")
        meta = layouts.register(lid, label, cfg)
        exam = {"name": name, "title": f"{name} 답안지", "layout": lid, "choices": sm["choices"], "id_digits": sm["id_digits"],
                "forms": sm["forms"] or ["A"], "form_rule": "none",
                "subjects": [{"name": f"{i + 1}영역" if len(sm["rows"]) > 1 else "전체", "short": f"{i + 1}영역" if len(sm["rows"]) > 1 else "전체", "count": r}
                             for i, r in enumerate(sm["rows"])]}
        try:
            exam = exam_mod.normalize(exam, meta)
        except ValueError as ex:
            return jsonify({"status": "error", "message": str(ex)}), 400
        eid = "e" + uuid.uuid4().hex[:8]
        store.set_setting("state:" + eid, new_state(exam))
        registry["list"].append({"id": eid, "name": name})
        save_registry()
    return jsonify({"status": "success", "id": eid, "layout": lid})


# ------------------------- 시험 설정 (여러 시험 관리) -------------------------
def exam_summary(eid):
    st = state if eid == registry["active"] else store.get_setting("state:" + eid)
    c = store.count(exam_id=eid)
    return {"id": eid, "name": st["exam"]["name"], "title": st["exam"]["title"], "counts": c,
            "subjects": len(st["exam"]["subjects"]), "questions": exam_mod.total_questions(st["exam"]),
            "forms": st["exam"]["forms"], "active": eid == registry["active"], "exam": st["exam"],
            "locked": sum(c.values()) > 0}


@app.route("/api/exams", methods=["GET"])
def list_exams():
    with lock:
        return jsonify({"active": registry["active"], "exams": [exam_summary(e["id"]) for e in registry["list"]],
                        "layouts": layouts.info(), "form_rules": exam_mod.FORM_RULES,
                        "current": state["exam"], "exam_info": exam_info(),
                        "locked": sum(store.count(exam_id=registry["active"]).values()) > 0})


def _layout_of(e):
    lay = layouts.LAYOUTS.get(e.get("layout"))
    if lay is None:
        raise ValueError("알 수 없는 답안지 양식입니다.")
    return lay


@app.route("/api/exams", methods=["POST"])
def create_exam():
    """새 시험 만들기. body: {exam: {...}} 또는 {copy_from: id, name}. 정답/채점 결과는 비어 있는 상태로 시작."""
    data = request.get_json(silent=True) or {}
    with lock:
        try:
            if data.get("copy_from"):
                src = state if data["copy_from"] == registry["active"] else store.get_setting("state:" + data["copy_from"])
                if src is None:
                    return jsonify({"status": "error", "message": "복제할 시험을 찾을 수 없습니다."}), 404
                e = {**src["exam"], "name": data.get("name") or src["exam"]["name"] + " (복사)"}
                e["title"] = data.get("title") or (src["exam"]["title"] if not data.get("name") else e["name"])
            else:
                e = data.get("exam") or exam_mod.default_exam()
            e = exam_mod.normalize(e, _layout_of(e))
        except ValueError as ex:
            return jsonify({"status": "error", "message": str(ex)}), 400
        eid = "e" + uuid.uuid4().hex[:8]
        st = new_state(e)
        if data.get("copy_from"):      # 점수/기준 설정은 복제
            src_st = state if data["copy_from"] == registry["active"] else store.get_setting("state:" + data["copy_from"])
            for k in ("score_per_q", "pass_average", "subject_fail_cutoff", "threshold"):
                st[k] = src_st[k]
        store.set_setting("state:" + eid, st)
        registry["list"].append({"id": eid, "name": e["name"]})
        save_registry()
    return jsonify({"status": "success", "id": eid})


@app.route("/api/exams/<eid>", methods=["PUT"])
def update_exam(eid):
    """시험 설정 수정. 이미 채점 결과가 있으면 구조(과목 문항 수/선택지/수험번호/유형/양식)는 바꿀 수 없다(이름/약칭/제목은 가능)."""
    data = (request.get_json(silent=True) or {}).get("exam") or {}
    with lock:
        if eid not in {e["id"] for e in registry["list"]}:
            return jsonify({"status": "error", "message": "시험을 찾을 수 없습니다."}), 404
        st = state if eid == registry["active"] else store.get_setting("state:" + eid)
        try:
            e = exam_mod.normalize({**data}, _layout_of({**st["exam"], **data}))
        except ValueError as ex:
            return jsonify({"status": "error", "message": str(ex)}), 400
        structural = exam_mod.structure_signature(e) != exam_mod.structure_signature(st["exam"])
        if structural and sum(store.count(exam_id=eid).values()) > 0:
            return jsonify({"status": "error", "message":
                            "이미 채점된 결과가 있어 과목 문항 수, 선택지, 수험번호, 문제 유형, 답안지 양식은 바꿀 수 없습니다. "
                            "결과를 삭제하거나 새 시험으로 만들어 주세요. (시험명, 과목 이름/약칭, 제목은 바꿀 수 있습니다.)"}), 409
        st["exam"] = e
        if structural:       # 구조가 바뀌면 정답은 새 구조의 임시값으로 초기화
            st["answer_keys"] = exam_mod.default_keys(e)
            st["keys_set"] = {f: False for f in e["forms"]}
        for x in registry["list"]:
            if x["id"] == eid:
                x["name"] = e["name"]
        store.set_setting("state:" + eid, st)
        save_registry()
        if eid == registry["active"]:
            save_state()
    return jsonify({"status": "success", "structure_changed": structural})


@app.route("/api/exams/<eid>/activate", methods=["POST"])
def activate_exam(eid):
    with lock:
        if eid not in {e["id"] for e in registry["list"]}:
            return jsonify({"status": "error", "message": "시험을 찾을 수 없습니다."}), 404
        save_state()
        activate(eid)
    return jsonify({"status": "success"})


@app.route("/api/exams/<eid>", methods=["DELETE"])
def delete_exam(eid):
    with lock:
        ids = [e["id"] for e in registry["list"]]
        if eid not in ids:
            return jsonify({"status": "error", "message": "시험을 찾을 수 없습니다."}), 404
        if eid == registry["active"]:
            return jsonify({"status": "error", "message": "사용 중인 시험은 삭제할 수 없습니다. 다른 시험으로 전환한 뒤 삭제하세요."}), 409
        remove_result_files(store.load_results(eid))
        store.delete_results(exam_id=eid)
        store.delete_setting("state:" + eid)
        registry["list"] = [e for e in registry["list"] if e["id"] != eid]
        save_registry()
    return jsonify({"status": "success"})


if __name__ == "__main__":
    # debug=True 는 외부 공개 서버에서 원격 코드 실행 위험이 있어 사용하지 않는다.
    from waitress import serve
    if not auth.is_configured(DATA_DIR):
        print("※ 접속 비밀번호 미설정: 서버 PC 브라우저에서 http://127.0.0.1:5000 으로 접속해 설정하세요.")
    # 기본은 내부(127.0.0.1:5000)에서만 대기하고, 외부 접속은 Caddy(HTTPS)가 받아 전달한다.
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    print(f"OMR 채점 서버 시작: http://{host}:{port}")
    serve(app, host=host, port=port, threads=4)
