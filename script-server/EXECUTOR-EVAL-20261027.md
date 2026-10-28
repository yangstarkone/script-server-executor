# 脚本执行器（script-server 3.2.0）测评报告

> 测评对象：`C:\ScriptServer\script-server\script_server.py`（1584 行）+ `deploy\serve.py` + 计划任务托管
> 时间：2026-10-27 18:20~18:40 · 主机 `YANGSTARONE` / 16 逻辑核 / Python 3.12.10
> 方法：**代码走读**（全文）+ **黑盒探针 30 项**（真实 HTTP 调用，不 mock）+ **文档核对**
> 探针脚本与原始结果：`C:\ScriptServer\logs\eval-probe{,2,3}.py`、`eval-extra{,3}.json`（`logs/` 已 gitignore）
> 说明：本文所有“实测”均为本轮真实执行的结果；“代码事实”为读源码得出、未构造场景实测的结论。
> **2026-10-28 修订**：P1-3（日志分级 + 每日轮转）已修复并上线，见 `INSTALL-RECORD.md` §10.11、`MAINTENANCE.md` §5。

## 0. 结论

**作为内网单机、由可信平台调用的短任务执行器：合格、可用，实现成熟度明显高于一般自制执行器（7.0/10）。**
**作为通用/对外/多租户执行平台：不合格** —— 凭证等于管理员级 RCE，无 TLS、无审计、无队列、无自动化测试。

一句话定性：**“小而硬”的执行器**——鉴权、路径白名单、并发槽、超时/取消、幂等、进程树清理、启动对账、编码兼容、指标都做了且实测符合设计；短板集中在**安全模型、日志治理、测试与文档一致性**，都是“可加固”而非“要重写”。

适用 / 不适用：

| 维度 | 判断 |
|---|---|
| 适合 | 内网单机、调用方是可信平台、单任务 1s~几分钟、每天几十~几千次、允许重启时丢在跑任务（平台可重试） |
| 不适合 | 公网暴露、多租户/多部门共用、需要审计合规、长任务队列、要求任务不丢、需要水平扩展 |

## 1. 评分

| 维度 | 分 | 依据 |
|---|---|---|
| 功能完整性 | **9.0** | 幂等、取消、超时、409/429、排空、就绪、扫描、计数齐全，且语义分明 |
| 安全 | **5.5** | 输入侧防护到位（路径/env/扩展名）；但 token=主机完全控制权、明文 HTTP、无调用者审计、无 IP 白名单 |
| 可靠性 | **7.5** | 启动对账、看门狗、排空、槽归还、kill 兜底都对；单点单 worker、kill_failed 永久占槽、无队列 |
| 性能 | **7.5** | hello.py p50 49ms、4 并发真并行（4×2s=2.34s）；4/16 核未压满、每任务 ~40ms 解释器启动开销 |
| 可观测性 | **6.5** | `/health` `/health/ready` `/tasks` + 17 项计数很好；日志不分级、不轮转、无 metrics/审计 |
| 可维护性 | **4.5** | **零自动化测试**、单文件 1584 行、文档与现架构漂移、依赖未锁定 |
| 运维友好度 | **7.0** | `install.bat` 一键装、看门狗、verify-10、排空升级；被日志与文档拖后腿 |
| **加权** | **7.0** | 见第 3~5 节的加减分项 |

## 2. 实测结果（30 项）

