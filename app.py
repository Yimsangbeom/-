import io
import json
import os
import threading
import uuid

import cv2
import pandas as pd
from flask import Flask, request, jsonify, send_file, send_from_directory

import auth
import db
import stats
from form_map import derive_key, key_conflicts
from omr_engine import OMREngine, DEFAULT_CONFIG, load_pages_from_file

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("OMR_DATA_DIR", os.path.join(BASE_DIR, "data"))
TEMP_DIR = os.path.join(DATA_DIR, "temp")
RESULT_DIR = os.path.join(DATA_DIR, "results")
for d in (DATA_DIR, TEMP_DIR, RESULT_DIR):
    os.makedirs(d, exist_ok=True)

# 소스코드(app.py 등)가 외부에 노출되지 않도록 static 폴더만 공개한다.
app = Flask(__name__, static_folder=os.path.join(BASE_DIR, "static"), static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024 * 1024  # 요청당 최대 1GB
auth.init_auth(app, DATA_DIR, app.static_folder)

lock = threading.RLock()
engine = OMREngine(DEFAULT_CONFIG)

state = {
    "answer_keys": {
        "A": [(i % 5) + 1 for i in range(100)],
        "B": [((i + 2) % 5) + 1 for i in range(100)],
    },
    "score_per_q": 5.0,           # 문항당 배점: 20문항 x 5점 = 과목 100점, 5과목 = 총점 500점
    "pass_average": 60.0,         # 합격 평균: 과락이 없고 5과목 평균(총점/5)이 이 점수 이상이면 합격
    "subject_fail_cutoff": 40.0,  # 과락 점수: 한 과목이라도 이 점수 미만이면 과락(0=사용 안 함)
    "schema": 3,
    "threshold": 0.65,
    "keys_set": {"A": False, "B": False},   # 실제로 등록된 정답인지(기본 임시값 구분). 한쪽만 등록하면 다른 쪽은 블록 교체 규칙으로 자동 생성
}
graded_results = []
next_id = 0


try:
    store = db.Store(DATA_DIR)
except Exception as e:      # DB 접속 실패 시 원인을 로그에 남기고 종료
    raise SystemExit(f"데이터베이스에 연결하지 못했습니다: {e}\n접속 정보(OMR_DATABASE_URL 또는 data/db.json)를 확인하세요.")


def save_state():
    store.save_settings(state)


def load_all():
    global graded_results, next_id
    saved = store.load_settings()
    if saved is not None:
        state.update(saved)
        if state.get("schema") != 3:   # 이전 버전 설정을 현재 방식(문항당 5점, 합격 평균)으로 이전
            if state.get("schema") == 2 and "pass_cutoff" in state:
                state["pass_average"] = round(float(state["pass_cutoff"]) / 5, 1)   # 합격 총점 -> 합격 평균
            else:
                state.update({"score_per_q": 5.0, "subject_fail_cutoff": 40.0, "pass_average": 60.0})
            state.pop("pass_cutoff", None)
            state.pop("fail_cutoff", None)
            state.pop("subject_cutoff", None)
            if float(state.get("threshold", 0)) < 0.6:
                state["threshold"] = 0.65
            state["schema"] = 3
            save_state()
    graded_results = store.load_results()
    # 채점 당시 정답이 저장되지 않은 이전 버전의 테스트 결과는 다시 만들 수 있으므로 정리 (통계가 어긋나 보이는 것을 방지)
    stale = [r for r in graded_results if r.get("is_test") and not r.get("key_used")]
    if stale:
        for r in stale:
            p = os.path.join(RESULT_DIR, os.path.basename(r.get("vis_url", "")))
            if os.path.isfile(p):
                os.remove(p)
        store.delete_results(ids=[r["id"] for r in stale])
        graded_results = [r for r in graded_results if r not in stale]
    next_id = max((r["id"] for r in graded_results), default=-1) + 1
    default_keys = {"A": [(i % 5) + 1 for i in range(100)], "B": [((i + 2) % 5) + 1 for i in range(100)]}
    for t in "AB":   # 이전 버전에서 이미 등록해 둔 정답은 '등록됨'으로 간주
        if state["answer_keys"][t] != default_keys[t]:
            state["keys_set"][t] = True
    engine.fill_threshold = float(state["threshold"])


load_all()


def current_conflicts():
    """두 유형 정답이 모두 등록된 경우, A형 번호 기준으로 서로 다른 문항 목록."""
    if state["keys_set"]["A"] and state["keys_set"]["B"]:
        return key_conflicts(state["answer_keys"]["A"], state["answer_keys"]["B"])
    return []


def register_key(form, key):
    """정답 등록. 다른 유형이 미등록이면 블록 교체 규칙으로 자동 생성하고, 이미 등록돼 있으면 서로 일치하는지 검사."""
    other = "B" if form == "A" else "A"
    state["answer_keys"][form] = [int(x) for x in key][:100]
    state["keys_set"][form] = True
    derived = False
    if not state["keys_set"][other]:
        state["answer_keys"][other] = derive_key(state["answer_keys"][form])
        state["keys_set"][other] = True
        derived = True
    return {"derived_other": derived, "conflicts": current_conflicts()}


def save_upload(file_storage):
    """원본 파일명을 쓰지 않고 uuid 이름으로 저장 (경로조작/이름충돌 방지)."""
    ext = os.path.splitext(file_storage.filename or "")[1].lower()
    path = os.path.join(TEMP_DIR, uuid.uuid4().hex + ext)
    file_storage.save(path)
    return path


SUBJECT_CODES = ("A", "B", "C", "D", "E")


def scaled(item):
    """저장된 '맞힌 개수'에 현재 문항당 배점을 곱해 점수를 계산한다(배점을 바꿔도 즉시 반영)."""
    spq = state["score_per_q"]
    counts = item.get("subject_correct") or item.get("subject_scores", {})
    scores = {c: counts.get(c, 0) * spq for c in SUBJECT_CODES}
    return {**item, "subject_scores": scores, "total_score": sum(scores.values())}


def judge(item):
    """item 은 scaled() 결과. 한 과목이라도 과락 점수 미만 → 과락, 그 외 평균(총점/5)이 합격 평균 이상 → 합격, 아니면 불합격."""
    cut = state["subject_fail_cutoff"]
    fails = [c for c in SUBJECT_CODES if cut > 0 and item["subject_scores"][c] < cut]
    if fails:
        return "과락", fails
    return ("불합격" if item["total_score"] / 5 < state["pass_average"] else "합격"), []


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
    }
    if keys is not None:   # 채점에 쓰인 정답을 함께 저장 (나중에 정답을 바꿔도 문항 통계가 흔들리지 않게)
        item["key_used"] = [int(x) for x in keys.get(res["exam_type"], keys.get("A", [1] * 100))]
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


