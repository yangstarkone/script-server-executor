# ops-http.ps1 - script-server day-2 operations over HTTP + log files + task control.
#
# Architecture: the Windows scheduled task `WorkflowPlatform-ScriptServer` runs
# `python deploy\serve.py` directly (AtStartup / S4U / Highest). The task process
# IS the uvicorn server. No CLI or daemon is needed, so this script works from a
# normal (non-elevated) PowerShell window.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File deploy\ops-http.ps1 health
#   ... ready | tasks | logs | status | restart | drain
#
# Actions:
#   health  - GET /health (public liveness)
#   ready   - GET /health/ready (needs token; shows status + checks)
#   tasks   - GET /tasks (running tasks)
#   logs    - tail service logs (UTF-8)
#   status  - port/listener + task state + health + autostart log tail
#   drain   - POST /drain (stop accepting new tasks; restart to undo)
#   restart - stop the service and start it again via the scheduled task (works from a normal window)
param(
    [Parameter(Position = 0)]
    [ValidateSet('health', 'ready', 'tasks', 'logs', 'status', 'restart', 'drain')]
    [string]$Action = 'status',
    [int]$Lines = 50
)

$ErrorActionPreference = 'Continue'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

$Root     = Split-Path -Parent $PSScriptRoot        # ...\script-server
$Base     = 'http://127.0.0.1:9000'
$TaskName = 'WorkflowPlatform-ScriptServer'
$CutoverLog = Join-Path (Split-Path -Parent $Root) 'logs\cutover.log'
$OutLog   = Join-Path $Root 'logs\script-server-out.log'
$ErrLog   = Join-Path $Root 'logs\script-server-error.log'

function Get-Token {
    $t = $null
    if (Test-Path -LiteralPath (Join-Path $Root '.env')) {
        foreach ($line in Get-Content -LiteralPath (Join-Path $Root '.env') -Encoding UTF8) {
            if ($line.Trim() -match '^WF_SCRIPT_TOKEN=(.*)$') { $t = $Matches[1].Trim() }
        }
    }
    return $t
}
function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

$Token = Get-Token
$Headers = @{}
if ($Token) { $Headers['X-Script-Token'] = $Token }

function Show-Health {
    try {
        $r = Invoke-WebRequest -UseBasicParsing -Uri "$Base/health" -TimeoutSec 8
        [Text.Encoding]::UTF8.GetString($r.RawContentStream.ToArray())
    } catch {
        Write-Host ('[FAIL] /health: ' + $_.Exception.Message) -ForegroundColor Red
        exit 1
    }
}

