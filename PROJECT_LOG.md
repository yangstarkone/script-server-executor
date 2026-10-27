# PROJECT_LOG

> 本文件是项目的跨会话记忆，供人和 AI agent 共用。
> **读法**：只读「当前状态」+「待确认问题」+ 最近 3 条日志，不要通读全文。
> **详细安装/运维记录**：`script-server\INSTALL-RECORD.md`（按设备分节，第 10 节 = 本机）。

## 当前状态

- 目标：在 `C:\ScriptServer` 运行 standalone script-server 执行器（FastAPI/uvicorn，VERSION 3.2.0），供平台「设备管理」派发脚本。
- 本机：`YANGSTARONE` / `192.168.1.220` / 账户 `Administrator`；包从旧机 `WUYUAN`（192.168.1.222）整体拷贝而来。
- 运行方式：**不用 PM2**；Windows 计划任务 `WorkflowPlatform-ScriptServer`（AtStartup / S4U / Highest）直接托管 `python deploy\serve.py`（uvicorn 单 worker，`0.0.0.0:9000`），另有 1 分钟看护任务 `WorkflowPlatform-ScriptServer-Watchdog`。
- 现状（2026-10-27 17:41 用 `install.bat` 重装后）：主任务 Running，`/health` 200，`verify-10.ps1` **10/10 PASS**；动作已指向真实解释器 `...\Python312\python.exe`。
- 能跑通：`deploy\verify-10.ps1` 10/10（2026-10-27 重装后实测；2026-10-26 亦两次通过，Stop/Start 冷启动等价测试通过）。
- 不能跑通：无。
- 未验证：**真实重启后的开机自启**（未重启本机）；平台侧「设备管理」登记与「检测」在线（需在平台操作）。
- 已知问题：当前网络位置=公用配置文件且**防火墙关闭**（域配置文件为开启），未加 9000 放行规则；旧 PM2 残留 `~\.pm2`（daemon 未运行、无自启任务/Run 键，仅占空间）；DSH 沙箱里 `Get-NetTCPConnection`/`Get-ScheduledTask`/CIM 被拒，端口/任务状态不可在本 shell 里判断，以 `/health` 与提权脚本为准。
- 环境事实：`E:\workflow-scripts-center` **存在**（约 1.7 万文件，2026-10-26 同步），`.env` 的 `WF_ALLOWED_SCRIPT_ROOTS` 白名单有效。
- 下一步：① 真实重启一次并按 `INSTALL-RECORD.md` §6 清单核对；② 平台登记 `base_url=http://192.168.1.220:9000` + `.env` 的 token；③ 需要的话启用防火墙并只放行平台 IP/网段；④ 需要的话清理 `~\.pm2` 残留。
- 版本管理：GitHub 私有仓库 `script-server-executor`（main；`.env`、`logs/`、`.script_server_state/` 不入库）。
- 最后更新：2026-10-27

## 待确认问题

- 平台「设备管理」是否已登记本机（`base_url=http://192.168.1.220:9000` + `.env` 的 token）？未登记前平台侧「检测」不会在线。
- 是否保留与旧机相同的 `WF_SCRIPT_TOKEN`？（沿用拷贝值即可让平台用同一 token 登记本机，若要求每台一 token 需同步改平台）

---

## 日志（新→旧）

### 2026-10-27 用新安装器重装执行器（install.bat，替换旧 PM2 时代安装）

