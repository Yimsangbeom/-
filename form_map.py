"""A형 <-> B형 문항 대응 규칙 (시험 설정 exam.py 의 form_rule 에 따름).
기본 시험(인터넷중독전문상담사): 각 과목(20문항)에서 1~10번 블록과 11~20번 블록을 맞바꿔 B형을 만듦.
  A형 1~10번 = B형 11~20번,  A형 11~20번 = B형 1~10번 (과목마다 동일, 선지 순서는 같음). 변환은 양방향 동일.
exam 인자를 생략하면 기본 시험 설정을 쓴다."""
import exam as _exam


def other_q(q, exam=None):
    return _exam.other_q(exam or _exam.DEFAULT_EXAM, q)


def derive_key(key, exam=None):
    return _exam.derive_key(exam or _exam.DEFAULT_EXAM, key)


def key_conflicts(key_a, key_b, exam=None):
    return _exam.key_conflicts(exam or _exam.DEFAULT_EXAM, key_a, key_b)