@app.route("/api/config", methods=["GET", "POST"])
def handle_config():
    with lock:
        if request.method == "GET":
            return jsonify({**state, "key_conflicts": current_conflicts()})
        data = request.get_json(force=True) or {}
        if "answer_keys" in data:
            keys = data["answer_keys"]
            for t in ("A", "B"):
                if t in keys:
                    register_key(t, keys[t])
            if "A" in keys and "B" in keys:     # 둘 다 직접 준 경우에는 자동 생성 없이 입력값 그대로
                state["answer_keys"]["A"] = [int(x) for x in keys["A"]][:100]
                state["answer_keys"]["B"] = [int(x) for x in keys["B"]][:100]
        if "score_per_q" in data:
            state["score_per_q"] = float(data["score_per_q"])
        if "pass_average" in data:
            state["pass_average"] = float(data["pass_average"])
        if "subject_fail_cutoff" in data:
            state["subject_fail_cutoff"] = max(0.0, float(data["subject_fail_cutoff"]))
        if "threshold" in data:
            state["threshold"] = float(data["threshold"])
            engine.fill_threshold = state["threshold"]
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
            res = engine.grade_sheet(img_bgr, state["answer_keys"], state["score_per_q"])
            t_code = res["exam_type"]
            extracted, problems = [], []
            for q in range(1, 101):
                ans = res["student_answers"].get(q, "무표기")
                if ans.isdigit():
                    extracted.append(int(ans))
                else:
                    extracted.append(1)
                    problems.append(f"{q}번({ans})")
            if len(problems) > 10:
                return jsonify({"status": "error",
                                "message": f"인식 실패 문항이 {len(problems)}개입니다. 스캔 품질/민감도를 확인하세요."}), 422
            info = register_key(t_code, extracted)
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
                    res = engine.grade_sheet(img_bgr, state["answer_keys"], 1.0)
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
    return {**sc, "average": round(sc["total_score"] / 5, 1), "result": status, "fail_subjects": fails}


