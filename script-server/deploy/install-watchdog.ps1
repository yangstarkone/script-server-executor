# install-watchdog.ps1
# Register (or refresh) the 1-minute watchdog task for the script-server executor.
#
# Why: Task Scheduler "RestartOnFailure" does not recover the server when its
# process ends (measured 2026-10-27). deploy\watchdog.ps1 checks port 9000 once a
# minute and starts "WorkflowPlatform-ScriptServer" when nothing is listening.
#
# Requires administrator (registering an S4U/Highest task).
# Idempotent: safe to run again (overwrites the task).
#
# Usage (elevated PowerShell):
#   powershell -NoProfile -ExecutionPolicy Bypass -File C:\ScriptServer\script-server\deploy\install-watchdog.ps1
#   ... -Uninstall     remove the watchdog task
param(
    [string]$TaskName = 'WorkflowPlatform-ScriptServer-Watchdog',
    [string]$MainTask = 'WorkflowPlatform-ScriptServer',
    [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

$Root    = Split-Path -Parent $PSScriptRoot        # ...\script-server
$Script  = Join-Path $PSScriptRoot 'watchdog.ps1'
$LogFile = Join-Path (Split-Path -Parent $Root) 'logs\watchdog-install.log'

function Say([string]$Message, [string]$Level = 'i') {
    $line = ('{0} [{1}] {2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Level, $Message)
    Write-Host $line
    try {
        $dir = Split-Path -Parent $LogFile
        if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force $dir | Out-Null }
        Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8
    } catch { }
}

$id = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Say '需要管理员权限：注册 S4U 计划任务。请用管理员 PowerShell 执行。' 'ERROR'
    exit 1
}

if ($Uninstall) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Say "已删除 watchdog 任务: $TaskName"
    } else {
        Say "watchdog 任务不存在: $TaskName"
    }
    exit 0
}

if (-not (Test-Path -LiteralPath $Script)) { Say "找不到 $Script" 'ERROR'; exit 1 }
if (-not (Get-ScheduledTask -TaskName $MainTask -ErrorAction SilentlyContinue)) {
    Say "主任务 $MainTask 不存在：请先运行 deploy\cutover-to-uvicorn-task.ps1" 'ERROR'
    exit 1
}

$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $Script + '"') `
    -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 1) `
    -RepetitionDuration ([TimeSpan]::FromDays(3650))
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -Hidden `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
    -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $id.Name -LogonType S4U -RunLevel Highest

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force `
    -Description 'script-server watchdog: every minute, if port 9000 is not listening, start WorkflowPlatform-ScriptServer' | Out-Null

$t = Get-ScheduledTask -TaskName $TaskName
$rep = $t.Triggers[0].Repetition
Say ("已注册 watchdog: {0}（state={1} interval={2} duration={3}）" -f $TaskName, $t.State, $rep.Interval, $rep.Duration)
Say "暂停自动恢复：在 $Root 下建一个 .maintenance 文件；恢复时删除它。"
exit 0
