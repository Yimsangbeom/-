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

## 합격 / 과락 판정
- 홈 화면의 **합격 총점**(이상이면 합격)과 **과락 점수**(과목별 이 점수 미만이면 과락·불합격, 0=사용 안 함, 기본 8점=20점의 40%)로 판정
- 과락이 불합격보다 우선하며, 설정을 바꾸면 이미 채점된 결과도 즉시 다시 판정됨 (엑셀에도 판정/과락과목 포함)

## 테스트용 가상 답안지
- 홈 화면 [▶ 테스트 실행]: `static/samples/` 의 가상 스캔 답안지 4장을 채점하고 정답 기록과 대조(🧪 표시, 실제 정답 설정은 변경 안 됨), [테스트 결과만 삭제]로 정리
- 재생성: `python tools/make_samples.py <양식.pdf> static/samples`

## 테스트
- `python test_features.py` (내장 샘플: 테스트 실행/판정/과락/삭제), `python test_samples.py <양식.pdf>` (스캔 열화 샘플 정확도)
`python test_synthetic.py` — 합성 답안지로 인식/채점/엑셀/보안 검증

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
