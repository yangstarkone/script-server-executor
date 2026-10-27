# 执行器（script-server）运维 runbook

> 对象：`script-server/script_server.py`（FastAPI，Windows 宿主 :9000，PM2 7.0.1 托管）
> 依据：`script-server/EXECUTOR-STABILITY-EVAL-20261023.md` §10 建议 2（外部看护 + 可观测性）
> 维护：2026-10-23 · 注：本文所有“已实测”均为本次（10-23 18:17~18:35）实际执行的结果。
>
> 状态图例：**已实测** = 实际执行并看到结果；**未实测（原因）** = 只核对代码/配置或按约束未执行。
> **2026-10-26 起：执行器 3.2.0 已上线（P1 修复版；3.1.0 于 10-23 上线）**（`GET /health` 含 `version`，`GET /health/ready` 返回 200）——
> 下文标注"待重启生效"的段落均按"已生效"理解；**新设备部署见 `deploy/INSTALL-EXECUTOR.md`**。

## 1. 日志轮转（防磁盘写满；评估 P1-8）
现状（**已实测**）：pm2-logrotate 未安装（`~\.pm2\module_conf.json` 为 `{}`）；PM2 日志无轮转。
日志路径（`ecosystem.config.js` script-server 段）：`logs\script-server-out.log`、`logs\script-server-error.log`。

```powershell
pm2 install pm2-logrotate                        # 未实测（需联网 npm install；本轮禁止装包）
pm2 set pm2-logrotate:max_size 50M               # 单文件 50MB 后切分
pm2 set pm2-logrotate:retain 14                  # 保留 14 个历史文件
pm2 set pm2-logrotate:compress true              # 历史文件 gzip
pm2 conf pm2-logrotate                           # 查看配置（只读）
```

验收（**未实测**，原因：未安装）：`logs\` 出现 `script-server-*-error__*.log` 轮转文件；
`pm2 list` 出现 `pm2-logrotate` 模块且 online。

## 2. 编码修复（PYTHONIOENCODING=utf-8；评估 P1-8 / A21）

已改（**已实测**配置文件）：`ecosystem.config.js` 的 script-server 段 `env` / `env_production` 增加
`PYTHONIOENCODING: "utf-8"`（只修 stdio 编码；未用 `PYTHONUTF8=1`，避免改业务脚本 `open()` 默认编码）。
同段另有 `treekill: true` + `kill_timeout: 10000`（PM2 7.0.1 源码核实：Windows 停进程走 `taskkill /T /F` 杀树）。

生效（**未实测**，原因：本轮禁止重启生产）：`pm2 startOrRestart ecosystem.config.js --only script-server && pm2 save`

验证分两步：解码命令本身**已实测**（对现网旧日志跑通）；修复效果待重启后验证（**未实测**，原因：未重启、无新日志）。

```powershell
# 2.1 触发一次失败任务（会真实起子进程；本轮未执行）
$t = '<从仓库根 .env 的 WF_SCRIPT_TOKEN 复制，勿写死到脚本/文档>'
Invoke-RestMethod -Method Post 'http://127.0.0.1:9000/run' `
  -Headers @{ 'X-Script-Token' = $t; 'Content-Type' = 'application/json' } `
  -Body '{"file_path":"_smoke_partial_exit1.py","timeout_seconds":30}'
