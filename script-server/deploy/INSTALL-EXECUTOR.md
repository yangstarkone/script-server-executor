# 在新设备上部署执行器（script-server）

> 适用：**只装执行器**的 Windows 设备（平台 backend/Redis 在别的机器）。
> 目标：`http://<新设备IP>:9000` 提供鉴权后的脚本执行能力，平台「设备管理」里能探活并派发任务。
> 版本：script-server 3.3.2；**托管方式：Windows 计划任务直接跑 `python deploy\serve.py` + 1 分钟 watchdog**，无第三方守护进程。
> 本文命令均以 `C:\ScriptServer\script-server` 为例，按实际路径替换。

## 0. 需要装什么（一句话）

| 组件 | 是否必须 | 用途 |
|---|---|---|
| **Python 3.10+**（3.12 实测） | 必须 | 运行执行器与业务脚本 |
| **管理员权限**（UAC 一次） | 必须（注册计划任务） | 开机自启 + 1 分钟看护 |
| git / 整个平台仓库 | 不需要 | 新设备只需 `script-server\` 目录 + `scripts\` 目录 |
| Node.js | 不需要 | 执行器与托管都不用 Node |
| Redis / 数据库 | 不需要 | 执行器不连 Redis，也不碰平台数据库 |

## 1. 拷贝目录

在新设备上建目录，例如 `C:\ScriptServer\`，最终结构：

```text
C:\ScriptServer\
├─ script-server\                     ← 从仓库复制整个目录
│  ├─ script_server.py                ← 服务本体（FastAPI，单 worker）
│  ├─ requirements.txt
│  ├─ .env.example                    ← 复制成 .env 后填 token
│  ├─ INSTALL.md / MAINTENANCE.md / INSTALL-RECORD.md
│  ├─ install.bat                     ← 一键安装（自提权）
│  ├─ start-script-server.bat         ← 启动/重启（自提权）
│  ├─ env-change-restart.bat          ← 改 .env 后重启并回显生效值
│  └─ deploy\
│     ├─ serve.py                     ← 启动器（读 .env、日志分级轮转、前台 uvicorn）
│     ├─ cutover-to-uvicorn-task.ps1  ← 注册主任务 + watchdog 并启动（install.bat 会调用）
│     ├─ install-watchdog.ps1         ← 单独注册/卸载 watchdog
│     ├─ watchdog.ps1                 ← 每分钟检查 9000，掉了就拉起主任务
│     ├─ ops-http.ps1                 ← 运维入口（health/ready/tasks/logs/status/restart/drain）
│     ├─ verify-10.ps1                ← 10 项验收
│     ├─ verify-logging.py            ← 日志分级/轮转离线验收（17 项）
│     └─ verify-hardening.py          ← 幂等/三态码/并发策略/审计在线验收（11 项）
└─ scripts\                          ← 业务脚本目录（也可改 WF_SCRIPTS_DIR 指向别处）
   ├─ hello.py
   └─ ...
```

> 不要只拷 `script_server.py`：`requirements.txt` 与整个 `deploy\` 都要带上（`install.bat` 依赖其中的注册脚本）。

## 2. 安装 Python 依赖

```powershell
cd C:\ScriptServer\script-server
python -m pip install -r requirements.txt
# 内网/离线：先在有网机器 pip download -r requirements.txt -d wheels，再 pip install --no-index --find-links wheels -r requirements.txt
```

> 确认 `python` 指向真实解释器（不是 `WindowsApps` 的 0 字节 Store 存根）：
> `python -c "import sys;print(sys.executable)"`。`cutover-to-uvicorn-task.ps1` 也会按候选路径找真实解释器，但仍建议先确认。

## 3. 配置 `.env`（同目录）

```powershell
copy .env.example .env
notepad .env
```

最少改这几项：

```ini
# 1) 必填：调用方鉴权 token（生成：python -c "import secrets;print(secrets.token_hex(24))"）
WF_SCRIPT_TOKEN=<生成的 48 位 hex>

# 2) token 为空时拒绝启动（生产建议）
WF_SCRIPT_REQUIRE_TOKEN=1

# 3) 脚本目录（不填默认 = <script-server 父目录>\scripts）
# WF_SCRIPTS_DIR=C:\ScriptServer\scripts

