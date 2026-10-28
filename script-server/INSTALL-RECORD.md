# INSTALL-RECORD — 执行器安装/验收记录（按设备）

> 用途：记录每台执行机的安装动作与“实测”结果，供换机、排障、审计时对照。
> 当前架构：**Windows 计划任务直接托管 `python deploy\serve.py`**（uvicorn 单 worker，进程即服务本体），
> 另有 1 分钟看护任务；不依赖任何第三方进程守护/日志轮转组件。
> 相关文档：[INSTALL.md](INSTALL.md)（安装+使用）、[MAINTENANCE.md](MAINTENANCE.md)（运维）、
> [deploy/INSTALL-EXECUTOR.md](deploy/INSTALL-EXECUTOR.md)（新设备）。
> 历史：早期版本曾用第三方进程守护，相关配置与文档已于 2026-10-28 从仓库移除，需要追溯时看 git 历史。

## 1. 设备清单

| 设备 | 地址 | 账户 | 状态 | 说明 |
|---|---|---|---|---|
| `YANGSTARONE` | 192.168.1.220 | `YANGSTARONE\Administrator` | **在用**（生产执行机） | 平台 backend 在 192.168.1.210 |
| `WUYUAN` | 192.168.1.222 | `WUYUAN\Administrator` | 已停用 | 旧执行机，本机目录由其整体拷贝而来 |

## 2. YANGSTARONE 安装记录（2026-10-26 ~ 2026-10-28）

### 2.1 环境（实测）

| 组件 | 版本/路径 |
|---|---|
| OS | Windows 10.0.26100（16 逻辑核） |
| Python | 3.12.10 · `C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe` |
| 依赖 | fastapi 0.119.1 / uvicorn 0.49.0 / pydantic 2.13.4（满足 `requirements.txt`） |
| 执行器 | `script_server.py` 3.3.0（2026-10-28 由 3.2.0 加固升级） |

> 注意：普通 PATH 里 `python` 会先命 `WindowsApps\python.exe`（0 字节 Store 存根），
> 注册任务必须用真实解释器路径。`cutover-to-uvicorn-task.ps1` 的 `Find-Python` 已按候选路径规避。

### 2.2 安装动作

1. `C:\ScriptServer` 由旧机整体拷贝，日志移入 `C:\ScriptServer\logs\_from-other-pc_<时间戳>\`，
   旧机幂等缓存 `results\*` 移到 `.script_server_state\_from-other-pc_<时间戳>\`（否则 24h TTL 内同
   `task_run_id` 会命中旧机缓存而“假成功”）。
2. 复制 `.env.example` → `.env`：`WF_SCRIPT_TOKEN`（48 hex，沿用旧机值）、`WF_SCRIPT_REQUIRE_TOKEN=1`、
   `WF_SCRIPTS_DIR=C:\ScriptServer\scripts`、`WF_SCRIPT_STATE_DIR=C:\ScriptServer\.script_server_state`、
   `WF_ALLOWED_SCRIPT_ROOTS=E:\workflow-scripts-center`、`WF_MAX_CONCURRENCY=4`。
3. `python -m pip install -r requirements.txt`。
4. **一键安装（2026-10-27 17:41）**：`install.bat -nopause`（自提权）→ `deploy\cutover-to-uvicorn-task.ps1`：
   结束旧任务实例 → 注册主任务 + watchdog → 启动。

### 2.3 计划任务（实测）

| 项 | 主任务 `WorkflowPlatform-ScriptServer` | 看护 `…-Watchdog` |
|---|---|---|
| 触发 | `AtStartup` | 每 1 分钟 |
| 身份 | 当前账户 `S4U` + `Highest` | 同左 |
| 动作 | `<真实 python.exe> "…\deploy\serve.py"` | `powershell -File …\deploy\watchdog.ps1` |
| 设置 | `IgnoreNew`、不限时长 | 仅当 9000 无 listener 时拉起主任务 |
| 状态 | Running | Ready |

> `RestartOnFailure` 在本环境实测**不生效**（进程退出后任务停在 Ready），因此必须有 watchdog。

### 2.4 验收（实测）

| 日期 | 项目 | 结果 |
|---|---|---|
| 2026-10-26 | `verify-10.ps1`（计划任务托管） | ✅ 10/10 PASS |
| 2026-10-26 | Stop/Start 冷启动等价测试 | ✅ `/health` 立即 200 |
| 2026-10-27 | `verify-10.ps1`（`install.bat` 重装后） | ✅ 10/10 PASS |
| 2026-10-28 09:37 | `verify-10.ps1`（日志改造重启后） | ✅ 10/10 PASS（提权下端口检查正常） |
| 2026-10-28 10:32 | `verify-10.ps1`（3.3.0 加固重启后） | ✅ 10/10 PASS（version=3.3.0） |
| 2026-10-28 10:32 | `verify-hardening.py`（在线，11 项） | ✅ 11/11 PASS |
| 2026-10-28 | `verify-logging.py` | ✅ 17/17 PASS（含 audit 日志） |
| 2026-10-28 09:01:55 | **真实重启开机自启** | ✅ 09:02:12 自动起来（`[serve] start pid=5100`），无人登录 |

**开机自启验证依据**：系统事件日志 `System/6005` 显示 2026-10-28 09:01:55 启动；
`script-server-out.log` 中 `[serve] start pid=5100 … at=2026-10-28 09:02:12`，中间无人工操作。

### 2.5 平台侧（已生效）

- 平台 backend（192.168.1.210）每 60 秒调用 `http://192.168.1.220:9000/health/ready`，返回 200；
  访问记录见 `logs\script-server-access.log`（改造前在 error.log 里）。
