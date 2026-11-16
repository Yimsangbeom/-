"""A형 <-> B형 문항 대응 규칙 (시험 설정 exam.py 의 form_rule 에 따름).
- half_swap: 각 과목에서 앞 절반/뒤 절반 블록을 맞바꿈 (A 1~10 = B 11~20 ...)
- custom: 문제지 분석(paper_match.py)으로 만든 대응표(exam['form_map']) 사용. 선택지 순서가 다를 수도 있다.
exam 인자를 생략하면 기본 시험 설정을 쓴다."""
import exam as _exam


def other_q(q, exam=None, frm=None):
    return _exam.other_q(exam or _exam.DEFAULT_EXAM, q, frm)


def derive_key(key, exam=None, from_form=None):
    return _exam.derive_key(exam or _exam.DEFAULT_EXAM, key, from_form)


def key_conflicts(key_a, key_b, exam=None):
    return _exam.key_conflicts(exam or _exam.DEFAULT_EXAM, key_a, key_b)
