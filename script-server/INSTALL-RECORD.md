# script-server 安装记录（本机）

- 安装时间：2026-10-26 14:20–14:30（Asia/Shanghai）
- 依据：`C:\script-server\SCRIPT-SERVER-新设备安装与使用说明.md`（适用 VERSION 3.2.0）
- 本机：Windows（以太网 192.168.1.222），账户 `Administrator`（**非管理员提权会话**）

> **当前架构（2026-10-26 16:2x 起，以第 8 节为准）**：执行器由 Windows 计划任务直接托管
> `python deploy\serve.py`（前台即 uvicorn），**不再使用 PM2**。第 7 节记录的 PM2 + S4U
> 方案为历史决策过程，其“Session 0 守护进程 / connect EPERM”问题已随本次架构变更彻底消除。
>
> **换新设备安装请看第 9 节**（照做即可避开本机踩过的 PM2 坑）；该节内容同时随包保存在
> `C:\ScriptServer\script-server\deploy\INSTALL-NEW-DEVICE.md`，可直接拷到新机器使用。

## 1. 环境

| 组件 | 版本 | 说明 |
|---|---|---|
| Python | 3.12.10 | `C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe`（服务与业务脚本同一个解释器） |
| Node.js | v24.14.0 | `C:\Program Files\nodejs\node.exe` |
| PM2 | 7.1.1 | 全局安装，`C:\Users\Administrator\AppData\Roaming\npm\pm2.cmd`（已在用户 PATH） |
| 依赖 | fastapi 0.119.1 / uvicorn 0.54.0 | 按 `requirements.txt` 安装 |

## 2. 目录（独立执行器布局，按说明 1.4）

```text
C:\ScriptServer\
├─ script-server\                 ← 服务目录
│  ├─ script_server.py            VERSION 3.2.0
│  ├─ requirements.txt
│  ├─ ecosystem.config.js
│  ├─ start-script-server.bat
│  ├─ env-change-restart.bat      ← 改完 .env 双击这个
│  ├─ .env                        ← 含 token，不要外传
│  ├─ .env.example
│  ├─ deploy\install-autostart.ps1 / INSTALL-EXECUTOR.md / OPERATIONS-executor.md
│  └─ logs\script-server-out.log / script-server-error.log
├─ scripts\hello.py, sleep5.py    ← WF_SCRIPTS_DIR
├─ .script_server_state\          ← results\ + running.json（幂等缓存/台账）
└─ logs\                          ← 自启日志（尚未注册自启）
```

未拷贝：`start.bat`、`run_server.py`（仅开发排障）、`verify\`、`notes\`、`tests\`、`__pycache__\`。

## 3. 关键配置（`C:\ScriptServer\script-server\.env`）

```ini
WF_SCRIPT_TOKEN=<本机 .env 中的实际值，勿提交>
WF_SCRIPT_REQUIRE_TOKEN=1
WF_SCRIPTS_DIR=C:\ScriptServer\scripts
WF_SCRIPT_STATE_DIR=C:\ScriptServer\.script_server_state
WF_MAX_CONCURRENCY=4
```

- 平台「设备管理」登记的 `token` 必须与上面**逐字符一致**；
- `base_url`（平台视角）= `http://192.168.1.222:9000`（去尾斜杠）；
- 改 `.env` 后必须 `pm2 delete` + `pm2 start ecosystem.config.js --only script-server` + `pm2 save`，仅 `pm2 restart` 不保证重读。

## 4. 当前运行状态

- 启动方式：PM2（`ecosystem.config.js --only script-server`），`pm2 save` 已写入 `C:\Users\Administrator\.pm2\dump.pm2`；
- 监听：`0.0.0.0:9000`（`--workers 1`）；
- `/health`：`status=ok`、`version=3.2.0`、`auth_required=true`、`draining=false`。

常用命令（2026-10-26 16:21 切换后：计划任务托管 uvicorn，普通窗口即可，不需要 pm2）：

```powershell
$ops = 'C:\ScriptServer\script-server\deploy\ops-http.ps1'
powershell -NoProfile -ExecutionPolicy Bypass -File $ops status      # 端口/进程/任务/health/cutover 日志
powershell -NoProfile -ExecutionPolicy Bypass -File $ops logs        # 服务日志（UTF-8）
powershell -NoProfile -ExecutionPolicy Bypass -File $ops restart     # drain → Stop/Start 计划任务
# 或直接双击 start-script-server.bat（已改为 schtasks /End → /Run → 等 health）
```

## 5. 验收结果：10/10 通过

> 2026-10-26 16:22 在新架构（计划任务直接托管 `serve.py`，pid 21388）上**复跑仍 10/10**；
> 下表 pid 为首次验收时的值，pid 每次重启都会变。