| # | 探针 | 期望 | 实测 | 结论 |
|---|---|---|---|---|
| 1 | `/run` 不带 token | 401 | 401 | PASS |
| 2 | `/run` 错 token | 401 | 401 | PASS |
| 3 | `/tasks` 不带 token | 401 | 401 | PASS |
| 4 | `/health` 公开 | 200、无业务细节 | 200，p50 **1.3ms** | PASS |
| 5 | `/health/ready` 带 token | status=ok | 200 ok | PASS |
| 6 | 相对路径逃逸 `..\..\Windows\System32\cmd.py` | 403 | 403 | PASS |
| 7 | 目录外绝对路径 | 403 | 403 | PASS |
| 8 | 白名单根内不存在的文件 | 400 | 400 | PASS |
| 9 | 非法扩展名 `.txt` | 403 | 403 | PASS |
| 10 | `/scan` 目录外 | 403 | 403 | PASS |
| 11 | 跑 `hello.py` | 200/exit 0 | 200，min 37 / **p50 49** / max 67ms（8 次） | PASS |
| 12 | 同 `task_run_id` 重投 | 不重跑 | `deduplicated=true`，55→**39ms** | PASS |
| 13 | 同 id 但 args 变了 | 重跑 | 重跑（fingerprint 生效） | PASS |
| 14 | **脚本内容变了、同 id 重投** | 应重跑 | **仍返回旧结果**（VERSION-1） | **FAIL（见 P1-1）** |
| 15 | `args` 引号/空格/反斜杠 | 原样 | `['--a','b c','d\\e']` | PASS |
| 16 | `args='a "" b'` 空引号参数 | 3 个参数 | **`['a','b']` 丢空参数** | **FAIL（P2-1）** |
| 17 | 未闭合引号 | 400 | 400 | PASS |
| 18 | env 注入业务变量 | 生效 | `DEMO=v1` | PASS |
| 19 | env 注入 `PATH` | 400 | 400 | PASS |
| 20 | env 注入 `wf_script_token` | 400 | 400 | PASS |
| 21 | env 大小写重复 `DEMO/demo` | 400 | 400 | PASS |
| 22 | 未知字段 `{"timeout":1}` | 4xx | **200（silently 忽略）** | **FAIL（P2-2）** |
| 23 | `timeout_seconds=1` 跑 sleep5 | 超时+杀进程 | 1.22s，exit -1，`脚本超时`，cancelled=false | PASS |
| 24 | `/cancel/{id}` | 杀掉并返回 | 0.18s 返回；任务侧 `cancelled=true` | PASS |
| 25 | 同 id 并发 | 409 | 409（带 pid） | PASS |
| 26 | 5 并发（上限 4） | 1×429 | `[200,200,200,200,429]` | PASS |
| 27 | 429 是否带 `Retry-After` | 建议带 | **无** | FAIL（P2-3） |
| 28 | 4×`sleep(2)` 并行 | 墙钟≈2s | **2.34s** | PASS |
| 29 | 9MB stdout | 截断且不爆内存 | `truncated=true`，保留 8,388,608B，0.23s | PASS |
| 30 | GBK 字节输出（stdout+stderr） | 正确还原中文 | 两项均正确 | PASS |
| 31 | exit 1 无 traceback | 部分成功 | `partial=true`，`部分成功` | PASS |
| 32 | 抛异常 | 脚本错误 | exit 1，`脚本错误`，partial=false | PASS |
| 33 | `timeout_seconds` 0 / 8000 | 422 | 422（中文说明） | PASS |
| 34 | 中文+空格文件名 | 可执行 | 200 | PASS |
| 35 | token 是否落日志 | 不落 | 0 次 | PASS |
| 36 | argv 是否落日志 | 无要求 | **完整入日志**（1 次） | 注意（P1-4） |

## 3. 做得好的地方（设计层面，带源码位置）

1. **启动期护栏**（`:130-201`）：token 为空且监听非回环 → 直接拒绝启动；`WF_SCRIPT_REQUIRE_TOKEN=1` fail-fast；检测到 `--workers>1` 拒绝启动（单 worker 是台账/取消/并发槽的前提）。这三条把最常见的“裸奔上线”和“多 worker 互杀”堵死了。
2. **路径边界 fail-closed**（`:830-854`）：先 `resolve()` 再 `is_relative_to`，相对逃逸、绝对路径、扩展名三道关；`/scan` 同样白名单（`:1182-1208`）。注释里还留了修复前的漏洞记录（2026-10-23 评估 P1-04），说明走过真实攻击面。
3. **env 注入硬化**（`:287-327`）：硬拒绝表覆盖 `PYTHONPATH/PATH/COMSPEC/PYTHONSTARTUP/LD_PRELOAD/NODE_OPTIONS/...`（防 sitecustomize 劫持、防命令搜索路径劫持），还专门处理了 Windows 不区分大小写导致的 `DEMO/demo` 重复键 → 400。
4. **进程生命周期**（`:765-819`、`:991-1043`、`:1048-1086`）：`taskkill /F /T` 杀树 + `process.kill()` 兜底 + 5s 确认；`running.json` 启动对账杀孤儿，**用 PID 创建时间（2s 容差）防 PID 复用误杀**；lifespan 关停时先杀在跑任务；`CREATE_NO_WINDOW` 避免弹窗。这是长期无人值守能活下来的关键。
5. **幂等**（`:534-648`、`:1226-1238`）：结果按 `task_run_id` 落盘缓存，带 TTL/文件数/字节上限；只缓存 **exit 0**（失败允许平台真重试）；同 id 运行中 → 409；“进程已退出但收尾未完”的窗口也判 409（复审 N5）。语义正确。
6. **超时/取消判定顺序**（`:1355-1375`）：先判正常退出，再判取消，最后超时——避免“被取消”误报成部分成功。这种细节是真踩过坑才会写。
7. **编码兼容**（`:901-923`）：先严格 UTF-8 → GBK → GB18030 → 替错兜底，给子进程注入 `PYTHONIOENCODING=utf-8`；实测 GBK 字节脚本输出正确还原。
8. **可观测性**（`:1099-1179`）：`/health` 公开但只给状态/版本/计数（不泄露路径）；`/health/ready` 能区分“活着但不能干活”（degraded）；`/tasks` 给 pid/elapsed/limit；17 项计数能定位 429/超时/kill 失败/孤儿清理。比多数自研执行器好。
9. **输出上限**（`:940-954`）：边读边截断，超限只标记 `truncated` 不中断读取（不会因管道写满死锁）。9MB 实测 0.23s、内存不炸。
10. **单文件无第三方中间件**：只依赖 fastapi/uvicorn，不需要 Redis/DB，新设备 `install.bat` 一键装。这是它相对 Celery/AWX 之类最大的运维优势。

