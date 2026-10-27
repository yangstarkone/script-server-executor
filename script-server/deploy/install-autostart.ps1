# ══════════════════════════════════════════════════════════════════
#  工作流调度平台 — 执行器（script-server）开机自启 · 任务计划程序
#
#  背景（EXECUTOR-STABILITY-EVAL-20261023.md §5 P0-3 / §10 建议 2）：
#    评估实测：注册表 Run、启动文件夹、计划任务、Windows 服务里都没有 script-server /
#    pm2 的自启产物；PM2 7 在 Windows 又没有 `pm2 startup` 能力（部署文档的承诺落空）。
#    于是 Windows 重启/断电后 :9000 永久停摆，且 max_restarts=5 用尽后无任何告警。
#
#  本脚本注册一个「任务计划程序」任务：
#    • 触发：AtStartup —— 开机即启动，不依赖用户登录
#    • 身份：当前用户 + S4U（“不管用户是否登录都运行”，无需保存密码）
#    • 动作：本脚本 -AtBoot 模式，先看 http://127.0.0.1:9000/health：
#        1) 已 200 → 服务已在跑，直接成功退出
#        2) 否则优先 `pm2 resurrect`（依赖安装时执行的 `pm2 save` 生成的 dump）
#        3) 等待 $WaitSeconds（默认 60s）仍未 200 → 回退
#           `cmd /c "<仓库>\script-server\start-script-server.bat"`
#    • 失败重试：任务失败后每 1 分钟重启一次（RestartInterval），最多 999 次
#    • 隐藏窗口：-WindowStyle Hidden + 任务 Hidden；日志写 logs\script-server-autostart.log
#
#  用法（必须“管理员” PowerShell，普通窗口会直接报错退出）：
#    安装：powershell -NoProfile -ExecutionPolicy Bypass -File script-server\deploy\install-autostart.ps1
#    卸载：powershell -NoProfile -ExecutionPolicy Bypass -File script-server\deploy\install-autostart.ps1 -Uninstall
#
#  ⚠️ 需管理员权限：注册/删除开机计划任务需要 Administrator。
#  ⚠️ 未实测开机重启：本次评估只做了只读排查与配置修复，没有执行本脚本、
#     也没有真实重启 Windows 验证自启——首次上线务必按
#     script-server\deploy\OPERATIONS-executor.md 的「自启验收」真实重启一次。
#  ⚠️ 幂等：重复安装用 Register-ScheduledTask -Force 覆盖同名任务，不会产生多条。
#  ⚠️ dump 说明：pm2 save 会把“当前 pm2 列表里的所有 app”写进 dump，开机 resurrect
#     会一并拉起。当前评估环境 pm2 只有 script-server（pm2 list 实测）；若以后把
#     backend/worker 也挂到 pm2，注意别与 WorkflowPlatform 计划任务重复启动。
# ══════════════════════════════════════════════════════════════════
#Requires -Version 5.1
[CmdletBinding()]
param(
    [switch]$Uninstall,                      # 删除计划任务
    [switch]$AtBoot,                         # 计划任务入口：只执行 resurrect / 回退，不注册
    [int]$WaitSeconds = 60,                  # 等待 /health 返回 200 的秒数
    [string]$TaskName = "WorkflowPlatform-ScriptServer"
)

$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$RepoRoot        = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$ScriptServerDir = Join-Path $RepoRoot "script-server"
$FallbackBat     = Join-Path $ScriptServerDir "start-script-server.bat"
# 仓库内运行时用仓库根 ecosystem（读根 .env）；只装执行器的新设备用 script-server/ecosystem.config.js
$Ecosystem       = Join-Path $RepoRoot "ecosystem.config.js"
if (-not (Test-Path -LiteralPath $Ecosystem)) {
    $Ecosystem = Join-Path $ScriptServerDir "ecosystem.config.js"
}
$LogDir          = Join-Path $RepoRoot "logs"
$LogFile         = Join-Path $LogDir "script-server-autostart.log"
$HealthUrl       = "http://127.0.0.1:9000/health"
$TaskDescription = "工作流平台执行器 script-server 开机自启（优先 pm2 resurrect，失败回退 start-script-server.bat）"

function Write-Log([string]$Message) {
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Write-Host $line
    try {
        if (-not (Test-Path -LiteralPath $LogDir)) {
            New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
        }
        Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8
    } catch {
        # 日志写失败不能影响启动流程
    }
}

