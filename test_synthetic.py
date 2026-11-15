"""합성 답안지로 엔진/API 동작을 검증한다. (실제 스캔본 정확도 검증이 아님)"""
import os, sys, tempfile, random
import cv2, numpy as np
from omr_engine import DEFAULT_CONFIG as C

def make_sheet(exam_type, sid, answers, blanks=(), doubles=()):
    w, h = C['canonical_size']
    img = np.full((h, w, 3), 255, np.uint8)
    ax, ay, aw, ah = C['regions']['answer_box']; cv2.rectangle(img, (ax, ay), (ax+aw, ay+ah), (0,0,0), 3)
    ix, iy, iw, ih = C['regions']['id_box']; cv2.rectangle(img, (ix, iy), (ix+iw, iy+ih), (0,0,0), 3)
    r = C['bubble_radius']
    def mark(x, y): cv2.circle(img, (int(x), int(y)), r-2, (0,0,0), -1)
    mark(*C['type_bubbles'][exam_type])
    for i, ch in enumerate(sid): mark(C['id_grid']['x_cols'][i], C['id_grid']['y_rows'][int(ch)])
    xs, ys = C['answer_grid']['x_cols'], C['answer_grid']['y_rows']
    for q in range(1, 101):
        s, row = divmod(q-1, 20)
        if q in blanks: continue
        mark(xs[s*5 + answers[q-1]-1], ys[row])
        if q in doubles: mark(xs[s*5 + answers[q-1] % 5], ys[row])
    return img

if __name__ == '__main__':
    random.seed(1)
    os.environ['OMR_DATA_DIR'] = tempfile.mkdtemp()
    import auth
    auth.set_password(os.environ['OMR_DATA_DIR'], 'testpass123')
    from app import app, state
    key = [random.randint(1, 5) for _ in range(100)]
    c = app.test_client()
    # 인증: 미로그인 차단 / 오답 / 로그인
    assert c.get('/').status_code == 302 and c.get('/api/results').status_code == 401
    assert c.get('/results/x.jpg').status_code == 401 and c.get('/app.py').status_code in (302, 401)
    assert c.get('/login').status_code == 200
    assert c.post('/api/login', json={'password': 'wrong'}).status_code == 401
    assert c.post('/api/login', json={'password': 'testpass123'}).status_code == 200
    assert c.get('/').status_code == 200
    print('인증 OK')
    d = tempfile.mkdtemp()
    def put(name, img): p = os.path.join(d, name); cv2.imwrite(p, img); return p

    # 마스터(정답) 등록
    r = c.post('/api/grade/master', data={'file': open(put('master.png', make_sheet('A', '000000000', key)), 'rb')})
    j = r.get_json(); assert j['status'] == 'success' and j['answers'] == key, j
    print('마스터 인식 OK')

    # 학생: 만점 / 5문항 오답 / 무표기2+이중1
    wrong = list(key)
    for q in (1, 2, 3, 4, 5): wrong[q-1] = key[q-1] % 5 + 1
    files = [(open(put(n, im), 'rb'), n) for n, im in [
        ('만점.png', make_sheet('A', '123456789', key)),
        ('오답5.png', make_sheet('A', '987654321', wrong)),
        ('검수.png', make_sheet('A', '111111111', key, blanks=(10, 20), doubles=(30,)))]]
    j = c.post('/api/grade/students', data=dict(files=files)).get_json()
    res = {x['filename']: x for x in j['results']}
    assert res['만점.png']['total_score'] == 100 and res['만점.png']['student_id'] == '123456789' and not res['만점.png']['needs_review'], res['만점.png']
    assert res['오답5.png']['total_score'] == 95 and res['오답5.png']['student_id'] == '987654321'
    assert res['검수.png']['blank_count'] == 2 and res['검수.png']['multi_count'] == 1 and res['검수.png']['needs_review']
    print('채점 OK:', {k: v['total_score'] for k, v in res.items()})

    assert c.get('/api/export/excel').status_code == 200
    assert c.get('/app.py').status_code == 404 and c.get('/omr_engine.py').status_code == 404
    assert c.get(res['만점.png']['vis_url']).status_code == 200
    print('엑셀/보안/미리보기 OK')
    c.delete('/api/results')
    # 로그아웃 후 차단, 5회 실패 시 잠금
    assert c.post('/api/logout').status_code == 200 and c.get('/api/results').status_code == 401
    c2 = app.test_client()
    codes = [c2.post('/api/login', json={'password': 'bad'}).status_code for _ in range(6)]
    assert codes[:5] == [401]*5 and codes[5] == 429, codes
    assert c2.post('/api/login', json={'password': 'testpass123'}).status_code == 429
    print('로그아웃/잠금 OK')
    print('ALL PASS')