| # | 检查 | 结果 |
|---|---|---|
| 1 | 端口 9000 LISTENING | PASS（pid 13208） |
| 2 | `/health` 200 + version=3.2.0 + auth_required=true + draining=false | PASS |
| 3 | 无 token 访问 `/health/ready` | PASS（401） |
| 4 | 带 token `/health/ready` | PASS（status=ok，state_dir=true，python.exists=true，concurrency=4） |
| 5 | `Authorization: Bearer` 兼容 | PASS（200） |
| 6 | `POST /scan` | PASS（hello.py, sleep5.py） |
| 7 | `POST /run` hello.py（含中文 args / env） | PASS（exit_code=0，stdout 正常） |
| 8 | 同 `task_run_id` 重投 | PASS（deduplicated=true） |
| 9 | sleep5.py + timeout=2 | PASS（exit_code=-1，status_label=脚本超时） |
| 10 | `..\..\script_server.py` 路径逃逸 | PASS（403） |

复跑脚本：`C:\Users\Administrator\Documents\deepseek-harness\default-workspace\verify-script-server.ps1`
原始结果：`...\default-workspace\verify-result.json`

## 6. 已完成 / 仍待办

1. **开机自启（说明 2.8）——已完成 ✅**，2026-10-26 16:21 起为“计划任务直接托管 uvicorn”版
   （见第 8 节；旧的 PM2 `-AtBoot` 版已在 16:21 被替换）
   - 任务 `WorkflowPlatform-ScriptServer`：`AtStartup`、`S4U`、`RunLevel=Highest`、
     动作 `python.exe "…\deploy\serve.py"`、失败每分钟重启 ×999；
   - 运行中任务状态显示 `State=Running`、`LastTaskResult=267009`（0x41301 = 正在运行），属正常；
   - 冷启动等价测试已做两次：旧版（`pm2 kill`→任务）与新版（任务 Stop→Start），均 ~2s 恢复 `/health` 200；
   - **真实重启验收仍需做一次**（重启后按下面清单核对）。

   **真实重启验收清单（重启后逐条核对）**：

   ```powershell
   # 1) 任务状态：重启后应为 State=Running（服务在跑）或 Ready+lastResult=0；动作应是 serve.py
   Get-ScheduledTask -TaskName 'WorkflowPlatform-ScriptServer' | Format-List TaskName,State
   Get-ScheduledTaskInfo -TaskName 'WorkflowPlatform-ScriptServer' | Format-List LastRunTime,LastTaskResult
   # 2) 一键体检（监听/进程/任务/health/cutover 日志）
   powershell -NoProfile -ExecutionPolicy Bypass -File C:\ScriptServer\script-server\deploy\ops-http.ps1 status
   # 3) 就绪探针（status=ok、state_dir.enabled=true）
   powershell -NoProfile -ExecutionPolicy Bypass -File C:\ScriptServer\script-server\deploy\ops-http.ps1 ready
   # 4) 平台侧：设备管理点"检测"应在线；从平台机 Test-NetConnection 192.168.1.222 -Port 9000
   ```

   通过判据：`ops-http.ps1 status` 显示 9000 监听、任务 Ready/lastResult=0、`/health` 返回 200，
   且 `C:\ScriptServer\logs\script-server-autostart.log` 末尾有 `[AtBoot][OK] 最终结果: OK（/health 200）`。

2. **防火墙（说明 2.7）**——本机 Public 配置文件防火墙当前为**关闭**状态（Domain 开启但非当前网络位置），
   因此跨机访问 9000 目前大概率可达；若要按生产基线收紧，管理员执行：

   ```powershell
   New-NetFirewallRule -DisplayName "script-server 9000" -Direction Inbound -Protocol TCP `
     -LocalPort 9000 -RemoteAddress <平台出口IP> -Action Allow
   ```

3. 平台侧：在「设备管理」登记 name / `http://192.168.1.222:9000` / 上面的 token，点「检测」应显示在线。
4. 建议（说明 7.2）：安装 `pm2 install pm2-logrotate`；按需把 `.env` 与 `dump.pm2` 的 ACL 收紧到运行账户。

---

## 7. 2026-10-26 14:36 实测：S4U 自启成功，但 PM2 CLI 被“Session 0 隔离”打断

### 7.1 已做的事与结果

- 用户在管理员窗口执行了 `deploy\install-autostart.ps1`：任务 `WorkflowPlatform-ScriptServer` 注册成功
  （`State=Ready`、`S4U`、`RunLevel=Highest`、`AtStartup` 触发、动作 `powershell -AtBoot`、`WorkingDirectory=C:\ScriptServer`）。
- `pm2 save` 由脚本内部用 `pm2.cmd` 执行成功（14:35:34 写 dump，dump 内含 `WF_SCRIPT_TOKEN` / `WF_SCRIPTS_DIR`）。
- **冷启动等价测试**（本机实测，非重启）：`pm2 kill` → `Start-ScheduledTask` →
  日志出现 `pm2 resurrect 成功，/health 200`、`LastTaskResult=0`，服务在 ~2s 内恢复、`/health` 200。
  → 自启链路本身**可用**。

### 7.2 缺陷：resurrect 出的 PM2 守护进程在 Session 0，交互会话连不上

