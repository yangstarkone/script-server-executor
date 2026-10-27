# script-server 执行器 · 运维手册

> 适用版本：`script_server.py` **3.2.0**；托管方式：**Windows 计划任务直接运行 `deploy\serve.py` + 1 分钟 watchdog**。
> 安装/首次验收见 [`INSTALL.md`](INSTALL.md)。
> 本文“实测”均指 2026-10-27 在 192.168.1.210（SERVICE）上的实际结果。

## 0. 架构一页

```text
Windows Task Scheduler
├─ WorkflowPlatform-ScriptServer          AtStartup / S4U / Highest / 无时长限制
│    └─ python.exe deploy\serve.py  ──►  uvicorn  0.0.0.0:9000   （进程即服务本体）
│          · 每次启动重读 .env
│          · stdout/stderr → logs\script-server-out.log / -error.log
│          · 进程退出后任务不会自己重跑（见 §4）
└─ WorkflowPlatform-ScriptServer-Watchdog  每 1 分钟 / S4U / Highest
     └─ powershell deploy\watchdog.ps1
          · 仅当 9000 无 listener 时 Start-ScheduledTask 主任务
          · 存在 .maintenance 文件时静默退出
```

平台侧每 60 秒探一次设备（`/health/ready`，带 token）；设备管理 `last_status=ok` = 在线且能执行。

## 1. 常用路径

| 项 | 路径 |
|---|---|
| 根目录 | `C:\ScriptServer\script-server` |
| 启动器 | `deploy\serve.py` |
| 配置 | `.env`（token / 脚本目录 / 状态目录 / 并发） |
| 业务脚本 | `C:\ScriptServer\scripts`，额外允许根 `E:\workflow-scripts-center` |
| 状态目录 | `C:\ScriptServer\.script_server_state`（缓存 + running.json） |
| 服务日志 | `logs\script-server-out.log`、`logs\script-server-error.log` |
| 看护日志 | `logs\watchdog.log` |
| 切换日志 | `C:\ScriptServer\logs\cutover.log` |
| 运维入口 | `deploy\ops-http.ps1`、`start-script-server.bat`、`env-change-restart.bat` |

> 上表以默认安装目录 `C:\ScriptServer` 为例；所有脚本都按自身位置推导路径，换目录也能用，
> 但 `.env` 里的 `WF_SCRIPTS_DIR` / `WF_SCRIPT_STATE_DIR` 等绝对路径要跟着改。

## 2. 启动 / 停止 / 重启

```powershell
$ops = 'C:\ScriptServer\script-server\deploy\ops-http.ps1'

powershell -NoProfile -ExecutionPolicy Bypass -File $ops restart   # drain → 停任务 → 起任务 → 等 health（普通窗口）
.\start-script-server.bat            # 同上（交互，结束 pause）；-nopause 供脚本调用
```

手工等价：

```powershell
schtasks /End /TN "WorkflowPlatform-ScriptServer"
schtasks /Run /TN "WorkflowPlatform-ScriptServer"
```

**注意**：

- `schtasks /End` 是正常停止；但 watchdog 会在 1 分钟内把它拉回来。要长时间停：
  `New-Item -ItemType File C:\ScriptServer\script-server\.maintenance`（恢复时删除）。
- 端口被别的进程占用时 `start-script-server.bat` 会报 `port_busy` 并提示 `netstat`；不要用开发用的 `start.bat`
  （它会无差别 taskkill 9000）。
- `Stop-ScheduledTask` 在受限策略下可能报 `Access is denied`：换**管理员**窗口重试；
  不要改成“直接杀进程”（会留孤儿，而且 watchdog 一分钟后照样拉起）。
- **Agent 约定**：不要自行重启 Windows；重启/升级前先 `/drain`；任何一步失败就停下，
  把命令、退出码、`logs\script-server-error.log` 末尾 30 行一起回报，不要继续后面的步骤。

## 3. 改配置（`.env`）

`serve.py` 每次启动重读 `.env`，**重启即生效**；不存在 dump 文件残留旧 token 的问题。

```bat
env-change-restart.bat          :: 重启 + 打印生效配置（token 打码）+ 校验并发数
env-change-restart.bat edit     :: 先开记事本改 .env，存盘关掉后自动重启
env-change-restart.bat nopause  :: 供脚本调用
```

退出码：`0` 成功 / `1` 重启或 ready 失败 / `2` 并发数不一致（说明 `.env` 没被读进去）。
校验锚点：`/health/ready` 的 `checks.concurrency.limit` 必须等于 `.env` 的 `WF_MAX_CONCURRENCY`。

