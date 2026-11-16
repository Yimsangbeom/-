"""시험 구조 정의(시험 설정): 시험명, 과목 구성, 선택지 수, 수험번호 자릿수, 문제 유형(A/B)과 유형 간 대응 규칙.

답안지 '양식(좌표)'은 별도(layouts.py)이며, 시험 설정은 그 양식의 용량(문항 수/선택지/수험번호/유형 표시칸) 안에서만 정할 수 있다.
"""
import copy

LETTERS = "ABCDEFGHIJ"          # 과목 코드(순서대로). 과목은 최대 10개
FORM_RULES = {
    "none": "없음 (유형마다 문제가 다름 / 유형이 하나)",
    "half_swap": "각 과목의 앞 절반과 뒤 절반 블록을 맞바꿈 (예: 20문항이면 1~10번 ↔ 11~20번)",
    "custom": "문제 배치가 서로 다름 (A/B 문제지를 등록해 분석한 대응표 사용)",
}

DEFAULT_EXAM = {
    "name": "인터넷중독전문상담사 자격검정",
    "title": "2026년 (제12회) 인터넷중독전문상담사 자격검정 답안지",
    "layout": "nia_a4_100",
    "subjects": [
        {"name": "인터넷중독의 이해", "short": "이해", "count": 20},
        {"name": "인터넷중독의 진단과 평가", "short": "진단", "count": 20},
        {"name": "인터넷중독 상담기법", "short": "기법", "count": 20},
        {"name": "인터넷중독 상담의 이론과 실제", "short": "이론", "count": 20},
        {"name": "인터넷중독의 정책과 법", "short": "정책", "count": 20},
    ],
    "choices": 5,
    "id_digits": 9,
    "forms": ["A", "B"],
    "form_rule": "half_swap",
}


def default_exam():
    return copy.deepcopy(DEFAULT_EXAM)


def normalize(exam, layout=None):
    """입력값을 검증하고 정리한 새 dict 를 돌려준다. 잘못된 값이면 ValueError(사용자에게 보여줄 한글 메시지)."""
    e = {}
    e["name"] = str(exam.get("name", "")).strip()
    if not e["name"]:
        raise ValueError("시험명을 입력하세요.")
    e["title"] = str(exam.get("title", "")).strip() or e["name"]
    e["layout"] = str(exam.get("layout") or DEFAULT_EXAM["layout"])
    subs = exam.get("subjects") or []
    if not 1 <= len(subs) <= len(LETTERS):
        raise ValueError(f"과목은 1~{len(LETTERS)}개로 구성하세요.")
    e["subjects"] = []
    for i, s in enumerate(subs):
        name = str(s.get("name", "")).strip()
        if not name:
            raise ValueError(f"{i + 1}번째 과목 이름을 입력하세요.")
        try:
            cnt = int(s.get("count"))
        except (TypeError, ValueError):
            raise ValueError(f"'{name}' 과목의 문항 수를 숫자로 입력하세요.")
        if cnt < 1:
            raise ValueError(f"'{name}' 과목의 문항 수는 1 이상이어야 합니다.")
        short = str(s.get("short", "")).strip() or name[:4]
        e["subjects"].append({"name": name, "short": short, "count": cnt})
    try:
        e["choices"] = int(exam.get("choices", 5))
        e["id_digits"] = int(exam.get("id_digits", 9))
    except (TypeError, ValueError):
        raise ValueError("선택지 수와 수험번호 자릿수는 숫자로 입력하세요.")
    if not 2 <= e["choices"] <= 5:
        raise ValueError("선택지 수는 2~5개여야 합니다.")
    if e["id_digits"] < 1:
        raise ValueError("수험번호 자릿수는 1 이상이어야 합니다.")
    forms = [str(f).strip().upper() for f in (exam.get("forms") or []) if str(f).strip()]
    if not forms or len(set(forms)) != len(forms):
        raise ValueError("문제 유형을 하나 이상 선택하세요.")
    e["forms"] = forms
    e["form_rule"] = exam.get("form_rule", "none")
    if e["form_rule"] not in FORM_RULES:
        raise ValueError("문제 유형 간 관계 값이 올바르지 않습니다.")
    if e["form_rule"] == "half_swap":
        if len(forms) != 2:
            raise ValueError("'앞뒤 절반 교체'는 문제 유형이 2개(A형/B형)일 때만 쓸 수 있습니다.")
        odd = [s["name"] for s in e["subjects"] if s["count"] % 2]
        if odd:
            raise ValueError("'앞뒤 절반 교체'를 쓰려면 각 과목 문항 수가 짝수여야 합니다: " + ", ".join(odd))
    if e["form_rule"] == "custom":
        if len(forms) != 2:
            raise ValueError("문제지 대응표는 문제 유형이 2개(A형/B형)일 때만 쓸 수 있습니다.")
        e["form_map"] = normalize_form_map(exam.get("form_map"), total_questions(e), e["choices"])
    if layout is not None:       # 답안지 양식의 용량 검사
        if total_questions(e) > layout["capacity"]:
            raise ValueError(f"총 문항 수 {total_questions(e)}개가 답안지 양식의 용량 {layout['capacity']}개를 넘습니다.")
        if e["choices"] > layout["choices_max"]:
            raise ValueError(f"이 답안지 양식은 선택지를 최대 {layout['choices_max']}개까지 지원합니다.")
        if e["id_digits"] > layout["id_digits_max"]:
            raise ValueError(f"이 답안지 양식은 수험번호를 최대 {layout['id_digits_max']}자리까지 지원합니다.")
        bad = [f for f in forms if f not in layout["forms"]]
        if bad:
            raise ValueError(f"이 답안지 양식에 없는 문제 유형입니다: {', '.join(bad)} (사용 가능: {', '.join(layout['forms'])})")
    return e