```text
pm2 list → Error: connect EPERM \\.\pipe\rpc.sock
tasklist: node.exe PID=16052 Session#=0 "Services"  ← pm2 守护（任务创建）
          python.exe PID=14848 Session#=0           ← 服务本体（守护的子进程）
当前会话: Session#=1（Console, WUYUAN\Administrator）
taskkill /F /PID 16052 → Access is denied（非提权会话杀不掉 Session 0 的服务进程）
```

- 影响：**服务本身正常**（9000 监听、`/health` 200、连脚本执行都正常）；
  但 `pm2 list / logs / restart / save` 在普通窗口全部 EPERM，且每次失败还会再拉起一个抢不到管道的野守护进程（已清理）。
- 根因：`-AtBoot` 由计划任务的 S4U 身份在 **Session 0** 里 `pm2 resurrect`，守护进程与命名管道都归 Session 0 的服务令牌；
  交互会话（Session 1）无法连接、也无法结束它。说明书第 0.5 节已注明“PM2 真机行为/开机自启需在新设备补证据”，这就是新证据。

### 7.3 三种收口方式（都需要管理员窗口改计划任务，二选一）

> **2026-10-26 16:2x 更新**：最终没有采用下表的 A/B/C，而是改为“计划任务直接托管 uvicorn”
> （第 8 节）。原因：`AutoAdminLogon` 未配置（重启后无人登录），B 不可行；A 让 pm2 永久不可用；
> C 的“任务托管 uvicorn”正是这次落地的方案。

| 方案 | 开机即起 | `pm2` CLI 可用 | 说明 |
|---|---|---|---|
| A 保持 S4U（**曾选：14:51**，后被第 8 节取代） | ✅ 不等登录 | ❌ 普通窗口不可用 | 日常用 HTTP + 计划任务/提权 taskkill 管理 |
| B 改 AtLogOn + Interactive | ❌ 等 Administrator 登录 | ✅ 正常 | 本机无自动登录，重启后服务不会起 → 不可行 |
| C 任务直接托管 uvicorn（不用 PM2） | ✅ | —（不依赖 pm2） | 任务失败每分钟重启；管理用 HTTP/日志 → **已落地，见第 8 节** |

**方案 A 的运维脚本（普通窗口即可，不调用 pm2）**：

```powershell
$ops = 'C:\ScriptServer\script-server\deploy\ops-http.ps1'
powershell -NoProfile -ExecutionPolicy Bypass -File $ops status    # 监听/进程/任务/health/自启日志
powershell -NoProfile -ExecutionPolicy Bypass -File $ops ready     # 就绪探针（带 token）
powershell -NoProfile -ExecutionPolicy Bypass -File $ops tasks     # 运行中任务
powershell -NoProfile -ExecutionPolicy Bypass -File $ops logs      # 服务日志 + 自启日志（UTF-8）
powershell -NoProfile -ExecutionPolicy Bypass -File $ops drain     # 排空（粘性，重启才恢复接单）
# 重启需要管理员窗口：先 /drain，再
powershell -NoProfile -ExecutionPolicy Bypass -File $ops restart
```

等价的原始命令：

```powershell
$t = '<token>'
curl.exe -s http://127.0.0.1:9000/health
curl.exe -s -H "X-Script-Token: $t" http://127.0.0.1:9000/health/ready
curl.exe -s -H "X-Script-Token: $t" http://127.0.0.1:9000/tasks
Get-Content C:\ScriptServer\script-server\logs\script-server-out.log -Tail 50 -Encoding UTF8
Get-Content C:\ScriptServer\logs\script-server-autostart.log -Tail 50 -Encoding UTF8
# 重启（管理员窗口）：开始菜单任务不能中断在跑任务，先 /drain
Start-ScheduledTask -TaskName 'WorkflowPlatform-ScriptServer'   # 仅当 9000 已停才有意义
```

> 注意：在 Session 0 守护进程存活期间，**不要**在普通窗口敲 `pm2 ...`——会连带生成抢不到 `rpc.sock` 的野守护进程。

---

## 8. 2026-10-26 16:2x 架构变更：计划任务直接托管 uvicorn（彻底移除 PM2）

### 8.1 触发事件

用户在 `start-script-server.bat` 里再次遇到：

```text
connect EPERM \\.\pipe\rpc.sock
[ERROR] pm2 start failed (exit 1).
```

根因同第 7 节：S4U 的 `AtStartup` 任务在 **Session 0** 里 `pm2 resurrect`，守护进程与命名管道都在
Session 0，交互会话既连不上也杀不掉；而且每次失败的 pm2 调用还会再拉起一个抢不到管道的野守护进程。

### 8.2 为什么不是“改成登录后自启”

实测 `HKLM\...\Winlogon` 的 `AutoAdminLogon` 为**空（未启用）**，机器重启后没有人登录 →
AtLogOn 方案会导致 9000 端口永远不起。因此只能走“任务直接托管进程”。

### 8.3 新架构

