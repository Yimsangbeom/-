import copy
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
import paper_match
import stats
from omr_engine import OMREngine, load_pages_from_file

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("OMR_DATA_DIR", os.path.join(BASE_DIR, "data"))
TEMP_DIR = os.path.join(DATA_DIR, "temp")
RESULT_DIR = os.path.join(DATA_DIR, "results")
SOURCE_DIR = os.path.join(DATA_DIR, "sources")      # 다시 채점용 원본 페이지 이미지 (화면으로는 제공하지 않음)
for d in (DATA_DIR, TEMP_DIR, RESULT_DIR, SOURCE_DIR):
    os.makedirs(d, exist_ok=True)
layouts.load_user_layouts(DATA_DIR)   # 자동 인식으로 등록한 답안지 양식

# 소스코드(app.py 등)가 외부에 노출되지 않도록 static 폴더만 공개한다.
app = Flask(__name__, static_folder=os.path.join(BASE_DIR, "static"), static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024 * 1024  # 요청당 최대 1GB
auth.init_auth(app, DATA_DIR, app.static_folder)

lock = threading.RLock()        # 설정/결과(메모리·DB) 접근용: 짧게만 잡는다
grade_lock = threading.Lock()   # 이미지 판독(CPU 작업)용: 판독은 한 번에 하나씩, 그동안 다른 요청(결과/통계/상태 확인)은 막히지 않는다
_engines = {}


def get_engine(layout_id):
    if layout_id not in _engines:
        _engines[layout_id] = OMREngine(layouts.LAYOUTS[layout_id]["config"])
    return _engines[layout_id]


def snapshot():
    """판독에 필요한 설정을 한 번에 복사해 둔다(판독하는 동안 잠금을 쥐지 않기 위해)."""
    with lock:
        return copy.deepcopy(state["exam"]), {k: list(v) for k, v in state["answer_keys"].items()}, float(state["threshold"])


def grade_image(img_bgr, ex, keys, threshold, infer=True):
    """이미지 한 장 판독. 전체 잠금(lock) 밖에서 실행하고, 판독끼리만 grade_lock 으로 직렬화한다.
    infer=True: 등록된 정답이 2개 이상이면 수험생이 표시한 유형이 맞는지 정답과 대조해 확인한다."""
    forms = None
    if infer:
        with lock:
            forms = [f for f in ex["forms"] if state["keys_set"].get(f)]
    with grade_lock:
        eng = get_engine(ex["layout"])
        eng.fill_threshold = threshold
        return eng.grade_sheet(img_bgr, keys, 1.0, exam=ex, infer_forms=forms)


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
        "key_source": {},             # 정답을 어떻게 등록했는지: sheet(정답 답안지)/excel/derived(다른 유형에서 자동 생성)
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
    st.setdefault("key_source", {})
    defaults = exam_mod.default_keys(ex)
    for f in ex["forms"]:
        st["answer_keys"].setdefault(f, defaults[f])
        # 이전 버전에서 이미 등록해 둔 정답은 '등록됨'으로 간주
        st["keys_set"][f] = bool(st["keys_set"].get(f)) or st["answer_keys"][f] != defaults[f]
    st["schema"] = 4
    return st


def remove_result_files(items):
    for r in items:
        for k in ("vis_url", "name_url"):
            if r.get(k):
                p = os.path.join(RESULT_DIR, os.path.basename(r[k]))
                if os.path.isfile(p):
                    os.remove(p)
        if r.get("src"):
            p = os.path.join(SOURCE_DIR, os.path.basename(r["src"]))
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
    if exam_mod.is_paired(ex) and all(state["keys_set"][f] for f in ex["forms"]):
        a, b = ex["forms"]
        return exam_mod.key_conflicts(ex, state["answer_keys"][a], state["answer_keys"][b])
    return []


def register_key(form, key, source="sheet"):
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
    state.setdefault("key_source", {})[form] = source
    derived = False
    if exam_mod.is_paired(ex):
        other = [f for f in ex["forms"] if f != form][0]
        if not state["keys_set"][other]:
            state["answer_keys"][other] = exam_mod.derive_key(ex, state["answer_keys"][form], form)
            state["keys_set"][other] = True
            state["key_source"][other] = "derived"
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


def make_result(res, page_label, is_test=False, verify=None, keys=None, src_img=None, rid=None, keep=None):
    """채점 결과를 저장용 dict 로 만들고 판독 이미지를 저장한다. (lock 안에서 호출)
    src_img: 원본 페이지 이미지(다시 채점용으로 보관). rid/keep: 다시 채점할 때 기존 번호와 이름 등을 유지."""
    global next_id
    if rid is None:
        rid = next_id
        next_id += 1
    vis_name = f"vis_{rid}.jpg"
    cv2.imwrite(os.path.join(RESULT_DIR, vis_name), res["vis_img"])
    name_url = ""
    if res.get("name_img") is not None and res["name_img"].size:
        cv2.imwrite(os.path.join(RESULT_DIR, f"name_{rid}.jpg"), res["name_img"])
        name_url = f"/results/name_{rid}.jpg"
    item = {
        "id": rid,
        "filename": page_label,
        "name_url": name_url,
        "name": "",
        "exam_type": res["exam_type"],
        "marked_type": res.get("marked_type"),
        "type_status": res.get("type_status", "정상"),
        "type_note": res.get("type_note", ""),
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
    if src_img is not None:
        cv2.imwrite(os.path.join(SOURCE_DIR, f"src_{rid}.jpg"), src_img, [cv2.IMWRITE_JPEG_QUALITY, 92])
        item["src"] = f"src_{rid}.jpg"
    if keep:
        item["name"] = keep.get("name", "")
        for k in ("src", "verify"):
            if keep.get(k) and k not in item:
                item[k] = keep[k]
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
        if data.get("reset"):
            # 시험지 새로 설정 저장: 기존 정답 등록을 모두 지우고(미등록 상태) 아래 값으로 새로 설정한다. 이미 채점된 결과는 그대로 둔다.
            state["answer_keys"] = exam_mod.default_keys(state["exam"])
            state["keys_set"] = {f: False for f in state["exam"]["forms"]}
            state["key_source"] = {}
        try:
            if "answer_keys" in data:
                keys = data["answer_keys"]
                forms = [f for f in exam_forms() if f in keys]
                for t in forms:
                    register_key(t, keys[t], "excel")
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
        ex, keys, thr = snapshot()
        res = grade_image(img_bgr, ex, keys, thr, infer=False)
        slot = request.form.get("form", "").strip()          # 화면에서 'A형 칸/B형 칸' 중 어디에 올렸는지
        if slot and slot not in ex["forms"]:
            return jsonify({"status": "error", "message": f"이 시험에 없는 문제 유형입니다: {slot}"}), 400
        if slot and res["type_status"] == "정상" and res["exam_type"] != slot:
            return jsonify({"status": "error", "message": f"{slot}형 칸에 올렸지만 이 답안지는 문제 유형이 {res['exam_type']}형으로 표시되어 있습니다. "
                                                          f"{res['exam_type']}형 칸에 올리거나, 답안지의 문제 유형 표시를 확인하세요."}), 422
        with lock:
            t_code = slot or res["exam_type"]
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
                info = register_key(t_code, extracted, "sheet")
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


jobs = {}          # 채점 작업 진행 상황 (job_id -> dict)
jobs_lock = threading.Lock()


def _grade_files(saved, ex, keys, thr, job=None):
    """saved: [(임시경로, 표시용 파일명)]. 판독은 전체 잠금 밖에서, 결과 저장만 짧게 잠금. job 이 있으면 진행 상황을 갱신."""
    new_results, errors = [], []
    for temp_path, shown in saved:
        try:
            pages = load_pages_from_file(temp_path)
            if job is not None:
                with jobs_lock:
                    job["current"] = shown
                    job["total"] += max(0, len(pages) - 1) if pages else 0      # 파일 1개로 잡아둔 칸을 페이지 수로 확장
            if not pages:
                errors.append(f"{shown}: 이미지를 읽을 수 없습니다.")
                if job is not None:
                    with jobs_lock:
                        job["done"] += 1
            for label, img_bgr in pages:
                res = grade_image(img_bgr, ex, keys, thr)
                with lock:
                    item = make_result(res, label.replace(os.path.basename(temp_path), shown), keys=keys, src_img=img_bgr)
                    apply_roster(item)
                    graded_results.append(item)
                    store.add_results([item])
                    new_results.append(item)
                if job is not None:
                    with jobs_lock:
                        job["done"] += 1
        except Exception as e:
            errors.append(f"{shown}: {e}")
            if job is not None:
                with jobs_lock:
                    job["done"] += 1
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
    return new_results, errors


def _run_job(job, saved, ex, keys, thr):
    try:
        new_results, errors = _grade_files(saved, ex, keys, thr, job)
        with jobs_lock:
            job["errors"] = errors
            job["n_results"] = len(new_results)
            job["status"] = "done"
    except Exception as e:
        with jobs_lock:
            job["errors"] = [str(e)]
            job["status"] = "done"


@app.route("/api/grade/students", methods=["POST"])
def grade_students():
    files = request.files.getlist("files")
    if not files:
        return jsonify({"status": "error", "message": "파일이 없습니다."}), 400
    ex, keys, thr = snapshot()
    saved = [(save_upload(f), display_name(f)) for f in files]
    if request.form.get("async") == "1":
        # 업로드 직후 바로 돌려주고 채점은 뒤에서 진행 -> 화면이 /api/grade/progress/<id> 로 진행 상황을 확인
        job_id = uuid.uuid4().hex[:12]
        job = {"status": "running", "total": len(saved), "done": 0, "current": "", "errors": [], "n_results": 0, "t": time.time()}
        with jobs_lock:
            for k in [k for k, v in jobs.items() if time.time() - v["t"] > 3600]:
                jobs.pop(k, None)
            jobs[job_id] = job
        threading.Thread(target=_run_job, args=(job, saved, ex, keys, thr), daemon=True).start()
        return jsonify({"status": "started", "job": job_id, "total": len(saved)})
    new_results, errors = _grade_files(saved, ex, keys, thr)
    return jsonify({"status": "success", "results": [with_judge(r) for r in new_results], "errors": errors})


@app.route("/api/grade/progress/<job_id>")
def grade_progress(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            return jsonify({"status": "error", "message": "작업을 찾을 수 없습니다."}), 404
        return jsonify({k: job[k] for k in ("status", "total", "done", "current", "errors", "n_results")})


def _run_regrade(job, targets, ex, keys, thr):
    errors, n = [], 0
    for old in targets:
        try:
            with jobs_lock:
                job["current"] = old.get("filename", "")
            if old.get("corrected"):
                # 사람이 교정한 답은 그대로 두고, 현재 정답/설정으로 점수만 다시 계산한다(이미지를 다시 읽지 않음)
                with lock:
                    cur = next((r for r in graded_results if r["id"] == old["id"]), None)
                    if cur is not None:
                        rescore_item(cur, cur["student_answers"], cur["exam_type"], cur["student_id"])
                        apply_roster(cur)
                        store.update_result(cur)
                        n += 1
                continue
            img = cv2.imread(os.path.join(SOURCE_DIR, os.path.basename(old["src"])))
            if img is None:
                raise ValueError("원본 이미지를 읽을 수 없습니다.")
            res = grade_image(img, ex, keys, thr)
            with lock:
                if not any(r["id"] == old["id"] for r in graded_results):
                    continue                                   # 그 사이에 삭제된 건
                new = make_result(res, old["filename"], is_test=old.get("is_test", False), keys=keys, rid=old["id"], keep=old)
                new["exam_id"] = old.get("exam_id", new["exam_id"])
                apply_roster(new)
                graded_results[:] = [new if r["id"] == old["id"] else r for r in graded_results]
                store.update_result(new)
            n += 1
        except Exception as e:
            errors.append(f"{old.get('filename', old['id'])}: {e}")
        finally:
            with jobs_lock:
                job["done"] += 1
    with jobs_lock:
        job["errors"], job["n_results"], job["status"] = errors, n, "done"


@app.route("/api/regrade", methods=["POST"])
def regrade():
    """저장해 둔 원본 이미지로 다시 채점한다(현재 정답/설정/판독 방식 적용). 번호, 파일명, 입력한 이름은 유지.
    body: {"ids": [...]} 이면 그 건만, 없으면 현재 시험의 실제 응시자 전체."""
    ids = (request.get_json(silent=True) or {}).get("ids")
    ex, keys, thr = snapshot()
    with lock:
        pool = [r for r in graded_results if not r.get("is_test")] if ids is None else [r for r in graded_results if r["id"] in {int(i) for i in ids}]
        n_corrected = sum(1 for r in pool if r.get("corrected"))       # 교정한 건은 이미지를 다시 읽지 않고 점수만 다시 계산
        targets = [r for r in pool if r.get("corrected") or (r.get("src") and os.path.isfile(os.path.join(SOURCE_DIR, os.path.basename(r["src"]))))]
        skipped = len(pool) - len(targets)
    if not targets:
        return jsonify({"status": "error", "message": "다시 채점할 수 있는 답안지가 없습니다. (원본이 보관되지 않은 이전 결과는 파일을 다시 올려야 하고, 교정한 답안지는 제외됩니다.)", "skipped": skipped, "corrected": n_corrected}), 400
    job_id = uuid.uuid4().hex[:12]
    job = {"status": "running", "total": len(targets), "done": 0, "current": "", "errors": [], "n_results": 0, "t": time.time()}
    with jobs_lock:
        jobs[job_id] = job
    threading.Thread(target=_run_regrade, args=(job, targets, ex, keys, thr), daemon=True).start()
    return jsonify({"status": "started", "job": job_id, "total": len(targets), "skipped": skipped, "corrected": n_corrected})


def rescore_item(item, answers, form, student_id):
    """교정된 답안(문항별 선택)으로 점수를 다시 계산해 item 에 반영한다. (lock 안에서 호출)"""
    ex = state["exam"]
    n_q = exam_mod.total_questions(ex)
    codes = exam_mod.codes(ex)
    key = [int(x) for x in state["answer_keys"][form]][:n_q]
    sc = {c: 0 for c in codes}
    correct = multi = blank = 0
    out = {}
    for q in range(1, n_q + 1):
        a = str(answers.get(str(q), "무표기"))
        out[str(q)] = a
        if a == "이중":
            multi += 1
        elif a == "무표기":
            blank += 1
        elif a == str(key[q - 1]):
            sc[codes[exam_mod.subject_index(ex, q)]] += 1
            correct += 1
    item.update({"student_answers": out, "subject_correct": sc, "correct_count": correct, "multi_count": multi,
                 "blank_count": blank, "exam_type": form, "student_id": student_id, "key_used": key})


@app.route("/api/results/<int:rid>/correct", methods=["POST"])
def correct_result(rid):
    """답안지 수정(교정): 유형/수험번호/이름/문항별 답을 사람이 고치면 그 값으로 다시 채점한다(결과·통계에 반영).
    원래 판독값은 orig 로 보관해 되돌릴 수 있다. body: {answers:{"1":"3",...}, exam_type, student_id, name}"""
    data = request.get_json(silent=True) or {}
    ex = state["exam"]
    n_q, n_ch = exam_mod.total_questions(ex), ex["choices"]
    form = str(data.get("exam_type", ""))
    sid = str(data.get("student_id", "")).strip()
    ans = data.get("answers") or {}
    if form not in ex["forms"]:
        return jsonify({"status": "error", "message": "문제 유형이 올바르지 않습니다."}), 400
    if not sid or len(sid) > 20 or not all(ch.isdigit() or ch == "?" for ch in sid):
        return jsonify({"status": "error", "message": "수험번호는 숫자(모르는 자리는 ?)로 입력하세요."}), 400
    valid = {str(c) for c in range(1, n_ch + 1)} | {"무표기", "이중"}
    for q in range(1, n_q + 1):
        if str(ans.get(str(q), "무표기")) not in valid:
            return jsonify({"status": "error", "message": f"{q}번 문항의 답이 올바르지 않습니다."}), 400
    with lock:
        for r in graded_results:
            if r["id"] != rid:
                continue
            if "orig" not in r:       # 처음 교정할 때 원래 판독값 보관
                r["orig"] = {k: copy.deepcopy(r.get(k)) for k in ("student_answers", "subject_correct", "correct_count", "multi_count", "blank_count",
                                                                   "exam_type", "student_id", "key_used", "needs_review", "type_status", "type_note", "name")}
            rescore_item(r, ans, form, sid)
            r["name"] = str(data.get("name", r.get("name", ""))).strip()[:40]
            r.update({"corrected": True, "needs_review": False, "type_status": "정상", "type_note": ""})
            for k in ("id_read", "id_candidates", "roster_flags", "name_src"):       # 사람이 확인한 값이므로 자동 처리 표시는 지운다
                r.pop(k, None)
            if not r["name"]:
                apply_roster(r)
            store.update_result(r)
            return jsonify({"status": "success", "result": with_judge(r)})
    return jsonify({"status": "error", "message": "결과를 찾을 수 없습니다."}), 404


@app.route("/api/results/<int:rid>/uncorrect", methods=["POST"])
def uncorrect_result(rid):
    """교정을 취소하고 원래 판독값으로 되돌린다."""
    with lock:
        for r in graded_results:
            if r["id"] == rid:
                if not r.get("corrected") or "orig" not in r:
                    return jsonify({"status": "error", "message": "교정한 기록이 없습니다."}), 400
                r.update(r.pop("orig"))
                r["corrected"] = False
                store.update_result(r)
                return jsonify({"status": "success", "result": with_judge(r)})
    return jsonify({"status": "error", "message": "결과를 찾을 수 없습니다."}), 404


@app.route("/api/results/<int:rid>/name", methods=["POST"])
def set_result_name(rid):
    """응시자 이름 입력/수정 (손글씨 이름은 자동 인식이 안 되므로 사진을 보고 입력)."""
    name = str((request.get_json(silent=True) or {}).get("name", "")).strip()[:40]
    with lock:
        for r in graded_results:
            if r["id"] == rid:
                r["name"] = name
                r.pop("name_src", None)
                if not name:
                    apply_roster(r)
                store.update_result(r)
                return jsonify({"status": "success", "name": name})
    return jsonify({"status": "error", "message": "결과를 찾을 수 없습니다."}), 404


def by_student_id(items):
    """수험번호 순 정렬 (판독 못 한 자리가 '?'인 건은 맨 뒤, 같은 번호는 채점 순서)."""
    return sorted(items, key=lambda x: ("?" in str(x.get("student_id", "")), str(x.get("student_id", "")), x["id"]))


# ------------------------- 응시자 명단 (수험번호 -> 이름) -------------------------
_roster_cache = {}


def roster_of():
    eid = registry["active"]
    if eid not in _roster_cache:
        _roster_cache[eid] = store.get_setting("roster:" + eid) or {}
    return _roster_cache[eid]


def apply_roster(item, ro=None):
    """명단으로 이름을 채우고, 못 읽은 수험번호(?)를 복구하고, 명단에 없는 번호를 표시한다. (lock 안에서 호출)
    사람이 직접 입력한 이름/수험번호는 덮어쓰지 않는다."""
    ro = roster_of() if ro is None else ro
    ents = ro.get("entries") or {}
    if item.get("id_read"):                                   # 이전에 자동 복구한 번호는 되돌린 뒤 다시 계산
        item["student_id"] = item.pop("id_read")
    item.pop("id_candidates", None)
    item.pop("roster_flags", None)
    if item.get("name_src") == "명단":
        item["name"] = ""
        item.pop("name_src", None)
    if not ents:
        return
    sid, flags, corrected = item["student_id"], [], bool(item.get("corrected"))
    if "?" in sid and not corrected:
        pat = re.compile(sid.replace("?", "."))
        cands = [k for k in ents if len(k) == len(sid) and pat.fullmatch(k)]
        if len(cands) == 1:
            item["id_read"], item["student_id"], sid = sid, cands[0], cands[0]
            flags.append("수험번호 자동 복구(확인 필요)")
        elif 1 < len(cands) <= 8:
            item["id_candidates"] = cands
            flags.append(f"수험번호 후보 {len(cands)}명")
        else:
            flags.append("수험번호를 명단에서 찾지 못함")
    if "?" not in sid:
        if sid in ents:
            if not item.get("name"):
                item["name"], item["name_src"] = ents[sid], "명단"
        elif not corrected:
            flags.append("명단에 없는 수험번호")
    if flags and not corrected:
        item["roster_flags"] = flags


def parse_roster(file_storage):
    """엑셀(.xlsx)/CSV 에서 수험번호와 이름 열을 찾아 {수험번호: 이름} 으로 만든다."""
    name = (file_storage.filename or "").lower()
    raw = file_storage.read()
    if name.endswith(".csv"):
        for enc in ("utf-8-sig", "cp949"):
            try:
                df = pd.read_csv(io.BytesIO(raw), header=None, dtype=str, encoding=enc, keep_default_na=False)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise ValueError("CSV 문자 인코딩을 읽을 수 없습니다. 엑셀(.xlsx)로 저장해서 올려 주세요.")
    else:
        df = pd.read_excel(io.BytesIO(raw), header=None, dtype=str).fillna("")
    sid_keys, name_keys = ("수험번호", "응시번호", "수험 번호"), ("이름", "성명", "성 명", "응시자명", "응시자")
    hdr = sid_col = name_col = None
    for i in range(min(8, len(df))):
        cells = [str(v).replace(" ", "") for v in df.iloc[i].tolist()]
        sc = next((j for j, c in enumerate(cells) if any(k.replace(" ", "") in c for k in sid_keys)), None)
        nc = next((j for j, c in enumerate(cells) if j != sc and any(c == k.replace(" ", "") or k.replace(" ", "") in c for k in name_keys)), None)
        if sc is not None and nc is not None:
            hdr, sid_col, name_col = i, sc, nc
            break
    if hdr is None:
        raise ValueError("첫 줄에서 '수험번호'와 '이름'(또는 '성명') 열 제목을 찾지 못했습니다.")
    entries, dups, skipped = {}, [], 0
    for i in range(hdr + 1, len(df)):
        sid = re.sub(r"\D", "", re.sub(r"\.0+$", "", str(df.iat[i, sid_col]).strip()))
        nm = str(df.iat[i, name_col]).strip()
        if not sid and not nm:
            continue
        if not (4 <= len(sid) <= 20) or not nm:
            skipped += 1
            continue
        if sid in entries and entries[sid] != nm:
            dups.append(sid)
        entries[sid] = nm
    if not entries:
        raise ValueError("명단에서 읽을 수 있는 행이 없습니다. 수험번호(숫자)와 이름이 있는지 확인하세요.")
    return entries, dups, skipped


def roster_summary():
    ro = roster_of()
    ents = ro.get("entries") or {}
    if not ents:
        return {"registered": False}
    real = [r for r in graded_results if not r.get("is_test")]
    seen = {r["student_id"] for r in real}
    absent = [{"id": k, "name": v} for k, v in sorted(ents.items()) if k not in seen]
    unknown = sum(1 for r in real if "?" not in r["student_id"] and r["student_id"] not in ents)
    return {"registered": True, "filename": ro.get("filename", ""), "count": len(ents), "updated": ro.get("updated", ""),
            "graded": len(real), "absent": absent, "unknown": unknown, "duplicates": ro.get("duplicates", []), "skipped": ro.get("skipped", 0)}


@app.route("/api/roster", methods=["GET", "POST", "DELETE"])
def handle_roster():
    global graded_results
    with lock:
        if request.method == "GET":
            return jsonify(roster_summary())
        eid = registry["active"]
        if request.method == "DELETE":
            store.delete_setting("roster:" + eid)
            _roster_cache.pop(eid, None)
            for r in graded_results:
                apply_roster(r, {})
                store.update_result(r)
            return jsonify({"status": "success", **roster_summary()})
        if "file" not in request.files:
            return jsonify({"status": "error", "message": "파일이 없습니다."}), 400
        try:
            entries, dups, skipped = parse_roster(request.files["file"])
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 400
        ro = {"entries": entries, "filename": display_name(request.files["file"]), "updated": time.strftime("%Y-%m-%d %H:%M"),
              "duplicates": dups[:50], "skipped": skipped}
        store.set_setting("roster:" + eid, ro)
        _roster_cache[eid] = ro
        for r in graded_results:                      # 이미 채점된 결과에도 바로 적용
            apply_roster(r, ro)
            store.update_result(r)
        return jsonify({"status": "success", **roster_summary()})


def with_judge(item):
    sc = scaled(item)
    status, fails = judge(sc)
    sc = {k: v for k, v in sc.items() if k != "orig"}       # 원래 판독값 백업은 화면/통계로 내보내지 않는다
    if sc.get("roster_flags") and not sc.get("corrected"):
        sc["needs_review"] = True
    return {**sc, "exam_title": state["exam"].get("title") or state["exam"]["name"], "average": round(sc["total_score"] / exam_mod.n_subjects(state["exam"]), 1), "result": status, "fail_subjects": fails}


@app.route("/api/results", methods=["GET", "DELETE"])
def handle_results():
    global graded_results, next_id
    with lock:
        if request.method == "GET":
            return jsonify([with_judge(r) for r in by_student_id(graded_results)])
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
        remove_result_files(gone)
        graded_results = [r for r in graded_results if r["id"] not in ids]
        store.delete_results(ids=[r["id"] for r in gone])
    return jsonify({"status": "success", "deleted": len(gone)})


@app.route("/healthz")
def healthz():
    """상태 확인용(로그인 불필요, 내부 정보 없음): 서버와 DB 가 정상이면 200. 감시 작업(healthcheck.ps1)이 사용한다."""
    try:
        store.ping()
    except Exception:
        return jsonify({"status": "error"}), 503
    return jsonify({"status": "ok"})


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
    ex, _, thr = snapshot()
    graded = []
    for fn in meta["run_files"]:
        for label, img in load_pages_from_file(os.path.join(SAMPLE_DIR, fn)):
            graded.append((label, grade_image(img, ex, meta["keys"], thr, infer=False)))      # 판독은 전체 잠금 밖에서
    with lock:
        for label, res in graded:
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


@app.route("/api/export/excel", methods=["GET", "POST"])
def export_excel():
    """엑셀 다운로드. part=stats 이면 통계(차트 포함)만, 아니면 결과 + 문항별 답안 + 통계(차트 포함).
    POST 로 화면의 그래프 그림(images=[{title, png(base64)}])을 함께 보내면 '통계(화면)' 시트에 화면과 같은 그래프를 넣는다."""
    body = (request.get_json(silent=True) or {}) if request.method == "POST" else {}
    scope = body.get("scope") or request.args.get("scope", "all")
    part = body.get("part") or request.args.get("part", "all")
    images = []
    for im in (body.get("images") or [])[:24]:
        png = str(im.get("png", ""))
        if png.startswith("data:image/png;base64,") and len(png) < 4_000_000:
            images.append({"title": str(im.get("title", ""))[:80], "png": png})
    with lock:
        base = scoped(scope)
        if not base:
            return jsonify({"status": "error", "message": "채점 결과가 없습니다."}), 400
        judged = [with_judge(r) for r in by_student_id(base)]
        # 통계 대상 (범위가 all 이면 실제 응시자 우선, 없으면 테스트)
        st_scope = scope if scope in ("real", "test") else ("real" if scoped("real") else "test")
        st = stats.compute(stats_items(st_scope), state["score_per_q"], state["exam"])
        out = excel_export.build(state["exam"], state["score_per_q"], judged, st, st_scope,
                                 state["pass_average"], state["subject_fail_cutoff"], part, absent=roster_summary().get("absent"), images=images)
    name = state["exam"]["name"].replace("/", "_").replace("\\", "_")
    return send_file(
        out,
        download_name=f"{name}_{'통계' if part == 'stats' else '채점결과'}.xlsx",
        as_attachment=True,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ------------------------- A형/B형 문제 대응표 (문제지 분석) -------------------------
def refresh_derived_keys():
    """대응 규칙이 바뀌면, 다른 유형에서 자동 생성했던 정답을 새 규칙으로 다시 만든다. (lock 안에서 호출)"""
    ex = state["exam"]
    if not exam_mod.is_paired(ex):
        return
    for f in ex["forms"]:
        other = [x for x in ex["forms"] if x != f][0]
        if state.get("key_source", {}).get(f) == "derived" and state["keys_set"].get(other) and state.get("key_source", {}).get(other) != "derived":
            state["answer_keys"][f] = exam_mod.derive_key(ex, state["answer_keys"][other], other)


def formmap_payload(pairs=None):
    ex = state["exam"]
    return {"forms": ex["forms"], "n": exam_mod.total_questions(ex), "choices": ex["choices"], "rule": ex["form_rule"],
            "pairs": pairs if pairs is not None else [{"a": a, "b": b, "perm": perm} for a, b, perm in (ex.get("form_map") or [])]}


@app.route("/api/formmap", methods=["GET"])
def get_formmap():
    with lock:
        return jsonify({"status": "success", **formmap_payload()})


def _paper_key(form):
    return f"paper:{registry['active']}:{form}"


def _groups(ex):
    """문항 위치(0부터)별 과목 번호."""
    return [exam_mod.subject_index(ex, q) for q in range(1, exam_mod.total_questions(ex) + 1)]


@app.route("/api/paper", methods=["GET"])
def get_papers():
    """등록된 문제지 현황 (유형별)."""
    with lock:
        out = {}
        for f in state["exam"]["forms"]:
            p = store.get_setting(_paper_key(f))
            out[f] = {"filename": p["filename"], "count": len(p["questions"]), "updated": p.get("updated", "")} if p else None
        return jsonify({"status": "success", "papers": out, "forms": state["exam"]["forms"]})


@app.route("/api/paper/<form>", methods=["POST"])
def upload_paper(form):
    """유형(A/B)별 시험지(문제지) 등록: 글자를 읽어 문제로 나눠 저장한다."""
    f = request.files.get("file")
    with lock:
        ex = copy.deepcopy(state["exam"])
    if form not in ex["forms"]:
        return jsonify({"status": "error", "message": f"이 시험에 없는 문제 유형입니다: {form}"}), 400
    if not f:
        return jsonify({"status": "error", "message": "파일이 없습니다."}), 400
    try:
        qs = paper_match.parse_paper(f.read(), f.filename, exam_mod.total_questions(ex))
    except Exception as e:
        return jsonify({"status": "error", "message": f"{form}형 문제지: {e}"}), 422
    with lock:
        store.set_setting(_paper_key(form), {"filename": display_name(f), "questions": qs, "updated": time.strftime("%Y-%m-%d %H:%M")})
    return jsonify({"status": "success", "form": form, "count": len(qs), "filename": display_name(f)})


@app.route("/api/paper/<form>", methods=["DELETE"])
def delete_paper(form):
    with lock:
        store.delete_setting(_paper_key(form))
    return jsonify({"status": "success"})


@app.route("/api/formmap/analyze", methods=["POST"])
def analyze_formmap():
    """등록된 A형/B형 문제지로 같은 과목 안에서 대응 문제를 찾는다(저장하지 않음 - 화면에서 확인/수정 후 저장)."""
    with lock:
        ex = copy.deepcopy(state["exam"])
        pa = store.get_setting(_paper_key(ex["forms"][0])) if ex["forms"] else None
        pb = store.get_setting(_paper_key(ex["forms"][1])) if len(ex["forms"]) > 1 else None
    if len(ex["forms"]) != 2:
        return jsonify({"status": "error", "message": "문제 유형이 2개(A형/B형)인 시험에서만 쓸 수 있습니다."}), 400
    if not pa or not pb:
        return jsonify({"status": "error", "message": "A형과 B형 시험지를 모두 등록한 뒤 분석하세요."}), 400
    try:
        r = paper_match.analyze_questions(pa["questions"], pb["questions"], exam_mod.total_questions(ex), ex["choices"], _groups(ex))
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 422
    return jsonify({"status": "success", **r, **formmap_payload(r["pairs"]), "groups": _groups(ex)})


@app.route("/api/formmap", methods=["POST"])
def save_formmap():
    """확인한 대응표를 저장한다. body: {pairs:[{a, b, perm}]}"""
    data = request.get_json(silent=True) or {}
    with lock:
        try:
            e = copy.deepcopy(state["exam"])
            e["form_rule"] = "custom"
            e["form_map"] = [[int(p["a"]), int(p["b"]), p.get("perm")] for p in (data.get("pairs") or [])]
            e = exam_mod.normalize(e, _layout_of(e))
        except (ValueError, KeyError, TypeError) as ex_:
            return jsonify({"status": "error", "message": str(ex_)}), 400
        state["exam"] = e
        refresh_derived_keys()
        save_state()
        return jsonify({"status": "success", "key_conflicts": current_conflicts(), **formmap_payload()})


@app.route("/api/formmap", methods=["DELETE"])
def delete_formmap():
    with lock:
        e = copy.deepcopy(state["exam"])
        e["form_rule"] = "none"
        e.pop("form_map", None)
        state["exam"] = e
        save_state()
        return jsonify({"status": "success", **formmap_payload()})


@app.route("/api/formmap/template", methods=["GET"])
def formmap_template():
    """대응표 엑셀(현재 대응표, 없으면 같은 번호). 열: A형 번호, B형 번호, 보기 순서(A형 1~n번 보기가 B형 몇 번인지, 쉼표)."""
    with lock:
        ex = state["exam"]
        pairs = ex.get("form_map") or [[q, q, list(range(1, ex["choices"] + 1))] for q in range(1, exam_mod.total_questions(ex) + 1)]
        a, b = ex["forms"][0], ex["forms"][1] if len(ex["forms"]) > 1 else "B"
        df = pd.DataFrame([{f"{a}형 번호": x[0], f"{b}형 번호": x[1], "보기 순서(선택)": ",".join(map(str, x[2]))} for x in pairs])
    out = io.BytesIO()
    df.to_excel(out, index=False, sheet_name="문제대응표")
    out.seek(0)
    return send_file(out, download_name="문제대응표.xlsx", as_attachment=True,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route("/api/formmap/excel", methods=["POST"])
def formmap_from_excel():
    """대응표 엑셀을 읽어 화면에서 확인/저장할 수 있게 돌려준다(저장하지 않음)."""
    f = request.files.get("file")
    if not f:
        return jsonify({"status": "error", "message": "파일이 없습니다."}), 400
    with lock:
        n_ch = state["exam"]["choices"]
    try:
        df = pd.read_excel(f, dtype=str).fillna("")
        cols = list(df.columns)
        pairs = []
        for _, r in df.iterrows():
            a, b = int(float(r[cols[0]])), int(float(r[cols[1]]))
            perm = [int(float(x)) for x in str(r[cols[2]]).replace(" ", "").split(",") if x] if len(cols) > 2 and str(r[cols[2]]).strip() else list(range(1, n_ch + 1))
            pairs.append({"a": a, "b": b, "perm": perm, "score": None})
    except Exception:
        return jsonify({"status": "error", "message": "대응표 엑셀을 읽지 못했습니다. 첫 두 열이 'A형 번호', 'B형 번호'(숫자)인지 확인하세요."}), 422
    return jsonify({"status": "success", "pairs": pairs, "warnings": [], **{k: v for k, v in formmap_payload(pairs).items() if k != "pairs"}})


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
        data = {**data}
        if data.get("form_rule") == "custom" and not data.get("form_map"):
            data["form_map"] = st["exam"].get("form_map")          # 편집 창은 대응표를 갖고 있지 않으므로 기존 것을 유지
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
            st["key_source"] = {}
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
        store.delete_setting("roster:" + eid)
        _roster_cache.pop(eid, None)
        for f_ in ("A", "B", "C"):
            store.delete_setting(f"paper:{eid}:{f_}")
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
    threads = int(os.environ.get("OMR_THREADS", "12"))   # 동시 요청 처리 수(판독 자체는 한 번에 하나씩이라 여유 있게)
    serve(app, host=host, port=port, threads=threads)