def normalize_form_map(fm, n_q, n_ch):
    """대응표 [[첫 유형 번호, 둘째 유형 번호, [선택지 대응]], ...] 를 검증해 정리한다.
    선택지 대응 perm: 첫 유형의 선택지 i+1 이 둘째 유형에서는 perm[i] 번 선택지."""
    if not fm:
        raise ValueError("문제 대응표가 없습니다. 홈 화면의 '시험지(문제지) 등록'에서 A형/B형 문제지를 분석해 대응표를 먼저 저장하세요.")
    out, seen_a, seen_b = [], set(), set()
    for row in fm:
        try:
            a, b = int(row[0]), int(row[1])
            perm = [int(x) for x in (row[2] if len(row) > 2 and row[2] else range(1, n_ch + 1))]
        except (TypeError, ValueError, IndexError):
            raise ValueError("문제 대응표 형식이 올바르지 않습니다.")
        if not (1 <= a <= n_q and 1 <= b <= n_q):
            raise ValueError(f"문제 번호는 1~{n_q} 사이여야 합니다: {a} ↔ {b}")
        if sorted(perm) != list(range(1, n_ch + 1)):
            raise ValueError(f"A형 {a}번의 선택지 대응이 올바르지 않습니다 (1~{n_ch}를 한 번씩).")
        if a in seen_a or b in seen_b:
            raise ValueError(f"한 문제가 두 번 대응되었습니다 (A형 {a}번 또는 B형 {b}번).")
        seen_a.add(a)
        seen_b.add(b)
        out.append([a, b, perm])
    if len(out) != n_q:
        raise ValueError(f"대응표에 {n_q}문항이 모두 있어야 합니다 (현재 {len(out)}개).")
    return sorted(out)


def is_paired(exam):
    """두 유형의 문제가 서로 대응되는(같은 문제를 다른 위치/순서로 푸는) 시험인지."""
    return exam.get("form_rule") in ("half_swap", "custom") and len(exam.get("forms", [])) == 2


def _custom_maps(exam):
    a2b, b2a = {}, {}
    for a, b, perm in exam.get("form_map") or []:
        a2b[a] = (b, perm)
        b2a[b] = (a, perm)
    return a2b, b2a


def total_questions(exam):
    return sum(s["count"] for s in exam["subjects"])


def n_subjects(exam):
    return len(exam["subjects"])


def codes(exam):
    return list(LETTERS[:len(exam["subjects"])])


