@echo off
rem 서버 실행기(run_server.ps1)를 실행합니다. 서버가 종료되면 자동으로 다시 시작합니다. 자동 시작 등록은 install_service.ps1 참고
cd /d %~dp0
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_server.ps1"
