@echo off
rem ==========================================================================
rem  Restart the executor after changing .env.
rem
rem  deploy\serve.py re-reads .env on every start, so a plain restart is enough.
rem
rem  usage:  env-change-restart.bat          restart, then show effective config
rem          env-change-restart.bat edit     open notepad first, then restart
rem          env-change-restart.bat nopause  non-interactive (no key wait)
rem  exit:   0 = ok, 1 = restart/ready failed, 2 = concurrency mismatch
rem  Keep this file ASCII-only: cmd parses .bat files in the OEM code page.
rem ==========================================================================
setlocal EnableExtensions
chcp 65001 >nul
title Script-Server .env restart
cd /d "%~dp0"

if /i "%~1"=="edit" (
    echo Opening notepad for %~dp0.env - save and close it to continue ...
    start /wait notepad "%~dp0.env"
)

echo [1/2] Restarting executor via scheduled task ...
call "%~dp0start-script-server.bat" -nopause
if errorlevel 1 goto :fail

echo [2/2] Effective configuration (token masked) ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$root=(Get-Location).Path; $cfg=@{}; foreach($l in Get-Content -LiteralPath (Join-Path $root '.env') -Encoding UTF8){ $s=$l.Trim(); if($s -match '^([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$'){ $cfg[$Matches[1]]=$Matches[2].Trim() } }; $t=$cfg['WF_SCRIPT_TOKEN']; if($t){ Write-Host ('token=' + $t.Substring(0,[Math]::Min(8,$t.Length)) + '...' + $t.Substring([Math]::Max(0,$t.Length-4)) + ' len=' + $t.Length) } else { Write-Host 'token=MISSING' }; Write-Host ('WF_MAX_CONCURRENCY=' + $cfg['WF_MAX_CONCURRENCY']); $hdr=@{'X-Script-Token'=$t}; try { $r=Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:9000/health/ready' -Headers $hdr -TimeoutSec 10; $j=[Text.Encoding]::UTF8.GetString($r.RawContentStream.ToArray()) | ConvertFrom-Json; Write-Host ('ready status=' + $j.status + ' concurrency.limit=' + $j.checks.concurrency.limit); if($cfg['WF_MAX_CONCURRENCY'] -and [int]$cfg['WF_MAX_CONCURRENCY'] -ne [int]$j.checks.concurrency.limit){ Write-Host '[FAIL] concurrency mismatch: .env not applied'; exit 2 }; Write-Host 'OK: .env applied' } catch { Write-Host ('[FAIL] /health/ready: ' + $_.Exception.Message); exit 1 }"
if errorlevel 2 goto :fail_mismatch
if errorlevel 1 goto :fail

echo [OK] configuration applied.
if /i "%~1"=="nopause" exit /b 0
pause
exit /b 0

:fail_mismatch
echo [FAIL] .env was not applied (concurrency mismatch) - check .env syntax and logs.
goto :fail

:fail
if /i not "%~1"=="nopause" pause
exit /b 1