```text
Windows 计划任务 WorkflowPlatform-ScriptServer
  AtStartup / S4U / RunLevel Highest / RestartInterval 1min ×999 / MultipleInstances IgnoreNew
  动作: C:\...\Python312\python.exe  "C:\ScriptServer\script-server\deploy\serve.py"
  工作目录: C:\ScriptServer\script-server
        │
        └─ serve.py（同一进程，前台）：读 .env → 设 WF_BIND_HOST/PYTHONIOENCODING
           → stdout/stderr 重定向到 logs\script-server-{out,error}.log（UTF-8）
           → uvicorn.run(app, host=0.0.0.0, port=9000, log_config=None)   # 单 worker，不 fork
```

- **没有 PM2 守护进程、没有 `rpc.sock`、没有跨会话 IPC**，`connect EPERM` 这一类问题从架构上消失；
- 开机自启等价于以前（不等登录）、崩溃由计划任务每分钟重启（最多 999 次，与旧任务一致）；
- `dump.pm2` 在切换时已备份为 `dump.pm2.bak-<时间戳>` 并移走，避免以后误用 pm2 复活第二个实例；
- 旧的 PM2 版启动脚本保留为 `start-script-server.bat.pm2-bak`，`ecosystem.config.js` 保留仅作参考。

### 8.4 一次性的切换命令（管理员窗口）

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File C:\ScriptServer\script-server\deploy\cutover-to-uvicorn-task.ps1
```

脚本做的事（幂等，可重复执行）：结束 9000 端口旧进程树 → 结束所有 PM2 守护 → 备份 dump →
注册新任务 → 启动 → 等 `/health` 200 → 打印 `/health/ready`；日志写 `C:\ScriptServer\logs\cutover.log`。

### 8.5 切换后的日常运维（普通窗口，不需要管理员、不需要 pm2）

```powershell
$ops = 'C:\ScriptServer\script-server\deploy\ops-http.ps1'
... -File $ops status     # 监听/进程/任务(含 action)/health/日志
... -File $ops ready      # 就绪探针
... -File $ops tasks      # 运行中任务
... -File $ops logs       # 服务日志
... -File $ops drain      # 排空
... -File $ops restart    # Stop-ScheduledTask → Start-ScheduledTask（内部会先 drain）
```

或双击/执行 `start-script-server.bat`（已改写为 shim：`schtasks /End` → `schtasks /Run` → 等 health）。

**改 `.env` 后**：直接 `ops-http.ps1 restart` 即可（serve.py 每次启动都重新读 `.env`，
不再有“pm2 restart 不重读 `.env`”“dump 里是旧 token”的问题）。

更省事的一键入口：`C:\ScriptServer\script-server\env-change-restart.bat`

```bat
env-change-restart.bat          :: 重启 + 显示生效中的 .env（token 打码）+ 校验并发数
env-change-restart.bat edit     :: 先弹记事本改 .env，存盘关掉后自动重启
env-change-restart.bat nopause  :: 给脚本/计划任务调用，结束时不停住
```

它内部就是调 `ops-http.ps1 restart`，额外做三件事：回读 `.env` 逐项打印（`WF_SCRIPT_TOKEN`
只显示前 8 位）、把 `/health/ready` 的 `concurrency.limit` 与 `WF_MAX_CONCURRENCY` 对比
（不一致就报 WARN 并打日志）、改 token 时提醒同步平台侧。退出码 0=成功、1=失败。
实测：`WF_MAX_CONCURRENCY` 4→5→4，服务分别报 `limit: 5` / `limit: 4`，证明 `.env` 确实被重读。
批处理**故意只写 ASCII**（中文注释在 cp936 下会被 cmd 误解析，今天已因此踩过两次坑）。

### 8.6 不要再做的事

- 不要 `pm2 start ecosystem.config.js`（会尝试再起一个 9000 实例）；pm2 只保留给排障；
- 不要用 `start.bat`（开发用，会无差别 taskkill 9000 端口）；
- 若要回退到 PM2 方案：`start-script-server.bat.pm2-bak` 改名回来，重新执行
  `deploy\install-autostart.ps1`（会重新注册 pm2 resurrect 任务，第 7 节的问题也会回来）。

### 8.7 切换后的实测验证（2026-10-26 16:21–16:24）

| 验证项 | 结果 |
|---|---|
| 任务动作 | `python.exe "…\deploy\serve.py"`（不再是 `powershell -AtBoot`） |
| 任务身份/触发 | `S4U` / `Highest` / `AtStartup` / `State=Running`、`LastTaskResult=267009`（正在运行） |
| 服务进程 | python pid 23516，**Session 0**，父进程 `svchost.exe`（任务计划宿主）——直接托管，无中间层 |
| PM2 残留 | `node.exe` 数量 **0**；`pm2 list` 不再 EPERM（自起空守护、列表为空，验证后已 `pm2 kill` 清掉） |
| `start-script-server.bat`（普通窗口） | ✅ 通过：`schtasks /End` → `/Run` → health 200，pid 23516 → 18836 |
| `ops-http.ps1 restart`（**非提权**） | ✅ 通过：drain(drained=true) → Stop → Start → health 200，pid 18836 → 21388 |
| 第 3 章 10 项验收（新架构复跑） | ✅ **10/10 PASS**（pid 21388） |
| 日志 | `logs\script-server-{out,error}.log` 由 serve.py 直接写（UTF-8），`logs\cutover.log` 记录切换过程 |

> 结论：`connect EPERM \\.\pipe\rpc.sock` 的根因（Session 0 的 PM2 守护 + 命名管道）已从架构上消除；
> 启动、重启、日志、健康检查全部在**普通窗口**可用，不再需要 `pm2`、也不再需要管理员（除非改计划任务本身）。

---

## 9. 新设备安装：照这一节做，就不会再踩 PM2 的坑

> 本节内容同时存为 `deploy\INSTALL-NEW-DEVICE.md`（不含本机 token），随安装包拷到新设备即可直接用。
> 上述新脚本已**回写到安装包源目录** `C:\script-server\deploy\`，下次从源目录拷贝即包含新方案；
> 源目录的 `start-script-server.bat` 也已换成 shim（原件备份 `start-script-server.bat.pm2-bak`）。
>
> 说明书第 2.8 节（PM2 + `pm2 resurrect` + `install-autostart.ps1`）在本机已证明会在
> 交互窗口产生 `connect EPERM \\.\pipe\rpc.sock`。**换新机器时请用本节替代 2.8 节**，
> 说明书的其余部分（1.1–1.6、2.1–2.7、第 3 章验收、第 4 章用法、第 5 章运维、第 6 章排障）照旧有效。

### 9.1 差异一览（与说明书 2.8 的对比）

| 项 | 说明书 2.8（PM2） | 本节（计划任务托管 uvicorn） |
|---|---|---|
| 依赖 | Python + Node.js + PM2 | **只要 Python**（Node/PM2 可以完全不装） |
| 自启动作 | 计划任务 S4U 跑 `pm2 resurrect` | 计划任务 S4U 直接跑 `python deploy\serve.py` |
| 进程模型 | pm2 守护 + 被守护的 python 子进程 | python 进程 **就是** uvicorn 本体（单进程） |
| 崩溃拉起 | PM2 `max_restarts` | 计划任务“失败每分钟重启 ×999” |
| 日志 | PM2 收集到 `logs\script-server-*.log` | serve.py 自己重定向到同样两个文件（UTF-8） |
| 日常管理 | `pm2 list/logs/restart` | `deploy\ops-http.ps1 status/logs/restart`、`start-script-server.bat` |
| 已知坑 | Session 0 守护、`rpc.sock` EPERM、dump 里旧 token | 无（不存在跨会话 IPC） |

### 9.2 安装步骤（新机器，从零）

```powershell
# 1) 前置：Python 3.10+（推荐 3.12，勾 Add to PATH）。Node/PM2 不需要。
python --version

