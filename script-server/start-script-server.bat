@echo off
rem ==========================================================================
rem  Start / restart the script-server executor (script-server 3.2.0).
rem
rem  The Windows scheduled task "WorkflowPlatform-ScriptServer" runs
rem  python deploy\serve.py directly; the task process IS the uvicorn server.
rem
rem  -nopause : non-interactive (autostart / scripts); do not wait for key press
rem  Keep this file ASCII-only: cmd parses .bat files in the OEM code page.
rem ==========================================================================
setlocal EnableExtensions
chcp 65001 >nul
title Script-Server (scheduled task)
cd /d "%~dp0"

set "TASK=WorkflowPlatform-ScriptServer"

echo [1/4] Checking scheduled task and Python ...
where python >nul 2>&1
if errorlevel 1 goto :no_python
schtasks /Query /TN "%TASK%" >nul 2>&1
if errorlevel 1 goto :no_task

echo [2/4] Stopping task "%TASK%" (if running) ...
schtasks /End /TN "%TASK%" >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ok=$false; for($i=0;$i -lt 30;$i++){ if(-not (Get-NetTCPConnection -LocalPort 9000 -State Listen -ErrorAction SilentlyContinue)){ $ok=$true; break }; Start-Sleep -Seconds 1 }; if(-not $ok){ exit 1 }"
if errorlevel 1 goto :port_busy

echo [3/4] Starting task ...
schtasks /Run /TN "%TASK%" >nul 2>&1
if errorlevel 1 goto :task_start_failed

echo [4/4] Waiting for http://127.0.0.1:9000/health ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ok=$false; $last=''; for($i=0;$i -lt 45;$i++){ try { $r=Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:9000/health' -TimeoutSec 2; if($r.StatusCode -eq 200){ $ok=$true; $last=[Text.Encoding]::UTF8.GetString($r.RawContentStream.ToArray()); break } } catch { $last=$_.Exception.Message }; Start-Sleep -Seconds 2 }; if($ok){ Write-Host ('OK ' + $last); exit 0 } else { Write-Host ('[WARN] /health not 200 in 90s: ' + $last); exit 1 }"
if errorlevel 1 goto :health_failed

echo [OK] executor is up (scheduled task "%TASK%").
if /i not "%~1"=="-nopause" pause
exit /b 0

:no_python
echo [ERROR] Python not found in PATH.
echo         Install Python 3.10+ (3.12 tested) and tick "Add Python to PATH".
goto :fail

:no_task
echo [ERROR] scheduled task "%TASK%" not found.
echo         Register it once, from an elevated PowerShell:
echo           powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy\cutover-to-uvicorn-task.ps1"
goto :fail

:port_busy
echo [ERROR] port 9000 is still in LISTEN state after stopping the task.
echo         Something else owns 9000 (stray process?). Check:
echo           netstat -ano ^| findstr :9000
echo         Then, from an elevated prompt:  taskkill /F /T /PID ^<pid^>
goto :fail

:task_start_failed
echo [ERROR] schtasks /Run failed.
echo         A normal (non-elevated) window can run/stop this S4U task; if it is
echo         denied, run this .bat from an elevated prompt once.
goto :fail

:health_failed
echo [ERROR] task started but /health did not return 200.
echo         Diagnostics:
echo           powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy\ops-http.ps1" status
echo           powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy\ops-http.ps1" logs
goto :fail

:fail
if /i not "%~1"=="-nopause" pause
exit /b 1