# 2.2 严格 UTF-8 解码 + 统计（可直接粘贴；PM2 独占写日志句柄，ReadAllBytes 会报占用，用共享读）
$p = 'C:\Users\yang\Documents\workflow-platform-20261022\logs\script-server-error.log'
$fs = [IO.File]::Open($p, 'Open', 'Read', 'ReadWrite'); $ms = New-Object IO.MemoryStream; $fs.CopyTo($ms); $fs.Close(); [byte[]]$bytes = $ms.ToArray()
$strict = New-Object System.Text.UTF8Encoding($false, $true)   # true=非法字节抛异常
try { $text = $strict.GetString($bytes) } catch { "FAIL 非严格 UTF-8: $($_.Exception.Message)"; return }
$fffd = ([regex]::Matches($text, [char]0xFFFD)).Count
$cjk  = ([regex]::Matches($text, '[\u4e00-\u9fff]')).Count
"bytes=$($bytes.Length) U+FFFD=$fffd CJK=$cjk"
```

判据：`U+FFFD=0` 且 `CJK>0`。注意本次损坏是把替换字符**字面**写成 `EF BF BD`（合法 UTF-8），严格解码不会抛错，
所以必须数 U+FFFD 与 CJK；用上面命令对现网旧日志实跑（**已实测**）：18,794B、941 U+FFFD、CJK=0（评估 A21 为 18,402B/922）。

## 3. 开机自启（P0-3；任务计划程序）

现状（**已实测**）：注册表 Run、启动文件夹、计划任务、Windows 服务里都没有 script-server / pm2 自启产物；
PM2 7 在 Windows 没有 `pm2 startup`。交付脚本：`script-server\deploy\install-autostart.ps1`（幂等，含 `-Uninstall`）。

```powershell
# 安装（必须管理员 PowerShell）：脚本内先 pm2 save，再注册 AtStartup
# + 失败每 1 分钟重启（RestartInterval，最多 999 次）+ 当前用户 S4U + 隐藏窗口
powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\yang\Documents\workflow-platform-20261022\script-server\deploy\install-autostart.ps1
# 卸载
powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\yang\Documents\workflow-platform-20261022\script-server\deploy\install-autostart.ps1 -Uninstall
# 安装后自查（只读）
Get-ScheduledTask -TaskName 'WorkflowPlatform-ScriptServer' | Format-List TaskName,State
Get-ScheduledTaskInfo -TaskName 'WorkflowPlatform-ScriptServer' | Format-List LastRunTime,LastTaskResult
```

说明：任务名 `WorkflowPlatform-ScriptServer`；开机动作先 `pm2 resurrect`，60s 内 `/health` 非 200 则回退
`cmd /c "...\script-server\start-script-server.bat"`；运行日志 `logs\script-server-autostart.log`。
手工等价流程（**未实测**）：`pm2 save` → 运行安装脚本 → 真实重启 Windows → `GET /health` 200。
**未实测**：本轮未执行安装脚本、未真实重启 Windows（需管理员 + 真实重启，约束禁止动生产服务）。
**必须注意（已实测）**：现网 `~\.pm2\dump.pm2` mtime 10-23 00:11，进程 11:49 启动后没 `pm2 save`——旧 dump 会
resurrect 出旧 token → 平台全员 401（评估 P2-12）；任何 resurrect 前先 `pm2 save`。
验收（**未实测**）：真实重启主机后 3 分钟内 `/health` 200、跑通一次 `hello.py`。

## 4. 探活与告警（评估 P1-8）

新代码（lead 已改 `script_server.py`，v3.1.0，**待重启生效**；源码已核对）：
- `GET /health`（公开）：只返回 `status / version / auth_required / running_tasks / uptime_seconds / counters`，不校验 token；
- `GET /health/ready`（需 `X-Script-Token`，或 Bearer）：自检 scripts_dir 存在且可写、state_dir、
  python.exe、并发占用、locale；`status=degraded` 表示“活着但不能执行”；
- `GET /tasks`（需 token）：running 任务（pid / elapsed_seconds / timeout_seconds）、concurrency_limit、counters。

建议：平台探活从 `/health` 改为**带 token 调 `/health/ready`（或 `/tasks`）**，连续 3 次非 200 / 401 告警；
`/health` 只作进程存活探针。原因：`/health` 恒 200 时也可能 token 错 / 解释器坏 / 目录不可写
（评估实测：生产某时段 `/run` 连续 401 而 `/health` 一直 200，设备灯“在线”但任务全失败）。

```powershell
# 手工探针（未实测：本轮未调用；token 从 .env 复制，勿写进脚本或工单）
$t = '<WF_SCRIPT_TOKEN>'
(Invoke-WebRequest 'http://127.0.0.1:9000/health/ready' -Headers @{ 'X-Script-Token' = $t } -UseBasicParsing).Content
```

计数告警（`/health`、`/tasks` 的 `counters`）：`run_kill_failed`、`run_timeout`、`rejected_busy`（429）、
`spawn_errors` 非 0 要告警；`orphans_killed_at_startup` 非 0 说明上次崩溃/重启留下孤儿且启动对账生效，记录排查。

## 4.1 升级前排空（3.2.0 新增）

```powershell
# 先进入排空：拒绝新任务并等待运行中任务结束（返回 drained=true 再重启）
$t = '<WF_SCRIPT_TOKEN>'
curl.exe -s -X POST -H "X-Script-Token: $t" "http://127.0.0.1:9000/drain?timeout=600"
# 若返回 drained=false，按业务决定继续等或强制重启；重启后自动解除排空
pm2 restart script-server && pm2 save
```

## 5. 10 分钟定位清单

| # | 看什么 | 命令（只读） | 状态 |
|---|---|---|---|
| 1 | PM2 状态/重启次数 | `pm2 list`、`pm2 describe script-server` | 已实测（pm2 list） |
| 2 | 端口/进程 | `Get-NetTCPConnection -LocalPort 9000 -State Listen` | 已实测（PID 42428） |
| 3 | 进程存活 + counters | `curl.exe -s http://127.0.0.1:9000/health` | 已实测（旧代码字段，locale=cp936） |
| 4 | 能否执行 | 带 token GET `/health/ready`，看 `status` / `checks` | 未实测（新代码待重启；当期旧进程实测 404） |
| 5 | 在跑什么 | 带 token GET `/tasks`（pid / elapsed / limit） | 未实测（需 token，本轮未调） |
| 6 | 最近日志 | `pm2 logs script-server --lines 100 --nostream` | 已实测（只读，仍乱码） |
| 7 | 状态目录 | `Get-ChildItem script-server\.script_server_state -Recurse` | 未实测（旧进程未创建，见下） |
| 8 | 日志编码 | §2.2 严格解码命令 | 已实测（旧日志 922 U+FFFD） |
| 9 | 日志文件大小 | `Get-ChildItem logs` | 已实测（error 18KB / out 104KB） |
| 10 | 恢复动作 | `pm2 startOrRestart ecosystem.config.js --only script-server && pm2 save` | 未实测（会重启，本轮禁止） |

- 第 7 项：新代码默认状态目录 `<scripts 父级>\.script_server_state`（即 `script-server\.script_server_state`），含
  `results\<hash>.json`（幂等缓存）与 `running.json`（运行台账，重启时对账杀孤儿）；当前旧代码不存在该目录（**已实测** `Test-Path`=False）。
- 判定顺序：`/health` 200 但 `/health/ready` degraded → 解释器/目录问题；`/tasks` 中 `elapsed_seconds`
  远超 `timeout_seconds` → 卡死，按 pid 查进程并 `POST /cancel/{task_id}`；`pm2 list` 显示 errored /
  restarts 持续涨 → 看 `logs\script-server-error.log` 与 `logs\script-server-autostart.log`。
- 完成标准（**未实测**，需演练）：超时任务日志 CJK 可读、`/health/ready` 能区分 degraded、
  重启后不再有“跑着但 `/tasks` 看不见”的脚本。