## 4. 缺陷与风险

### P0-1 凭证等于管理员级 RCE，且链路明文（安全）

- 事实：`.env` 里 `WF_SCRIPT_TOKEN` 明文；`/run` 只校验 token，不校验“谁在调”（无调用者标识/审计）；监听 `0.0.0.0:9000` 无 TLS；计划任务以 **Administrator + RunLevel=Highest** 运行，业务脚本继承该身份；`WF_ALLOWED_SCRIPT_ROOTS=E:\workflow-scripts-center`（约 1.7 万文件）全部可在白名单内执行。
- 影响：token 一旦泄漏（平台数据库、截图、日志转发、内网嗅探），攻击者即获得**该主机管理员级任意 Python 执行**，无需再提权。执行器自身的输入防护（路径/env）在此前提下形同虚设——因为 token 持有者本来就被授权执行任意白名单内脚本。
- 建议（按性价比）：① 专用**低权限服务账户**跑计划任务 + 脚本目录 ACL（执行器只读、状态目录可写）；② 平台→执行器走内网隔离或反向代理 + IP 白名单；③ token 定期轮换并在平台侧同步；④ 日志加调用方标识（平台可在 header 传 `X-Caller`，执行器记入日志/结果），否则出事后无法定位是谁派的活。

### P1-1 幂等缓存会返回“陈旧成功”（实测 FAIL）

- 事实：`_request_fingerprint()`（`:636-648`）只哈希 `file_path/args/env/timeout`，**不含脚本内容**。实测：脚本从 `VERSION-1` 改成 `VERSION-2` 后用同一 `task_run_id` 重投 → 仍返回 `VERSION-1` 且 `deduplicated=true`。
- 影响：修好一个 bug、平台用同一 run_id 重试 → 拿到旧结果并按成功记账；24h TTL 内可复现。这是“看起来成功、实际没跑”的静默错误，比显式失败更危险。
- 建议：fingerprint 加入脚本 `mtime+size`（便宜）或前 64KB sha256；或在文档里明确“task_run_id 必须每次新生成，重试语义由平台保证”。

### P1-2 零自动化测试

- 事实：`git ls-files` 无任何 test 文件；唯一验收是 `deploy\verify-10.ps1` 的 10 项冒烟（端口/health/401/ready/Bearer/scan/真跑/幂等/超时/路径逃逸），**未覆盖** 409、429、取消、截断、env 注入、编码、退出码语义。
- 影响：1584 行、含安全边界与并发/进程管理的代码，任何改动只能靠人工回归；代码注释里已有 N1~N9、P1-1~P1-5 等手工复审记录，说明历史上确实反复出问题。
- 建议：把本报告第 2 节的探针直接改写成 `pytest`（TestClient 或对本机 HTTP 均可，20~30 条），纳入 `install.bat` 后置检查；接 GitHub Actions `windows-latest` 跑。

### P1-3 日志：不分级、不轮转、无错误专档 ✅ 2026-10-28 已修复

