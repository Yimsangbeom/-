"""통계 계산 검증: 능력 모델로 가상 응시자 60명을 만들고, 문제 있는 문항을 심어 두어 탐지하는지 확인."""
import math, random
import stats
from form_map import other_q, derive_key, key_conflicts

random.seed(3)
KEY = [random.randint(1, 5) for _ in range(100)]
KEYS = {"A": KEY, "B": derive_key(KEY)}
ITEM_DIFF = [random.uniform(-1.5, 1.5) for _ in range(100)]
BAD_KEY_Q, HARD_Q, REV_Q = 10, 20, 30      # 정답키 오류 / 매우 어려움 / 역변별(잘하는 사람이 오히려 더 틀림)
N = 60

def make(n, form="A", spq=5.0):
    items = []
    for i in range(n):
        theta = random.gauss(0.3, 1.0)                       # 능력
        ans = {}
        for q in range(1, 101):
            qa = q if form == "A" else other_q(q)          # B형 q번 = A형 qa번과 같은 문제
            diff = ITEM_DIFF[qa - 1]
            p = 1 / (1 + math.exp(-(theta - diff) * 1.7))
            if qa == HARD_Q: p = 0.12
            if qa == REV_Q: p = 0.9 - 0.8 / (1 + math.exp(-theta * 2))      # 능력이 높을수록 정답률 하락
            right = KEYS[form][q - 1]
            if qa == BAD_KEY_Q:                                 # 학생 70% 가 특정 오답(=실제 정답)을 고름
                pick = (right % 5) + 1 if random.random() < 0.7 else random.choice([c for c in range(1, 6) if c != (right % 5) + 1])
            elif random.random() < p:
                pick = right
            else:
                pick = random.choice([c for c in range(1, 6) if c != right])
            ans[str(q)] = "무표기" if (q % 37 == 0 and random.random() < 0.05) else str(pick)
        correct = [sum(1 for q in range(s * 20 + 1, s * 20 + 21) if ans[str(q)] == str(KEYS[form][q - 1])) for s in range(5)]
        sc = {c: correct[k] * spq for k, c in enumerate("ABCDE")}
        tot = sum(sc.values()); avg = tot / 5
        fails = [c for c in "ABCDE" if sc[c] < 40]
        items.append(dict(exam_type=form, student_answers=ans, subject_scores=sc, total_score=tot, average=avg,
                          result="과락" if fails else ("합격" if avg >= 60 else "불합격"), fail_subjects=fails,
                          needs_review=False, blank_count=sum(1 for v in ans.values() if v == "무표기"), multi_count=0,
                          _key=KEYS[form]))
    return items

items = make(N)
r = stats.compute(items, 5.0)
sm = r["summary"]
assert sm["n"] == N and sm["pass"] + sm["fail"] + sm["cut"] == N
assert sum(d["count"] for d in r["distribution"]) == N
assert abs(sm["avg_mean"] - sum(i["average"] for i in items) / N) < 0.06
assert all(0 <= s["correct_rate"] <= 100 for s in r["subjects"]) and sum(s["cut_n"] for s in r["subjects"]) >= sm["cut"]
assert [x["label"] for x in r["forms"]] == ["A형"] and r["key_conflicts"] == [] and r["blocks"] == []   # 한 유형만: 통합/블록 비교 없음
f = r["forms"][0]; assert f["n"] == N and f["enough"]
assert f["kr20"] is not None and 0.3 < f["kr20"] < 1.0, f["kr20"]
it = {x["q"]: x for x in f["items"]}
assert "key" in it[BAD_KEY_Q]["flags"], it[BAD_KEY_Q]          # 정답키 오류 의심 탐지
assert "hard" in it[HARD_Q]["flags"], it[HARD_Q]                # 매우 어려움 탐지
assert it[REV_Q]["disc"] < 0 and "revdisc" in it[REV_Q]["flags"], it[REV_Q]   # 역변별 탐지
good = [x["disc"] for q, x in it.items() if q not in (BAD_KEY_Q, HARD_Q, REV_Q)]
assert sum(good) / len(good) > 0.2, "정상 문항의 평균 변별도가 낮음"
for x in f["items"]:                                            # 선지별 분포는 합이 100
    assert abs(sum(x["choices"].values()) - 100) < 0.8, x["choices"]
    assert abs(x["correct_rate"] + x["wrong_rate"] - 100) < 0.2
