# 채점 서버 실행기(감독자): 서버가 어떤 이유로든 종료되면 5초 뒤 자동으로 다시 시작한다.
# 작업 스케줄러(OCR_Score_Server)가 부팅 때 이 스크립트를 실행한다. 직접 실행해도 된다.
$ErrorActionPreference = 'Continue'
Set-Location $PSScriptRoot
$env:PORT = '5000'                # 내부 포트(nginx 가 이 포트로 전달). 외부에는 열지 않는다.
$env:OMR_SECURE_COOKIE = '1'
$env:PYTHONIOENCODING = 'utf-8'   # 로그 한글이 깨지지 않게
New-Item -ItemType Directory -Force -Path data | Out-Null
$log = Join-Path $PSScriptRoot 'data\server.log'
while ($true) {
    # 로그가 20MB 를 넘으면 이전 로그(server.log.1)로 보관하고 새로 시작
    if ((Test-Path $log) -and ((Get-Item $log).Length -gt 20MB)) { Move-Item $log "$log.1" -Force }
    Add-Content -Path $log -Encoding UTF8 -Value ("[{0}] 서버 시작" -f (Get-Date -Format 's'))
    cmd /c ".\venv\Scripts\python.exe app.py >> data\server.log 2>&1"
    Add-Content -Path $log -Encoding UTF8 -Value ("[{0}] 서버가 종료됨 (종료 코드 {1}). 5초 후 자동으로 다시 시작합니다." -f (Get-Date -Format 's'), $LASTEXITCODE)
    Start-Sleep -Seconds 5
}
