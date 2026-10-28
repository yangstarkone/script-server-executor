# register-autostart-task.ps1 - register (or remove) the script-server autostart task.
#
# Architecture (task-scheduler hosted):
#   Windows Task Scheduler task "WorkflowPlatform-ScriptServer"
#     trigger  : AtStartup          -> service comes up after reboot WITHOUT a login
#     principal: S4U + RunLevel Highest (runs as this account, no stored password)
#     action   : <python.exe> "C:\ScriptServer\script-server\deploy\serve.py"
#     settings : MultipleInstances IgnoreNew; restart every 1 min on failure, 999 times
#
#   serve.py loads .env, writes logs into logs\script-server-*.log and runs uvicorn in
#   the foreground (single worker) - one plain python process, no daemon layer and no
#   cross-session named pipes involved.
#
# Usage (ELEVATED PowerShell / "Run as administrator"):
#   powershell -NoProfile -ExecutionPolicy Bypass -File deploy\register-autostart-task.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File deploy\register-autostart-task.ps1 -Uninstall
#
# Idempotent: re-running replaces the task. Any process already listening on 9000 is
# stopped first so the new instance does not fail with WinError 10048.
# Keep this file ASCII-only (Windows PowerShell 5.1 parses .ps1 without BOM as ANSI).

param(
    [switch]$Uninstall,
    [switch]$NoStart,
    [int]$HealthTimeoutSeconds = 60
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

$Root      = 'C:\ScriptServer\script-server'
$TaskName  = 'WorkflowPlatform-ScriptServer'
$ServePy   = Join-Path $Root 'deploy\serve.py'
$EnvFile   = Join-Path $Root '.env'
$LogDir    = 'C:\ScriptServer\logs'
$LogFile   = Join-Path $LogDir 'register-task.log'
$Base      = 'http://127.0.0.1:9000'

function Write-Log([string]$msg) {
    $line = ('{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg)
    Write-Host $line
    try {
        if (-not (Test-Path -LiteralPath $LogDir)) { New-Item -ItemType Directory -Force $LogDir | Out-Null }
        Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8
    } catch { }
}

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-Token {
    $t = ''
    if (Test-Path -LiteralPath $EnvFile) {
        foreach ($line in Get-Content -LiteralPath $EnvFile -Encoding UTF8) {
            if ($line.Trim() -match '^WF_SCRIPT_TOKEN=(.*)$') { $t = $Matches[1].Trim() }
        }
    }
    return $t
}

function Find-Python {
    # Prefer a real interpreter; the WindowsApps "python.exe" alias is a Store stub.
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe'),
        'C:\Program Files\Python312\python.exe',
        'C:\Program Files\Python311\python.exe'
    )
    foreach ($c in $candidates) {
        if ($c -and (Test-Path -LiteralPath $c)) { return $c }
    }
    $cmd = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -notmatch 'WindowsApps') { return $cmd.Source }
    return $null
}

function Stop-Port9000 {
    $pids = @()
    foreach ($line in (netstat -ano 2>$null | Select-String -Pattern ':9000' | Where-Object { $_ -match 'LISTENING' })) {
        $parts = ($line.ToString().Trim() -split '\s+')
        if ($parts.Count -ge 5) { $pids += [int]$parts[-1] }
    }
    $pids = $pids | Sort-Object -Unique
    foreach ($p in $pids) {
        Write-Log ("[stop] taskkill /F /T /PID " + $p)
        & taskkill /F /T /PID $p 2>&1 | Out-Null
    }
    if ($pids.Count -eq 0) { Write-Log '[stop] nothing listening on 9000' }
    Start-Sleep -Seconds 2
}

function Wait-Health([int]$timeoutSeconds) {
    $deadline = (Get-Date).AddSeconds($timeoutSeconds)
    $last = ''
    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-WebRequest -UseBasicParsing -Uri "$Base/health" -TimeoutSec 3
            if ([int]$r.StatusCode -eq 200) { return [Text.Encoding]::UTF8.GetString($r.RawContentStream.ToArray()) }
        } catch { $last = $_.Exception.Message }
        Start-Sleep -Seconds 2
    }
    Write-Log ("[health] /health not 200 within {0}s: {1}" -f $timeoutSeconds, $last)
    return $null
}

