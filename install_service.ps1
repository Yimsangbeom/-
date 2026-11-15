# 서버를 '항상 켜져 있게' 등록한다 (관리자 PowerShell 에서 한 번 실행, 다시 실행해도 안전):
#   cd D:\OCR_Score_app ; .\install_service.ps1
# - OCR_Score_Server : 부팅 때 자동 시작(SYSTEM 계정), 실행 시간 제한 없음, 오류로 종료되면 1분 뒤 재시작
# - OCR_Score_Watchdog: 5분마다 상태 확인, 꺼졌거나 응답이 없으면 되살림
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
if (-not (Test-Path "$root\venv\Scripts\python.exe")) { throw "venv 가 없습니다. 먼저 run.bat 으로 환경을 만들거나 python -m venv venv 를 실행하세요." }

$limit = New-TimeSpan -Seconds 0         # 0 = 무제한 (기본값 72시간이면 3일 뒤 서버가 강제 종료됨)
$pri = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest

# 1) 서버
$act = New-ScheduledTaskAction -Execute 'powershell.exe' -WorkingDirectory $root `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$root\run_server.ps1`""
$set = New-ScheduledTaskSettingsSet -ExecutionTimeLimit $limit -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName 'OCR_Score_Server' -Action $act -Trigger (New-ScheduledTaskTrigger -AtStartup) `
    -Principal $pri -Settings $set -Force | Out-Null

# 2) 감시
$act2 = New-ScheduledTaskAction -Execute 'powershell.exe' -WorkingDirectory $root `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$root\healthcheck.ps1`""
$trg2 = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 3650)
$set2 = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 4) -MultipleInstances IgnoreNew -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName 'OCR_Score_Watchdog' -Action $act2 -Trigger $trg2 -Principal $pri -Settings $set2 -Force | Out-Null

# 지금 바로 (재)시작: 5000번 포트를 쓰는 기존 채점 서버만 종료하고 작업으로 다시 띄운다
Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
Start-Sleep -Seconds 2
Start-ScheduledTask -TaskName 'OCR_Score_Server'
Start-Sleep -Seconds 10
$ok = $false
try { $ok = ((Invoke-WebRequest -UseBasicParsing -TimeoutSec 10 'http://127.0.0.1:5000/healthz').StatusCode -eq 200) } catch {}
Write-Host ''
Write-Host ("서버 작업 상태 : " + (Get-ScheduledTask -TaskName 'OCR_Score_Server').State)
Write-Host ("실행 시간 제한 : " + (Get-ScheduledTask -TaskName 'OCR_Score_Server').Settings.ExecutionTimeLimit + "  (PT0S = 무제한)")
Write-Host ("감시 작업 상태 : " + (Get-ScheduledTask -TaskName 'OCR_Score_Watchdog').State)
Write-Host ("서버 응답      : " + $(if ($ok) { '정상 (http://127.0.0.1:5000/healthz)' } else { '아직 응답 없음 - 잠시 후 data\server.log 를 확인하세요' }))
