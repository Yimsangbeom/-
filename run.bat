@echo off
chcp 65001 >nul
cd /d %~dp0
if not exist venv ( python -m venv venv )
call venv\Scripts\activate
pip install -q -r requirements.txt
set PORT=5000
set OMR_SECURE_COOKIE=1
python app.py
pause