# 4) 并发上限（默认 4；执行机核少/脚本重时调低）
# WF_MAX_CONCURRENCY=4
```

> `.env` 含凭据：不要提交代码库/共享目录，Windows 上建议把目录 ACL 收紧到运行账户。
>
> 3.2.0 新行为：① token 为空且监听非回环地址会**拒绝启动**（仅调试可 `WF_SCRIPT_ALLOW_INSECURE=1`）；
> ② 请求 `env` 不允许覆盖 `PYTHONPATH`/`PATH`/`WF_SCRIPT_*` 等（会 400）；③ `--workers` 必须为 1，多 worker 拒绝启动；
> ④ 升级前建议 `POST /drain?timeout=600` 排空在跑任务。

## 4. 一键安装（推荐）

双击 `script-server\install.bat`（或命令行 `install.bat -nopause`）。它会：

1. 自提权（UAC）；
2. 结束旧的执行器进程/task（若有）；
3. 用 `deploy\cutover-to-uvicorn-task.ps1` 注册主任务 `WorkflowPlatform-ScriptServer`
   （`AtStartup` / `S4U` / `Highest` / 不限时长）与看护任务 `WorkflowPlatform-ScriptServer-Watchdog`（每 1 分钟）；
4. 启动主任务并调用 `ops-http.ps1 status` 回显状态。

手工等价（管理员 PowerShell）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File C:\ScriptServer\script-server\deploy\cutover-to-uvicorn-task.ps1
# 只改 .env：不用重装，restart 即可（serve.py 每次启动重读 .env）
powershell -NoProfile -ExecutionPolicy Bypass -File C:\ScriptServer\script-server\deploy\ops-http.ps1 restart
```

## 5. 验证

```powershell
# 5.1 健康（不需要 token）：应看到 version=3.3.2、auth_required=true
curl.exe http://127.0.0.1:9000/health

# 5.2 就绪（需要 token）：status=ok + checks（解释器/目录/状态目录/并发）
curl.exe -H "X-Script-Token: <WF_SCRIPT_TOKEN>" http://127.0.0.1:9000/health/ready

# 5.3 真跑一个脚本（exit_code=0 即通）
curl.exe -X POST http://127.0.0.1:9000/run -H "X-Script-Token: <WF_SCRIPT_TOKEN>" `
  -H "Content-Type: application/json" -d '{\"file_path\":\"hello.py\",\"timeout_seconds\":30}'

# 5.4 10 项验收（提权窗口下跑，端口检查才准）
powershell -NoProfile -ExecutionPolicy Bypass -File C:\ScriptServer\script-server\deploy\verify-10.ps1
# 5.5 日志分级/轮转离线验收（17 项）
python C:\ScriptServer\script-server\deploy\verify-logging.py
# 5.6 加固项在线验收（幂等/内容指纹/三态码/并发策略/审计，11 项，需要服务在跑 + token 在 .env）
python C:\ScriptServer\script-server\deploy\verify-hardening.py
```

平台侧：设备管理 → 新增设备 `name=任意（如 exec-220）`、`base_url=http://<新设备IP>:9000`、
`token=` 与 `.env` 的 `WF_SCRIPT_TOKEN` 逐字符一致；保存后点「检测」应在线（走 `/health/ready`）。

## 6. 开机自启与看护

- 主任务 `WorkflowPlatform-ScriptServer`：`AtStartup` + `S4U` + `Highest`，动作
  `<python.exe> "…\deploy\serve.py"`，工作目录 `…\script-server`；开机即起，不等登录。
- 看护任务 `WorkflowPlatform-ScriptServer-Watchdog`：每 1 分钟检查 9000；**只**在无 listener 时
  `Start-ScheduledTask` 主任务，不会杀正在跑的进程。存在 `script-server\.maintenance` 文件时静默退出
  （维护时建它，完事删掉）。

> 实测（2026-10-27）：计划任务的 `RestartOnFailure` 在本环境**不生效**——进程被杀后任务停在 `State=Ready`
> 不再重跑；watchdog 实测 37~56 秒恢复。所以 watchdog 是必需的，不要删。

**装完必须做一次真实重启验收**（本机 2026-10-28 已通过）：

```powershell
# 重启 Windows，等 3 分钟后：
powershell -NoProfile -ExecutionPolicy Bypass -File C:\ScriptServer\script-server\deploy\ops-http.ps1 status
# 期望：端口 LISTENING、任务 Running、/health 200
```

## 7. 路径边界（默认 fail-closed）

执行器**默认只允许执行 `WF_SCRIPTS_DIR` 下的 `.py/.pyw`**：

| 请求 | 结果 |
|---|---|
| `/run {"file_path":"hello.py"}`（相对路径，在脚本目录内） | 执行 |
| `/run {"file_path":"C:\\Windows\\...\\x.py"}`（目录外绝对路径） | **403** |
| `/run {"file_path":"..\\..\\x.py"}`（相对逃逸） | **403** |
| `/scan?dir_path=C:\\其他目录` | **403** |

