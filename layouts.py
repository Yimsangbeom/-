"""답안지 양식(좌표) 목록. 기본 양식 + layouts/*.json (양식을 추가하려면 json 파일을 넣으면 됨).

json 형식: {"label": "표시 이름", "config": { omr_engine.DEFAULT_CONFIG 와 같은 구조 }}"""
import glob
import json
import os

from omr_engine import DEFAULT_CONFIG

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _meta(label, cfg):
    cpb = cfg.get("choices_per_block", 5)
    rows = len(cfg["answer_grid"]["y_rows"])
    blocks = len(cfg["answer_grid"]["x_cols"]) // cpb
    return {"label": label, "config": cfg, "capacity": rows * blocks, "choices_max": cpb,
            "id_digits_max": len(cfg["id_grid"]["x_cols"]), "forms": list(cfg["type_bubbles"].keys())}


LAYOUTS = {"nia_a4_100": _meta("NIA 자격검정 답안지 (A4 가로, 100문항 5지선다, 수험번호 9자리, A/B형)", DEFAULT_CONFIG)}
for _f in sorted(glob.glob(os.path.join(BASE_DIR, "layouts", "*.json"))):
    try:
        with open(_f, encoding="utf-8") as fh:
            _d = json.load(fh)
        LAYOUTS[os.path.splitext(os.path.basename(_f))[0]] = _meta(_d.get("label", os.path.basename(_f)), _d["config"])
    except Exception as e:   # 잘못된 양식 파일이 있어도 서버는 뜨게 한다
        print(f"양식 파일을 읽지 못했습니다: {_f}: {e}")


def info():
    return [{"id": k, "label": v["label"], "capacity": v["capacity"], "choices_max": v["choices_max"],
             "id_digits_max": v["id_digits_max"], "forms": v["forms"]} for k, v in LAYOUTS.items()]