# 2) 目录与文件（不拷 jinteng\、verify\、notes\、tests\、start.bat、__pycache__）
$dst = 'C:\ScriptServer\script-server'
New-Item -ItemType Directory -Force $dst, 'C:\ScriptServer\scripts', "$dst\logs", 'C:\ScriptServer\logs' | Out-Null
$src = '<安装包目录>\script-server'      # 或 U 盘/共享盘里的副本
Copy-Item "$src\script_server.py", "$src\requirements.txt", "$src\.env.example", "$src\ecosystem.config.js", "$src\start-script-server.bat", "$src\env-change-restart.bat" -Destination $dst -Force
Copy-Item "$src\deploy" -Destination $dst -Recurse -Force      # deploy\ 里要有 serve.py / ops-http.ps1 / cutover-to-uvicorn-task.ps1 / verify-10.ps1

# 3) Python 依赖
cd $dst
python -m pip install -r requirements.txt

# 4) 生成 token 并写 .env（值不要加引号）
python -c "import secrets;print(secrets.token_hex(24))"
Copy-Item .env.example .env
notepad .env      # 至少填下面 5 行
```

`.env` 最小内容：

```ini
WF_SCRIPT_TOKEN=<上一步生成的 48 位 hex>
WF_SCRIPT_REQUIRE_TOKEN=1
WF_SCRIPTS_DIR=C:\ScriptServer\scripts
WF_SCRIPT_STATE_DIR=C:\ScriptServer\.script_server_state
WF_MAX_CONCURRENCY=4
```

```powershell
# 5) 【管理员】注册开机自启任务并启动（新机器上就是“安装”，没有旧进程时会自动跳过清理步骤）
powershell -NoProfile -ExecutionPolicy Bypass -File C:\ScriptServer\script-server\deploy\cutover-to-uvicorn-task.ps1