> 改 token 后，平台 `device.token`（或旧 setting `host_token`）要同步改，否则平台侧全部 401。

## 4. 进程看护（watchdog）

**实测：计划任务的 `RestartOnFailure` 不生效。** 外部杀进程后 `LastTaskResult=0xFFFFFFFF`、`State=Ready`；
动作进程 `exit 7` 后 `LastTaskResult=7`、`LastRunTime` 不再前进——都不重跑。因此必须有 watchdog：

```powershell
# 安装/刷新（管理员）
powershell -NoProfile -ExecutionPolicy Bypass -File C:\ScriptServer\script-server\deploy\install-watchdog.ps1
# 删除（管理员）
powershell -NoProfile -ExecutionPolicy Bypass -File C:\ScriptServer\script-server\deploy\install-watchdog.ps1 -Uninstall

# 状态与日志
Get-ScheduledTask -TaskName 'WorkflowPlatform-ScriptServer-Watchdog' | Select-Object TaskName,State
Get-Content C:\ScriptServer\script-server\logs\watchdog.log -Tail 10 -Encoding UTF8
```

- 恢复时间：实测 **37–56 秒**（等待下一次 tick）；平台下一次探活（≤60 秒）回到 `ok`。
- 看护绝不杀正在跑任务的执行器：只要 9000 有 listener 就直接退出。
- 暂停：建 `.maintenance`；忘记删 = 永久静默。

## 5. 日志与磁盘

| 日志 | 位置 | 内容 |
|---|---|---|
| 服务输出 | `logs\script-server-out.log` | uvicorn access、`[serve] start pid=...` |
| 服务错误 | `logs\script-server-error.log` | 启动 INFO、异常栈 |
| 看护 | `logs\watchdog.log` | 仅在“掉了→拉起”时写 |
| 切换 | `C:\ScriptServer\logs\cutover.log` | 安装/切换过程 |
| 验收结果 | `C:\ScriptServer\logs\verify-result.json` | `verify-10.ps1` 的 JSON |

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File $ops logs   # 两个服务日志 + 切换日志（UTF-8）
```

**没有自动轮转**（`serve.py` 以追加方式直接写文件，没有内置轮转）。单机约 100KB/天，短期无风险；长期建议每月归档：

```powershell
# 停任务 → 改名 → 起任务（serve.py 以追加方式打开新文件）
schtasks /End /TN "WorkflowPlatform-ScriptServer"
Get-ChildItem C:\ScriptServer\script-server\logs\*.log |
  ForEach-Object { Move-Item $_.FullName ($_.FullName + '.' + (Get-Date -Format yyyyMMdd)) -Force }
schtasks /Run /TN "WorkflowPlatform-ScriptServer"
```

日志含完整 args，可能敏感；读中文用 `-Encoding UTF8`。

## 6. 巡检与告警

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File $ops status   # 端口/进程/任务/health/切换日志
powershell -NoProfile -ExecutionPolicy Bypass -File $ops ready    # 带 token 的就绪探针
powershell -NoProfile -ExecutionPolicy Bypass -File $ops tasks    # 运行中任务
```

判据：

- `/health` 200：进程活着；`version=3.2.0`、`auth_required=true`、`draining=false`。
- `/health/ready` 200 且 `status=ok`：脚本目录可写、状态目录可用、解释器存在、并发未满。
- `status=degraded`：活着但不能执行（看 `checks` 哪项 false）。
- 401：调用方 token ≠ `.env`。
- `/tasks` 的 `elapsed_seconds` 远超 `timeout_seconds` → 卡死，`POST /cancel/{task_id}`。

告警建议：`/health/ready` 连续 3 次非 200 或 `degraded`；端口不可达 > 60s；
`counters.spawn_errors` / `kill_failed` / `rejected_busy` / `conflict_409` 持续增长。

## 7. 排空、升级、回滚

```powershell
# 1) 排空（粘性：拒绝新任务，重启才解除）
powershell -NoProfile -ExecutionPolicy Bypass -File $ops drain
# 2) 备份 script_server.py 后覆盖新文件（不要覆盖 .env）
# 3) 依赖有变化时
cd C:\ScriptServer\script-server; python -m pip install -r requirements.txt
# 4) 重启并验收
powershell -NoProfile -ExecutionPolicy Bypass -File $ops restart
powershell -NoProfile -ExecutionPolicy Bypass -File C:\ScriptServer\script-server\deploy\verify-10.ps1
```

升级/重启会中断在跑任务（启动对账会杀孤儿），平台侧会收到失败，安排在低峰。
回滚：恢复备份的 `script_server.py` → `ops-http.ps1 restart` → 验证 `/health` 的 `version`。