- 设备登记：`base_url=http://192.168.1.220:9000`、`token` 与 `.env` 的 `WF_SCRIPT_TOKEN` 逐字符一致。
- 改 token 不需要重启执行器（平台侧改完即生效）；执行器侧改 token 需 `env-change-restart.bat`。

### 2.6 防火墙（现状，仅记录）

当前网络位置=**公用配置文件，防火墙为关闭**（域配置文件为开启但当前不是域网络），因此 9000 跨机可达，未加规则。
若以后启用防火墙，只放行平台网段：

```powershell
New-NetFirewallRule -DisplayName "script-server 9000" -Direction Inbound -Protocol TCP `
  -LocalPort 9000 -RemoteAddress 192.168.1.0/24 -Action Allow
```

### 2.7 本机的坑（实测证据）

- **DSH 沙箱里 `/run` 必 500**：`PermissionError: [WinError 5]`（asyncio 为子进程建命名管道被拒）；
  `/health*`、`/scan` 正常。与执行器无关——排查时先确认服务进程是否由计划任务在 `Session 0` 托管。
- `/scan` 的 `dir_path` 是 **query 参数**（`POST /scan?dir_path=…`），传 JSON body 会被忽略并返回 `{"files":[]}`；
  `GET /scan` 返回 405。
- DSH 沙箱里 `Get-NetTCPConnection` / `Get-ScheduledTask` / `Get-CimInstance` / `Get-Process` 不可信
  （会隐藏进程或直接拒绝），端口/任务/进程状态要用提权脚本或 `/health` 判断。
- 拷机后必须先处理 `.script_server_state\results`（见 2.2 第 1 条）。

### 2.8 日志改造（2026-10-28 09:37）

- 背景：旧日志把访问日志和业务日志混在一份 `-error.log` 里且无轮转。改造前实测量化：
  41,737 B / 393 行 = access 207 行 + WARNING 14 行 + INFO 其余，**ERROR 0 行**。
- 改动：`deploy\serve.py` 新增 `setup_logging()`，三份日志均按天轮转、保留 14 份：
  `script-server-access.log`（每请求一行）、`script-server-out.log`（INFO+，含 ERROR 副本）、
  `script-server-error.log`（仅 ERROR+）。启动行改为 `[serve] start …` 走 logger。
- 应用：提权跑 `logs\apply-logging.ps1`（暂停 watchdog → Stop/Start 任务 → 等 `/health` → 删标记 → 验收）。
  结果：新 pid 25888，`/health` 200（约 3s），`verify-10.ps1` 10/10。
- 线上验证：access.log 只含访问行；out.log 有业务 INFO/WARNING；error.log 自重启起 0 行新增。
- 未验证：跨午夜真实轮转（等 2026-10-29 00:00 后确认 `script-server-*.log.2026-10-28` 出现）；14 份淘汰需 15 天。

### 2.9 加固升级 3.2.0 → 3.3.0（2026-10-28 10:32）

- 背景：测评（`EXECUTOR-EVAL-20261027.md`）遗留项 + 用户确认「失败重试不能被幂等挡住」。
- 改动（`script_server.py`）：① 幂等指纹加入**脚本内容 sha256**（脚本改了同 `task_run_id` 会重跑）；
  ② 响应新增 `code`（200/500/502）与 `timed_out`；③ 新增 `block_strategy`（`reject` 默认 / `queue` 排队，
  `WF_QUEUE_MAX_WAIT_SECONDS` 默认 30s）；429/409 都带 `Retry-After`；④ 审计日志 `script-server-audit.log`；
  ⑤ 运行日志不再落明文 argv（只记脚本名 + `args_sha`）；⑥ 未知字段仍接受但打 WARNING。
- 改动（`deploy\serve.py`）：`setup_logging()` 增加第 4 份日志 `script-server-audit.log`（同样按天轮转、保留 14 份）。
- 应用：提权跑 `logs\apply-hardening.ps1`（暂停 watchdog → Stop/Start 任务 → 等 `/health` → 验收）。
  结果：10:32:27 `/health` 200 `version=3.3.0`（pid 17652），主任务 `State=Running`。
- 验收：`verify-10.ps1` **10/10 PASS**；`verify-hardening.py` **11/11 PASS**（首跑/幂等/内容指纹/失败 500/超时 502/
  reject 429+Retry-After/queue 排队 4.9s/审计三类事件/不落明文参数）；`verify-logging.py` **17/17 PASS**。
- 未验证：平台侧（192.168.1.210）尚未实测新的 `block_strategy=queue` 与 `code` 字段的消费逻辑（执行器侧已就绪）。
- 说明：失败/超时/取消**不写缓存**，所以平台「失败工作流里的重试」会真正重跑；幂等只防“已成功但响应丢失”的重投。

### 2.10 证据文件

| 内容 | 路径 |
|---|---|
| 重装全过程 | `C:\ScriptServer\logs\reinstall-report.log` |
| 任务注册/切换 | `C:\ScriptServer\logs\cutover.log`、`logs\register-task.log` |
| 日志改造 | `C:\ScriptServer\logs\apply-logging-20261028.log` |
| 加固升级 3.3.0 | `C:\ScriptServer\logs\apply-hardening-20261028.log` |
| 10 项验收结果 | `C:\ScriptServer\script-server\logs\verify-result.json` |
| 运维操作日志 | `C:\ScriptServer\script-server\logs\watchdog.log` |

## 3. 变更记录

| 日期 | 变更 |
|---|---|
| 2026-10-26 | 本机从旧机拷贝包补齐 `serve.py` / 注册脚本 / 验收脚本；改用计划任务托管 |
| 2026-10-27 17:41 | `install.bat` 一键重装：注册主任务 + watchdog；`Find-Python` 规避 Store 存根 |
| 2026-10-27 17:46 | 移除旧进程守护组件（配置、数据目录、命令入口） |
| 2026-10-28 09:37 | 日志拆成 access/out/error 三份 + 内置按天轮转保留 14 天 |
| 2026-10-28 10:32 | **3.3.0 加固**：内容指纹 / 三态 code / block_strategy / 审计日志（4 份日志）；verify-10 10/10 + verify-hardening 11/11 |
| 2026-10-28 | 文档清理：删除旧架构安装/运维文档与备份配置，统一到 INSTALL / MAINTENANCE / 本记录 |