function Test-IsAdmin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Resolve-Pm2 {
    # 计划任务/开机环境的 PATH 可能不含 npm 全局目录，按固定位置兜底
    foreach ($candidate in @(
        "$env:APPDATA\npm\pm2.cmd",
        "$env:APPDATA\npm\pm2.exe",
        "$env:APPDATA\npm\pm2.ps1"
    )) {
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    $cmd = Get-Command pm2.cmd -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $cmd = Get-Command pm2 -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

function Test-Health([int]$TimeoutSec = 3) {
    try {
        $resp = Invoke-WebRequest -Uri $HealthUrl -TimeoutSec $TimeoutSec -UseBasicParsing
        return ($resp.StatusCode -eq 200)
    } catch {
        return $false
    }
}

function Wait-Health([int]$Seconds) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-Health 3) { return $true }
        Start-Sleep -Seconds 2
    }
    return (Test-Health 3)
}

# ── 计划任务入口：开机时执行，不注册 ─────────────────────────────
function Invoke-BootStart {
    Write-Log ('[AtBoot] 启动流程开始（仓库: ' + $RepoRoot + '）')

    if (-not (Test-Path -LiteralPath $FallbackBat)) {
        Write-Log ('[AtBoot][ERROR] 找不到回退脚本: ' + $FallbackBat)
        return 1
    }
    if (Test-Health 3) {
        Write-Log '[AtBoot] /health 已 200，服务已在运行，跳过 resurrect'
        return 0
    }

    $pm2 = Resolve-Pm2
    $healthy = $false
    if ($pm2) {
        try {
            Write-Log ('[AtBoot] 执行: "' + $pm2 + '" resurrect')
            & $pm2 resurrect 2>&1 | ForEach-Object { Write-Log ('[AtBoot][resurrect] ' + $_) }
            if ($LASTEXITCODE -ne 0) {
                Write-Log ('[AtBoot][WARN] pm2 resurrect 退出码 ' + $LASTEXITCODE + '，准备回退')
            } elseif (Wait-Health $WaitSeconds) {
                $healthy = $true
                Write-Log ('[AtBoot][OK] pm2 resurrect 成功，/health 200（等待 <= ' + $WaitSeconds + 's）')
            } else {
                Write-Log ('[AtBoot][WARN] pm2 resurrect 已执行，但 ' + $WaitSeconds + 's 内 /health 未 200，准备回退')
            }
        } catch {
            Write-Log ('[AtBoot][WARN] pm2 resurrect 异常: ' + $_.Exception.Message + '，准备回退')
        }
    } else {
        Write-Log '[AtBoot][WARN] 找不到 pm2，直接回退 start-script-server.bat'
    }

    if (-not $healthy) {
        try {
            # -nopause：bat 在非交互（隐藏窗口/计划任务）下不能等待按键
            Write-Log ('[AtBoot] 回退: cmd /c "' + $FallbackBat + '" -nopause')
            $proc = Start-Process -FilePath $env:ComSpec -ArgumentList @("/c", ('"' + $FallbackBat + '" -nopause')) -WindowStyle Hidden -PassThru -Wait
            Write-Log ('[AtBoot] 回退脚本退出码: ' + $proc.ExitCode)
        } catch {
            Write-Log ('[AtBoot][ERROR] 回退脚本执行失败: ' + $_.Exception.Message)
        }
        $healthy = Wait-Health $WaitSeconds
    }

    if ($healthy) {
        Write-Log '[AtBoot][OK] 最终结果: OK（/health 200）'
        return 0
    }
    Write-Log '[AtBoot][ERROR] 最终结果: FAIL（/health 未就绪；任务计划程序将按 1 分钟间隔重试）'
    return 1
}

# ══════════════════════════════════════════════════════════════════
#  主流程
# ══════════════════════════════════════════════════════════════════
if ($AtBoot) {
    exit (Invoke-BootStart)
}

if (-not (Test-IsAdmin)) {
    Write-Host "[ERROR] 需要管理员权限：注册/删除开机计划任务必须以管理员身份运行。" -ForegroundColor Red
    Write-Host "        请右键 PowerShell ->“以管理员身份运行”，再执行本脚本。" -ForegroundColor Yellow
    Write-Host "        （只读检查或 -AtBoot 模式不需要管理员，但正常使用不会手动调它。）" -ForegroundColor Yellow
    exit 1
}