- 事实（改造前）：`serve.py:64-65` 以追加模式打开 `logs\script-server-{out,error}.log`，无轮转；`log_config=None` 使 uvicorn 的 INFO/access 日志全部走 stderr → **`script-server-error.log` 里混着所有正常访问日志**（实测 42 次请求后 error.log 30KB，全是 INFO），`out.log` 只有启动那几行。
- 影响：① 磁盘只增不减（旧部署曾计划用外部轮转工具，但从未启用，卸载后彻底没有轮转机制）；② 运维在 `error.log` 里找不到“error”；③ 访问日志与业务日志混在一起，无法按级别过滤。
- 建议：拆 `access.log`/`error.log`（`TimedRotatingFileHandler` 或计划任务每日切分压缩，保留 14 天、单文件 50MB 上限）。
- **修复（2026-10-28 09:37，见 `INSTALL-RECORD.md` §2.8）**：`serve.py` 新增 `setup_logging()`，拆成
  `script-server-access.log` / `-out.log`（INFO+）/ `-error.log`（仅 ERROR+），三份都 `when=midnight, backupCount=14`；
  离线验收 `deploy\verify-logging.py` 14/14 PASS，线上重启后 error.log 0 行新增、access.log 只含请求行。
  改造前实测旧 error.log 41,737B / 393 行中 ERROR 0 条，印证本条判断。

### P1-4 完整命令行写入日志

- 事实：`:1300` `logger.info("启动任务 %s: %s", task_id, " ".join(handle.cmd))`，实测能搜到 `--a b c`。
- 影响：平台若把订单号/日期之外的敏感值放 `args`（例如 token、密码类参数），会明文进日志并被转发/截图。
- 建议：日志只记脚本名 + args 长度/前 N 字符，或加 `WF_LOG_ARGS=0` 开关；同时平台约定敏感参数走 `env`（env 值当前也不落日志，符合预期）。

### P1-5 文档与现架构漂移 ✅ 2026-10-28 已修复

- 事实（改造前）：`deploy\INSTALL-EXECUTOR.md` §0 仍写“Node.js 18+ + 进程守护组件 **必须**（生产）”，§4 全是旧守护命令；
  `deploy\OPERATIONS-executor.md` 全篇旧守护托管，且路径还是旧机 `C:\Users\yang\Documents\workflow-platform-20261022`；
  两份文档引用的 `EXECUTOR-STABILITY-EVAL-20261023.md`、`FIX-P1-20261026.md` 不在仓库（悬空引用）。
- 影响：新设备照此安装会**重新引入旧守护组件**，与本机现状（计划任务 + `serve.py`）相反；排障手册的路径/命令全部失效。
- **修复（2026-10-28）**：`INSTALL-EXECUTOR.md` 按现架构重写（只要 Python、`install.bat`、计划任务 + watchdog、
  三份日志与轮转、真实重启验收）；删除 `OPERATIONS-executor.md`（并入 `MAINTENANCE.md`）、
  删除旧守护配置/启动器备份/旧版新设备说明书/旧守护版自启安装器；`INSTALL-RECORD.md` 改为按设备记录；
  全仓库（排除 `.git`/`logs`）旧守护组件引用清零，悬空引用一并消除（相关文件已在 git 历史中可追溯）。

### P2 级（不阻塞，但建议排期）

| # | 问题 | 证据 | 建议 |
|---|---|---|---|
| P2-1 | `args` 空引号参数丢失 | `a "" b` → `['a','b']` | 空 token 也入栈（`:874-886`） |
| P2-2 | 未知字段静默忽略 | `{"timeout":1}` → 200，实际用默认 300s | `model_config = ConfigDict(extra="forbid")` |
| P2-3 | 429 无 `Retry-After` | 实测 header 为 null | 加 `Retry-After: 5`；平台侧加退避 |
| P2-4 | 截断只留头部 | 8MB 头保留、尾丢失 | 头尾各留一半（`:525-531`） |
| P2-5 | `/health/ready` 每次写探测文件 | 代码 `:1120-1125` | 平台轮询频繁时改为只 check 存在性/缓存结果 |
| P2-6 | 客户端断开默认取消执行 | `WF_CANCEL_ON_DISCONNECT=1` | **平台 HTTP 超时必须 > 脚本 timeout**，否则长任务被静默取消；建议平台侧显式配 |
| P2-7 | 服务端内部异常返回 `exit_code=3 / 脚本错误` | `:1498-1506` | 与业务脚本错误区分（如 `server_error: true`） |
| P2-8 | 依赖未锁定 | `uvicorn>=0.30` 无上限、无 lock | 固定 patch 版本 + `pip freeze` 存档 |
| P2-9 | 单点 + kill 失败永久占槽 | `:1507-1524` 设计如此 | 已有 `/tasks` 可见 + `/cancel` 重试；建议加告警阈值 |
| P2-10 | 并发上限 4 / 16 核 | `WF_MAX_CONCURRENCY=4` | 若脚本以 IO 为主可上调到 8~12，压测后定 |

