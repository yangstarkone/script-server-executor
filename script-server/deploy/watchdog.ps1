# watchdog.ps1 - keep the script-server executor alive.
#
# WHY THIS EXISTS
#   The 2026-10-26 architecture runs `python deploy\serve.py` directly under the
#   scheduled task "WorkflowPlatform-ScriptServer". Measurements on 2026-10-27
#   showed the task setting "RestartOnFailure" does NOT bring the task back when
#   the server process dies (neither on an external kill nor on a non-zero exit:
#   LastTaskResult stayed 7 / 0xFFFFFFFF and State stayed Ready). A task that is
#   down therefore stays down until someone starts it.
#
#   This watchdog is a second, tiny scheduled task (every minute, S4U, Highest).
#   It only checks "is port 9000 listening?" and, when it is not, starts the main
#   task again. It never kills a running server.
#
# SAFETY
#   Put a file named `.maintenance` in the script-server root to pause the
#   watchdog (e.g. while you intentionally keep the executor stopped).
#   Delete it to re-enable automatic recovery.
#
# Keep this file ASCII-only (Windows PowerShell 5.1 reads BOM-less .ps1 as ANSI).

$ErrorActionPreference = 'SilentlyContinue'

$Root     = Split-Path -Parent $PSScriptRoot        # ...\script-server
$TaskName = 'WorkflowPlatform-ScriptServer'
$Port     = 9000
$LogFile  = Join-Path $Root 'logs\watchdog.log'
$Flag     = Join-Path $Root '.maintenance'

function Say([string]$Message) {
    $line = ('{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message)
    try {
        $dir = Split-Path -Parent $LogFile
        if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force $dir | Out-Null }
        Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8
    } catch { }
}

if (Test-Path -LiteralPath $Flag) { exit 0 }

$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    # Something is listening. Do not touch it (it may be a long-running task).
    # Only complain silently if /health is not answering, and even then do nothing:
    # killing a busy executor is worse than a stuck one.
    exit 0
}

Say ("executor down: no listener on port $Port - starting task $TaskName")
Start-ScheduledTask -TaskName $TaskName

for ($i = 1; $i -le 20; $i++) {
    Start-Sleep -Seconds 2
    try {
        $r = Invoke-WebRequest -UseBasicParsing -Uri ('http://127.0.0.1:{0}/health' -f $Port) -TimeoutSec 3
        if ($r.StatusCode -eq 200) {
            Say ("recovered: /health 200 after ~{0}s" -f (2 * $i))
            exit 0
        }
    } catch { }
}

Say 'task started but /health did not reach 200 within 40s - check logs\script-server-error.log'
exit 1