switch ($Action) {
    'health' { Show-Health }

    'ready' {
        if (-not $Token) { Write-Host '[FAIL] no WF_SCRIPT_TOKEN in .env'; exit 1 }
        try {
            $r = Invoke-WebRequest -UseBasicParsing -Uri "$Base/health/ready" -Headers $Headers -TimeoutSec 8
            [Text.Encoding]::UTF8.GetString($r.RawContentStream.ToArray())
        } catch {
            $code = $null; if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode }
            Write-Host ("[FAIL] /health/ready HTTP " + $code + " " + $_.Exception.Message) -ForegroundColor Red
            if ($code -eq 401) { Write-Host '       token mismatch: caller token != WF_SCRIPT_TOKEN in .env' }
            exit 1
        }
    }

    'tasks' {
        if (-not $Token) { Write-Host '[FAIL] no WF_SCRIPT_TOKEN in .env'; exit 1 }
        try {
            $r = Invoke-WebRequest -UseBasicParsing -Uri "$Base/tasks" -Headers $Headers -TimeoutSec 8
            [Text.Encoding]::UTF8.GetString($r.RawContentStream.ToArray())
        } catch { Write-Host ('[FAIL] /tasks: ' + $_.Exception.Message) -ForegroundColor Red; exit 1 }
    }

    'logs' {
        foreach ($f in @($OutLog, $ErrLog)) {
            Write-Host ("===== " + $f + " =====")
            if (Test-Path -LiteralPath $f) {
                Get-Content -LiteralPath $f -Encoding UTF8 -Tail $Lines
            } else { Write-Host '(missing)' }
        }
        Write-Host ("===== " + $CutoverLog + " =====")
        if (Test-Path -LiteralPath $CutoverLog) { Get-Content -LiteralPath $CutoverLog -Encoding UTF8 -Tail $Lines }
    }

    'drain' {
        if (-not $Token) { Write-Host '[FAIL] no WF_SCRIPT_TOKEN in .env'; exit 1 }
        try {
            $r = Invoke-WebRequest -UseBasicParsing -Method Post -Uri "$Base/drain?timeout=60" -Headers $Headers -TimeoutSec 90
            [Text.Encoding]::UTF8.GetString($r.RawContentStream.ToArray())
            Write-Host ''
            Write-Host 'NOTE: draining is sticky; only a restart resumes accepting tasks.'
        } catch { Write-Host ('[FAIL] /drain: ' + $_.Exception.Message) -ForegroundColor Red; exit 1 }
    }

    'restart' {
        # stop task -> start task
        Write-Host '[1/4] draining (best effort) ...'
        try {
            $r = Invoke-WebRequest -UseBasicParsing -Method Post -Uri "$Base/drain?timeout=60" -Headers $Headers -TimeoutSec 90
            [Text.Encoding]::UTF8.GetString($r.RawContentStream.ToArray())
        } catch { Write-Host ('      drain skipped: ' + $_.Exception.Message) }

        Write-Host '[2/4] stopping scheduled task ...'
        try {
            Stop-ScheduledTask -TaskName $TaskName -ErrorAction Stop
        } catch {
            Write-Host ('[FAIL] Stop-ScheduledTask: ' + $_.Exception.Message) -ForegroundColor Red
            Write-Host '       If it says access denied, run this action from an elevated PowerShell.' -ForegroundColor Yellow
            exit 1
        }
        $stopped = $false
        for ($i = 0; $i -lt 30; $i++) {
            if (-not (Get-NetTCPConnection -LocalPort 9000 -State Listen -ErrorAction SilentlyContinue)) { $stopped = $true; break }
            Start-Sleep -Seconds 1
        }
        if (-not $stopped) { Write-Host '[WARN] port 9000 still listening after 30s' -ForegroundColor Yellow }

        Write-Host '[3/4] starting scheduled task ...'
        Start-ScheduledTask -TaskName $TaskName
        $ok = $false
        for ($i = 0; $i -lt 40; $i++) {
            Start-Sleep -Seconds 2
            try {
                $h = Invoke-WebRequest -UseBasicParsing -Uri "$Base/health" -TimeoutSec 3
                if ($h.StatusCode -eq 200) { $ok = $true; break }
            } catch { }
        }
        if (-not $ok) { Write-Host '[FAIL] /health not 200 within 80s - check logs' -ForegroundColor Red; exit 1 }
        Write-Host '[4/4] OK:'
        Show-Health
    }

    'status' {
        Write-Host '=== listener ==='
        $c = Get-NetTCPConnection -LocalPort 9000 -State Listen -ErrorAction SilentlyContinue
        if ($c) { $c | Select-Object LocalAddress, LocalPort, OwningProcess | Format-Table -AutoSize | Out-String | Write-Host }
        else { Write-Host '9000 NOT listening' }

        Write-Host '=== process ==='
        foreach ($p in @($c | Select-Object -ExpandProperty OwningProcess -Unique)) {
            $proc = Get-Process -Id $p -ErrorAction SilentlyContinue
            if ($proc) { Write-Host ("pid=" + $p + " name=" + $proc.ProcessName + " session=" + $proc.SessionId) }
        }

        Write-Host '=== scheduled task ==='
        $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if ($t) {
            $i = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction SilentlyContinue
            Write-Host ("state=" + $t.State + " lastResult=" + $i.LastTaskResult + " lastRun=" + $i.LastRunTime)
            Write-Host ("action=" + $t.Actions[0].Execute + " " + $t.Actions[0].Arguments)
            Write-Host ("logon=" + $t.Principal.LogonType + " runLevel=" + $t.Principal.RunLevel)
        } else { Write-Host 'task NOT registered' }

        Write-Host '=== /health ==='
        Show-Health
        Write-Host ''
        Write-Host '=== cutover log (tail 10) ==='
        if (Test-Path -LiteralPath $CutoverLog) { Get-Content -LiteralPath $CutoverLog -Encoding UTF8 -Tail 10 }
    }
}
