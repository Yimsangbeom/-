"""시험 통계 계산 (고전적 검사 이론 기반). app.py 의 with_judge() 결과 목록을 받아 계산한다.

items 의 각 원소에 필요한 값:
  exam_type, student_answers({"1": "3", ... "무표기"/"이중"}), subject_scores(배점 반영), total_score,
  average, result(합격/불합격/과락), fail_subjects, needs_review, _key(채점에 쓰인 정답 100개)
"""
import math

import exam as exam_mod

MIN_N_ITEM = 10      # 문항 분석(변별도/정답키 의심 등)을 판단하는 최소 응시자 수(유형별)


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def _median(xs):
    s = sorted(xs)
    n = len(s)
    if not n:
        return 0.0
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def _sd(xs):
    n = len(xs)
    if n < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


def level(pct):
    """정답률(%) 기준 난이도: 80 이상 쉬움, 40 이상 보통, 그 미만 어려움."""
    return "쉬움" if pct >= 80 else ("보통" if pct >= 40 else "어려움")


def _r(x, nd=1):
    return None if x is None else round(x, nd)


def compute(items, spq, exam=None):
    exam = exam or exam_mod.default_exam()
    subj = exam_mod.subject_ranges(exam)         # [(코드, 이름, 약칭, 시작, 끝)]
    n_q = exam_mod.total_questions(exam)
    n = len(items)
    if n == 0:
        return {"n": 0}
    totals = [it["total_score"] for it in items]
    avgs = [it["average"] for it in items]
    count = {k: sum(1 for it in items if it["result"] == k) for k in ("합격", "불합격", "과락")}

    summary = {
        "n": n,
        "pass": count["합격"], "fail": count["불합격"], "cut": count["과락"],
        "pass_rate": _r(count["합격"] / n * 100),
        "cut_rate": _r(count["과락"] / n * 100),
        "total_mean": _r(_mean(totals)), "total_median": _r(_median(totals)), "total_sd": _r(_sd(totals)),
        "total_max": max(totals), "total_min": min(totals),
        "avg_mean": _r(_mean(avgs)), "avg_median": _r(_median(avgs)), "avg_sd": _r(_sd(avgs)),
        "avg_max": max(avgs), "avg_min": min(avgs),
        "review": sum(1 for it in items if it["needs_review"]),
        "blank_total": sum(it["blank_count"] for it in items),
        "multi_total": sum(it["multi_count"] for it in items),
    }

    # 점수 분포: 평균 점수를 10점 구간으로 (0~10 ... 90~100)
    # 평균점수 만점 = 총점 만점 / 과목 수 (기본 시험은 100점). 만점이 100이 아니면 만점 대비 % 구간으로 나눈다.
    avg_full = n_q * spq / len(subj) if subj else 100
    bins = [0] * 10
    for a in avgs:
        bins[min(int(a / avg_full * 10), 9) if avg_full else 0] += 1
    unit = "" if abs(avg_full - 100) < 1e-9 else "%"
    distribution = [{"label": f"{i * 10}~{i * 10 + 10}{unit}", "count": bins[i]} for i in range(10)]

    # 과목별
    subjects = []
    for code, name, short, a0, b0 in subj:
        smax = (b0 - a0 + 1) * spq
        sc = [it["subject_scores"][code] for it in items]
        pct = _mean(sc) / smax * 100 if smax else 0
        cut_n = sum(1 for it in items if code in it["fail_subjects"])
        subjects.append({
            "code": code, "name": name, "short": short, "max_score": smax, "mean": _r(_mean(sc)), "sd": _r(_sd(sc)), "max": max(sc), "min": min(sc),
            "correct_rate": _r(pct), "level": level(pct), "cut_n": cut_n, "cut_rate": _r(cut_n / n * 100),
        })

    # 유형별 요약
    forms_summary = []
    for t in sorted({it["exam_type"] for it in items}):
        g = [it for it in items if it["exam_type"] == t]
        forms_summary.append({
            "form": t, "n": len(g), "total_mean": _r(_mean([x["total_score"] for x in g])),
            "avg_mean": _r(_mean([x["average"] for x in g])),
            "pass_rate": _r(sum(1 for x in g if x["result"] == "합격") / len(g) * 100),
            "cut_n": sum(1 for x in g if x["result"] == "과락"),
        })

    # 문항 분석: (유형, 정답 설정) 별로 따로 (A형/B형은 같은 번호가 같은 문제가 아닐 수 있음)
    groups = {}
    for it in items:
        groups.setdefault((it["exam_type"], tuple(it["_key"])), []).append(it)
    forms = []
    seen = {}
    for (t, key), g in sorted(groups.items(), key=lambda kv: (kv[0][0], -len(kv[1]))):
        seen[t] = seen.get(t, 0) + 1
        label = f"{t}형" + ("" if seen[t] == 1 else f" (정답 설정 {seen[t]})")
        forms.append(_item_analysis(label, t, list(key), g, exam))

    types = {it["exam_type"] for it in items}
    f1, f2 = (exam["forms"] + [None, None])[:2]
    key_of = {}
    for t in exam["forms"]:
        gs = [(len(g), k) for (tt, k), g in groups.items() if tt == t]
        if gs:
            key_of[t] = list(max(gs)[1])      # 인원이 가장 많은 정답 설정
    conflicts, blocks = [], []
    paired = exam["form_rule"] == "half_swap" and f2 is not None
    if paired and {f1, f2} <= types and len(key_of) == 2:
        conflicts = exam_mod.key_conflicts(exam, key_of[f1], key_of[f2])
        # 통합 문항 분석: 두 유형 응시자를 첫 번째 유형 번호 기준으로 합쳐서 분석 (같은 문제를 푼 사람이 늘어남)
        merged = []
        for it in items:
            sa = it["student_answers"]
            if it["exam_type"] != f1:
                sa = {str(q): sa.get(str(exam_mod.other_q(exam, q)), "무표기") for q in range(1, n_q + 1)}
            merged.append({**it, "student_answers": sa, "exam_type": f1, "_key": key_of[f1]})
        forms.insert(0, _item_analysis(f"통합 ({f1}형 번호 기준, {f1}·{f2} 합산)", f1 + f2, key_of[f1], merged, exam))
        # 블록 정답률: 같은 문제를 앞쪽에서 풀었는지 뒤쪽에서 풀었는지의 차이 (과목의 앞 절반 / 뒤 절반)
        for code, name, short, a0, b0 in subj:
            n_s = b0 - a0 + 1
            for b_i, blabel in enumerate((f"1~{n_s // 2}번", f"{n_s // 2 + 1}~{n_s}번")):
                qs = range(a0 + b_i * (n_s // 2), a0 + (b_i + 1) * (n_s // 2))      # 첫 유형 번호 기준
                rate = {}
                for t in (f1, f2):
                    g = [it for it in items if it["exam_type"] == t]
                    tot = hit = 0
                    for it in g:
                        for q in qs:
                            qq = q if t == f1 else exam_mod.other_q(exam, q)
                            tot += 1
                            hit += it["student_answers"].get(str(qq)) == str(it["_key"][qq - 1])
                    rate[t] = (hit / tot * 100) if tot else None
                blocks.append({"subject": code, "subject_name": name, "block": blabel,
                               "n_a": sum(1 for it in items if it["exam_type"] == f1),
                               "n_b": sum(1 for it in items if it["exam_type"] == f2),
                               "a_rate": _r(rate[f1]), "b_rate": _r(rate[f2]),
                               "diff": _r(rate[f2] - rate[f1]) if None not in rate.values() else None,
                               "form_a": f1, "form_b": f2})

    return {"n": n, "summary": summary, "distribution": distribution, "avg_full": round(avg_full, 2), "subjects": subjects,
            "forms_summary": forms_summary, "forms": forms, "min_n_item": MIN_N_ITEM,
            "key_conflicts": conflicts, "blocks": blocks, "paired": paired, "forms_list": exam["forms"],
            "legacy_n": sum(1 for it in items if it.get("_legacy"))}


def _item_analysis(label, form, key, g, exam):
    n_q = exam_mod.total_questions(exam)
    chs = [str(c) for c in range(1, exam["choices"] + 1)]
    subj = exam_mod.subject_ranges(exam)
    n = len(g)
    ordered = sorted(g, key=lambda x: -x["total_score"])
    k = max(1, round(0.27 * n))
    upper, lower = ordered[:k], ordered[-k:]
    enough = n >= MIN_N_ITEM
    rows = []
    corrects = []   # 문항별 정답 여부 행렬 (KR-20 용)
    for q in range(1, n_q + 1):
        ans = [x["student_answers"].get(str(q), "무표기") for x in g]
        cnt = {c: ans.count(c) for c in chs + ["무표기", "이중"]}
        right = str(key[q - 1])
        p = cnt[right] / n
        corrects.append(p)
        pu = sum(1 for x in upper if x["student_answers"].get(str(q)) == right) / len(upper)
        pl = sum(1 for x in lower if x["student_answers"].get(str(q)) == right) / len(lower)
        d = (pu - pl) if enough else None
        wrong_best = max((c for c in chs if c != right), key=lambda c: cnt[c])
        flags = []
        if enough:
            # 정답키/문항 오류 의심: 실력 상위 27% 집단에서도 특정 오답이 정답보다 많고(40% 이상) 선택됨
            # (전체 비율만 보면 어려운 문항에서 우연히 걸리므로, 정답을 알 만한 상위 집단 기준으로 판단)
            cu = {c: sum(1 for x in upper if x["student_answers"].get(str(q)) == c) for c in chs}
            wu = max((c for c in chs if c != right), key=lambda c: cu[c])
            if cu[wu] > cu[right] and cu[wu] / len(upper) >= 0.4:
                flags.append("key")
            if p < 0.3:
                flags.append("hard")
            if p > 0.95:
                flags.append("easy")
            if d is not None and 0.05 < p < 0.95:
                if d < 0:
                    flags.append("revdisc")
                elif d < 0.2:
                    flags.append("lowdisc")
        if (cnt["무표기"] + cnt["이중"]) / n >= 0.1 and enough:
            flags.append("marking")
        rows.append({
            "q": q, "alt_q": exam_mod.other_q(exam, q), "subject": subj[exam_mod.subject_index(exam, q)][0],
            "subject_name": subj[exam_mod.subject_index(exam, q)][1], "answer": int(right),
            "correct_rate": _r(p * 100), "wrong_rate": _r((1 - p) * 100),
            "choices": {c: _r(cnt[c] / n * 100) for c in cnt},
            "top_wrong": int(wrong_best), "top_wrong_rate": _r(cnt[wrong_best] / n * 100),
            "disc": _r(d, 2), "flags": flags,
        })
    # KR-20 신뢰도 (문항이 모두 정오 채점일 때의 내적 일관성)
    kr20 = None
    if enough:
        totals = [sum(1 for q in range(1, n_q + 1) if x["student_answers"].get(str(q)) == str(key[q - 1])) for x in g]
        var = _sd(totals) ** 2
        if var > 0:
            sum_pq = sum(p * (1 - p) for p in corrects)
            kr20 = _r(n_q / (n_q - 1) * (1 - sum_pq / var), 2) if n_q > 1 else None
    return {"label": label, "form": form, "n": n, "enough": enough, "kr20": kr20, "items": rows}
