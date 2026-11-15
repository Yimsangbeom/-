"""A형 <-> B형 문항 대응 규칙.
이 시험은 각 과목(20문항) 안에서 1~10번 블록과 11~20번 블록을 맞바꿔 A형/B형을 만들었다.
  A형 1~10번 = B형 11~20번,  A형 11~20번 = B형 1~10번 (과목마다 동일). 선지(1~5번) 순서는 같다.
이 변환은 양방향으로 똑같이 적용된다(두 번 하면 원래 번호)."""


def other_q(q):
    """한 유형의 문항 번호(1~100)에 대응하는 다른 유형의 문항 번호."""
    s, i = divmod(q - 1, 20)
    return s * 20 + (i + 10) % 20 + 1


def derive_key(key):
    """한 유형의 정답(100개)으로 다른 유형의 정답을 만든다."""
    return [int(key[other_q(q) - 1]) for q in range(1, 101)]


def key_conflicts(key_a, key_b):
    """A형 번호 기준으로, A형 정답과 B형 정답이 서로 다른 문항 번호 목록."""
    return [q for q in range(1, 101) if int(key_a[q - 1]) != int(key_b[other_q(q) - 1])]
