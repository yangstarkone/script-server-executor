# Ensure the script-server executor is running (idempotent "autostart" hook).
#
# The Windows scheduled task
#   "WorkflowPlatform-ScriptServer"  ->  python <安装目录>\deploy\serve.py
# hosts uvicorn directly (AtStartup / S4U / Highest), so "autostart" here only means
# "make sure the task is running".
#
# Called by E:\workflow-platform-20260622\start-platform.bat.

$ErrorActionPreference = 'SilentlyContinue'
$TaskName = 'WorkflowPlatform-ScriptServer'
$LogFile  = Join-Path (Split-Path -Parent $PSScriptRoot) 'logs\executor-ensure.log'

function Say([string]$Message) {
    $line = ('{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message)
    try {
        $dir = Split-Path -Parent $LogFile
        if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force $dir | Out-Null }
        Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8
    } catch { }
}

try {
    $r = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:9000/health' -TimeoutSec 3
    if ($r.StatusCode -eq 200) { exit 0 }
} catch { }

$task = Get-ScheduledTask -TaskName $TaskName
if (-not $task) {
    Say ('task missing: ' + $TaskName + ' (register it once with deploy\cutover-to-uvicorn-task.ps1 as admin)')
    exit 1
}

Say ('starting scheduled task ' + $TaskName)
Start-ScheduledTask -TaskName $TaskName
exit 0
