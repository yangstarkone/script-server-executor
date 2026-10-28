# script-server acceptance (10 checks per INSTALL.md ch.3)
# ASCII-only source: PS 5.1 reads BOM-less files as ANSI, so no literal non-ASCII here.
$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$OutputEncoding = [Text.Encoding]::UTF8
$enc = New-Object System.Text.UTF8Encoding($false)

$root  = Split-Path -Parent $PSScriptRoot        # ...\script-server
$base  = 'http://127.0.0.1:9000'
$stamp = (Get-Date).ToString('HHmmss')

$token = $null
$scriptsDir = $null
foreach ($line in Get-Content -LiteralPath "$root\.env" -Encoding UTF8) {
    $l = $line.Trim()
    if ($l -match '^WF_SCRIPT_TOKEN=(.*)$') { $token = $Matches[1].Trim() }
    elseif ($l -match '^WF_SCRIPTS_DIR=(.*)$') { $scriptsDir = $Matches[1].Trim() }
}
if (-not $token) { Write-Host '[FATAL] no WF_SCRIPT_TOKEN in .env'; exit 1 }
if (-not $scriptsDir) { $scriptsDir = Join-Path (Split-Path -Parent $root) 'scripts' }

# expected version is read from script_server.py, so this check survives future upgrades
$expected = ''
$verLine = Select-String -LiteralPath "$root\script_server.py" -Pattern '^VERSION\s*=\s*"([^"]+)"' -ErrorAction SilentlyContinue |
    Select-Object -First 1
if ($verLine) { $expected = $verLine.Matches[0].Groups[1].Value }

$hdr = @{ 'X-Script-Token' = $token }
$result = [ordered]@{}
function Line($n, $ok, $detail) {
    $mark = if ($ok) { 'PASS' } else { 'FAIL' }
    Write-Host ("[{0}] {1,-2} {2}" -f $mark, $n, $detail)
    $result["$n"] = [ordered]@{ pass = [bool]$ok; detail = $detail }
}
function Decode($r) {
    return [Text.Encoding]::UTF8.GetString($r.RawContentStream.ToArray())
}

# 1 port listening
$listen = Get-NetTCPConnection -LocalPort 9000 -State Listen -ErrorAction SilentlyContinue
Line 1 ([bool]$listen) ("LISTENING pid=" + (($listen | Select-Object -ExpandProperty OwningProcess) -join ','))

# 2 /health public probe
try {
    $r = Invoke-WebRequest -UseBasicParsing -Uri "$base/health" -TimeoutSec 10
    $j = Decode $r | ConvertFrom-Json
    $ok = ($r.StatusCode -eq 200 -and $j.version -eq $expected -and $j.auth_required -eq $true -and $j.draining -eq $false)
    Line 2 $ok ("HTTP " + $r.StatusCode + " version=" + $j.version + " expected=" + $expected + " auth_required=" + $j.auth_required + " draining=" + $j.draining)
} catch { Line 2 $false $_.Exception.Message }

# 3 without token -> 401
try {
    $r = Invoke-WebRequest -UseBasicParsing -Uri "$base/health/ready" -TimeoutSec 10
    Line 3 ($r.StatusCode -eq 401) ("HTTP " + $r.StatusCode)
} catch {
    $code = $null; if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode }
    Line 3 ($code -eq 401) ("HTTP " + $code)
}

# 4 with token -> ready
try {
    $r = Invoke-WebRequest -UseBasicParsing -Uri "$base/health/ready" -Headers $hdr -TimeoutSec 10
    $j = Decode $r | ConvertFrom-Json
    $ok = ($j.status -eq 'ok' -and $j.checks.state_dir.enabled -eq $true -and $j.checks.python.exists -eq $true)
    Line 4 $ok ("status=" + $j.status + " state_dir=" + $j.checks.state_dir.enabled + " python.exists=" + $j.checks.python.exists + " concurrency=" + $j.checks.concurrency.limit)
} catch { Line 4 $false $_.Exception.Message }

# 5 Bearer compat
try {
    $r = Invoke-WebRequest -UseBasicParsing -Uri "$base/health/ready" -Headers @{ Authorization = "Bearer $token" } -TimeoutSec 10
    Line 5 ($r.StatusCode -eq 200) ("HTTP " + $r.StatusCode)
} catch { Line 5 $false $_.Exception.Message }