# 6) 验证（普通窗口即可）
powershell -NoProfile -ExecutionPolicy Bypass -File C:\ScriptServer\script-server\deploy\ops-http.ps1 status
powershell -NoProfile -ExecutionPolicy Bypass -File C:\ScriptServer\script-server\deploy\ops-http.ps1 ready
powershell -NoProfile -ExecutionPolicy Bypass -File C:\ScriptServer\script-server\deploy\verify-10.ps1     # 第 3 章 10 项
```

```powershell
# 7) 【管理员】防火墙：只放行平台出口 IP（说明 2.7）
New-NetFirewallRule -DisplayName "script-server 9000" -Direction Inbound -Protocol TCP `
  -LocalPort 9000 -RemoteAddress <平台出口IP> -Action Allow
```

### 9.3 三条硬指标（新设备验收，缺一不可）

```powershell
# ① 动作必须是 serve.py（如果是 powershell -AtBoot，说明又走了 PM2 老路）
(Get-ScheduledTask -TaskName 'WorkflowPlatform-ScriptServer').Actions | Format-List Execute,Arguments
#   期望 Execute=C:\...\python.exe  Arguments="C:\ScriptServer\script-server\deploy\serve.py"

# ② 机器上不应有 node.exe（有 = 还有 PM2 守护）
tasklist /FI "IMAGENAME eq node.exe"

# ③ 重启一次后：任务 Running、/health 200、日志有 serve 启动行
powershell -NoProfile -ExecutionPolicy Bypass -File C:\ScriptServer\script-server\deploy\ops-http.ps1 status
Get-Content C:\ScriptServer\script-server\logs\script-server-out.log -Tail 5 -Encoding UTF8
#   期望看到 "[serve] start pid=... version=3.2.0 host=0.0.0.0 port=9000 ..."
```

### 9.4 新设备上不要做的事

