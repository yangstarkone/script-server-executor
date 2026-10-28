# PROJECT_LOG

> 本文件是项目的跨会话记忆，供人和 AI agent 共用。
> **读法**：只读「当前状态」+「待确认问题」+ 最近 3 条日志，不要通读全文。
> **详细安装/运维记录**：`script-server\INSTALL-RECORD.md`（按设备记录，§2 = 本机）。

## 当前状态

- 目标：在 `C:\ScriptServer` 运行 standalone script-server 执行器（FastAPI/uvicorn，VERSION 3.3.0），供平台「设备管理」派发脚本。
- 本机：`YANGSTARONE` / `192.168.1.220` / 账户 `Administrator`；包从旧机 `WUYUAN`（192.168.1.222）整体拷贝而来。
- 运行方式：Windows 计划任务 `WorkflowPlatform-ScriptServer`（AtStartup / S4U / Highest）直接托管 `python deploy\serve.py`（uvicorn 单 worker，`0.0.0.0:9000`），另有 1 分钟看护任务 `WorkflowPlatform-ScriptServer-Watchdog`。
- 现状（2026-10-28 10:32 为 3.3.0 加固重启后，pid 17652）：主任务 Running，`/health` 200 `version=3.3.0`；日志 4 份（access/out/error/**audit**）均按天轮转保留 14 天；响应新增 `code`/`timed_out`、请求新增 `block_strategy`、幂等指纹含脚本 sha256。
- 能跑通：`deploy\verify-10.ps1` **10/10**（2026-10-28 10:32 / 09:37 / 10-27 / 10-26）；`deploy\verify-hardening.py` **11/11**（2026-10-28 10:32，在线）；`deploy\verify-logging.py` **17/17**（离线）。
- 不能跑通：无。
- 已验证：**真实重启开机自启 ✅**（系统 2026-10-28 09:01:55 启动，09:02:12 `serve.py` 自启，无人登录干预）；**平台设备登记 ✅**（192.168.1.210 每 60s 调 `/health/ready` 返回 200）。
- 未验证：跨午夜的真实日志轮转（需等 2026-10-29 00:00 后看是否出现 `script-server-*.log.2026-10-28`）；14 份淘汰需 15 天观察；**平台侧尚未实测 `block_strategy=queue` 与 `code` 字段的消费**（执行器侧已就绪）。
- 已知问题：当前网络位置=公用配置文件且**防火墙关闭**（域配置文件为开启），未加 9000 放行规则；DSH 沙箱里 `Get-NetTCPConnection`/`Get-ScheduledTask`/`Get-CimInstance`/`Get-Process` 均不可信（会隐藏进程或直接拒绝），端口/任务/进程状态必须用提权脚本或 `/health` 判断。
- 环境事实：`E:\workflow-scripts-center` **存在**（约 1.7 万文件，2026-10-26 同步），`.env` 的 `WF_ALLOWED_SCRIPT_ROOTS` 白名单有效；本机 `node.exe` 进程属于 WorkBuddy、紫鸟等其它软件，不是执行器。
- 下一步：① 2026-10-29 00:00 后确认首次跨天轮转产生归档；② 需要的话启用防火墙并只放行平台 IP/网段；③ 测评剩余 P0-1（执行器降权 + 脚本目录 ACL，系统级，需确认）。
- 文档（2026-10-28 清理后）：`INSTALL.md`（安装+使用，附录 E = 3.3.0 变更）、`MAINTENANCE.md`（运维）、`deploy\INSTALL-EXECUTOR.md`（新设备）、`INSTALL-RECORD.md`（本机安装记录 §2.9 = 3.3.0）、`EXECUTOR-EVAL-20261027.md`（测评 + §9 修复状态）；旧架构配置/文档已删除，仓库内已无相关引用。
- 版本管理：GitHub 私有仓库 `script-server-executor`（main；`.env`、`logs/`、`.script_server_state/` 不入库）。
- 选型调研（2026-10-28）：GitHub 上无覆盖本场景（被中心平台调用/无头/token/幂等/并发上限/Windows 计划任务）的现成项目，结论**继续自研、只借鉴机制**（详见当日日志）。
- 测评（2026-10-27 18:40，详见 `script-server\EXECUTOR-EVAL-20261027.md`）：作为内网单机执行器 **7.0/10（合格）**。2026-10-28 已修：幂等缓存陈旧、未知字段静默、429 无 Retry-After、无审计、argv 落盘、超时/失败不区分（3.3.0）。仍未修：token=管理员级 RCE + 明文 HTTP 无 TLS、零自动化测试（pytest/CI）、args 空引号、`/scan` 相对路径。
- 最后更新：2026-10-28

## 待确认问题

- 是否保留与旧机相同的 `WF_SCRIPT_TOKEN`？（沿用拷贝值即可让平台用同一 token 登记本机，若要求每台一 token 需同步改平台）
- 是否继续做测评剩余 P0-1：执行器降权到低权限账户 + 脚本目录 ACL（要改计划任务账户与 ACL，属系统级改动，需确认后再动）。
- 平台侧是否要改用新能力：`code` 判定（200/500/502）、`block_strategy=queue`（替代 429 退避）、409 的 `Retry-After` 等待逻辑？（执行器已就绪，需平台侧配合改）

---

## 日志（新→旧）

### 2026-10-28 执行器 3.3.0 加固（脚本内容指纹 / 三态 code / 阻塞策略 / 审计日志）

- 触发：用户问「平台的失败重试会不会被 run_id 幂等挡住」并批准其余加固项，约束「只用 Python、别把系统弄复杂」。
- **结论（该问题）**：不影响。失败/超时/取消**从不写缓存**（3.2.0 起就只缓存 exit 0），同一 `task_run_id` 重投会真正重跑；幂等只防“已成功但响应丢失”的重复执行。会挡住重试的只有两种情况：① 同 ID 仍在跑 → 409（现带 `Retry-After: 5`）；② 脚本改过而指纹没变 → 旧 bug，3.3.0 把脚本 sha256 纳入指纹后消失。
- 改 `script_server.py`（3.2.0 → 3.3.0）：① 幂等指纹加脚本内容 sha256；② `RunResponse` 增 `code`(200/500/502) 与 `timed_out`；③ `RunRequest` 增 `block_strategy`（`reject` 默认 / `queue`，`WF_QUEUE_MAX_WAIT_SECONDS`=30），429/409 带 `Retry-After: 5`；④ 审计 logger `audit`：`run.start`/`run.dedup`/`run.finish`/`run.reject`/`run.cancel` 一行 JSON（run_id、客户端 IP、脚本名+sha、args_sha、耗时、code/exit_code）；⑤ INFO 不再落明文 argv；⑥ 未知字段仍接受但打 WARNING；⑦ 计数器加 `queued`；⑧ 幂等查询移到路径校验之后（缓存不能绕过校验）。
- 改 `deploy\serve.py`：第 4 份日志 `script-server-audit.log`（同样 `when=midnight`/`backupCount=14`，`audit` logger `propagate=False`）。
- 新增 `deploy\verify-hardening.py`（11 项在线验收，纯 stdlib urllib，自动清理临时脚本）；`verify-logging.py` 扩到 17 项（含 audit）；`verify-10.ps1` 版本断言改 3.3.0；`ops-http.ps1 logs` 显示 audit。
- 应用/验收：提权跑 `logs\apply-hardening.ps1` → 10:32:25 重启（pid 17652）、10:32:27 `/health` 200 `version=3.3.0`；`verify-10.ps1` **10/10**、`verify-hardening.py` **11/11**、`verify-logging.py` **17/17**（实测：queue 等 4.9s 后成功、429 `Retry-After='5'`、改脚本后同 ID 返回 PROBE-B、超时 `code=502`）。
- 踩坑：① 本会话 shell 是 **Windows PowerShell 5.1**（不是 pwsh 7），`Get-Content -Raw` 默认按 ANSI 解码，用它改中文文件会把 UTF-8 写坏 → 已 `git checkout` 复原，改用编辑工具/Python 处理；② PATH 里 `python` 是 WindowsApps 0 字节存根，编译/运行必须用 `%LOCALAPPDATA%\Programs\Python\Python312\python.exe`。
- 未验证：平台侧（192.168.1.210）尚未消费新字段 `code`/`block_strategy`；跨午夜真实轮转仍待 2026-10-29 00:00。
- 文档：`INSTALL.md` 附录 E、`MAINTENANCE.md` §5/§11、`deploy\INSTALL-EXECUTOR.md` §5/§8/§9、`INSTALL-RECORD.md` §2.9、`EXECUTOR-EVAL-20261027.md` §9、`.env.example`、`README.md`。

### 2026-10-28 XXL-JOB 深入调研（哪些设计能借到本执行器上）

- 版本/许可：最新 **3.5.0（2026-10-09）**，**GPL-3.0** → 只借鉴设计，不复制代码。
- 已核对源码（master，未部署实测）：`Const.java`（`XXL-JOB-ACCESS-TOKEN` / `XXL-JOB-APPNAME` 请求头、`BEAT_TIMEOUT=30s`、`DEAD_TIMEOUT=90s`、优雅停机等待 5s）、`EmbedServer.java`（Netty 内嵌 HTTP Server，**仅 POST**，按 uri 分发 beat/idleBeat/trigger/kill/log，校验 accessToken+appname）、`JobThread.java`（`triggerLogIdSet` 按 logId 去重 → “repeat trigger job”；`FutureTask.get(executorTimeout)` 超时中断）、`ExecutorBlockStrategyEnum`（SERIAL_EXECUTION / DISCARD_LATER / COVER_EARLY）、`JobScheduleHelper`（`PRE_READ_MS=5000`、60 槽时间轮、`preReadCount=(fastMax+slowMax)*10`、scheduleThread+ringThread）、`JobTriggerPoolHelper`（快/慢双线程池隔离）、`JobRegistryHelper`（注册/剔除线程池 + monitor）、`TriggerCallbackThreadHelper`（回调 MessageQueue + 失败落文件重试线程）、`tables_xxl_job.sql`（`executor_timeout` 秒 / `executor_fail_retry_count` / `glue_source` / `trigger_next_time` / `misfire_strategy` / `executor_route_strategy`）。
- 结论：**不引入 XXL-JOB**（GPL + Java/MySQL 重 + 本场景调度方已是平台），借鉴 5 项：① 按 `run_id` 去重（修幂等缓存陈旧）；② 三态返回码 200/500/502（超时单独区分）；③ 阻塞策略 serial/discard/cover（替代裸 429）；④ 执行器主动注册 + 30s 心跳 / 90s 剔除（替代平台轮询）；⑤ 按 run_id 查日志 + 审计字段。
- 未验证：以上均来自源码/README 阅读，未部署 xxl-job 实测。

### 2026-10-28 开源对标调研（GitHub 上有没有类似的调度执行器）

- 目的：给自研执行器找参照/替代，判断是否重复造轮子。
- 方法：GitHub API 抓 star/语言/最近推送，逐项目 README 关键词核对（2026-10-28 抓取，**均未在本机部署实测**）；未跑任何压测。
- 结论：**有同类，但没有覆盖本场景的现成项目；继续自研，抄机制不抄代码。** 本场景组合 = 无头 API + token + 平台心跳注册 + 幂等指纹 + 并发上限 + 脚本目录扫描 + Windows 计划任务 + 看门狗，开源里没有一家同时具备。
- 三类项目：①脚本执行器：bugy/script-server 1.9k★ Python（Web UI 跑脚本，Windows 仅附加支持/轻测）、OliveTin 3.9k★ Go（预定义命令 + REST/Connect RPC API）、Cronicle 5.9k★ Node（多机调度 + REST API + API Key）、Dkron 4.7k★ Go（分布式 cron + REST API，README 称支持 Windows）、Rundeck 6.4k★ Java（自助运维 + ACL + REST）；②调度中心 + 执行器：XXL-JOB 30.6k★ Java（执行器周期自动注册、Cron/固定间隔/API 触发、阻塞策略、超时中断、失败重试与告警、GLUE 脚本任务含 PowerShell、OpenAPI、审计日志）、PowerJob 7.8k★、Nomad 17k★（HTTP API 提交 batch）、StackStorm 6.5k★、Semaphore 14.3k★（Ansible/Terraform/PowerShell）；③重工作流（不适用）：Airflow 47k★、Kestra 30k★、Windmill 18k★、DolphinScheduler 14.5k★、Temporal 24k★、n8n 208k★。
- 可借鉴（对上测评遗留项）：xxl-job 的阻塞策略（单机串行/丢弃/覆盖、比裸 429 友好）/超时主动中断/失败重试与告警/审计日志 → 对应 P0「无审计」与 P1「429 无 Retry-After」；bugy/script-server、OliveTin 的 action 定义与参数校验 → 若以后要给人手动跑；Cronicle 的 API Key 模型 → 若要多系统接入；WinSW 14.4k★ → 可替代「计划任务 + 看门狗」的更稳进程托管。
- 不做：不迁移不替换（平台侧 `/run`、`/health/ready` 契约要重写，收益远小于成本）。

### 2026-10-28 文档与现架构对齐（P1-5）+ 清理旧架构记录

- 目标：修测评 P1-5（文档与现架构漂移），并按用户要求删除旧架构（进程守护时代）相关记录。
- 删（5 个文件，`git status` 中为 D）：`script-server/ecosystem.config.js`（旧守护配置）、
  `script-server/start-script-server.bat` 的历史备份文件、`deploy/install-autostart.ps1`（旧守护版自启安装器）、
  `SCRIPT-SERVER-新设备安装与使用说明.md`（1128 行旧说明书，内容已被 `INSTALL.md` 覆盖）、
  `deploy/OPERATIONS-executor.md`（旧运维 runbook，已被 `MAINTENANCE.md` 覆盖）。
- 重写：`deploy/INSTALL-EXECUTOR.md` 改为当前架构（只要 Python、`install.bat` 一键装、计划任务 + watchdog、
  三份日志与轮转、真实重启验收、防火墙/卸载）；`INSTALL-RECORD.md` 改为"按设备记录"，
  只保留本机（YANGSTARONE）的环境/安装/验收/坑/日志改造/证据，旧机与旧架构章节不再保留。
- 清理措辞：`serve.py`、`MAINTENANCE.md`、`register-autostart-task.ps1`、`install-autostart-uvicorn.bat` 中
  指向旧守护组件的说明文字；`INSTALL.md` 目录树/任务表已同步当前文件清单。
- 验证：全仓库（排除 `.git`/`logs`）旧守护组件名引用 **0 处**；`serve.py`/`verify-logging.py` 通过 `py_compile`；
  所有 `.ps1` 通过 PowerShell 语法解析；服务 `/health` 200、主任务 Running；`verify-10.ps1` 10/10。
- 说明：旧配置与旧文档仍可在 git 历史中追溯（本轮为删除而非改写历史）。

### 2026-10-28 日志拆分 access/out/error + 每日轮转保留 14 天

- 目标：修测评 P1-3（日志混在一起、无轮转）。旧 `script-server-error.log` 实测量化：41,737 B / 393 行，其中 **ERROR 0 条**、access 207 条、WARNING 14 条 → “错误日志里没有错误”。
- 改（`deploy\serve.py`）：新增 `setup_logging()`（`TimedRotatingFileHandler`，`when=midnight`、`backupCount=14`、UTF-8）：
  `script-server-access.log`（挂 `uvicorn.access`、`propagate=False`，每请求一行）、
  `script-server-out.log`（root，INFO+，含 ERROR 副本）、`script-server-error.log`（root，仅 ERROR+）；
  去掉原来的 stdout/stderr 重定向（启动行改为 logger 输出）；日志目录不可写时降级 stderr 且不阻止启动。
  同步改 `ops-http.ps1 logs`（显示三份 + 最近 5 个归档）；新增离线验收 `deploy\verify-logging.py`。
- 验收（离线）：`python deploy\verify-logging.py` **14/14 PASS**（分级、中文 UTF-8、默认 MIDNIGHT/14、`when='S'` 模拟轮转与保留淘汰）。
- 应用：提权 `Start-Process -Verb RunAs` 跑 `logs\apply-logging.ps1`（先建 `.maintenance` 暂停 watchdog → Stop/Start 计划任务 → 等 `/health` 200 → 删标记 → `verify-10.ps1`）。重启后新 pid 25888，`/health` 200（约 3s），`verify-10.ps1` **10/10 PASS**。
- 线上验证：access.log 只含 `uvicorn.access`；out.log 有 `[serve] start`、任务启动/完成、超时 WARNING；error.log 自 09:37:31 起 **0 行新增**（mtime 停在 09:37:21）。
- 顺带确认：① **真实重启自启已验证**（系统 2026-10-28 09:01:55 启动、09:02:12 `serve.py` 自启，out.log 有 `[serve] start pid=5100`）；② **平台设备登记已生效**（192.168.1.210 每 60s 探 `/health/ready` 200）。
- 未验证：跨午夜真实轮转（等 10-29 00:00 后确认 `script-server-*.log.2026-10-28` 出现）；14 份淘汰需 15 天。
- 证据：`logs\apply-logging-20261028.log`、`script-server\deploy\verify-logging.py`、`script-server\logs\verify-result.json`。文档同步：`MAINTENANCE.md` §5/§11、`INSTALL.md`、`deploy\INSTALL-EXECUTOR.md`、`INSTALL-RECORD.md` §2.8、`PROJECT_LOG.md`。

### 2026-10-27 执行器测评（代码走读 + 36 项黑盒探针）

- 目标：把执行器当“脚本执行器”做一次实测评测，不只看代码也不只看健康检查。
- 方法：`script_server.py` 1584 行全文走读 + `serve.py` + `verify-10.ps1`；黑盒探针 3 个脚本（`logs\eval-probe{,2,3}.py`，31+ 项，含并发/取消/超时/大输出/GBK/路径逃逸），结果落 `logs\eval-extra*.json`；主机 16 逻辑核、Python 3.12.10、fastapi 0.119.1 / uvicorn 0.49.0。
- 结论：**7.0/10，内网单机合格**。功能完整 9.0、可靠性 7.5、性能 7.5（p50 49ms、4 并发真并行 2.34s）、安全 5.5、可观测 6.5、可维护 4.5。报告 `script-server\EXECUTOR-EVAL-20261027.md`（未提交 git）。
- 实测通过的关键行为：无/错 token 均 401；相对逃逸/目录外绝对路径/非法扩展名/`/scan` 越界全部 403；5 并发→4×200+1×429；4×`sleep(2)` 墙钟 2.34s；`/cancel` 0.18s 生效；9MB 输出截断到 8MB 不炸；GBK 输出正确还原；exit1=部分成功、Traceback=脚本错误；token 不落日志。
- 实测 FAIL / 注意：① 脚本内容改了、同 `task_run_id` 重投仍返回旧结果（fingerprint 不含文件内容/mtime）；② `args='a "" b'` 丢空参数；③ 未知字段（如 `{"timeout":1}`）静默忽略，仍按默认 300s 跑；④ 429 无 `Retry-After`；⑤ 完整 argv 进日志（敏感参数会泄漏）；⑥ `script-server-error.log` 里全是 INFO/access 日志（uvicorn 走 stderr），`out.log` 只有启动行，且无轮转。
- 代码事实（未构造实测）：单 worker 单点、`kill_failed` 会永久占一个并发槽直到重启、无任务队列、依赖未锁定（`uvicorn>=0.30`）、文档曾与现架构漂移（2026-10-28 已清理）、引用的 `EXECUTOR-STABILITY-EVAL-20261023.md`/`FIX-P1-20261026.md` 不在仓库。
- 遗留：P0 加固（执行器降权到低权限账户 + 脚本目录 ACL、幂等指纹加脚本哈希、日志分级轮转）尚未做；探针脚本未转成 pytest。

### 2026-10-27 用新安装器重装执行器（install.bat，替换旧架构安装）

- 目标：按更新后的 `INSTALL.md` 重装执行器，替换旧架构安装。
- 预检：Python 3.12.10 + fastapi 0.119.1 / uvicorn 0.49.0 满足 `requirements.txt`；`.env` 沿用（token 未换）；旧计划任务仍在 Running（pid 4148，旧版 `serve.py`）；当前 shell 非管理员。
- 改（代码）：`deploy\cutover-to-uvicorn-task.ps1` 新增 `Find-Python`——优先固定候选路径找真实解释器，规避 `python` 解析到 `WindowsApps\python.exe`（0 字节 Store 存根；本机 machine PATH 中存根排在真实 Python 前）。不修的话任务会被注册成存根，永远起不来。
- 安装：`install.bat -nopause` 经 UAC 提权 → `cutover-to-uvicorn-task.ps1`：结束旧 pid 4148 → 注册主任务（真实 python + `serve.py`，Boot/S4U/Highest）→ 注册 watchdog → 启动；17:41:42 完成。
- 验收：`verify-10.ps1` **10/10 PASS**（含真实执行、幂等、超时、路径逃逸）；主任务 Running、watchdog Ready；`/health` 200 / `auth_required=true` / `concurrency=4`。node.exe 数安装前后均 5（属 DSH 等其它软件，不是执行器）。
- 证据：`C:\ScriptServer\logs\reinstall-report.log`（提权安装全过程）、`logs\cutover.log`、`script-server\logs\verify-result.json`。
- 坑：① DSH 沙箱里 `Get-NetTCPConnection`（CIM）、`Get-ScheduledTask`、`tasklist` 均被拒 → 会把"服务在跑"误判成"未监听"，端口/任务状态必须用提权脚本或 `/health` 判断；② 新 `cutover` 原用 `Get-Command python`，本机会注册成 Store 存根（已修）。
- 遗留：真实重启自启未验证；平台设备登记/检测未做；防火墙规则未加（当前公用配置文件关闭，跨机可达）。

### 2026-10-27 初始化 Git 与 GitHub 私有仓库（安装前留档）

- 目标：把当前 `C:\ScriptServer` 纳入版本管理，安装/改造前留下可回滚基线。
- 结论：已推送到 https://github.com/yangstarkone/script-server-executor（PRIVATE，main），首提交 30 文件 `2133a5a`。
- 改：新增 `.gitignore`（排除 `.env`、`logs/`、`.script_server_state/`、`__pycache__/`、`*.log`）与 `README.md`；`script-server/INSTALL-RECORD.md` 第 47 行真实 token 改为占位符；`git init -b main`，仓库内身份 `yangstarkone <yangstarkone@users.noreply.github.com>`。
- 敏感信息检查：独立只读审计 PASS——索引内无凭据（真实 token 零命中、无硬编码默认 token），远端树无 `.env`/日志/状态目录；文档保留内网 IP、主机名、`Administrator` 等 LOW 信息（仓库 private，暂不处理）。
- 坑：① Git for Windows 走 schannel 访问 GitHub 报 `SEC_E_NO_CREDENTIALS`；② credential helper `!gh auth git-credential` 在 DSH 受限环境起不了 sh（signal pipe Win32 5）。可行推送法：`http.sslBackend=openssl` + 用 `GIT_CONFIG_*` 环境变量注入 `http.extraHeader="Authorization: Basic base64(x-access-token:<gh token>)"`，token 不进 argv、不落盘。
- 遗留：未加 gitleaks/secret scanning。

### 2026-10-26 在新机 YANGSTARONE 装好 script-server 执行器

- 目标：把旧机拷来的 `C:\ScriptServer` 在本机装成可开机自启的执行器。
- 结论：走 `INSTALL-RECORD.md` 的"计划任务直接托管 uvicorn"模型。拷贝包缺的 `serve.py` / 注册脚本 / 验收脚本按描述重建（详见 `script-server\INSTALL-RECORD.md`）。
- 改：新增 `script-server\deploy\serve.py`、`deploy\register-autostart-task.ps1`、`deploy\install-autostart-uvicorn.bat`、`deploy\verify-10.ps1`；`script-server\INSTALL-RECORD.md` 追加第 10 节；登记计划任务 `WorkflowPlatform-ScriptServer`。
- 坑：① 拷贝包里只有旧架构 deploy 文件（无 `serve.py`），照旧说明书装不起来 → 只能重建；② 在 DSH 受限 shell 里启动服务后 `/run` 必 500 `WinError 5`（asyncio 建子进程命名管道被拒），换计划任务托管即正常；③ `/scan` 的 `dir_path` 是 query 参数，传 JSON body 返回空列表；④ 拷来的 `.script_server_state\results` 会让同 `task_run_id` 在 24h 内命中旧机缓存（假成功）→ 已移走备份。
- 遗留：真实重启自启未验证；防火墙规则与平台登记未做。