@app.route("/api/results", methods=["GET", "DELETE"])
def handle_results():
    global graded_results, next_id
    with lock:
        if request.method == "GET":
            return jsonify([with_judge(r) for r in graded_results])
        if request.args.get("scope") == "test":      # 테스트 결과만 삭제
            for r in graded_results:
                if r.get("is_test"):
                    p = os.path.join(RESULT_DIR, os.path.basename(r["vis_url"]))
                    if os.path.exists(p):
                        os.remove(p)
            graded_results = [r for r in graded_results if not r.get("is_test")]
            store.delete_results(is_test=True)
            return jsonify({"status": "success"})
        graded_results = []
        next_id = 0
        for f in os.listdir(RESULT_DIR):
            os.remove(os.path.join(RESULT_DIR, f))
        store.delete_results()
        return jsonify({"status": "success"})


@app.route("/api/system")
def api_system():
    """저장 위치/건수 (홈 화면 '데이터 관리'에 표시)."""
    with lock:
        info = store.describe()
        info["counts"] = {"real": len(scoped("real")), "test": len(scoped("test"))}
        info["images_dir"] = RESULT_DIR
    return jsonify(info)


SAMPLE_DIR = os.path.join(app.static_folder, "samples")


@app.route("/api/test/info")
def test_info():
    with open(os.path.join(SAMPLE_DIR, "truth.json"), encoding="utf-8") as f:
        meta = json.load(f)
    return jsonify({"files": meta["files"], "students": [
        {"student_id": k, "desc": v["desc"], "exam_type": v["exam_type"], "expected_correct": v["score"]}
        for k, v in meta["students"].items()]})


@app.route("/api/test/run", methods=["POST"])
def test_run():
    """내장된 가상 답안지를 채점한다. 테스트용 정답을 따로 쓰므로 실제 정답 설정은 바뀌지 않는다.
    결과에는 is_test 표시와 '인식 일치율'(가상 답안지를 만들 때의 정답 기록과 대조)이 붙는다."""
    global graded_results
    with open(os.path.join(SAMPLE_DIR, "truth.json"), encoding="utf-8") as f:
        meta = json.load(f)
    new = []
    with lock:
        for fn in meta["run_files"]:
            for label, img in load_pages_from_file(os.path.join(SAMPLE_DIR, fn)):
                res = engine.grade_sheet(img, meta["keys"], 1.0)
                exp = meta["students"].get(res["student_id"])
                verify = None
                if exp:
                    ok = sum(res["student_answers"].get(int(q)) == a for q, a in exp["answers"].items())
                    verify = {"match": ok, "total": 100, "expected_correct": exp["score"], "desc": exp["desc"]}
                new.append(make_result(res, "[테스트] " + label, is_test=True, verify=verify, keys=meta["keys"]))
        graded_results = [r for r in graded_results if not r.get("is_test")] + new   # 이전 테스트 결과는 교체
        store.delete_results(is_test=True)
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
    return [int(x) for x in keys.get(item["exam_type"], keys.get("A", [1] * 100))]


def stats_items(scope):
    # _legacy: 채점 당시 정답이 저장되지 않은 이전 버전 결과 (현재 정답 설정으로 대신 계산하므로 부정확할 수 있음)
    return [{**with_judge(r), "_key": item_key(r), "_legacy": not r.get("key_used")} for r in scoped(scope)]


@app.route("/api/stats")
def api_stats():
    scope = request.args.get("scope", "real")
    with lock:
        data = stats.compute(stats_items(scope), state["score_per_q"])
        data["scope"] = scope
        data["counts"] = {"real": len(scoped("real")), "test": len(scoped("test"))}
        data["score_per_q"] = state["score_per_q"]
    return jsonify(data)


FLAG_TEXT = {"key": "정답키 확인", "hard": "매우 어려움", "easy": "매우 쉬움", "lowdisc": "변별도 낮음",
             "revdisc": "역변별", "marking": "마킹 이상"}


