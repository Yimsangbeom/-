"""이름 칸 손글씨 인식 가능성 점검용 (서버의 data\\results\\name_*.jpg 로 시험). 채점 프로그램에는 영향이 없다.

준비(한 번):  pip install rapidocr onnxruntime      (처음 실행할 때 한글 인식 모델을 내려받는다: modelscope.cn)
실행:        python tools\\name_ocr_probe.py [이미지폴더] [개수]
출력: 파일명 -> 인식한 글자 (신뢰도).  결과를 사람이 보는 이름과 비교해 정확도를 가늠한다.
"""
import glob
import os
import sys
import time

import cv2

folder = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "results")
limit = int(sys.argv[2]) if len(sys.argv) > 2 else 15
files = sorted(glob.glob(os.path.join(folder, "name_*.jpg")))[:limit]
if not files:
    sys.exit(f"이름 칸 이미지(name_*.jpg)가 없습니다: {folder}")
try:
    from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR
    engine = RapidOCR(params={"Rec.lang_type": LangRec.KOREAN, "Rec.ocr_version": OCRVersion.PPOCRV4, "Rec.model_type": ModelType.MOBILE})
except Exception as e:
    sys.exit(f"인식 엔진을 준비하지 못했습니다(설치 또는 모델 내려받기 실패): {e}")

ok = 0
for f in files:
    img = cv2.imread(f)
    h, w = img.shape[:2]
    img = img[:, int(w * 0.24):]                      # 왼쪽 '성명' 라벨 칸 제외
    img = cv2.copyMakeBorder(img, 12, 12, 12, 12, cv2.BORDER_CONSTANT, value=(255, 255, 255))
    t = time.time()
    res = engine(img)
    txt = "".join(res.txts) if res.txts else ""
    sc = (sum(res.scores) / len(res.scores)) if res.scores else 0
    ok += bool(txt)
    print(f"{os.path.basename(f):16s} -> {txt!r:14s} (신뢰도 {sc:.2f}, {time.time() - t:.2f}초)")
print(f"\n글자를 읽어 낸 이미지 {ok}/{len(files)}장. 위 글자와 실제 이름을 비교해서 몇 장이 맞는지 알려 주세요.")