# 6 /scan
try {
    $u = "$base/scan?dir_path=" + [uri]::EscapeDataString($scriptsDir)
    $r = Invoke-RestMethod -Method Post -Uri $u -Headers $hdr -TimeoutSec 15
    $names = ($r.files | ForEach-Object { $_.name }) -join ','
    Line 6 ($names -match 'hello\.py') ("files=" + $names)
} catch { Line 6 $false $_.Exception.Message }

function Post-Run([string]$json) {
    $bytes = $enc.GetBytes($json)
    $out = @{ code = $null; json = $null; raw = ''; err = '' }
    try {
        $r = Invoke-WebRequest -UseBasicParsing -Method Post -Uri "$base/run" -Headers $hdr `
            -ContentType 'application/json; charset=utf-8' -Body $bytes -TimeoutSec 120
        $out.code = [int]$r.StatusCode
        $out.raw = Decode $r
        $out.json = $out.raw | ConvertFrom-Json
    } catch {
        $resp = $_.Exception.Response
        if ($resp) {
            $out.code = [int]$resp.StatusCode
            try {
                $ms = New-Object IO.MemoryStream
                $resp.GetResponseStream().CopyTo($ms)
                $out.raw = [Text.Encoding]::UTF8.GetString($ms.ToArray())
            } catch { $out.err = 'stream: ' + $_.Exception.Message }
        } else {
            $out.err = $_.Exception.GetType().FullName + ': ' + $_.Exception.Message
        }
    }
    return $out
}

$cnOk = [regex]::Unescape('\u6210\u529F')           # 成功
$cnTimeout = [regex]::Unescape('\u811A\u672C\u8D85\u65F6')  # 脚本超时
$cnArgs = [regex]::Unescape('\u6D4B\u8BD5')          # 测试

# 7 real execution (Chinese arg via JSON \u escape so this file stays ASCII)
$body7 = '{"file_path":"hello.py","args":"--name \u6d4b\u8bd5","env":{"DEMO":"1"},"timeout_seconds":30,"task_run_id":"acc-' + $stamp + '"}'
$a = Post-Run $body7
$ok7 = ($a.code -eq 200 -and $a.json -and $a.json.exit_code -eq 0 -and $a.json.success -eq $true -and $a.json.stdout -match 'hello from script-server' -and $a.json.stdout -match $cnArgs)
Line 7 ([bool]$ok7) ("HTTP " + $a.code + " task_id=" + $a.json.task_id + " exit_code=" + $a.json.exit_code + " success=" + $a.json.success + " stdout=" + (($a.json.stdout -replace "`r?`n", ' | ').Trim()) + $a.err)

# 8 idempotent re-post (same body / same task_run_id)
$b = Post-Run $body7
$ok8 = ($b.code -eq 200 -and $b.json -and $b.json.deduplicated -eq $true -and $b.json.exit_code -eq 0)
Line 8 ([bool]$ok8) ("HTTP " + $b.code + " deduplicated=" + $b.json.deduplicated + " task_id=" + $b.json.task_id + $b.err)

# 9 timeout semantics
$body9 = '{"file_path":"sleep5.py","timeout_seconds":2,"task_run_id":"tmo-' + $stamp + '"}'
$c = Post-Run $body9
$ok9 = ($c.code -eq 200 -and $c.json -and $c.json.exit_code -eq -1 -and $c.json.status_label -eq $cnTimeout -and $c.json.cancelled -eq $false)
Line 9 ([bool]$ok9) ("HTTP " + $c.code + " exit_code=" + $c.json.exit_code + " status_label=" + $c.json.status_label + " cancelled=" + $c.json.cancelled + " duration=" + $c.json.duration_seconds + $c.err)

# 10 path escape
$d = Post-Run '{"file_path":"..\\..\\script_server.py"}'
Line 10 ($d.code -eq 403) ("HTTP " + $d.code + " body=" + $d.raw + $d.err)

""
$pass = @($result.Values | Where-Object { $_.pass }).Count
Write-Host ("===== {0}/10 PASS =====" -f $pass)
$resultFile = Join-Path $root 'logs\verify-result.json'
New-Item -ItemType Directory -Force (Split-Path -Parent $resultFile) | Out-Null
$result | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $resultFile -Encoding UTF8