# ---------------------------------------------------------------- preconditions
if (-not (Test-Admin)) {
    Write-Host '[FAIL] This script must run in an ELEVATED PowerShell (Run as administrator).' -ForegroundColor Red
    exit 1
}

Write-Log ('=== {0} : {1}' -f $(if ($Uninstall) { 'UNINSTALL' } else { 'REGISTER' }), $TaskName)
Write-Log ('host={0} user={1}' -f $env:COMPUTERNAME, [Security.Principal.WindowsIdentity]::GetCurrent().Name)

if ($Uninstall) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Stop-Port9000
    Write-Log '[done] task removed and port 9000 released'
    exit 0
}

if (-not (Test-Path -LiteralPath $ServePy)) { Write-Log ("[FAIL] missing " + $ServePy); exit 1 }

$Python = Find-Python
if (-not $Python) { Write-Log '[FAIL] python.exe not found (install Python 3.10+ first)'; exit 1 }
Write-Log ("[1/5] python = " + $Python)
& $Python -c "import fastapi, uvicorn; print('fastapi', fastapi.__version__, 'uvicorn', uvicorn.__version__)" 2>&1 |
    ForEach-Object { Write-Log ('      ' + $_) }

# ---------------------------------------------------------------- register
Write-Log '[2/5] registering scheduled task ...'
$action = New-ScheduledTaskAction -Execute $Python -Argument ('"{0}"' -f $ServePy) -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId ("{0}\{1}" -f $env:USERDOMAIN, $env:USERNAME) `
    -LogonType S4U -RunLevel Highest
$description = 'script-server executor (standalone): python deploy\serve.py, uvicorn 0.0.0.0:9000, single worker.'
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Description $description -Force | Out-Null
Write-Log '[2/5] task registered'

# ---------------------------------------------------------------- start
Write-Log '[3/5] releasing port 9000 ...'
Stop-Port9000

if ($NoStart) { Write-Log '[3/5] -NoStart given, not starting the task'; exit 0 }

Write-Log '[4/5] starting task ...'
Start-ScheduledTask -TaskName $TaskName
$health = Wait-Health $HealthTimeoutSeconds
if (-not $health) {
    Write-Log '[FAIL] service did not become healthy; check logs\script-server-error.log'
    Get-Content -LiteralPath (Join-Path $Root 'logs\script-server-error.log') -Tail 30 -Encoding UTF8 -ErrorAction SilentlyContinue |
        ForEach-Object { Write-Log ('      ' + $_) }
    exit 1
}
Write-Log ('[4/5] /health 200: ' + $health)

# ---------------------------------------------------------------- summary
Write-Log '[5/5] summary'
$t = Get-ScheduledTask -TaskName $TaskName
$i = Get-ScheduledTaskInfo -TaskName $TaskName
Write-Log ('      task   : state={0} lastResult={1} lastRun={2}' -f $t.State, $i.LastTaskResult, $i.LastRunTime)
Write-Log ('      action : {0} "{1}"  (cwd {2})' -f $t.Actions[0].Execute, $t.Actions[0].Arguments, $t.Actions[0].WorkingDirectory)
$token = Get-Token
if ($token) {
    try {
        $r = Invoke-WebRequest -UseBasicParsing -Uri "$Base/health/ready" -Headers @{ 'X-Script-Token' = $token } -TimeoutSec 8
        Write-Log ('      ready  : ' + [Text.Encoding]::UTF8.GetString($r.RawContentStream.ToArray()))
    } catch { Write-Log ('      ready  : FAILED ' + $_.Exception.Message) }
} else {
    Write-Log '      ready  : skipped (no WF_SCRIPT_TOKEN in .env)'
}
Write-Log '[done] autostart registered. Reboot once and re-check with deploy\ops-http.ps1 status'
exit 0
