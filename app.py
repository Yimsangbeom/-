import io
import json
import os
import threading
import uuid

import cv2
import pandas as pd
from flask import Flask, request, jsonify, send_file, send_from_directory

import auth
from omr_engine import OMREngine, DEFAULT_CONFIG, load_pages_from_file

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("OMR_DATA_DIR", os.path.join(BASE_DIR, "data"))
TEMP_DIR = os.path.join(DATA_DIR, "temp")
RESULT_DIR = os.path.join(DATA_DIR, "results")
STATE_FILE = os.path.join(DATA_DIR, "state.json")
RESULTS_FILE = os.path.join(DATA_DIR, "results.json")
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
}
graded_results = []
next_id = 0


def save_state():
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)


def save_results():
    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump({"next_id": next_id, "results": graded_results}, f, ensure_ascii=False)


def load_all():
    global graded_results, next_id
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            state.update(json.load(f))
        if state.get("schema") != 3:   # 이전 버전 설정을 현재 방식(문항당 5점, 합격 평균)으로 이전
            if state.get("schema") == 2 and "pass_cutoff" in state:
                state["pass_average"] = round(float(state["pass_cutoff"]) / 5, 1)   # 합격 총점 → 합격 평균
            else:
                state.update({"score_per_q": 5.0, "subject_fail_cutoff": 40.0, "pass_average": 60.0})
            state.pop("pass_cutoff", None)
            state.pop("fail_cutoff", None)
            state.pop("subject_cutoff", None)
            if float(state.get("threshold", 0)) < 0.6:
                state["threshold"] = 0.65
            state["schema"] = 3
            save_state()
    if os.path.exists(RESULTS_FILE):
        with open(RESULTS_FILE, encoding="utf-8") as f:
            data = json.load(f)
            graded_results = data.get("results", [])
            next_id = data.get("next_id", len(graded_results))
    engine.fill_threshold = float(state["threshold"])


load_all()


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


def make_result(res, page_label, is_test=False, verify=None):
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
            return jsonify({**state})
        data = request.get_json(force=True) or {}
        if "answer_keys" in data:
            keys = data["answer_keys"]
            for t in ("A", "B"):
                if t in keys:
                    state["answer_keys"][t] = [int(x) for x in keys[t]][:100]
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
        return jsonify({"status": "success", "message": "설정이 업데이트되었습니다."})


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
            state["answer_keys"][t_code] = extracted
            save_state()
        return jsonify({"status": "success", "exam_type": t_code, "answers": extracted,
                        "problems": problems})
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
                    new_results.append(make_result(res, label.replace(os.path.basename(temp_path), shown)))
        except Exception as e:
            errors.append(f"{shown}: {e}")
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    with lock:
        graded_results.extend(new_results)
        save_results()
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
            save_results()
            return jsonify({"status": "success"})
        graded_results = []
        next_id = 0
        for f in os.listdir(RESULT_DIR):
            os.remove(os.path.join(RESULT_DIR, f))
        save_results()
        return jsonify({"status": "success"})


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
                new.append(make_result(res, "[테스트] " + label, is_test=True, verify=verify))
        graded_results = [r for r in graded_results if not r.get("is_test")] + new   # 이전 테스트 결과는 교체
        save_results()
    return jsonify({"status": "success", "results": [with_judge(r) for r in new]})


@app.route("/api/export/excel", methods=["GET"])
def export_excel():
    with lock:
        if not graded_results:
            return jsonify({"status": "error", "message": "채점 결과가 없습니다."}), 400
        spq = state["score_per_q"]
        smax, tmax = 20 * spq, 100 * spq
        rows, detail = [], []
        for raw in graded_results:
            item = with_judge(raw)
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

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        pd.DataFrame(rows).to_excel(writer, sheet_name="성적총괄표", index=False)
        pd.DataFrame(detail).to_excel(writer, sheet_name="문항별답안", index=False)
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
