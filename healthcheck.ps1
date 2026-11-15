# 감시 작업(5분마다): 서버가 꺼졌거나 응답이 없으면 되살린다. 정상이면 아무것도 하지 않는다.
$ErrorActionPreference = 'SilentlyContinue'
$root = $PSScriptRoot
$log = Join-Path $root 'data\server.log'
function Log($m) { Add-Content -Path $log -Encoding UTF8 -Value ("[{0}] [감시] {1}" -f (Get-Date -Format 's'), $m) }
function Healthy {
    try { return ((Invoke-WebRequest -UseBasicParsing -TimeoutSec 10 'http://127.0.0.1:5000/healthz').StatusCode -eq 200) } catch { return $false }
}
if (Healthy) { exit 0 }
Start-Sleep -Seconds 20            # 일시적인 지연일 수 있어 한 번 더 확인
if (Healthy) { exit 0 }

$task = Get-ScheduledTask -TaskName 'OCR_Score_Server'
if ($null -eq $task) { Log '작업 OCR_Score_Server 가 없습니다. install_service.ps1 을 다시 실행하세요.'; exit 1 }
if ($task.State -ne 'Running') {
    Log '서버 작업이 실행 중이 아니어서 시작합니다.'
    Start-ScheduledTask -TaskName 'OCR_Score_Server'
    exit 0
}
# 작업은 실행 중인데 응답이 없음(멈춤): 5000번 포트의 프로세스를 종료하면 감독자(run_server.ps1)가 5초 뒤 다시 띄운다
$conns = Get-NetTCPConnection -LocalPort 5000 -State Listen
if ($conns) {
    Log '서버가 응답하지 않아 프로세스를 종료합니다(자동으로 다시 시작됨).'
    $conns | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
} else {
    Log '서버가 포트를 열지 못한 채 멈춰 있어 작업을 다시 시작합니다.'
    Stop-ScheduledTask -TaskName 'OCR_Score_Server'
    Start-Sleep -Seconds 3
    Start-ScheduledTask -TaskName 'OCR_Score_Server'
}
