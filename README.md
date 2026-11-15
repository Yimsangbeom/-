# OMR 카드 자동 채점 (ocr.ehancom.com)

## 서버(D:\OCR_Score)에서 실행
1. 이 저장소 파일을 `D:\OCR_Score`에 덮어쓰기
2. `run.bat` 실행 (venv 생성 → 패키지 설치 → waitress로 80포트 서비스)
3. 브라우저에서 `http://localhost` 확인 후 `http://ocr.ehancom.com`

## 아이디 / 비밀번호
- **최초 설정**: 서버 PC 브라우저에서 `http://127.0.0.1:5000` 접속 → 아이디(기본 `hancom`)와 비밀번호 입력. (외부 접속으로는 설정 불가 → 남이 먼저 선점할 수 없음)
- **변경**: 로그인 후 화면 오른쪽 위 [아이디/비밀번호 변경] (현재 비밀번호 필요, 변경 시 다른 기기는 로그아웃)
- **잊어버렸을 때**: 서버에서 `reset_password.bat` 실행(= `data\auth.json` 삭제, 채점 결과/정답은 유지) → 위 최초 설정 다시
- 5회 연속 실패 시 해당 IP 5분 잠금, 로그인 12시간 유지. 비밀번호는 해시로만 저장.

## 자동 시작 (재부팅 후에도)
`schtasks /create /tn OCR_Score_Server /tr "D:\OCR_Score_app\start_server.bat" /sc onstart /ru SYSTEM /rl highest /f`

## 배점 / 합격 / 과락 판정
- 5과목 x 20문항, **문항당 5점 -> 과목 만점 100점, 총점 만점 500점** (홈 화면에서 변경 가능)
- **과락**: 한 과목이라도 과락 점수(기본 40점) **미만**이면 과락 (평균이 높아도 불합격). 0 이면 사용 안 함
- **합격**: 과락이 없고 **평균(총점/5)이 합격 평균(기본 60점) 이상**. 미달이면 불합격
- 설정(문항당 배점/합격 평균/과락 점수)을 바꾸면 이미 채점된 결과도 즉시 다시 계산/판정 (엑셀에 과목별 점수, 총점, 평균, 판정, 과락과목 포함)
- 이전 버전 설정이 남아 있으면 처음 시작할 때 자동 이전 (합격 총점 300 -> 합격 평균 60)

## A형 / B형 구성과 정답 연결
- 각 과목(20문항)에서 **1~10번 블록과 11~20번 블록을 맞바꿔** B형을 만듦: A형 1~10번 = B형 11~20번, A형 11~20번 = B형 1~10번 (선지 순서는 동일). 규칙: `form_map.py`
- 한 유형의 정답(정답 답안지/엑셀)만 등록하면 다른 유형 정답은 자동 생성. 둘 다 등록하면 서로 일치하는지 검사해 어긋난 문항을 경고(홈 화면/통계 화면)

## 결과 확인 / 통계 (3번 탭)
- 개별 결과 표 + 통계: 요약(합격률/과락률/평균/표준편차/KR-20 신뢰도), 점수 분포, 과목별(평균/정답률/난이도/과락), 유형별 비교, 블록 정답률 비교, **문항 분석**(정답률/오답률/선지별 선택 비율/변별도/검토 표시)
- 문항 분석은 A·B 합산 **통합**(A형 번호 기준, 같은 문제끼리 합산) + 유형별로 제공. 유형별 응시자 10명 미만이면 변별도·검토는 보류(참고용)
- 실제 응시자 / 테스트 결과를 구분해서 집계. 엑셀 다운로드에 결과 + 통계 시트 포함

## 테스트용 가상 답안지
- 홈 화면 [▶ 테스트 실행]: `static/samples/` 의 가상 스캔 답안지 4장을 채점하고 정답 기록과 대조(🧪 표시, 실제 정답 설정은 변경 안 됨), [테스트 결과만 삭제]로 정리
- 재생성: `python tools/make_samples.py <양식.pdf> static/samples`

## 테스트
- `python test_db.py` (저장소: 영속성/이전/복사, `PG_TEST_URL` 지정 시 PostgreSQL 포함), `python test_stats.py` (통계 계산: 가상 응시자 시뮬레이션), `python test_features.py` (내장 샘플: 테스트 실행/판정/과락/삭제), `python test_samples.py <양식.pdf>` (스캔 열화 샘플 정확도)
`python test_synthetic.py` — 합성 답안지로 인식/채점/엑셀/보안 검증

## 데이터 저장 (SQL 데이터베이스)
- 기본: **SQLite 파일 `data/omr.db`** (별도 설치/계정 불필요, 서버를 재시작해도 유지). 판독 이미지는 `data/results/` 폴더, 접속 계정은 `data/auth.json`(해시)
- 이전 버전의 `state.json`/`results.json` 은 처음 시작할 때 자동으로 DB 로 옮기고 `.migrated` 로 이름을 바꿈
- 나중에 PostgreSQL 등으로 바꾸려면: `venv\Scripts\python set_db.py` (서버 주소/DB/ID/비밀번호를 이 창에서만 입력 -> `data/db.json`, 기존 데이터 복사 선택 가능) 후 서버 재시작. 되돌리기도 같은 메뉴(3번)
- 홈 화면 [데이터 관리]에서 저장 위치/건수 확인, 백업 엑셀 다운로드, 테스트 결과만 삭제, 채점 결과 전체 삭제("삭제" 입력 확인)

## 데이터
`data/` 폴더에 설정(state.json), 채점결과(results.json), 판독 이미지 저장. 서버 재시작해도 유지됨.

## HTTPS (기존 nginx 사용, 새 포트 개방 없음)
- 채점 서버: `127.0.0.1:5000` (외부 직접 접속 불가) — `run.bat`
- 기존 nginx(80/443)에 `nginx_ocr.conf.example` 의 server 블록을 추가하고 `nginx -t` 통과 후 `nginx -s reload`
- 인증서는 ocr.ehancom.com 이 포함된 것(와일드카드 또는 신규 발급) 사용

## 폐쇄망 지원 (외부 사이트 사용 안 함)
화면에 필요한 모든 파일이 서버 안에 있습니다. (인터넷 없이도 동작)
- 글꼴: `static/fonts/noto-sans-kr/` (Noto Sans KR 400/500/700, SIL OFL 라이선스)
- 스타일: `static/vendor/tailwind.css` (미리 빌드), 엑셀: `static/vendor/xlsx.full.min.js`
- `index.html`/`login.html` 의 클래스를 바꾸면 스타일을 다시 만들어야 합니다 (개발 PC에서 Node 필요):
  `npm i -D tailwindcss@3.4.17` → `npx tailwindcss -c tailwind.config.js -i tailwind.input.css -o static/vendor/tailwind.css --minify`
