# OMR 카드 자동 채점 (ocr.ehancom.com)

## 서버(D:\OCR_Score)에서 실행
1. 이 저장소 파일을 `D:\OCR_Score`에 덮어쓰기
2. `run.bat` 실행 (venv 생성 → 패키지 설치 → waitress로 80포트 서비스)
3. 브라우저에서 `http://localhost` 확인 후 `http://ocr.ehancom.com`

## 테스트
`python test_synthetic.py` — 합성 답안지로 인식/채점/엑셀/보안 검증

## 데이터
`data/` 폴더에 설정(state.json), 채점결과(results.json), 판독 이미지 저장. 서버 재시작해도 유지됨.

## HTTPS (기존 nginx 사용, 새 포트 개방 없음)
- 채점 서버: `127.0.0.1:5000` (외부 직접 접속 불가) — `run.bat`
- 기존 nginx(80/443)에 `nginx_ocr.conf.example` 의 server 블록을 추가하고 `nginx -t` 통과 후 `nginx -s reload`
- 인증서는 ocr.ehancom.com 이 포함된 것(와일드카드 또는 신규 발급) 사용
