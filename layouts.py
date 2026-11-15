"""답안지 양식(좌표) 목록. 기본 양식 + layouts/*.json (양식을 추가하려면 json 파일을 넣으면 됨).

json 형식: {"label": "표시 이름", "config": { omr_engine.DEFAULT_CONFIG 와 같은 구조 }}"""
import glob
import json
import os

from omr_engine import DEFAULT_CONFIG

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _meta(label, cfg):
    blocks = cfg.get("answer_blocks")
    if blocks:
        capacity, choices_max = sum(len(b["y_rows"]) for b in blocks), max(len(b["x_cols"]) for b in blocks)
    else:
        cpb = cfg.get("choices_per_block", 5)
        capacity, choices_max = len(cfg["answer_grid"]["y_rows"]) * (len(cfg["answer_grid"]["x_cols"]) // cpb), cpb
    return {"label": label, "config": cfg, "capacity": capacity, "choices_max": choices_max,
            "id_digits_max": len(cfg["id_grid"]["x_cols"]), "forms": list(cfg["type_bubbles"]) or ["A"]}


LAYOUTS = {"nia_a4_100": _meta("NIA 자격검정 답안지 (A4 가로, 100문항 5지선다, 수험번호 9자리, A/B형)", DEFAULT_CONFIG)}


def _load_dir(path):
    for f in sorted(glob.glob(os.path.join(path, "*.json"))):
        try:
            with open(f, encoding="utf-8") as fh:
                d = json.load(fh)
            LAYOUTS[os.path.splitext(os.path.basename(f))[0]] = _meta(d.get("label", os.path.basename(f)), d["config"])
        except Exception as e:   # 잘못된 양식 파일이 있어도 서버는 뜨게 한다
            print(f"양식 파일을 읽지 못했습니다: {f}: {e}")


_load_dir(os.path.join(BASE_DIR, "layouts"))
USER_DIR = None


def load_user_layouts(data_dir):
    """자동 인식으로 등록한 양식(data/layouts/*.json)을 불러온다."""
    global USER_DIR
    USER_DIR = os.path.join(data_dir, "layouts")
    os.makedirs(USER_DIR, exist_ok=True)
    _load_dir(USER_DIR)


def register(layout_id, label, config):
    """새 양식을 저장하고 즉시 사용 가능하게 한다."""
    if USER_DIR is None:
        raise RuntimeError("load_user_layouts 를 먼저 호출해야 합니다.")
    with open(os.path.join(USER_DIR, layout_id + ".json"), "w", encoding="utf-8") as f:
        json.dump({"label": label, "config": config}, f)
    LAYOUTS[layout_id] = _meta(label, config)
    return LAYOUTS[layout_id]


def info():
    return [{"id": k, "label": v["label"], "capacity": v["capacity"], "choices_max": v["choices_max"],
             "id_digits_max": v["id_digits_max"], "forms": v["forms"]} for k, v in LAYOUTS.items()]