def subject_ranges(exam):
    """[(코드, 이름, 약칭, 시작문항, 끝문항)] (1부터, 끝 포함)"""
    out, start = [], 1
    for i, s in enumerate(exam["subjects"]):
        out.append((LETTERS[i], s["name"], s["short"], start, start + s["count"] - 1))
        start += s["count"]
    return out


def subject_index(exam, q):
    """문항 번호(1부터)가 속한 과목의 순번(0부터)."""
    for i, (_, _, _, a, b) in enumerate(subject_ranges(exam)):
        if a <= q <= b:
            return i
    return len(exam["subjects"]) - 1


def other_q(exam, q, frm=None):
    """한 유형의 문항 번호에 대응하는 다른 유형의 문항 번호. 규칙이 없으면 같은 번호.
    frm: q 가 어느 유형의 번호인지(대응표 방식은 방향이 있다. 생략하면 첫 번째 유형)."""
    if exam["form_rule"] == "custom":
        a2b, b2a = _custom_maps(exam)
        if frm is None or frm == exam["forms"][0]:
            return a2b.get(q, (q,))[0]
        return b2a.get(q, (q,))[0]
    if exam["form_rule"] != "half_swap":
        return q
    for _, _, _, a, b in subject_ranges(exam):
        if a <= q <= b:
            n = b - a + 1
            return a + ((q - a) + n // 2) % n
    return q


def to_first_choice(exam, q_first, choice_second):
    """둘째 유형 응시자의 선택(번호)을, 첫 유형 문항 q_first 기준 선택지 번호로 바꾼다(선택지 순서가 다른 경우). 알 수 없으면 None."""
    if exam["form_rule"] != "custom":
        return choice_second
    a2b, _ = _custom_maps(exam)
    perm = a2b.get(q_first, (None, []))[1]
    return perm.index(choice_second) + 1 if choice_second in perm else None


def derive_key(exam, key, from_form=None):
    """한 유형의 정답으로 다른 유형의 정답을 만든다(대응 규칙이 있을 때). from_form: key 가 어느 유형의 정답인지(생략: 첫 유형)."""
    n = total_questions(exam)
    if exam["form_rule"] == "custom":
        a2b, b2a = _custom_maps(exam)
        if from_form is None or from_form == exam["forms"][0]:      # 첫 유형 정답 -> 둘째 유형 정답
            out = []
            for qb in range(1, n + 1):
                qa = b2a[qb][0]
                out.append(a2b[qa][1][int(key[qa - 1]) - 1])
            return out
        out = []                                                    # 둘째 유형 정답 -> 첫 유형 정답
        for qa in range(1, n + 1):
            qb, perm = a2b[qa]
            out.append(perm.index(int(key[qb - 1])) + 1)
        return out
    return [int(key[other_q(exam, q) - 1]) for q in range(1, n + 1)]


def key_conflicts(exam, key_a, key_b):
    """첫 번째 유형 번호 기준으로 두 정답이 서로 다른 문항 번호."""
    n = total_questions(exam)
    if exam["form_rule"] == "custom":
        a2b, _ = _custom_maps(exam)
        return [q for q in range(1, n + 1) if a2b[q][1][int(key_a[q - 1]) - 1] != int(key_b[a2b[q][0] - 1])]
    return [q for q in range(1, n + 1) if int(key_a[q - 1]) != int(key_b[other_q(exam, q) - 1])]


def default_keys(exam):
    """등록 전 임시 정답(유형마다 다른 패턴)."""
    n = total_questions(exam)
    return {f: [((i + 2 * k) % exam["choices"]) + 1 for i in range(n)] for k, f in enumerate(exam["forms"])}


def structure_signature(exam):
    """채점 결과 해석에 영향을 주는 구조 (이 값이 바뀌면 이미 채점된 결과가 의미를 잃는다)."""
    # 유형 간 대응 규칙/대응표는 포함하지 않는다: 바꿔도 이미 채점된 답은 그대로이고 통계만 다시 계산되기 때문
    return (tuple(s["count"] for s in exam["subjects"]), exam["choices"], exam["id_digits"],
            tuple(exam["forms"]), exam["layout"])


def is_default_structure(exam):
    return structure_signature(exam) == structure_signature(DEFAULT_EXAM)