- 目标：按更新后的 `INSTALL.md` 重装执行器，替换旧（PM2 时代）安装。
- 预检：Python 3.12.10 + fastapi 0.119.1 / uvicorn 0.49.0 满足 `requirements.txt`；`.env` 沿用（token 未换）；旧计划任务仍在 Running（pid 4148，旧版 `serve.py`）；当前 shell 非管理员。
- 改（代码）：`deploy\cutover-to-uvicorn-task.ps1` 新增 `Find-Python`——优先固定候选路径找真实解释器，规避 `python` 解析到 `WindowsApps\python.exe`（0 字节 Store 存根；本机 machine PATH 中存根排在真实 Python 前）。不修的话任务会被注册成存根，永远起不来。
- 安装：`install.bat -nopause` 经 UAC 提权 → `cutover-to-uvicorn-task.ps1`：结束旧 pid 4148 → 注册主任务（真实 python + `serve.py`，Boot/S4U/Highest）→ 注册 watchdog → 启动；17:41:42 完成。
- 验收：`verify-10.ps1` **10/10 PASS**（含真实执行、幂等、超时、路径逃逸）；主任务 Running、watchdog Ready；`/health` 200 / `auth_required=true` / `concurrency=4`。node.exe 数安装前后均 5（DSH 等其它软件；PM2 daemon 未运行、`pm2.pid` 陈旧、无 PM2 自启任务/Run 键）。
- 证据：`C:\ScriptServer\logs\reinstall-report.log`（提权安装全过程）、`logs\cutover.log`、`script-server\logs\verify-result.json`。
- 坑：① DSH 沙箱里 `Get-NetTCPConnection`（CIM）、`Get-ScheduledTask`、`tasklist` 均被拒 → 会把"服务在跑"误判成"未监听"，端口/任务状态必须用提权脚本或 `/health` 判断；② 新 `cutover` 原用 `Get-Command python`，本机会注册成 Store 存根（已修）。
- 遗留：真实重启自启未验证；平台设备登记/检测未做；防火墙规则未加（当前公用配置文件关闭，跨机可达）；`~\.pm2` 残留未清理。

### 2026-10-27 初始化 Git 与 GitHub 私有仓库（安装前留档）

- 目标：把当前 `C:\ScriptServer` 纳入版本管理，安装/改造前留下可回滚基线。
- 结论：已推送到 https://github.com/yangstarkone/script-server-executor（PRIVATE，main），首提交 30 文件 `2133a5a`。
- 改：新增 `.gitignore`（排除 `.env`、`logs/`、`.script_server_state/`、`__pycache__/`、`*.log`）与 `README.md`；`script-server/INSTALL-RECORD.md` 第 47 行真实 token 改为占位符；`git init -b main`，仓库内身份 `yangstarkone <yangstarkone@users.noreply.github.com>`。
- 敏感信息检查：独立只读审计 PASS——索引内无凭据（真实 token 零命中、无硬编码默认 token），远端树无 `.env`/日志/状态目录；文档保留内网 IP、主机名、`Administrator` 等 LOW 信息（仓库 private，暂不处理）。
- 坑：① Git for Windows 走 schannel 访问 GitHub 报 `SEC_E_NO_CREDENTIALS`；② credential helper `!gh auth git-credential` 在 DSH 受限环境起不了 sh（signal pipe Win32 5）。可行推送法：`http.sslBackend=openssl` + 用 `GIT_CONFIG_*` 环境变量注入 `http.extraHeader="Authorization: Basic base64(x-access-token:<gh token>)"`，token 不进 argv、不落盘。
- 遗留：未加 gitleaks/secret scanning；`script-server/start-script-server.bat.pm2-bak` 作为历史备份一并提交。

### 2026-10-26 在新机 YANGSTARONE 装好 script-server 执行器（无 PM2）

- 目标：把旧机拷来的 `C:\ScriptServer` 在本机装成可开机自启的执行器。
- 结论：走 `INSTALL-RECORD.md` §8.3 的"计划任务直接托管 uvicorn"模型；PM2 不参与。拷贝包缺的 `serve.py` / 注册脚本 / 验收脚本按 §8.3 描述重建（详见 `script-server\INSTALL-RECORD.md` §10）。
- 改：新增 `script-server\deploy\serve.py`、`deploy\register-autostart-task.ps1`、`deploy\install-autostart-uvicorn.bat`、`deploy\verify-10.ps1`；`script-server\INSTALL-RECORD.md` 追加第 10 节；登记计划任务 `WorkflowPlatform-ScriptServer`。
- 坑：① 拷贝包里只有 PM2 时代 deploy 文件（无 serve.py），照 §9 装不起来 → 只能重建；② 在 DSH 受限 shell 里启动服务后 `/run` 必 500 `WinError 5`（asyncio 建子进程命名管道被拒），换计划任务托管即正常；③ `/scan` 的 `dir_path` 是 query 参数，传 JSON body 返回空列表；④ 拷来的 `.script_server_state\results` 会让同 `task_run_id` 在 24h 内命中旧机缓存（假成功）→ 已移走备份。
- 遗留：真实重启自启未验证；防火墙规则与平台登记未做。
