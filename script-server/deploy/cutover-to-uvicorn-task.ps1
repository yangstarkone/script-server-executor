# cutover-to-uvicorn-task.ps1
# 安装/修复执行器：注册「计划任务直接托管 uvicorn（deploy\serve.py）」+ 1 分钟 watchdog，并启动。
#
# 做的事：停掉占用 9000 的旧进程 → 注册主任务（AtStartup / S4U / Highest）→ 注册 watchdog →
# 启动 → 校验 /health 与 /health/ready。只依赖 Python，可重复执行（幂等）。
#
# 需要管理员（注册 S4U 计划任务）。
# 用法（管理员 PowerShell）：
#   powershell -NoProfile -ExecutionPolicy Bypass -File C:\ScriptServer\script-server\deploy\cutover-to-uvicorn-task.ps1
param(
    [string]$TaskName = 'WorkflowPlatform-ScriptServer',
    [int]$Port = 9000,
    [int]$HealthTimeoutSeconds = 90
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

$Root      = Split-Path -Parent $PSScriptRoot        # ...\script-server
function Find-Python {
    # Prefer a real interpreter; the WindowsApps "python.exe" alias is a 0-byte Store stub.
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
$Python    = Find-Python
$ServePy   = Join-Path $PSScriptRoot 'serve.py'
$LogFile   = Join-Path (Split-Path -Parent $Root) 'logs\cutover.log'
$Base      = "http://127.0.0.1:$Port"

function Say([string]$Message, [string]$Level = 'i') {
    $line = ('{0} [{1}] {2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Level, $Message)
    Write-Host $line
    try {
        $dir = Split-Path -Parent $LogFile
        if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force $dir | Out-Null }
        Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8
    } catch { }
}

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-Admin)) {
    Write-Host '[ERROR] 需要管理员权限：注册 S4U 计划任务、结束 Session 0 的旧进程都需要提权。' -ForegroundColor Red
    Write-Host '        请右键 PowerShell -> 以管理员身份运行，再执行本脚本。'
    exit 1
}
if (-not $Python) { Say '找不到 python（PATH 里没有）。' 'ERROR'; exit 1 }
if (-not (Test-Path -LiteralPath $ServePy)) { Say "找不到 $ServePy" 'ERROR'; exit 1 }

Say ("开始切换执行器到「计划任务 + uvicorn」：task=$TaskName port=$Port python=$Python")

# ── 1) 停掉旧服务（端口 9000 的进程树）────────────────────────────
$listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
               Select-Object -ExpandProperty OwningProcess -Unique)
foreach ($procId in $listeners) {
    Say ("结束旧服务进程树: pid=$procId")
    & taskkill /F /T /PID $procId 2>&1 | ForEach-Object { Say ("  " + $_) }
}

# ── 2) 确认端口已释放 ────────────────────────────────────────────
Start-Sleep -Seconds 2
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    Say "端口 $Port 仍被占用，中止。" 'ERROR'
    exit 1
}

# ── 3) 注册计划任务：直接跑 serve.py（前台进程 = uvicorn 本体）──────
$action = New-ScheduledTaskAction -Execute $Python `
    -Argument ('"' + $ServePy + '"') -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -Hidden `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -RestartCount 999 `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
    -MultipleInstances IgnoreNew
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType S4U -RunLevel Highest

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force `
    -Description '工作流平台执行器 script-server（计划任务直接托管 uvicorn）' | Out-Null
Say ("已注册计划任务: $TaskName（AtStartup / S4U / Highest / 动作: $Python `"$ServePy`"）")

# ── 4) 启动并等健康 ────────────────────────────────────────────────
Say '启动计划任务 ...'
Start-ScheduledTask -TaskName $TaskName
$ok = $false; $last = ''
$deadline = (Get-Date).AddSeconds($HealthTimeoutSeconds)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 2
    try {
        $r = Invoke-WebRequest -UseBasicParsing -Uri "$Base/health" -TimeoutSec 3
        if ($r.StatusCode -eq 200) { $ok = $true; $last = [Text.Encoding]::UTF8.GetString($r.RawContentStream.ToArray()); break }
    } catch { $last = $_.Exception.Message }
}
if (-not $ok) {
    Say ("/health 在 $HealthTimeoutSeconds 秒内未返回 200：$last") 'ERROR'
    Say '最近日志：' 'ERROR'
    Get-Content (Join-Path $Root 'logs\script-server-error.log') -Encoding UTF8 -Tail 20 -ErrorAction SilentlyContinue |
        ForEach-Object { Say ("  " + $_) 'ERROR' }
    exit 1
}
Say ("/health 200：" + $last)

# ── 5) 就绪探针（带 token）─────────────────────────────────────────
$token = $null
$envFile = Join-Path $Root '.env'
if (Test-Path -LiteralPath $envFile) {
    foreach ($line in Get-Content -LiteralPath $envFile -Encoding UTF8) {
        if ($line.Trim() -match '^WF_SCRIPT_TOKEN=(.*)$') { $token = $Matches[1].Trim() }
    }
}
if ($token) {
    try {
        $r = Invoke-WebRequest -UseBasicParsing -Uri "$Base/health/ready" -Headers @{ 'X-Script-Token' = $token } -TimeoutSec 8
        Say ("/health/ready 200：" + ([Text.Encoding]::UTF8.GetString($r.RawContentStream.ToArray())))
    } catch { Say ('/health/ready 失败：' + $_.Exception.Message) 'WARN' }
}

# ── 6) 注册看护任务（实测：主任务的 RestartOnFailure 不会拉起已退出的进程）──
$watchdog = Join-Path $Root 'deploy\install-watchdog.ps1'
if (Test-Path -LiteralPath $watchdog) {
    Say '注册 watchdog（每分钟检查 9000 端口，掉了就 Start-ScheduledTask 主任务）...'
    & powershell -NoProfile -ExecutionPolicy Bypass -File $watchdog
    Say ("watchdog 注册结束，退出码=" + $LASTEXITCODE)
} else {
    Say "未找到 $watchdog：跳过 watchdog 注册（进程意外退出后不会自动恢复）" 'WARN'
}

$task = Get-ScheduledTask -TaskName $TaskName
Say ("切换完成：任务 State=" + $task.State + "；后续请用 deploy\ops-http.ps1 管理。")
exit 0
