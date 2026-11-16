"""마킹 규칙 검증: (1) 빨간색 표시는 검토용이라 답안으로 인식하지 않는다 (2) 한 문항에 2개 이상 표시하면(정답 포함) 이중마킹 = 오답 처리.
스캔처럼 열화(JPEG, 블러, 기울기)시킨 답안지로 시험한다."""
import random, sys
import cv2, numpy as np
sys.path.insert(0, "tools")
from make_samples import fill, scan
from omr_engine import OMREngine, DEFAULT_CONFIG as C

xs, ys = C["answer_grid"]["x_cols"], C["answer_grid"]["y_rows"]
def pos(q, choice):                       # 문항 q(1~100), 선택지 choice(1~5)의 중심
    s, row = divmod(q - 1, 20); return xs[s * 5 + choice - 1], ys[row]

def blank():
    img = np.full((C["canonical_size"][1], C["canonical_size"][0], 3), 255, np.uint8)
    ax, ay, aw, ah = C["regions"]["answer_box"]; cv2.rectangle(img, (ax, ay), (ax + aw, ay + ah), (0, 0, 0), 3)
    ix, iy, iw, ih = C["regions"]["id_box"]; cv2.rectangle(img, (ix, iy), (ix + iw, iy + ih), (0, 0, 0), 3)
    for q in range(1, 101):
        for c in range(1, 6):
            x, y = pos(q, c); cv2.circle(img, (int(x), int(y)), 11, (0, 0, 0), 2)
            cv2.putText(img, str(c), (int(x) - 5, int(y) + 5), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 1, cv2.LINE_AA)
    return img

def red_fill(img, x, y):                  # 빨간 펜으로 칠함
    cv2.ellipse(img, (int(x), int(y)), (11, 10), 20, 0, 360, (30, 30, 215), -1, cv2.LINE_AA)
def red_check(img, x, y):                 # 빨간 체크(V) 표시
    cv2.polylines(img, [np.int32([[x - 10, y], [x - 3, y + 9], [x + 12, y - 12]])], False, (40, 40, 220), 4, cv2.LINE_AA)
def red_ring(img, x, y):                  # 빨간 동그라미 쳐 둠
    cv2.circle(img, (int(x), int(y)), 19, (35, 35, 210), 3, cv2.LINE_AA)
def red_slash(img, x, y):                 # 빨간 사선
    cv2.line(img, (int(x - 14), int(y + 14)), (int(x + 14), int(y - 14)), (30, 30, 220), 4, cv2.LINE_AA)

key = [(i % 5) + 1 for i in range(100)]            # 기본 정답(임시값과 같음)
rng = random.Random(3)
img = blank()
def black(q, c): fill(img, *pos(q, c), rng)
# 1: 정답 3번(검정) + 틀린 5번에 빨간 체크 -> 빨강 무시, 정답 인정
black(1, key[0]); red_check(img, *pos(1, 5))
# 2: 검정 표시 없음, 정답 위치에만 빨간 칠 -> 무표기
red_fill(img, *pos(2, key[1]))
# 3: 정답 + 다른 번호 2개 검정 -> 이중마킹(오답)
black(3, key[2]); black(3, key[2] % 5 + 1)
# 4: 틀린 두 개 검정 -> 이중마킹(오답)
black(4, 1); black(4, 2)
# 5: 틀린 번호만 검정 + 정답에 빨간 동그라미 -> 오답(검정 선택)
wrong5 = key[4] % 5 + 1; black(5, wrong5); red_ring(img, *pos(5, key[4]))
# 6: 정답 검정 + 그 위에 빨간 사선 -> 검정 인식(정답)
black(6, key[5]); red_slash(img, *pos(6, key[5]))
# 7: 정답 검정 + 다른 번호에 빨간 칠 -> 정답(빨강 무시, 이중 아님)
black(7, key[6]); red_fill(img, *pos(7, key[6] % 5 + 1))
# 8: 정답 검정 + 빨간 칠도 정답 위에 겹침 -> 정답
black(8, key[7]); red_check(img, *pos(8, key[7]))
# 9: 검정 두 개 + 다른 하나에 빨간 표시 -> 이중마킹
black(9, 1); black(9, 3); red_fill(img, *pos(9, 5))
# 10~: 아무 표시 없음 -> 무표기

scanned, _ = scan(img, rng, rot=0.8, shift=(10, -8), persp=2, blur=0.8, q=72)     # JPEG 72 로 압축: 빨강/검정 경계 번짐 포함
eng = OMREngine(C)
res = eng.grade_sheet(scanned, {"A": key, "B": key}, 1.0)
a = res["student_answers"]
exp = {1: str(key[0]), 2: "무표기", 3: "이중", 4: "이중", 5: str(wrong5), 6: str(key[5]), 7: str(key[6]), 8: str(key[7]), 9: "이중"}
bad = {q: (a[q], v) for q, v in exp.items() if a[q] != v}
print("판독:", {q: a[q] for q in range(1, 11)})
assert not bad, f"기대와 다른 문항(문항: (판독, 기대)): {bad}"
assert res["correct_count"] == 4, res["correct_count"]          # 1, 6, 7, 8 번만 정답. 이중마킹(3, 4, 9)은 정답 포함이어도 오답
assert res["multi_count"] == 3 and res["blank_count"] == 92 and res["needs_review"]
print("ALL PASS")