# ── 卸载（幂等）──────────────────────────────────────────────────
if ($Uninstall) {
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $existing) {
        Write-Log ('[OK] 计划任务 ' + $TaskName + ' 不存在，无需卸载（幂等）')
        exit 0
    }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Log ('[OK] 已删除计划任务: ' + $TaskName)
    Write-Log '[i] 服务本身不受影响（仍由 PM2 托管）；要停服务请用 pm2 stop script-server'
    exit 0
}

# ── 安装 ─────────────────────────────────────────────────────────
Write-Log ('[i] 开始安装执行器开机自启任务: ' + $TaskName)

if (-not (Test-Path -LiteralPath $Ecosystem)) {
    Write-Log ('[ERROR] 找不到 ' + $Ecosystem + ' —— 需要 <仓库>\ecosystem.config.js 或 <仓库>\script-server\ecosystem.config.js')
    exit 1
}
if (-not (Test-Path -LiteralPath $FallbackBat)) {
    Write-Log ('[ERROR] 找不到回退脚本 ' + $FallbackBat)
    exit 1
}

$pm2 = Resolve-Pm2
if (-not $pm2) {
    Write-Log '[ERROR] 找不到 pm2（PATH、%APPDATA%\npm 都没有）。先确认 pm2 -v 可用，再重试。'
    exit 1
}
Write-Log ('[i] pm2: ' + $pm2)

# 1) 先 pm2 save：resurrect 只认 dump；未 save 会回到旧状态
#    （评估 P2-12：现网 dump.pm2 早于进程启动，resurrect 会回退旧 token -> 全员 401）
Write-Log '[i] 执行 pm2 save（把当前 pm2 进程列表写入 dump，resurrect 依赖它）'
& $pm2 save 2>&1 | ForEach-Object { Write-Log ('[save] ' + $_) }
if ($LASTEXITCODE -ne 0) {
    Write-Log ('[ERROR] pm2 save 失败（退出码 ' + $LASTEXITCODE + '），中止安装：没有 dump 的 resurrect 没意义')
    exit 1
}

# 2) 检查 dump 里是否确实有 script-server（没有也能装，只是 resurrect 会回退 bat）
$listRaw = (& $pm2 jlist 2>$null | Out-String)
if ($listRaw -notmatch '"name"\s*:\s*"script-server"') {
    Write-Log '[WARN] 当前 pm2 列表里没有 script-server：开机 resurrect 不会拉起它，会走 start-script-server.bat 回退'
}

# 3) 注册任务（-Force = 幂等覆盖）
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$psExe = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
if (-not (Test-Path -LiteralPath $psExe)) { $psExe = "powershell.exe" }
$argLine = '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $PSCommandPath + '" -AtBoot'

$action = New-ScheduledTaskAction -Execute $psExe -Argument $argLine -WorkingDirectory $RepoRoot
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
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType S4U -RunLevel Highest

try {
    Register-ScheduledTask -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Principal $principal `
        -Description $TaskDescription `
        -Force | Out-Null
} catch {
    Write-Log ('[ERROR] 注册计划任务失败: ' + $_.Exception.Message)
    Write-Log '        备选方案：把 -LogonType S4U 换成 Interactive（只在本用户登录后启动，不需要 S4U），'
    Write-Log '                  或按 OPERATIONS-executor.md 用 NSSM 做真正的 Windows 服务。'
    exit 1
}

$task = Get-ScheduledTask -TaskName $TaskName
$info = Get-ScheduledTaskInfo -TaskName $TaskName
Write-Log ('[OK] 已注册计划任务: ' + $task.TaskName + '（State=' + $task.State + '，LastResult=' + $info.LastTaskResult + '）')
Write-Log ('[i] 触发: AtStartup；身份: ' + $identity + '（S4U）；失败重试: 每 1 分钟，最多 999 次')
Write-Log ('[i] 动作: 本脚本 -AtBoot（先 pm2 resurrect，失败回退 ' + $FallbackBat + '）')
Write-Log ('[i] 日志: ' + $LogFile)
Write-Log ('[i] 卸载: powershell -NoProfile -ExecutionPolicy Bypass -File "' + $PSCommandPath + '" -Uninstall')
Write-Log '[WARN] 未实测：本次没有执行本脚本注册任务，也没有真实重启 Windows 验证自启。'
exit 0
