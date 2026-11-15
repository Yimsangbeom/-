# OMR 카드 자동 채점 (ocr.ehancom.com)

## 서버(D:\OCR_Score)에서 실행
1. 이 저장소 파일을 `D:\OCR_Score`에 덮어쓰기
2. `run.bat` 실행 (venv 생성 → 패키지 설치 → waitress로 80포트 서비스)
3. 브라우저에서 `http://localhost` 확인 후 `http://ocr.ehancom.com`

## 테스트
`python test_synthetic.py` — 합성 답안지로 인식/채점/엑셀/보안 검증

## 데이터
`data/` 폴더에 설정(state.json), 채점결과(results.json), 판독 이미지 저장. 서버 재시작해도 유지됨.

## HTTPS 적용 (ocr.ehancom.com → 58.75.21.228)
구성: 인터넷 → Caddy(80/443, 인증서 자동) → Flask(127.0.0.1:8000)
1. 서버에서 80/443을 쓰는 프로그램(IIS 등)이 없어야 합니다. (`netstat -ano | findstr ":80 :443"`)
2. Windows 방화벽 인바운드 TCP 80, 443 허용 + (공유기/방화벽 뒤라면) 58.75.21.228 로 80/443 포트포워딩
3. https://caddyserver.com/download 에서 Windows amd64 `caddy.exe` 를 이 폴더에 저장
4. 창 두 개: `run.bat`(채점 서버) , `run_https.bat`(Caddy). 첫 실행 시 인증서가 자동 발급됩니다.
5. `https://ocr.ehancom.com` 접속 확인. 상시 운영은 작업 스케줄러(시스템 시작 시)로 두 배치를 등록하세요.
외부 접속 없이 로컬만 쓰려면 `set HOST=0.0.0.0` / `set PORT=80` 후 `python app.py`.