@app.route("/api/export/excel", methods=["GET"])
def export_excel():
    scope = request.args.get("scope", "all")
    with lock:
        base = scoped(scope)
        if not base:
            return jsonify({"status": "error", "message": "채점 결과가 없습니다."}), 400
        spq = state["score_per_q"]
        smax, tmax = 20 * spq, 100 * spq
        judged = [with_judge(r) for r in base]
        totals = [x["total_score"] for x in judged]
        rows, detail = [], []
        for item in judged:
            ss = item["subject_scores"]
            rows.append({
                "구분": "테스트" if item.get("is_test") else "",
                "순번": item["id"] + 1,
                "파일명": item["filename"],
                "문제유형": item["exam_type"],
                "수험번호": item["student_id"],
                f"A.인터넷중독의 이해({smax:g})": ss["A"],
                f"B.진단과 평가({smax:g})": ss["B"],
                f"C.상담기법({smax:g})": ss["C"],
                f"D.상담의 이론과 실제({smax:g})": ss["D"],
                f"E.정책과 법({smax:g})": ss["E"],
                f"총점({tmax:g})": item["total_score"],
                "평균": item["average"],
                "석차": 1 + sum(1 for t in totals if t > item["total_score"]),
                "판정": item["result"],
                "과락과목": ",".join(item["fail_subjects"]),
                "무표기": item["blank_count"],
                "이중마킹": item["multi_count"],
                "검수필요여부": "검수필요" if item["needs_review"] else "정상",
            })
            sa = item.get("student_answers", {})
            d = {"수험번호": item["student_id"], "유형": item["exam_type"]}
            for q in range(1, 101):
                d[str(q)] = sa.get(str(q), "")
            detail.append(d)
        # 통계 시트 (범위가 all 이면 실제 응시자 우선, 없으면 테스트)
        st_scope = scope if scope in ("real", "test") else ("real" if scoped("real") else "test")
        st = stats.compute(stats_items(st_scope), spq)

    sm = st["summary"]
    summary_df = pd.DataFrame([
        ("통계 대상", "실제 응시자" if st_scope == "real" else "테스트 결과"),
        ("응시자 수", sm["n"]), ("합격", sm["pass"]), ("불합격", sm["fail"]), ("과락", sm["cut"]),
        ("합격률(%)", sm["pass_rate"]), ("과락률(%)", sm["cut_rate"]),
        (f"총점 평균({tmax:g}점 만점)", sm["total_mean"]), ("총점 중앙값", sm["total_median"]), ("총점 표준편차", sm["total_sd"]),
        ("총점 최고", sm["total_max"]), ("총점 최저", sm["total_min"]),
        ("평균점수 평균(100점 만점)", sm["avg_mean"]), ("평균점수 표준편차", sm["avg_sd"]),
        ("검수필요 건수", sm["review"]), ("무표기 문항 합계", sm["blank_total"]), ("이중마킹 문항 합계", sm["multi_total"]),
    ], columns=["항목", "값"])
    dist_df = pd.DataFrame([{"평균점수 구간": d["label"], "인원": d["count"]} for d in st["distribution"]])
    subj_df = pd.DataFrame([{"과목": f'{s["code"]}.{s["name"]}', f"평균({smax:g}점 만점)": s["mean"], "표준편차": s["sd"],
                             "최고": s["max"], "최저": s["min"], "정답률(%)": s["correct_rate"], "난이도": s["level"],
                             "과락 인원": s["cut_n"], "과락률(%)": s["cut_rate"]} for s in st["subjects"]])
    form_df = pd.DataFrame([{"유형": f["form"], "응시자": f["n"], f"총점 평균({tmax:g})": f["total_mean"],
                             "평균점수 평균": f["avg_mean"], "합격률(%)": f["pass_rate"], "과락 인원": f["cut_n"]}
                            for f in st["forms_summary"]])
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        pd.DataFrame(rows).to_excel(writer, sheet_name="성적총괄표", index=False)
        pd.DataFrame(detail).to_excel(writer, sheet_name="문항별답안", index=False)
        summary_df.to_excel(writer, sheet_name="통계요약", index=False)
        dist_df.to_excel(writer, sheet_name="점수분포", index=False)
        subj_df.to_excel(writer, sheet_name="과목별통계", index=False)
        form_df.to_excel(writer, sheet_name="유형별통계", index=False)
        for fm in st["forms"]:
            items_df = pd.DataFrame([{
                "문항": it["q"], "과목": it["subject_name"], "정답": it["answer"], "정답률(%)": it["correct_rate"],
                "오답률(%)": it["wrong_rate"], "1번(%)": it["choices"]["1"], "2번(%)": it["choices"]["2"],
                "3번(%)": it["choices"]["3"], "4번(%)": it["choices"]["4"], "5번(%)": it["choices"]["5"],
                "무표기(%)": it["choices"]["무표기"], "이중(%)": it["choices"]["이중"],
                "최다 오답": it["top_wrong"], "최다 오답(%)": it["top_wrong_rate"],
                "변별도": it["disc"], "검토": ", ".join(FLAG_TEXT[f] for f in it["flags"]),
            } for it in fm["items"]])
            name = "문항분석_" + fm["label"].replace(" ", "").replace("(", "").replace(")", "")[:20]
            items_df.to_excel(writer, sheet_name=name, index=False)
    output.seek(0)
    return send_file(
        output,
        download_name="NIA_자격검정_OMR_채점결과.xlsx",
        as_attachment=True,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


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