## 8. 卸载

```powershell
# 1) 删看护（管理员），否则它会把主任务拉回来
powershell -NoProfile -ExecutionPolicy Bypass -File C:\ScriptServer\script-server\deploy\install-watchdog.ps1 -Uninstall
# 2) 停并删主任务（管理员）
schtasks /End /TN "WorkflowPlatform-ScriptServer"
schtasks /Delete /TN "WorkflowPlatform-ScriptServer" /F
# 3) 防火墙
Remove-NetFirewallRule -DisplayName "script-server 9000" -ErrorAction SilentlyContinue
# 4) 目录（确认无业务数据）
Remove-Item -Recurse -Force C:\ScriptServer
```

最后在平台「设备管理」停用/删除该设备，否则平台会持续探活报错。

## 9. token 轮换

```powershell
# 1) 生成新 token
python -c "import secrets;print(secrets.token_hex(24))"
# 2) 写入 .env 的 WF_SCRIPT_TOKEN，然后重启（会重读 .env）
.\env-change-restart.bat nopause
# 3) 平台「设备管理」把 token 改成新值（或 setting host_token）
# 4) 验证：旧 token 401，新 token 200
curl.exe -s -o NUL -w "%{http_code}" -H "X-Script-Token: <旧>" http://127.0.0.1:9000/health/ready
curl.exe -s -o NUL -w "%{http_code}" -H "X-Script-Token: <新>" http://127.0.0.1:9000/health/ready
```

## 10. 10 分钟定位清单

| # | 看什么 | 命令（只读） |
|---|---|---|
| 1 | 端口/进程 | `Get-NetTCPConnection -LocalPort 9000 -State Listen` |
| 2 | 任务状态/动作 | `Get-ScheduledTask -TaskName 'WorkflowPlatform-ScriptServer' \| fl State,Actions` |
| 3 | 任务最近结果 | `Get-ScheduledTaskInfo -TaskName 'WorkflowPlatform-ScriptServer' \| fl LastRunTime,LastTaskResult` |
| 4 | 存活 | `powershell -File $ops health` |
| 5 | 能否执行 | `powershell -File $ops ready`（看 status/checks） |
| 6 | 在跑什么 | `powershell -File $ops tasks` |
| 7 | 最近日志 | `powershell -File $ops logs` |
| 8 | 看护是否工作 | `Get-Content C:\ScriptServer\script-server\logs\watchdog.log -Tail 10` |
| 9 | 平台视角 | 设备管理 `last_status/last_checked_at`，或 `docker exec docker-postgres-1 psql -U wf -d workflow -c "select name,last_status,last_error from device"` |
| 10 | 恢复 | `powershell -File $ops restart`；仍失败看 `logs\script-server-error.log` 末尾 |

判定顺序：无 listener → 看任务是否 Ready + watchdog 日志 → 有 listener 但 ready 失败 → 看 `checks` 与 error.log →
`/tasks` 有超时任务 → `/cancel` → 最后才重启（重启会杀正在跑的任务）。

### 10.1 常见坑

| 现象 | 原因 | 处理 |
|---|---|---|
| 进程被杀后不恢复 | `.maintenance` 还在 / watchdog 被删 | 删 `.maintenance`；重建 watchdog |
| 开机后 9000 未起 | 任务未注册 / S4U 失败 / 任务被停 | `ops-http.ps1 status`；重跑 `cutover-to-uvicorn-task.ps1` |
| `deploy\*.ps1` 报 `Missing closing '}'` | 含中文的 `.ps1` 存成了 UTF-8 **无 BOM**，PS 5.1 按 ANSI 读 | 另存为 UTF-8 with BOM；或用 `powershell -File` 前先转换 |
| `/health` 200 但平台全 401 | 平台设备 token ≠ `.env` | 两边逐字符核对，改完不用重启执行器 |
| 改了 `.env` 不生效 | 没重启 / 改错文件 | `env-change-restart.bat`（会校验并发数） |
| 开机后平台探活报 `ConnectError` | 执行器还没起完 / 防火墙 | 先 `ops-http.ps1 status`；跨机直连再查 9000 入站规则 |
| `start.bat` 杀掉别的程序 | 它无差别 taskkill 9000 | 生产禁用 `start.bat` |

## 11. 变更记录

| 日期 | 变更 |
|---|---|
| 2026-10-27 | 执行器改为计划任务托管 `serve.py`；新增 watchdog（实测 `RestartOnFailure` 无效）；清理旧守护进程相关文件；本文合并原 `OPERATIONS-executor.md` 与原说明书第 5 章 |
