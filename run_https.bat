@echo off
chcp 65001 >nul
cd /d %~dp0
if not exist caddy.exe (
  echo caddy.exe 가 없습니다. https://caddyserver.com/download 에서 Windows amd64 를 받아 이 폴더에 caddy.exe 로 저장하세요.
  pause & exit /b 1
)
caddy run --config Caddyfile