print(f"대규모 OK: KR-20={f['kr20']}, 평균 변별도={sum(good)/len(good):.2f}, 검토 문항={[q for q,x in it.items() if x['flags']][:8]}")

# 소규모(N<10): 문항 판정/신뢰도 보류
small = stats.compute(make(5), 5.0)["forms"][0]
assert not small["enough"] and small["kr20"] is None and all(x["flags"] == [] and x["disc"] is None for x in small["items"])
# A/B 형은 문항 분석을 따로
two = stats.compute(make(12, "A") + make(12, "B"), 5.0)
assert [f["label"][:2] for f in two["forms"]] == ["통합", "A형", "B형"] and {x["form"] for x in two["forms_summary"]} == {"A", "B"}
assert stats.compute([], 5.0) == {"n": 0}
print("소규모/유형 분리 OK")

# A/B 통합 분석: B형 응시자의 답을 A형 번호로 되돌려 합산 -> 심어 둔 문제 문항이 합산 분석에서도, B형 단독 분석에서는 '대응 번호'에서 잡혀야 한다
mix = stats.compute(make(30, "A") + make(30, "B"), 5.0)
m, fa, fb = mix["forms"]
assert m["label"].startswith("통합") and m["n"] == 60 and m["enough"] and fa["n"] == 30 and fb["n"] == 30
mi = {x["q"]: x for x in m["items"]}
assert "key" in mi[BAD_KEY_Q]["flags"] and "hard" in mi[HARD_Q]["flags"] and "revdisc" in mi[REV_Q]["flags"], (mi[BAD_KEY_Q], mi[HARD_Q], mi[REV_Q])
assert mi[BAD_KEY_Q]["alt_q"] == other_q(BAD_KEY_Q) == 20          # A 10번 = B 20번
fbi = {x["q"]: x for x in fb["items"]}
assert "key" in fbi[other_q(BAD_KEY_Q)]["flags"] and "hard" in fbi[other_q(HARD_Q)]["flags"]   # B형 20번/30번(=A 10번/20번... 대응)에서도 보임
assert mix["key_conflicts"] == [] and len(mix["blocks"]) == 10
assert all(b["a_rate"] is not None and b["b_rate"] is not None for b in mix["blocks"])
normal_keyflags = [q for q, x in mi.items() if "key" in x["flags"] and q != BAD_KEY_Q]
assert len(normal_keyflags) <= 1, normal_keyflags                 # 정답키 의심이 정상 문항에 남발되지 않음
# 정답 불일치 감지: B형 정답을 한 곳 틀리게 등록하면 A형 번호 기준으로 보고
bad = make(15, "A") + make(15, "B")
for it in bad:
    if it["exam_type"] == "B":
        k = list(it["_key"]); k[0] = k[0] % 5 + 1; it["_key"] = k          # B형 1번 = A형 11번
assert stats.compute(bad, 5.0)["key_conflicts"] == [11]
# A/B 정답이 대부분 맞지 않으면(블록 맞바꿈 가정이 틀림) 통합 분석/블록 비교를 만들지 않는다
off = make(15, "A") + make(15, "B")
for it in off:
    if it["exam_type"] == "B":
        it["_key"] = [k % 5 + 1 for k in it["_key"]]
r = stats.compute(off, 5.0)
assert r["pair_invalid"] and r["blocks"] == [] and r["key_conflicts"] == [] and [f["label"][:2] for f in r["forms"]] == ["A형", "B형"], [f["label"] for f in r["forms"]]
assert stats.compute(bad, 5.0)["pair_invalid"] is False and stats.compute(bad, 5.0)["forms"][0]["label"].startswith("통합")
print("통합 분석/블록 비교/정답 불일치/관계 불일치 OK"); print("ALL PASS")
