@echo off
rem 작업 스케줄러(부팅 시, SYSTEM)용 - 창 없이 실행, 로그는 data\server.log
cd /d %~dp0
if not exist data mkdir data
set PORT=5000
set OMR_SECURE_COOKIE=1
venv\Scripts\python.exe app.py >> data\server.log 2>&1