- ❌ 不要执行 `deploy\install-autostart.ps1`（PM2 版，会在 Session 0 resurrect，产生本机踩过的 EPERM）；
- ❌ 不要 `pm2 start ecosystem.config.js` / `pm2 resurrect`；
- ❌ 不要用 `start.bat`（开发用，无差别 taskkill 9000）；
- ❌ 不要把 `jinteng\` 拷过去（与本系统无集成的独立凭据工具）；
- ❌ 不要设 `PYTHONUTF8=1`（会改变业务脚本 `open()` 默认编码），serve.py 只设 `PYTHONIOENCODING=utf-8`；
- ❌ 不要用记事本把 `.ps1` 存成“UTF-8 无 BOM”又保留中文注释——Windows PowerShell 5.1 会按 ANSI 解析，
  中文注释里的字节可能吃掉引号导致脚本莫名失败（本机踩过两次）。`deploy\*.ps1` 已统一存为 **UTF-8 with BOM**，
  且 `ops-http.ps1`、`verify-10.ps1` 保持 ASCII；改脚本时要么保持 BOM，要么只用英文注释；
- ✅ `.env` 改完直接双击 `env-change-restart.bat`，或 `ops-http.ps1 restart`（serve.py 每次启动重读 `.env`）。

### 9.5 文件清单（新设备建议随包携带）

| 文件 | 说明 |
|---|---|
| `script_server.py`、`requirements.txt` | 服务本体与依赖 |
| `.env.example` | 配置模板 |
| `deploy\serve.py` | **启动器（本方案核心）**，计划任务直接调它 |
| `deploy\cutover-to-uvicorn-task.ps1` | **注册计划任务**（管理员，幂等；新机器上即安装脚本） |
| `deploy\ops-http.ps1` | 日常运维（status/ready/tasks/logs/drain/restart） |
| `deploy\verify-10.ps1` | 第 3 章 10 项验收脚本 |
| `deploy\INSTALL-NEW-DEVICE.md` | 本节的独立副本（不含本机 token），随包携带 |
| `start-script-server.bat` | 已改写为 schtasks 启停 shim（可直接双击） |
| `env-change-restart.bat` | 改完 `.env` 双击它：重启 + 校验新配置已生效（见 8.5 节末） |
| `ecosystem.config.js`、`start-script-server.bat.pm2-bak` | 仅回退 PM2 时用，平时不要碰 |

---

## 10. 第二台设备（YANGSTARONE / 192.168.1.220）：从拷贝包补齐新架构并安装

- 时间：2026-10-26 16:45–17:05（Asia/Shanghai），账户 `YANGSTARONE\Administrator`，Session 1 交互桌面。
- 来源：用户把旧机（`WUYUAN` / 192.168.1.222）的 `C:\ScriptServer` 整个目录拷到本机。

### 10.1 拷贝包的实际状态（与第 9 节的偏差）

`deploy\` 只有 **PM2 时代**的文件：`install-autostart.ps1`、`INSTALL-EXECUTOR.md`、
`OPERATIONS-executor.md`、`ops-http.ps1`；`start-script-server.bat` 仍是 PM2 版（`call pm2 start`）。
第 8/9 节依赖的 `serve.py`、`cutover-to-uvicorn-task.ps1`、`verify-10.ps1`、`INSTALL-NEW-DEVICE.md`、
`env-change-restart.bat` **全部不在包里** → 无法照 9.2 直接装。

### 10.2 处置：按第 8.3 节的进程模型补齐缺失文件

> 说明：以下文件是**依据第 8.3 节描述重建**的（旧机原件未随包拷贝过来），行为等价但非逐字节相同；
> 业务代码 `script_server.py`（3.2.0，未改）与 `requirements.txt` 均为原件。

| 新增文件 | 作用 |
|---|---|
| `deploy\serve.py` | 读 `.env` → 注入 `os.environ` → `stdout/stderr` 重定向到 `logs\script-server-{out,error}.log`（UTF-8）→ 前台 `uvicorn.run(app, 0.0.0.0:9000)` 单 worker |
| `deploy\register-autostart-task.ps1` | 注册/卸载计划任务（幂等；`-Uninstall` 卸载；`-NoStart` 只注册不启动） |
| `deploy\install-autostart-uvicorn.bat` | 双击自动提权调用上面 ps1（普通窗口即可用） |
| `deploy\verify-10.ps1` | 第 3 章 10 项验收（ASCII，退出码 0/1） |

`.ps1`/`.bat` 全部**纯 ASCII**；`serve.py` 为 UTF-8（Python 3 默认）。

另：`start-script-server.bat` 由 PM2 版改写为 **shim**（双击自提权 → `schtasks /End` → `/Run` → 等 `/health` 200，
`-nopause` 供脚本调用），原件已备份为 `start-script-server.bat.pm2-bak`。改完 `.env` 直接跑它即可
（`serve.py` 每次启动都重读 `.env`，不存在 PM2 dump 里旧 token 的问题）。

### 10.3 本机安装结果

- **计划任务** `WorkflowPlatform-ScriptServer`：`AtStartup`（MSFT_TaskBootTrigger）/ `S4U` /
  `Highest` / `IgnoreNew` / `RestartCount=999, RestartInterval=PT1M` / `ExecutionTimeLimit=PT0S`（不限）；
  动作 `python.exe "C:\ScriptServer\script-server\deploy\serve.py"`，工作目录 `C:\ScriptServer\script-server`。
- **解释器**：`C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe`（3.12.10）；
  fastapi 0.119.1 / uvicorn 0.49.0 已满足 `requirements.txt`（`pip install -r requirements.txt` 复核通过）。
  注意 `python` 在普通 PATH 里解析到 `WindowsApps` 的 Store 存根，注册脚本用固定路径规避。
- **PM2 不参与**：本机 Node 是 nvm4w 装的 v24.13.1、`pm2` 7.x 存在但**未使用**（避免第 7 节 Session 0 问题）。
- **`.env`**：沿用拷贝来的值（token / `WF_SCRIPT_REQUIRE_TOKEN=1` / `WF_SCRIPTS_DIR=C:\ScriptServer\scripts` /
  `WF_SCRIPT_STATE_DIR=C:\ScriptServer\.script_server_state` / `WF_MAX_CONCURRENCY=4`）；
  `WF_ALLOWED_SCRIPT_ROOTS=C:\workflow-scripts-center` 本机**不存在该目录**，仅作白名单，暂无害。
- **清理**：旧机日志移到 `C:\ScriptServer\logs\_from-other-pc_<时间戳>\`；旧机幂等缓存
  `results\*` 移到 `.script_server_state\_from-other-pc_<时间戳>\`（否则 24h TTL 内同一 `task_run_id`
  会被判 `deduplicated` 而**不真正执行**）。

### 10.4 验收（本机实测）

| # | 项目 | 结果 |
|---|---|---|
| 1 | `verify-10.ps1`（计划任务托管，pid 29496） | ✅ **10/10 PASS** |
| 2 | Stop/Start 计划任务（冷启动等价测试） | ✅ pid 29496 → 32384，`/health` 立即 200 |
| 2b | 双击 `start-script-server.bat -nopause`（shim） | ✅ pid 32384 → 21692，`/health` 200 |
| 3 | 重启后再跑 `verify-10.ps1` | ✅ 10/10 PASS |
| 4 | 任务详情 | ✅ Boot 触发 / S4U / Highest / serve.py 动作 |
| 5 | 进程 | ✅ `python.exe` pid 32384，`Session 0`，无 `node.exe` 参与 |
| 6 | 真实重启后的自启 | ❌ **未验证**（未重启本机） |
| 7 | 防火墙 9000 放行 | ⏸ 当前网络位置是 **公用配置文件，防火墙为关闭**，未加规则（见 10.6） |
| 8 | 平台「设备管理」登记 | ⏳ 待做（见 10.5） |

### 10.5 平台侧还要做的两件事

1. 「设备管理」新增设备：`name` 任意（如 `exec-220`）、`base_url=http://192.168.1.220:9000`、
   `token` 与 `C:\ScriptServer\script-server\.env` 的 `WF_SCRIPT_TOKEN` **逐字符一致**；保存后点「检测」应在线。
2. 若平台机与执行机不在同一网段/防火墙策略不同，先在平台机 `Test-NetConnection 192.168.1.220 -Port 9000`。

