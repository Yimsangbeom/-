@echo off
chcp 65001 >nul
cd /d %~dp0
echo 비밀번호를 초기화합니다. (data\auth.json 삭제 - 채점 결과/정답 설정은 유지됨)
echo 이후 서버 PC 브라우저에서 http://127.0.0.1:5000 접속 -> 비밀번호 새로 설정
pause
del /q data\auth.json
echo 완료
pause