## 5. 性能与容量

| 指标 | 实测 |
|---|---|
| 单次执行额外开销（hello.py 含 Python 启动） | min 37ms / **p50 49ms** / max 67ms |
| `/health` | p50 1.3ms |
| 并发能力 | 上限 4；4×`sleep(2)` 墙钟 2.34s（≈线性并行）；第 5 个立即 429 |
| 大输出 | 9MB → 截断在 8MB，0.23s，无内存异常 |
| 取消延迟 | `/cancel` 0.18s 返回，任务侧 1.4s 内收尾 |
| 粗略吞吐 | 平均 10s 任务 ≈ 0.4 任务/s（≈3.4 万/天）；平均 60s ≈ 5.7 千/天；并发上限是唯一瓶颈 |

结论：容量对“平台派发脚本”这一用途足够；瓶颈不是吞吐而是**单机单点**。若日均任务量上万或单任务常>10min，应改为“多执行机 + 平台侧排队”，执行器本身不必改。

## 6. 与替代方案对比

| 方案 | 优势 | 代价 | 何时选 |
|---|---|---|---|
| **现状 + 加固（推荐）** | 已跑通、Windows 原生、零中间件、一键装 | 单点、无队列、无审计 | 内网单机、任务量中低 —— **就是现在** |
| Celery/RQ + worker | 任务不丢、水平扩展、重试策略成熟 | 需 Redis/RabbitMQ；Windows 上 worker 池支持一般；运维复杂度明显↑ | 任务量上万/需多机调度 |
| AWX / Ansible | 权限、审计、模板化、界面 | 重（K8s/Docker）、Windows 目标限制多、要单独维护 | 需要合规审计、运维编排 |
| Jenkins agent / GH Actions runner | 日志/审计/权限成熟 | 面向 CI，业务脚本调度别扭 | 任务本质是构建/发布 |
| 直接 SSH 执行 | 最轻 | 无并发控制/幂等/结构化结果；Windows 侧 ssh 不便 | 临时排障 |

## 7. 改进清单（按优先级）

**P0（1~2 天）**
1. 执行器降权：专用低权限账户 + 脚本目录 ACL + 内网可达性收敛（半天）。
2. 幂等指纹加脚本 `mtime+size` 或文档强制“run_id 每次新生成”（2h）。
3. 日志分级 + 轮转（半天）。 ✅ **已完成 2026-10-28**

**P1（1 周）**
4. 把本报告探针改写成 pytest（20~30 条）+ CI（1 天）。
5. API 严格化：`extra=forbid`、429 带 `Retry-After`、内部异常与脚本错误分开（半天）。
6. 文档同步到计划任务 + `serve.py` 架构、修旧机路径、删悬空引用（半天）。 ✅ **已完成 2026-10-28**

**P2（有空）**
7. 压测后把并发从 4 调到 8~12（IO 型脚本）（2h）。
8. 截断保留头尾、stdout/stderr 带标记合并、`/tasks/{id}/log` 流式（半天）。
9. args 空引号、`/scan` 返回相对路径、日志不落完整 argv（2h）。
10. 依赖锁定 + 升级检查单（2h）。
11. 需要跨网段时加 TLS/反代（半天）。

## 8. 未验证项

- 真实重启后的开机自启（未重启本机；`Stop/Start` 冷启动等价测试此前通过）。
- 平台「设备管理」登记与「检测」在线（需平台侧操作，`base_url=http://192.168.1.220:9000` + `.env` token）。
- `kill_failed` 场景（需构造不可杀进程）、断电/网络中断、长时间运行的内存/句柄趋势。
- 磁盘写满时状态目录降级路径（代码有降级分支，未构造）。

## 附：复现方式

```powershell
# 1) 探针（需要 .env 里的 token；不改动业务脚本，临时脚本自动清理）
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" C:\ScriptServer\logs\eval-probe.py
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" C:\ScriptServer\logs\eval-probe2.py
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" C:\ScriptServer\logs\eval-probe3.py
# 2) 验收冒烟
powershell -NoProfile -ExecutionPolicy Bypass -File C:\ScriptServer\script-server\deploy\verify-10.ps1
```