### 10.6 防火墙（当前无需操作，仅记录）

`netsh advfirewall show currentprofile` → 当前为**公用配置文件，状态=关闭**（域配置文件为开启但当前网络不是域），
故 9000 目前跨机可达。若以后启用防火墙，按说明书 2.7 放宽（管理员）：

```powershell
New-NetFirewallRule -DisplayName "script-server 9000" -Direction Inbound -Protocol TCP `
  -LocalPort 9000 -RemoteAddress 192.168.1.0/24 -Action Allow
```

### 10.7 本机的坑（新增证据）

- **DSH 沙箱会话里 `/run` 必 500**：`PermissionError: [WinError 5]`（asyncio 为子进程建命名管道被拒），
  `GET /scan`、`/health*` 正常。与执行器无关；用计划任务/普通窗口启动即正常。→ 排查时先看服务进程是
  `Session 0`（任务托管）还是被沙箱/受限 shell 拉起。
- `/scan` 的 `dir_path` 是 **query 参数**（`POST /scan?dir_path=...`），传 JSON body 会被忽略并返回
  `{"files":[]}`；`GET /scan` 返回 405。
- 拷机后必须先处理 `.script_server_state\results`，否则旧机结果会在 TTL 内让新机"假成功"（只回缓存不执行）。

### 10.8 日常运维（本机，普通窗口）

```powershell
$ops = 'C:\ScriptServer\script-server\deploy\ops-http.ps1'
powershell -NoProfile -ExecutionPolicy Bypass -File $ops status   # 监听/进程/任务/health
powershell -NoProfile -ExecutionPolicy Bypass -File $ops ready    # 就绪探针
powershell -NoProfile -ExecutionPolicy Bypass -File $ops logs     # 服务日志
powershell -NoProfile -ExecutionPolicy Bypass -File $ops tasks    # 运行中任务
# 重启/改 .env 后：需要管理员（任务 Stop/Start + Session 0 进程）
powershell -NoProfile -ExecutionPolicy Bypass -File $ops restart
# 或双击/执行 start-script-server.bat（自提权，等价于 /End → /Run → 等 health）
# 或双击 deploy\install-autostart-uvicorn.bat 重新注册并启动
```

### 10.9 2026-10-27 用新安装器重装（install.bat + watchdog）

- 时间：2026-10-27 17:41（Asia/Shanghai）；方式：`install.bat -nopause`（UAC 提权）→ `deploy\cutover-to-uvicorn-task.ps1`。
- 替换：旧计划任务实例 pid 4148（旧版 `serve.py`）被结束；重新注册并启动新任务 pid 30884，`/health` 200。
- 结果：`verify-10.ps1` **10/10 PASS**；主任务 State=Running（Boot 触发 / S4U / Highest），动作
  `...\Python312\python.exe "C:\ScriptServer\script-server\deploy\serve.py"`；watchdog State=Ready（每 1 分钟）。
- 修复：`cutover-to-uvicorn-task.ps1` 增加 `Find-Python`（固定候选路径优先），规避 `WindowsApps\python.exe`
  0 字节 Store 存根——本机 machine PATH 中存根排在真实 Python 前，原 `Get-Command python` 会取到存根。
- PM2：重装时发现 daemon 实际仍在运行（pid 26412，`...\npm\node_modules\pm2\lib\Daemon.js`；被 DSH 沙箱隐藏），
  已于 17:46 卸载（见 10.10）；`node.exe` 进程数随后减少 1，其余属 WorkBuddy/紫鸟等软件。
- 未做：真实重启自启验证、防火墙 9000 规则、平台设备登记。
- 证据：`C:\ScriptServer\logs\reinstall-report.log`、`logs\cutover.log`、`script-server\logs\verify-result.json`。

### 10.10 2026-10-27 17:46 卸载 PM2

- 原因：执行器已由计划任务托管，PM2 属旧架构遗留（`npm ls -g` 显示 `pm2@7.1.0`）。
- 发现：daemon 实际在运行（pid 26412）；本轮早期用普通 shell 的 `Get-Process` 看不到 node.exe，
  误判"daemon 未运行"，已纠正——DSH 沙箱会隐藏进程，必须用提权脚本的 `Win32_Process` 查。
- 操作：提权 `taskkill /F /T /PID 26412` → 删除 `~\.pm2`、`...\npm\node_modules\pm2`、11 个 `pm2*` shim
  （等价 `npm uninstall -g pm2`，直接删目录，不依赖 npm）。
- 结果：`where pm2` 无结果、`npm ls -g` 无 pm2、无 pm2 计划任务/服务/Run 键；执行器未受影响
  （`/health` 200、主任务 Running，进程未重启）。
- 保留：`ecosystem.config.js`、`start-script-server.bat.pm2-bak` 仅作回退参考；node/npm 与其它全局包未动。
- 证据：`C:\ScriptServer\logs\pm2-uninstall-report2.log`、`logs\pm2-uninstall-transcript.log`。