确需执行脚本目录外的固定目录时，在 `.env` 显式加白名单（分号分隔）后重启：

```ini
WF_ALLOWED_SCRIPT_ROOTS=D:\other-scripts;E:\jobs
WF_SCAN_ALLOWED_ROOTS=D:\other-scripts;E:\jobs
```

> 白名单是**目录**级。脚本内部能做什么（读写文件、调 API）由脚本自身权限决定，执行器只负责"只执行允许目录里的脚本"。

## 8. 日志（四份，内置按天轮转、保留 14 天）

| 文件（`script-server\logs\`） | 内容 |
|---|---|
| `script-server-access.log` | 每请求一行（客户端 / 方法 / 路径 / 状态码） |
| `script-server-out.log` | INFO+：启动、任务开始/完成、超时/取消、WARNING、ERROR 副本；**不含明文 argv**（只记脚本名 + args_sha） |
| `script-server-error.log` | **仅 ERROR+**，平时为空 |
| `script-server-audit.log` | 审计：一次运行一行 JSON（`run.start`/`run.dedup`/`run.finish`/`run.reject`/`run.cancel`；run_id、客户端 IP、脚本名、脚本 sha、参数 sha、耗时、code/exit_code） |

轮转：`when=midnight`、`backupCount=14`，历史文件 `<文件名>.YYYY-MM-DD`，UTF-8 写入；无需任何外部轮转工具。

## 9. 常见问题

| 现象 | 处理 |
|---|---|
| `install.bat` 闪退 | 用 cmd 打开再运行，或在命令行跑 `install.bat -nopause` 看报错；需管理员 |
| `/health` 404 或字段很少 | 跑的是旧代码/旧进程：`ops-http.ps1 restart` 后看 `version=3.3.2` |
| 平台「检测」401/离线 | 设备里登记的 token 与 `.env` 的 `WF_SCRIPT_TOKEN` 不一致（不用重启执行器） |
| `POST /run` 403 | 路径不在允许目录：用相对路径或配 `WF_ALLOWED_SCRIPT_ROOTS` |
| `WinError 10048`（端口占用） | 残留进程：`netstat -ano | findstr :9000` → `taskkill /F /T /PID <pid>` → `ops-http.ps1 restart` |
| 429 执行机繁忙 | 并发满：响应带 `Retry-After: 5` 可退避；或调大 `WF_MAX_CONCURRENCY`（注意 CPU/内存）；或调用方改传 `"block_strategy":"queue"` 让执行器排队 |
| 日志中文乱码 | 日志统一 UTF-8 写入，读端用 `-Encoding UTF8` / UTF-8 解码 |
| 状态目录不可写 | 默认 `<脚本目录上级>\.script_server_state`；可用 `WF_SCRIPT_STATE_DIR` 指到可写目录 |
| 想彻底停掉 | 先建 `.maintenance`（否则 watchdog 1 分钟内拉起）→ `Stop-ScheduledTask` |

## 10. 升级与卸载

```powershell
# 升级（不中断正在跑的任务优先）
powershell -File C:\ScriptServer\script-server\deploy\ops-http.ps1 drain   # 排空
# 覆盖 script_server.py / serve.py / requirements.txt / deploy\ 下脚本（别覆盖 .env）
python -m pip install -r requirements.txt        # 依赖有变化时
powershell -File C:\ScriptServer\script-server\deploy\ops-http.ps1 restart
powershell -File C:\ScriptServer\script-server\deploy\verify-10.ps1
```

> 升级/重启会中断在跑任务（启动对账会清理孤儿，平台侧会收到取消/失败），尽量安排在低峰。

卸载：

```powershell
# 1) 删看护（管理员；否则它会把主任务拉回来）
powershell -File C:\ScriptServer\script-server\deploy\install-watchdog.ps1 -Uninstall
# 2) 停并删主任务
schtasks /End /TN "WorkflowPlatform-ScriptServer"
schtasks /Delete /TN "WorkflowPlatform-ScriptServer" /F
# 3) 防火墙规则（若加过）
Remove-NetFirewallRule -DisplayName "script-server 9000" -ErrorAction SilentlyContinue
# 4) 目录（确认无业务数据）
Remove-Item -Recurse -Force C:\ScriptServer
```

最后在平台「设备管理」停用/删除该设备，否则平台会持续探活报错。

## 11. 网络与防火墙

平台与执行器不在同一台机器时，需放行入站 TCP 9000（只放行平台 IP 更安全）：

```powershell
New-NetFirewallRule -DisplayName "script-server 9000" -Direction Inbound -Protocol TCP `
  -LocalPort 9000 -RemoteAddress <平台IP/网段> -Action Allow
```
