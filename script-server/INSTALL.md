# script-server（脚本执行器）新设备安装与使用说明

> **适用版本**：`script_server.py` VERSION = **3.3.0**（单文件 FastAPI）
> **适用对象**：在一台全新 Windows 机器上安装、启动、接入平台的运维/实施工程师
> **托管方式**：Windows 计划任务直接运行 `deploy\serve.py`，另有 1 分钟 watchdog 兜底（只依赖 Python）
> **文档日期**：2026-10-27（按新托管方式重写）
> **配套材料**：[`MAINTENANCE.md`](MAINTENANCE.md)（日常运维 / 排障 / 升级 / 回退）

---

## 0. 先读这一页

### 0.1 它是什么、不是什么

一句话：**装在 Windows 上、按 HTTP 请求执行本机 Python 脚本的"脚本执行代理"**。

| 是 | 不是 |
|---|---|
| `POST /run` 执行 `scripts\` 下的 `.py`，同步返回退出码和输出 | ❌ 不是 Web 界面（浏览器打开 `http://127.0.0.1:9000/` 只会看到 404 JSON） |
| HTTP API 服务，平台或任何持 token 的调用方可调 | ❌ 不参与工作流调度（谁先跑、重试几次是平台的职责） |
| 单进程、单机、单实例 | ❌ 不连数据库/Redis，不需要 Docker/WSL |
| 只开一个端口：**TCP 9000** | ❌ 不是 GitHub 上同名的 `bugy/script-server`（那个有 Web 配置界面） |

### 0.2 新设备要装什么

| 软件 | 是否必须 | 用途 | 获取方式 |
|---|---|---|---|
| **Python 3.10+（推荐 3.12）** | **必须** | 运行服务本体；也是所有业务脚本的解释器 | python.org 安装包 |
| **Windows 计划任务** | **必须**（生产） | 开机自启 + 托管进程本体；`deploy\cutover-to-uvicorn-task.ps1` 一键注册 | 系统自带（脚本需管理员） |
| **watchdog（随 `deploy\` 提供）** | **必须**（生产） | 进程意外退出后 1 分钟内自动拉起；`deploy\install-watchdog.ps1` 注册 | 无需下载 |
| 浏览器 / Redis / PostgreSQL / Docker / WSL | **不需要** | — | — |

> 交付目录里若出现 `jinteng\`（独立的凭据工具，约 18MB，与本系统无集成），**不要拷到新设备、也不要启动它**。

### 0.3 最短路径（熟悉命令行的人照抄即可）

```powershell
# 1) 环境：Python 3.10+（推荐 3.12，勾 Add to PATH）
python --version

# 2) 放文件：C:\ScriptServer\script-server\（代码+配置）、C:\ScriptServer\scripts\（业务脚本）

# 3) 依赖
cd C:\ScriptServer\script-server
python -m pip install -r requirements.txt

# 4) 生成 token 并写 .env（复制 .env.example 为 .env；填 token，并显式写 WF_SCRIPTS_DIR）
python -c "import secrets;print(secrets.token_hex(24))"
copy .env.example .env
notepad .env

# 5) 【管理员】注册开机自启 + 1 分钟看护，并立即启动（幂等，可重复执行）
powershell -NoProfile -ExecutionPolicy Bypass -File .\deploy\cutover-to-uvicorn-task.ps1
#    或直接双击 .\install.bat（自动请求管理员权限，内部执行同一步）

# 6) 验证（普通窗口）
powershell -NoProfile -ExecutionPolicy Bypass -File .\deploy\ops-http.ps1 status
powershell -NoProfile -ExecutionPolicy Bypass -File .\deploy\verify-10.ps1

# 7) 防火墙（管理员）：只放行平台机器访问 9000

# 8) 按第 3 章 10 项验收逐条打勾
```

每一步的细节、为什么这么做、失败怎么排查，见第 1–6 章。

### 0.4 部署后必须记住的 6 条

1. **`WF_SCRIPT_TOKEN` 必须非空**（建议 ≥32 位随机串），并同时设 `WF_SCRIPT_REQUIRE_TOKEN=1`；3.2.0 起"空 token + 对外监听"会**直接拒绝启动**；
2. **`--workers` 必须为 1**（3.2.0 检测到多 worker 直接拒绝启动），不要自作主张加并发；
3. **`.env` 由 `deploy\serve.py` 在每次启动时读取**；直接用 `python -m uvicorn` 启动则不读 `.env`（这是最常见的“配了没生效”原因）；
4. **超时和取消返回 `exit_code=-1`**，不是 4/5；`exit_code=1` 是"部分成功"；
5. 只放行平台出口 IP 访问 9000，**不要暴露公网**；本服务无 HTTPS；
6. 升级/重启前先 `POST /drain` 排空；崩溃/断电仍会中断在跑任务，业务脚本必须幂等。

### 0.5 本文的证据来源

- 代码与配置事实（版本、环境变量、接口字段、状态码、退出码语义、默认值）来自 **3.2.0 源码逐行核对**；
- 安装/运行经验来自本仓库的实测记录（`notes/02-install-verify.md`）与评审文档；
- 托管方式（计划任务 + `serve.py` + watchdog）与开机自启已在 192.168.1.210 实测（2026-10-27）：10/10 验收、强杀后 37–56 秒恢复、平台设备探活回到 `ok`；新设备仍请按第 3 章逐条复核。

### 0.6 交给自动化 / Agent 执行时的约定

如果这份文档由脚本或 AI Agent 在新机器上执行，按“顺序 + 每步验收 + 失败即停”来，不要跳步：

1. **先自检环境**（Python、依赖、管理员、9000 端口、脚本目录），任何一项不满足就停下报告，不要“先装了再说”；
2. **每步都有验收命令**：拷贝后 `Test-Path`、依赖后 `python -c "import fastapi,uvicorn"`、`.env` 后核对 token 长度、
   注册后 `Get-ScheduledTask`、启动后 `/health`；
3. **只能由人确认的操作**：重启 Windows、改平台设备 token、改防火墙、删除已有任务；
4. **失败时不要继续下一步**，把命令、退出码、`logs\script-server-error.log` 最后 30 行一起回报；
5. **完成判据**：第 3 章 10/10 + §2.8 四条硬指标 + 平台设备 `last_status=ok`，缺一不算完成。

---

## 1. 安装前准备

### 1.1 机器要求

| 项 | 最低 | 建议 |
|---|---|---|
| 操作系统 | Windows 10 / Server 2016 | Windows 11 / Server 2019+ |
| CPU | 2 核 | 4 核 |
| 内存 | 2 GB | 4 GB+ |
| 磁盘 | 2 GB 空闲 | 20 GB+ |
| 网络 | 与平台互通 | 固定 IP / 可解析主机名 |
| 权限 | 普通用户可运行服务 | 管理员用于装自启；业务脚本用专用低权限账户 |

### 1.2 安装 Python 3.12

1. 打开 <https://www.python.org/downloads/windows/>，下载 **Python 3.12.x 64-bit**；
2. 运行安装包，**勾选 "Add python.exe to PATH"**，建议 "Install for all users"；
3. 安装完成后**新开一个 PowerShell**（旧窗口 PATH 不会更新），确认：

```powershell
python --version          # 期望 Python 3.12.x
where.exe python          # 期望 C:\Program Files\Python312\python.exe
python -m pip --version   # 期望 pip 24.x/25.x
```

> PowerShell 里 `where` 是 `Where-Object` 的别名（会静默无输出），查路径请用 `where.exe` 或
> `(Get-Command python).Source`。

常见问题：

- 输入 `python` 弹出 Microsoft Store → Windows 设置里关闭"应用执行别名"中的 python；
- 机器离线 → 在有网机器下载完整安装包（不要用 embeddable 包，它没有 pip）；
- 多版本共存 → 用绝对路径启动服务，并把依赖装到**这个**解释器（见 1.5）。

### 1.3 只需要 Python

服务由 Windows 计划任务直接运行 `deploy\serve.py`，没有额外的守护进程或运行时依赖。

> 编码：不要设 `PYTHONUTF8=1`（会改变业务脚本 `open()` 默认编码）；`serve.py` 只设 `PYTHONIOENCODING=utf-8`。
> 手写的 `.ps1` 含中文时必须保存为 **UTF-8 with BOM**（否则 Windows PowerShell 5.1 按 ANSI 读会报 `Missing closing '}'`）。

### 1.4 目录规划（独立执行器布局）

```text
C:\ScriptServer\                        ← 建议根目录（不要放 C:\Program Files，权限麻烦）
├─ script-server\                       ← 本服务，只拷必要文件
│  ├─ script_server.py                  ← 服务本体（必须）
│  ├─ requirements.txt                  ← 依赖清单（必须）
│  ├─ serve.py 不在根目录 → 见 deploy\serve.py
│  ├─ .env                              ← 自己创建（含 token，收紧 ACL）
│  ├─ .env.example
│  ├─ start-script-server.bat           ← schtasks /End → /Run → 等 health
│  ├─ env-change-restart.bat            ← 改完 .env 一键重启并校验
│  ├─ INSTALL.md（本文）                 ← 安装 + 使用
│  ├─ MAINTENANCE.md                    ← 运维 + 排障
│  ├─ deploy\
│  │  ├─ serve.py                       ← 启动器（读 .env，前台跑 uvicorn，进程即服务本体）
│  │  ├─ cutover-to-uvicorn-task.ps1    ← 【管理员】注册主任务 + watchdog 并启动
│  │  ├─ install-watchdog.ps1           ← 【管理员】单独注册/删除 watchdog
│  │  ├─ watchdog.ps1                   ← 每分钟检查 9000，掉了就拉起主任务
│  │  ├─ ops-http.ps1                   ← 日常运维（health/ready/tasks/logs/status/restart/drain）
│  │  ├─ verify-10.ps1                  ← 10 项验收
│  │  ├─ verify-logging.py              ← 日志分级/轮转离线验收
│  │  ├─ verify-hardening.py            ← 幂等/三态码/并发策略/审计 在线验收
│  │  ├─ INSTALL-EXECUTOR.md             ← 新设备部署步骤（简版）
│  │  └─ register-autostart-task.ps1     ← 备用：单独注册自启任务（install.bat 之外）
│  └─ logs\                             ← 服务日志（serve.py 自动建，按天轮转保留 14 天）
│     ├─ script-server-out.log          ← 业务 INFO+
│     ├─ script-server-error.log        ← 仅 ERROR+
│     ├─ script-server-access.log       ← 每请求一行
│     ├─ script-server-audit.log        ← 业务审计（一次运行一行 JSON）
│     └─ watchdog.log
├─ scripts\                             ← 业务脚本目录（WF_SCRIPTS_DIR 指这里）
│  ├─ hello.py
│  └─ 采集\xxx.py                        ← 可建子目录，/run 用相对路径
├─ .script_server_state\                ← 自动创建：结果缓存 + running.json（勿放网络盘）
└─ logs\                                ← cutover.log 等安装/维护日志
```

> **目录可以放任意路径**：`deploy\*.ps1` 都以自身位置推导安装根，`start-script-server.bat` 用 `%~dp0`，
> 所以整个目录放 `D:\tools\script-server` 也能跑（`.env` 里的 `WF_SCRIPTS_DIR`、`WF_SCRIPT_STATE_DIR`
> 等绝对路径要同步改）。本文命令以 `C:\ScriptServer` 为例。

> **状态目录默认位置 = 脚本目录的上一级**（`<scripts 父目录>\.script_server_state`）。
> 为避免“脚本目录换位置、状态目录跟着跑”，`.env` 里建议**显式写** `WF_SCRIPT_STATE_DIR`。
>
> **`WF_SCRIPTS_DIR` 不要依赖默认值**：`serve.py` 只负责读 `.env`，不会替它补脚本目录默认值；
> 不同启动方式算出的默认值不一样（`<script_server.py 所在目录>\scripts`），所以本文布局下必须在 `.env` 里显式写绝对路径。

### 1.5 解释器选择（venv 与否）

- **单机专用执行器：直接用系统 Python 最省事**（`serve.py` 用启动它的解释器跑 uvicorn，并以同一解释器执行脚本）；
- 多项目共存/想隔离：建 venv，但**计划任务的动作必须指向 venv 的 python**——注册时改 `cutover-to-uvicorn-task.ps1` 的 `$Python`
  或 `New-ScheduledTaskAction -Execute`，否则依赖装错地方；
- **铁律：服务用哪个解释器，业务脚本就用哪个解释器**（`/run` 内部用 `sys.executable` 子进程执行脚本）。
  用 `/health/ready` 返回的 `checks.python.path` 可确认当前是哪个；

### 1.6 网络与端口

| 项 | 值 |
|---|---|
| 服务监听 | `0.0.0.0:9000`（`serve.py` 默认，可用 `WF_BIND_HOST` / `--host` 覆盖） |
| 平台访问地址 | `http://<执行机IP>:9000`（去尾斜杠） |
| Docker 里的平台 | `http://host.docker.internal:9000`（**不能写 127.0.0.1**，那是容器自己） |
| TLS | 服务不提供；跨不可信网段需 Nginx/Caddy 反向代理终止 TLS |

---

## 2. 安装步骤

### 2.1 第 1 步：拷贝文件

从**当前这台机器**（或安装包/共享盘，例如 `\\<文件服务器>\serviceg共享\ScriptServer\script-server`）把以下文件复制到新设备 `C:\ScriptServer\script-server\`。
**不要整个目录无脑拷**：

```powershell
# $src = 安装包所在目录（共享盘或先拷到本机 D:\install\script-server）
$src = 'D:\install\script-server'          # 改成你实际存放安装文件的目录
$dst = 'C:\ScriptServer\script-server'

New-Item -ItemType Directory -Force $dst, 'C:\ScriptServer\scripts' | Out-Null
Copy-Item "$src\script_server.py", "$src\requirements.txt", "$src\.env.example",
          "$src\start-script-server.bat", "$src\env-change-restart.bat",
          "$src\autostart-logon.ps1" -Destination $dst -Force
Copy-Item "$src\deploy" -Destination $dst -Recurse -Force
Copy-Item "$src\INSTALL.md", "$src\MAINTENANCE.md" -Destination $dst -Force
```

> `autostart-logon.ps1` 必须带：平台自带的 `start-platform.bat` 第 6 步会调用它来“确保执行器任务在跑”。
> `logs\` 不用拷，`serve.py` 首次启动会自动创建。

**不要拷**：`jinteng\`（与本系统无关的独立凭据工具）、`.deploy-backup-*\`（旧版本备份）、`.script_server_state\`（运行数据）、
`logs\`、`__pycache__\`、`verify\`、`notes\`、`tests\`、`tools\`；`start.bat` 与 `run_server.py` 仅开发/排障用，生产可不拷。

> 包根目录的 `install.bat` 是 §2.5 方式 B 的一键版（自动提权 → 注册任务 → 启动 → 打印状态），
> 适合“拷贝整个目录后双击安装”。

### 2.2 第 2 步：安装 Python 依赖

先自检（任一项不满足就先修，别继续）：

```powershell
python --version                                         # 3.10+（推荐 3.12）
where.exe python                                         # 记下绝对路径，注册任务时会用到
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
(New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)   # 后面注册任务需要 True
Get-NetTCPConnection -LocalPort 9000 -State Listen -ErrorAction SilentlyContinue  # 期望为空
```

```powershell
cd C:\ScriptServer\script-server
python -m pip install -r requirements.txt
```

`requirements.txt` 内容：

```text
fastapi>=0.115,<0.120
uvicorn[standard]>=0.30
```

**离线/内网机器**（在有网机器上打包 wheel）：

```powershell
# 有网机器
python -m pip download -r requirements.txt -d wheels

# 拷贝整个 wheels 目录到新设备后
python -m pip install --no-index --find-links wheels -r requirements.txt
```

**验证依赖装对了**（会打印版本，且就是服务将使用的解释器）：

```powershell
python -c "import sys, fastapi, uvicorn; print(sys.executable); print(fastapi.__version__, uvicorn.__version__)"
```

### 2.3 第 3 步：生成鉴权 token

任选一种，**建议 ≥32 位随机串**（执行器不强制校验长度，但生产就这么做）；**每台执行机用不同 token**：

```powershell
# 方式 1（推荐，需要 Python）
python -c "import secrets;print(secrets.token_hex(24))"

# 方式 2（PowerShell，48 位十六进制）
-join (1..48 | ForEach-Object { '0123456789abcdef'[(Get-Random -Maximum 16)] })

# 方式 3（安装了 Git for Windows）
openssl rand -hex 24
```

把输出整行复制备用（下一步写入 `.env`，平台设备登记时也用同一个值）。

### 2.4 第 4 步：编写 `.env`

```powershell
cd C:\ScriptServer\script-server
copy .env.example .env
notepad .env
```

**最小可用模板**（`WF_SCRIPTS_DIR` 在本布局下是**必填**，原因见 1.4）：

```ini
# ===== 必填 =====
WF_SCRIPT_TOKEN=把上一步生成的48位hex粘到这里
WF_SCRIPT_REQUIRE_TOKEN=1
WF_SCRIPTS_DIR=C:\ScriptServer\scripts

# ===== 推荐显式配置 =====
WF_SCRIPT_STATE_DIR=C:\ScriptServer\.script_server_state
WF_MAX_CONCURRENCY=4
```

> 写 `.env` 时**值不要加引号**；`serve.py` 只剥成对引号，不会处理行尾注释
> （`KEY=value  # 注释` 会把注释算进值里）。

常用可选项（默认值即可，按需打开）：

```ini
# 允许执行脚本目录之外的固定目录（分号分隔；默认不允许）
# WF_ALLOWED_SCRIPT_ROOTS=D:\other-scripts;E:\jobs
# 允许 /scan 扫描的目录（分号分隔；默认只允许 WF_SCRIPTS_DIR）
# WF_SCAN_ALLOWED_ROOTS=D:\other-scripts
# 单流输出上限（字节，默认 8MB）
# WF_MAX_OUTPUT_BYTES=8388608
# 成功结果缓存 TTL（秒，默认 24h）
# WF_RESULT_CACHE_TTL=86400
```

> **本环境（工作流平台）特有的绝对路径**：平台的 http 任务用的是绝对路径 `E:\workflow-scripts-center\...`。
> 新机器必须把该目录一起带过去（或把任务配置改成新路径），并在 `.env` 里加：
> `WF_ALLOWED_SCRIPT_ROOTS=E:\workflow-scripts-center`（多个根用分号分隔）。
> 不加这一行，这些任务会返回 403「路径不在允许的目录内」。

**`.env` 到底怎么生效（必须先理解）**：

- `script_server.py` **自己不读 `.env`**；读它的是 `deploy\serve.py`（每次启动把键值对放进 `os.environ`）；
- `serve.py` 只额外设 `WF_BIND_HOST`、`PYTHONIOENCODING=utf-8`、`PYTHONUNBUFFERED=1`，**不会补 `WF_SCRIPTS_DIR`**，
  所以 `.env` 里必须显式写脚本目录（见 1.4）；
- 因此：**计划任务 / `start-script-server.bat` / `python deploy\serve.py` 启动时 `.env` 生效；直接 `python -m uvicorn` 不生效**；
- 改完 `.env` 需要重启（`env-change-restart.bat` 或 `ops-http.ps1 restart`），`serve.py` 会重新读取。

### 2.5 第 5 步：首次启动（先前台验证，再交给计划任务）

**方式 A：前台验证（第一次安装推荐，能直接看到报错）**

`python deploy\serve.py` 在当前进程里读 `.env` 并启动 uvicorn（workers=1，不 fork）：

```powershell
cd C:\ScriptServer\script-server
python .\deploy\serve.py --host 127.0.0.1 --port 9000
```

看到 `[serve] start pid=... version=3.3.0 ...` 与 `Uvicorn running on http://127.0.0.1:9000` 即成功；
另开窗口验证后 `Ctrl+C` 停止。前台方式日志只在控制台，生产不要这么跑。

**方式 B：计划任务（生产标准做法）**

```powershell
# 管理员 PowerShell：注册主任务 + watchdog，并立即启动
powershell -NoProfile -ExecutionPolicy Bypass -File C:\ScriptServer\script-server\deploy\cutover-to-uvicorn-task.ps1
```

> 也可以直接双击包根目录的 `install.bat`：它会自动请求管理员权限，再执行同一脚本。

脚本结束时会打印 `/health` 与 `/health/ready` 的结果；退出码非 0 表示没起来（看 `logs\script-server-error.log`）。

之后日常启动/重启用：

```powershell
# 普通窗口即可
powershell -NoProfile -ExecutionPolicy Bypass -File C:\ScriptServer\script-server\deploy\ops-http.ps1 restart
# 或双击
C:\ScriptServer\script-server\start-script-server.bat
```

**停止服务**

```powershell
# 推荐：停计划任务（干净，不会留孤儿）
Stop-ScheduledTask -TaskName 'WorkflowPlatform-ScriptServer'
# 若要停超过 1 分钟，先建 .maintenance 文件，否则 watchdog 会把它拉回来
New-Item -ItemType File -Path C:\ScriptServer\script-server\.maintenance -Force
```

只在前台方式下才需要按端口找 pid：

```powershell
Get-NetTCPConnection -LocalPort 9000 -State Listen | Select-Object -ExpandProperty OwningProcess
taskkill /F /T /PID <pid>
```

### 2.6 第 6 步：放一个测试脚本

在 `C:\ScriptServer\scripts\` 下新建 `hello.py`：

```python
# -*- coding: utf-8 -*-
import os
import sys

print("hello from script-server")
print("args:", sys.argv[1:])
print("env DEMO:", os.environ.get("DEMO", ""))
sys.exit(0)
```

再放一个用于超时验证的 `sleep5.py`：

```python
import time
print("start", flush=True)
time.sleep(5)
print("end", flush=True)
```

### 2.7 第 7 步：防火墙（跨机访问必须）

管理员 PowerShell，**只放行平台/Worker 的出口 IP**（不要长期用 `Any`）：

```powershell
Remove-NetFirewallRule -DisplayName "script-server 9000" -ErrorAction SilentlyContinue
New-NetFirewallRule -DisplayName "script-server 9000" -Direction Inbound -Protocol TCP `
  -LocalPort 9000 -RemoteAddress 192.168.1.10 -Action Allow
```

在平台机器上验证连通：

```powershell
Test-NetConnection -ComputerName <执行机IP> -Port 9000
curl.exe -s http://<执行机IP>:9000/health
```

### 2.8 第 8 步：开机自启 + 进程看护（管理员，生产必须）

不注册自启，机器重启/断电后 9000 会永久停摆。新方案由**计划任务直接托管 uvicorn**，再加一个 1 分钟看护兜底。

```powershell
# 一条命令完成：停旧服务 → 注册主任务 → 注册 watchdog → 启动 → 验 health
powershell -NoProfile -ExecutionPolicy Bypass -File `
  C:\ScriptServer\script-server\deploy\cutover-to-uvicorn-task.ps1
```

| 项 | 主任务 | 看护任务 |
|---|---|---|
| 任务名 | `WorkflowPlatform-ScriptServer` | `WorkflowPlatform-ScriptServer-Watchdog` |
| 触发 | `AtStartup`（开机即启，不等登录） | 每 1 分钟（duration 3650 天） |
| 身份 | 当前用户 `S4U` + `Highest` | 同左 |
| 动作 | `python.exe "…\deploy\serve.py"` | `powershell -File …\deploy\watchdog.ps1` |
| 作用 | 托管服务本体（进程 = uvicorn） | 9000 无 listener 时 `Start-ScheduledTask` 主任务 |
| 日志 | `logs\script-server-{out,error,access}.log`（按天轮转，保留 14 天） | `logs\watchdog.log` |

**为什么必须有 watchdog（实测，2026-10-27）**：计划任务的 `RestartOnFailure` 在本环境**不生效**——
外部杀进程（`LastTaskResult=0xFFFFFFFF`）或动作进程非零退出（`LastTaskResult=7`）后，任务都停在
`State=Ready` 且不再重跑。只靠主任务 = 进程一死就永久停摆。watchdog 实测 **37–56 秒**恢复。

维护时想暂停自动恢复：在 `C:\ScriptServer\script-server\` 建空文件 `.maintenance`，完事删掉（最多 1 分钟后恢复）。

**必须做一次真实重启验收**：

```powershell
# 重启 Windows，等 3 分钟后：
powershell -NoProfile -ExecutionPolicy Bypass -File C:\ScriptServer\script-server\deploy\ops-http.ps1 status
Get-Content C:\ScriptServer\script-server\logs\script-server-out.log -Tail 5 -Encoding UTF8
#   期望：任务 Running、/health 200、日志有 [serve] start pid=...
```

通过判据：`ops-http.ps1 status` 显示 9000 监听、任务 Running、`/health` 200；watchdog 任务 `State=Ready`。
若注册失败：确认是用**管理员** PowerShell 执行；`S4U` 被拒时改用 `-LogonType Interactive`（需登录后启动），
或改用 NSSM 注册 Windows 服务。

**安装完成的四条硬指标**（缺一不算装好）：

```powershell
# ① 主任务动作必须是 serve.py
(Get-ScheduledTask -TaskName 'WorkflowPlatform-ScriptServer').Actions | Format-List Execute,Arguments
# ② 看护任务存在且 Ready
(Get-ScheduledTask -TaskName 'WorkflowPlatform-ScriptServer-Watchdog').State
# ③ 没有任何 node.exe（有就是又走了旧方案）
(Get-Process node -ErrorAction SilentlyContinue | Measure-Object).Count     # 期望 0
# ④ 日志有 serve 启动行、/health 200
Get-Content C:\ScriptServer\script-server\logs\script-server-out.log -Tail 3 -Encoding UTF8
```

**没有管理员权限时**（Agent 常见）：不要反复重试 `Register-ScheduledTask`，用静默提权重跑一次：

```powershell
Start-Process powershell.exe -Verb RunAs -ArgumentList @(
  '-NoProfile','-ExecutionPolicy','Bypass','-File',
  'C:\ScriptServer\script-server\deploy\cutover-to-uvicorn-task.ps1') -Wait
```

（UAC 弹窗无人确认时此步必然失败——这种情况必须停下让人处理，不要绕过。）

---

## 3. 首次验收（10 项，逐条打勾）

> 前提：服务已启动；`C:\ScriptServer\scripts\hello.py`、`sleep5.py` 已放好；记 `$t = '<你的token>'`。

| # | 检查 | 命令 | 通过判据 | 失败先查 |
|---|---|---|---|---|
| 1 | 端口监听 | `netstat -ano \| findstr :9000` | 有 `LISTENING` | 进程没起 / 端口被占（`WinError 10048`）→ `ops-http.ps1 logs` |
| 2 | 公开探活 | `curl.exe -s http://127.0.0.1:9000/health` | 200，`version=3.3.0`，`auth_required=true`，`draining=false` | 仍是 `auth_required=false` → `.env` 未生效；版本不是 3.3.0 → 跑了旧代码 |
| 3 | 无 token 拒绝 | `curl.exe -s -o NUL -w "%{http_code}" http://127.0.0.1:9000/health/ready` | `401` | 返回 200 → token 没配（危险） |
| 4 | 带 token 就绪 | `curl.exe -s -H "X-Script-Token: $t" http://127.0.0.1:9000/health/ready` | `status=ok`，`checks.state_dir.enabled=true`，`checks.python.exists=true` | `degraded` → 看 `checks` 哪个 `false` |
| 5 | Bearer 兼容 | `-H "Authorization: Bearer $t"` 调 `/health/ready` | 200 | — |
| 6 | 扫描脚本 | `curl.exe -s -X POST -G --data-urlencode "dir_path=C:\ScriptServer\scripts" -H "X-Script-Token: $t" http://127.0.0.1:9000/scan` | 返回 `files` 列表含 `hello.py` | 403 → 路径非绝对/不在允许根；405 → 用了 GET |
| 7 | 真实执行 | `POST /run {"file_path":"hello.py","timeout_seconds":30,"task_run_id":"real-001"}` | `exit_code=0`、`success=true`、stdout 非空 | `exit_code=3` + `WinError 5` → 环境禁 asyncio 命名管道（换普通桌面/服务器，非产品缺陷） |
| 8 | 幂等重投 | 同 body 再发一次（同 `task_run_id`） | `deduplicated=true`，脚本不再执行 | 又执行了一次 → `task_run_id` 没传或变了 |
| 9 | 超时语义 | `sleep5.py` + `"timeout_seconds":2` | `exit_code=-1`、`status_label=脚本超时` | 返回 4 → 读了旧文档（4 只是文案） |
| 10 | 路径逃逸 | `POST /run {"file_path":"..\\..\\script_server.py"}` | `403` | 200 → 路径策略异常，停止上线排查 |

第 7–10 项的可复制命令（`$t` 换成你的 token）：

```powershell
$t = '<你的token>'; $base = 'http://127.0.0.1:9000'

# 7) 真实执行
@'
{"file_path":"hello.py","timeout_seconds":30,"task_run_id":"real-001"}
'@ | Set-Content .\run.json -Encoding UTF8
curl.exe -s -X POST "$base/run" -H "X-Script-Token: $t" -H "Content-Type: application/json" --data-binary "@run.json"

# 8) 幂等重投：同一条命令再执行一次，应答里应出现 "deduplicated":true

# 9) 超时语义
@'
{"file_path":"sleep5.py","timeout_seconds":2}
'@ | Set-Content .\run_timeout.json -Encoding UTF8
curl.exe -s -X POST "$base/run" -H "X-Script-Token: $t" -H "Content-Type: application/json" --data-binary "@run_timeout.json"

# 10) 路径逃逸
@'
{"file_path":"..\\..\\script_server.py"}
'@ | Set-Content .\run_escape.json -Encoding UTF8
curl.exe -s -X POST "$base/run" -H "X-Script-Token: $t" -H "Content-Type: application/json" --data-binary "@run_escape.json"
```

平台侧另做：设备管理里点"检测"应显示在线；从平台机 `Test-NetConnection <执行机IP> -Port 9000` 应通。

**Agent 执行的完成判定**：`verify-10.ps1` 输出 `===== 10/10 PASS =====`、§2.8 四条硬指标全过、
平台设备 `last_status=ok`，三者同时满足才算安装完成；任一失败按第 6 章排查，不要“先接平台再说”。

**不要自行重启 Windows**：§2.8 的真实重启验收会中断本机所有会话，必须由人确认时间后执行。
没有重启条件时，用 `schtasks /End` → `/Run` 做等价验收，并在交付说明里注明“真实重启未验证”。

---

## 4. 使用说明

### 4.1 核心概念

| 概念 | 含义 |
|---|---|
| 脚本 | `WF_SCRIPTS_DIR` 下的 `.py`/`.pyw`；`/run` 的相对路径按该目录解析 |
| 执行器实例 | 一个 uvicorn 进程（单 worker）；一台机器只跑一个 |
| `task_run_id` | 调用方传入的任务 ID，用于**幂等去重**与**取消定位**；不传则自动生成（此时无幂等） |
| 运行台账 | 内存 `task_manager` + 磁盘 `running.json`，记录在跑任务的 pid/脚本/超时 |
| 状态目录 | `.script_server_state\`：`results\<sha1>.json`（成功结果缓存）、`running.json`（对账台账） |
| 平台设备 | 平台「设备管理」里的一条记录（name / base_url / token），代表一台执行机 |

### 4.2 接口总览与鉴权

| 方法 | 路径 | 需要 token | 用途 |
|---|---|---|---|
| GET | `/health` | 否（公开） | 进程存活探针 |
| GET | `/health/ready` | **是** | 就绪探针（能不能执行） |
| POST | `/run` | **是** | 执行脚本（同步返回结果） |
| POST | `/scan?dir_path=<绝对路径>` | **是** | 扫描目录第一层 `*.py` |
| POST | `/cancel/{task_id}` | **是** | 取消运行中任务（杀进程树） |
| GET | `/tasks` | **是** | 列出运行中任务 |
| POST | `/drain?timeout=30` | **是** | 排空：拒绝新任务并等存量结束 |

鉴权头二选一（大小写不敏感）：

```http
X-Script-Token: <WF_SCRIPT_TOKEN>
Authorization: Bearer <WF_SCRIPT_TOKEN>
```

### 4.3 `GET /health` 与 `GET /health/ready` 的区别（最容易误判）

| | `/health` | `/health/ready` |
|---|---|---|
| 鉴权 | 公开 | 需 token |
| 含义 | **进程活着**（`status` 恒为 `ok`） | **现在能不能执行**（`ok` / `degraded`） |
| 关键字段 | `version`、`auth_required`、`draining`、`running_tasks`、`counters` | `checks.scripts_dir{writable}`、`checks.state_dir{enabled}`、`checks.python.path`、`checks.concurrency` |
| 注意 | 不泄露内部路径；**不能**用它判断"能执行" | HTTP 码仍是 200，**必须看 `status` 字段** |

`/health` 示例：

```json
{"status":"ok","version":"3.2.0","auth_required":true,"draining":false,
 "running_tasks":0,"uptime_seconds":9.7,"counters":{"requests":0,"spawned":0,"run_success":0}}
```

> 上例 `counters` 为**节选**，实际有 17 个计数键（含 `spawn_errors`、`kill_failed`、`rejected_busy`、
> `conflict_409`、`deduplicated`、`orphans_killed_at_startup` 等），排障时看它们更直接。

`/health/ready` 判读规则：

```text
status=ok 且 checks.state_dir.enabled=true        → 真正可用
status=degraded 且 checks.state_dir.enabled=false → 能跑脚本，但幂等/对账失效
status=degraded 且 draining=true                  → 正在排空/升级，暂不接受新任务
status=degraded 且 scripts_dir/python 异常         → 不能执行
HTTP 401                                          → token 不一致（最常见）
```

### 4.4 `POST /run`（核心接口）

#### 请求字段

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `file_path` | string | **是** | — | 相对 `WF_SCRIPTS_DIR` 或白名单内绝对路径；扩展名必须 `.py/.pyw` |
| `args` | string | 否 | `""` | 命令行参数：空格分隔，单/双引号可包裹含空格参数，反斜杠原样保留；超过 65536 个字符（约 64KB ASCII）→ 413 |
| `env` | object | 否 | `{}` | 追加/覆盖环境变量（值必须字符串）；危险键（`PYTHONPATH`/`PATH`/`WF_SCRIPT_*` 等）→ 400；超过 65536 个字符 → 413 |
| `timeout_seconds` | int | 否 | 300 | 1–7200；越界 → 422 |
| `task_run_id` | string | 否 | 自动生成 | 幂等键 + 取消键；建议直接用平台 TaskRun id |
| `block_strategy` | string | 否 | `reject` | 并发满时的策略：`reject` = 立即 429（带 `Retry-After`）；`queue` = 排队等空槽（最多 `WF_QUEUE_MAX_WAIT_SECONDS`=30s 或本次 `timeout_seconds`，取小） |

#### 调用示例

**PowerShell + curl.exe**（JSON 写文件，避免命令行引号地狱）：

```powershell
$t = '<你的token>'
$base = 'http://127.0.0.1:9000'

@'
{"file_path":"hello.py","args":"--name 测试","env":{"DEMO":"1"},
 "timeout_seconds":30,"task_run_id":"manual-001"}
'@ | Set-Content .\run.json -Encoding UTF8

curl.exe -s -X POST "$base/run" `
  -H "X-Script-Token: $t" `
  -H "Content-Type: application/json" `
  --data-binary "@run.json"
```

**PowerShell + Invoke-RestMethod**（直接得到对象）：

```powershell
$headers = @{ 'X-Script-Token' = '<你的token>' }
$body = @{ file_path = 'hello.py'; args = '--name 测试'; env = @{ DEMO = '1' }
           timeout_seconds = 30; task_run_id = 'manual-002' } | ConvertTo-Json -Depth 5
$r = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:9000/run' -Headers $headers `
       -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($body))
$r | Format-List task_id,exit_code,status_label,success,partial,deduplicated,duration_seconds
$r.stdout
```

**Python（httpx）**：

```python
import httpx

resp = httpx.post(
    "http://127.0.0.1:9000/run",
    headers={"X-Script-Token": "<你的token>"},
    json={"file_path": "hello.py", "args": "--name 测试", "env": {"DEMO": "1"},
          "timeout_seconds": 30, "task_run_id": "manual-003"},
    timeout=120,
)
data = resp.json()
print(data["exit_code"], data["status_label"], data["success"])
print(data["stdout"])
```

#### 响应字段（HTTP 200）

| 字段 | 说明 |
|---|---|
| `task_id` | 本次任务 ID（= 传入的 `task_run_id`，否则自动生成） |
| `code` | **三态判定码**：`200` 成功 / `500` 失败或取消 / `502` 超时（与 `exit_code` 互补，平台建议优先看它） |
| `exit_code` | 退出码；**超时/取消为 -1**；服务端内部异常为 3 |
| `stdout` / `stderr` | 标准输出/错误（UTF-8/GBK 智能解码；超上限截断） |
| `success` | 仅 `exit_code==0` 为 true |
| `partial` | `exit_code==1` 且不是崩溃时为 true（"部分成功"） |
| `status_label` | 成功 / 部分成功 / 网络错误 / 脚本错误 / 脚本超时 / 任务取消 / 终止失败 |
| `cancelled` | 是否因取消（/cancel、断连、关停）结束 |
| `timed_out` | 是否因超时结束（此时 `code=502`） |
| `deduplicated` | 是否命中幂等缓存（本次未真正执行） |
| `truncated` | 输出是否被截断 |
| `duration_seconds` | 执行耗时 |

示例：

```json
{"task_id":"manual-001","code":200,"exit_code":0,"stdout":"hello from script-server\n","stderr":"",
 "success":true,"partial":false,"status_label":"成功","cancelled":false,"timed_out":false,
 "deduplicated":false,"truncated":false,"duration_seconds":0.42}
```

#### 幂等与"平台重试"的关系（3.3.0，务必读懂）

| 情况（同一个 `task_run_id`） | 执行器行为 | 平台该怎么做 |
|---|---|---|
| 上次**成功**，响应丢了又重投 | 返回历史结果（`deduplicated=true`，`code=200`），**不会二次执行** | 当成成功继续；不要再换 ID 重跑（会产生重复副作用） |
| 上次**失败/超时/取消**（exit≠0、`code=500/502`） | **不缓存**，重投会真正重跑 | 正常重试即可，幂等不会挡 |
| 上次**还在运行** | `409` + `Retry-After` | 等几秒再查 `/tasks`，不要当成执行失败 |
| 从未见过这个 ID | 正常执行 | — |
| 脚本**内容改过**了，仍用旧 ID | 指纹含脚本 sha256 → 判定为新请求，**重跑**（不会返回旧结果） | 无需处理 |
| 平台每次重试都**换新** `task_run_id` | 每次都执行（幂等不介入） | 需要"同一次执行只跑一次"就固定 ID |

> 结论：**失败重试不受幂等影响**（失败不缓存）；幂等只防"其实已成功但响应丢了"的重复执行。
> 只有 exit 0 才写缓存（`results/<sha1(task_run_id)>.json`，TTL 24h）。

#### HTTP 状态码

| 码 | 场景 | 调用方处理 |
|---|---|---|
| **200** | 脚本执行完成（**含失败/超时/取消**，看 `exit_code`/`status_label`） | 按 `exit_code` 判定业务结果 |
| 400 | 路径无效/不存在/不是文件、args 引号未闭合、env 键非法 | 修正参数，不要重试 |
| 401 | token 缺失/错误 | 检查 token，不要重试 |
| 403 | 路径在允许目录外、扩展名不允许 | 修正路径/配置，不要重试 |
| 409 | 同 `task_run_id` 仍在运行（含"刚退出还没收尾"的窗口） | 响应带 `Retry-After: 5`；等几秒后查 `/tasks`，确认是"上次响应丢了"还是平台超时判失败 |
| 413 | `args` 或 `env` 超过 65536 个字符 | 减少参数 |
| 422 | 请求体不合法（timeout 越界、类型错、JSON 错） | 修正请求 |
| 429 | 并发已满（默认 4），响应带 `Retry-After: 5` | 退避重试，或改传 `"block_strategy":"queue"` 让执行器排队 |
| 500 | 服务端自身异常（典型：`/cancel` 杀进程树失败，可重试）；注意 `/run` 的**服务端内部异常仍返回 200 + `exit_code=3`**，不要只看 HTTP 码 | 看日志与 `counters.kill_failed` |
| 503 | 排空模式（调过 `/drain`） | 等待重启恢复；响应带 `Retry-After: 10` |

#### 退出码语义（平台按它判定，务必读懂）

| 退出码 | `status_label` | `success` | `partial` | 含义 |
|---|---|---|---|---|
| `0` | 成功 | true | false | 成功 |
| `1` | 部分成功（stderr 含 `Traceback` 时显示"脚本错误"） | false | 通常 true | **部分成功**，平台工作流继续（标警告） |
| `2` | 网络错误（只是文案） | false | false | 失败 |
| `3` | 脚本错误 | false | false | 失败（服务端内部异常也返回 3） |
| **`-1`** | 脚本超时 / 任务取消 / 终止失败 | false | false | 失败；看 `cancelled` 区分取消 |
| 4 / 5 | 仅文案表存在 | — | — | **执行器自己从不返回 4/5** |
| 其它 | `未知(N)` | false | false | 失败 |

三条实操结论：

1. **不要写 `exit_code == 4` 判超时**，永远不会命中；用 `status_label`/`cancelled`/`duration_seconds`；
2. **脚本失败请显式 `sys.exit(2+)`**：Python 未捕获异常默认退出码 1，会被平台当"部分成功"继续跑；
3. `partial=true` 不是成功，平台虽让工作流继续，但会标警告。

### 4.5 `/scan`、`/tasks`、`/cancel`、`/drain`

**`POST /scan`（三条硬规则：必须 POST、必须绝对路径、必须在允许根内）**：

```powershell
curl.exe -s -X POST -G --data-urlencode "dir_path=C:\ScriptServer\scripts" `
  -H "X-Script-Token: $t" http://127.0.0.1:9000/scan
```

- `dir_path` 省略/空 → `{"files":[]}`；不存在/是文件 → 400；
- **相对路径按服务进程 CWD 解析**，`dir_path=.` 通常 403，别用；
- 只扫第一层 `*.py`（**不递归**），返回 `name/relative/path`（`path` 是绝对路径）。

**`GET /tasks`**：

```powershell
curl.exe -s -H "X-Script-Token: $t" http://127.0.0.1:9000/tasks
```

返回 `count`、`tasks[]`（`task_id/pid/script/elapsed_seconds/timeout_seconds/cancelling`）、
`concurrency_limit`；内存台账，**重启即清空**。`elapsed_seconds` 远超 `timeout_seconds` 说明卡死，按 pid 排查或取消。

**`POST /cancel/{task_id}`**：

```powershell
curl.exe -s -X POST -H "X-Script-Token: $t" "http://127.0.0.1:9000/cancel/manual-001"
```

- 200 `{"status":"cancelled"}`：已杀进程树并确认退出；
- 200 `{"status":"cancelling"}`：还在 spawn 阶段，已登记取消；
- 404 = 任务不存在；400 = 已完成；500 = 终止失败（可重试）。

**`POST /drain`（升级/重启前排空）**：

```powershell
curl.exe -s -X POST -H "X-Script-Token: $t" "http://127.0.0.1:9000/drain?timeout=600"
```

返回 `{"draining":true,"running_tasks":N,"drained":bool,"timeout_seconds":600}`。
`drained=false` 表示还有任务没结束，可再次调用继续等。
**排空后没有"解除"接口，只能重启服务恢复接单**；排空期间 `/run` → 503。

### 4.6 业务脚本编写规范

| 规则 | 说明 |
|---|---|
| 位置与扩展名 | 必须在 `WF_SCRIPTS_DIR` 下（或其允许的额外根），`.py`/`.pyw` |
| 解释器 | 服务进程用的那个 Python（`sys.executable`）；装第三方库要装到这里 |
| 工作目录 | 子进程 `cwd = 脚本所在目录`；读写相对路径文件时注意 |
| 编码 | stdout/stderr 建议 UTF-8；服务端按 UTF-8→GBK→GB18030 顺序解码 |
| 退出码 | 成功 `sys.exit(0)`；失败 **`sys.exit(2+)`**；**不要用 1**（1 会被执行器标"部分成功"、平台继续跑工作流） |
| 参数 | 从 `sys.argv` 读；`args` 是单个字符串，按空格/引号切分 |
| 环境变量 | 从 `os.environ` 读；服务不会把 `WF_SCRIPT_TOKEN` 传给业务脚本 |
| 幂等 | 执行器重启、断连、超时都会中断任务，重试会重跑，脚本必须可重入 |

### 4.7 接入平台（设备任务）

#### 设备登记字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `name` | 是 | 业务唯一名（任务里 `config.device` 用它） |
| `base_url` | 是 | **平台/Worker 视角**可达地址，如 `http://192.168.1.11:9000`（去尾斜杠） |
| `token` | 生产必填 | 与执行机 `WF_SCRIPT_TOKEN` **逐字符一致** |
| `is_active` | — | 停用后任务在分发阶段失败 |

```powershell
# 平台 API 登记（需平台登录 JWT；示例）
$plat = 'http://192.168.1.10:8000'
$jwt  = '<平台 JWT>'
$body = @{ name='采集机1'; base_url='http://192.168.1.11:9000'; token='<执行机token>' } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "$plat/api/v1/devices" `
  -Headers @{ Authorization = "Bearer $jwt" } -ContentType 'application/json' -Body $body

# 探活
Invoke-RestMethod -Method Post -Uri "$plat/api/v1/devices/1/probe" -Headers @{ Authorization = "Bearer $jwt" }
```

也可以在平台「设置 → 设备管理」页面直接填写 name / base_url / token 后点"检测"。

#### 平台怎么探活

- 调度器每 **60s**（`WF_DEVICE_PROBE_SECONDS`）**串行**探活所有启用设备；
- 请求 `GET <base_url>/health/ready` + `X-Script-Token`；
- 200 且 `status=ok` → 在线；`degraded` → 报错并列出哪个 check 失败；**401 → token 不一致**；
- 404 → 回退 `GET /health`（兼容旧版执行器）。

#### 超时要写两处（高频出错）

| 处 | 写在哪 | 决定什么 |
|---|---|---|
| 平台等待 | 任务/节点 `timeout_seconds` | 平台等多久 = `max(任务/节点超时, body.timeout_seconds) + 60s` |
| 执行器超时 | `body.timeout_seconds`（1–7200，缺省 **300**） | 执行器多久杀脚本 |

```json
{
  "device": "采集机1",
  "method": "POST",
  "timeout_seconds": 900,
  "body": { "file_path": "采集.py", "timeout_seconds": 900 }
}
```

只写外层：平台等 900s，执行器 300s 就杀脚本。
只写 `body`：平台读超时（`max(任务/节点超时, body)+60s`）通常够用，但若工作流级 `workflow_timeout` 更短，
会在工作流层被取消，而执行器的脚本仍会跑到自己的 timeout——所以**两处都要与脚本实际时长留足余量**。

#### 设备任务如何传运行参数（别假设 `WF_INPUT_*`）

`WF_INPUT_*` 只对平台的 python/shell 任务自动注入，**http/设备任务不注入**。要传参：

```json
{
  "device": "采集机1",
  "method": "POST",
  "body": {
    "file_path": "采集.py",
    "args": "--date ${{ input.date }}",
    "env": { "WF_INPUT_DATE": "${{ input.date }}", "WF_INPUT_STORE": "${{ input.store }}" },
    "timeout_seconds": 900
  }
}
```

`body.env` 的值必须是字符串；脚本用 `os.environ["WF_INPUT_DATE"]` 读取。

#### 平台侧的 429 / 409 / 读超时

| 情况 | 平台行为 | 你该做什么 |
|---|---|---|
| 429 并发满 | 自动退避 2/4/6s 共 3 次 | 仍失败则降并发或扩机器 |
| 409 同 ID 在跑 | **不自动重试** | 查执行机 `/tasks`，人工核对是否重复投递 |
| 读超时 | `result_unknown=true`，**不自动重试** | 先查 `/tasks`，确认脚本是否还在跑 |
| 用户取消 | 回调 `POST /cancel/{task_run_id}` | 无需处理 |
| 平台 scheduler 超时清理 | **不回调 `/cancel`**（已知缺口） | 脚本会跑到自身 timeout，配置时留足余量 |

---

## 5. 日常运维

日常运维统一放在 [`MAINTENANCE.md`](MAINTENANCE.md)：启动/停止/重启、改 `.env`、日志与轮转、
巡检与告警、排空与升级、卸载、token 轮换、10 分钟排障清单。

这里只留三条最常用的：

```powershell
$ops = 'C:\ScriptServer\script-server\deploy\ops-http.ps1'
powershell -NoProfile -ExecutionPolicy Bypass -File $ops status    # 端口/进程/任务/health
powershell -NoProfile -ExecutionPolicy Bypass -File $ops restart   # drain → 停任务 → 起任务
powershell -NoProfile -ExecutionPolicy Bypass -File $ops logs      # 服务日志
```

---

## 6. 故障排查

| 现象 | 最可能原因 | 处理 |
|---|---|---|
| 服务起不来，日志 `WF_SCRIPT_TOKEN 为空…拒绝以无鉴权方式对外启动` | 3.2.0 行为：空 token + 对外监听 | 配 token（仅 127.0.0.1 调试允许空；生产禁止 `WF_SCRIPT_ALLOW_INSECURE=1`） |
| 服务起不来，日志 `检测到 --workers=N` | 用了多 worker | 改回 `--workers 1` |
| `/health` 的 `auth_required=false`（并发上限看 `checks.concurrency.limit`） | `.env` 没生效（裸 uvicorn 不读） | 用 `serve.py` 启动（`start-script-server.bat` / 计划任务），或改为启动前注入环境变量 |
| 改了 `.env` 不生效 | 服务没重启，或读的不是这份 `.env` | 跑 `env-change-restart.bat`（会校验 `concurrency.limit` 是否一致）；确认 `.env` 在 `script-server\` 根目录 |
| `/health/ready` 返回 401 | token 不一致（平台/调用方 vs 执行机） | 两边逐字符核对；注意尾随空格 |
| `/health/ready` `status=degraded` | 脚本目录不可写 / 状态目录不可用 / 正在排空 | 看 `checks` 哪项 `false`；状态目录放本地盘并显式配 `WF_SCRIPT_STATE_DIR` |
| `/run` 返回 403 `路径不在允许的目录内` | 用了脚本目录外的绝对路径或 `..` 逃逸 | 用相对 `WF_SCRIPTS_DIR` 的路径；确需别目录配 `WF_ALLOWED_SCRIPT_ROOTS` 后重启 |
| `/run` 返回 403 `不允许的脚本类型` | 扩展名不是 `.py/.pyw` | 改扩展名或调 `WF_ALLOWED_SCRIPT_EXTENSIONS` |
| `/run` 返回 400 `脚本不存在` | `WF_SCRIPTS_DIR` 配错 / 文件没放进去 | 核对 `.env` 与服务日志里的启动行（会打印 scripts 目录） |
| `/run` 返回 400 `不允许通过 env 注入的变量` | 3.2.0 起拒绝危险 env 键 | 换业务变量名，不要注入 `PYTHONPATH/PATH/WF_SCRIPT_*` 等 |
| `/run` 返回 200 但 `exit_code=3` + `WinError 5` | 受限环境禁 asyncio 命名管道 | 换普通 Windows 桌面/服务器；看 `counters.spawn_errors` |
| `/scan?dir_path=.` 403；GET 请求 405 | 相对路径按 CWD 解析；`/scan` 只接受 POST | 用 `-X POST -G` + 允许根内绝对路径 |
| 超时后拿不到 `exit_code=4` | 超时/取消实际返回 `-1` | 用 `status_label`/`cancelled` 判定 |
| 脚本异常被平台当"部分成功"继续跑 | Python 默认退出码 1 = 部分成功 | 脚本内显式 `sys.exit(2)` 以上 |
| 同 ID 重投又执行了一次 | 没传 `task_run_id`，或参数变了导致指纹不符 | 固定 `task_run_id` 且参数不变 |
| 改了脚本但同 ID 仍返回旧结果 | 3.2.0 的缓存指纹不含脚本内容（3.3.0 已改为含脚本 sha256，正常情况下不会再出现） | 3.3.0 起无需处理；若仍复现，确认跑的是 3.3.0（`/health` 的 version），再删 `results\<sha1(task_id)>.json` |
| 429 频繁 | 并发打满（默认 4） | 降调用并发或调大 `WF_MAX_CONCURRENCY`（按 CPU/内存） |
| 409 后任务"卡住" | 设计如此（防二次执行） | 查 `/tasks` 人工核对，必要时 `/cancel` |
| 长脚本跑到一半被取消 | 客户端断开即取消（`WF_CANCEL_ON_DISCONNECT=1`） | 反向代理/负载均衡超时 > 脚本超时；或设为 0（不建议） |
| 升级后 `/run` 一直 503 | 之前调过 `/drain`，排空未解除 | 重启服务（排空状态不持久化） |
| 开机后 9000 没起来 | 自启未注册 / S4U 失败 / 任务被停 | `ops-http.ps1 status` 看任务状态与 `LastTaskResult`；跑 `cutover-to-uvicorn-task.ps1` 重装 |
| 重启后平台全部 401 | 平台设备行 token ≠ `.env`（换过 token 但平台没同步） | 两边逐字符核对；`ops-http.ps1 ready` 用新 token 应 200 |
| 进程被杀后不自动恢复 | `.maintenance` 文件还在，或 watchdog 任务被删 | 删 `.maintenance`；`Get-ScheduledTask -TaskName 'WorkflowPlatform-ScriptServer-Watchdog'` 应为 `Ready` |
| 运行 `deploy\*.ps1` 报“未签名 / 无法加载” | PowerShell 执行策略 | 用 `powershell -NoProfile -ExecutionPolicy Bypass -File <脚本>` |
| 中文输出/日志乱码 | 控制台代码页 cp936 | `$env:PYTHONIOENCODING='utf-8'`；读文件用 `-Encoding UTF8` |
| 运行 `start.bat` 后别的程序被杀 | 它会 `taskkill` 所有含 `:9000` 的 PID | 生产不要用 `start.bat`；手工按监听 pid 精确停止 |

---

## 7. 适用边界与安全基线

### 7.1 适用 / 不适用

| 场景 | 结论 |
|---|---|
| 内网采集机/客户机，只跑自家脚本，有 token + 防火墙 | ✅ 适合 |
| 作为平台 http 任务的执行代理（一台机器一个设备） | ✅ 适合 |
| 短任务（秒~分钟级）、并发 ≤4、输出不大 | ✅ 适合 |
| 直接暴露公网 9000 | ❌ 不适合（无 TLS、单 token） |
| 多团队共用一台执行机、脚本来源不可信 | ❌ 不适合 |
| 要求"恰好一次"（转账、扣库存） | ❌ 不适合（同步协议只能做到"至少一次 + 幂等兜底"） |
| 高并发 / 多实例 HA | ❌ 不适合（单 worker、进程内台账、无队列） |

### 7.2 生产安全基线（逐条打勾）

1. `WF_SCRIPT_TOKEN` **建议** ≥32 位随机、每台不同（执行器不强制长度；平台设备登记要求 ≥8 位），并设 `WF_SCRIPT_REQUIRE_TOKEN=1`；
2. 防火墙只放行平台出口 IP；跨不可信网段加 TLS 反向代理；
3. `--workers 1`，一台机器一个实例，状态目录独占且放本地盘；
4. 用专用低权限账户运行；`.env`（含 token）ACL 收紧到运行账户与管理员；
5. 业务脚本幂等/可重入；重要失败显式 `sys.exit(2+)`；
6. 升级/重启前 `/drain`，并安排窗口；
7. 给 `logs\*.log` 加轮转/归档，防止占满磁盘（做法见 `MAINTENANCE.md`）；
8. 探活用 `/health/ready` 并检查 `status`、`state_dir.enabled`、`draining`。

### 7.3 已知限制

- 无 Web UI、无 TLS、单 token 无租户隔离；
- 同步长任务协议：一次 HTTP 等到脚本结束，重启/断电会中断在跑任务；
- 幂等缓存只缓存成功结果，指纹不含脚本内容；
- 取消能力依赖单进程内存台账，**不能**多 worker；
- `running.json` 对账能清理大部分孤儿进程，但极端情况下（kill 失败）仍可能残留。

---

## 附录 A：环境变量全表

| 变量 | 默认值 | 说明 | 建议 |
|---|---|---|---|
| `WF_SCRIPT_TOKEN` | 空 | 鉴权 token；**空 + 对外监听 = 拒绝启动** | **生产必填**，每台不同 |
| `WF_SCRIPT_REQUIRE_TOKEN` | 0 | 1 = token 为空拒绝启动 | **生产设为 1** |
| `WF_SCRIPT_ALLOW_INSECURE` | 0 | 1 = 允许"空 token + 对外监听" | 仅隔离调试，生产禁用 |
| `WF_SCRIPTS_DIR` | `<script_server.py 所在目录>\scripts\` | 脚本根目录（默认值随启动方式不同） | **本文布局下必填**，写绝对路径 |
| `WF_ALLOWED_SCRIPT_ROOTS` | 空（仅 `WF_SCRIPTS_DIR`） | 额外允许的绝对路径根（**分号**分隔） | 确需时加 |
| `WF_SCAN_ALLOWED_ROOTS` | 空（仅 `WF_SCRIPTS_DIR`） | `/scan` 允许的根（分号分隔） | 按需 |
| `WF_ALLOWED_SCRIPT_EXTENSIONS` | `.py,.pyw` | 允许执行的扩展名（逗号分隔） | 一般不改 |
| `WF_MAX_CONCURRENCY` | 4 | 同时执行脚本数上限，超出 429 | 按 CPU/内存调 |
| `WF_MAX_OUTPUT_BYTES` | 8388608（8MB） | 单流输出捕获上限，超出截断 | 大输出脚本可调大 |
| `WF_RESULT_CACHE_TTL` | 86400（24h） | 成功结果缓存保留秒数 | 热修频繁时调小 |
| `WF_RESULT_CACHE_MAX_FILES` | 2000 | 缓存文件数上限 | 按磁盘调 |
| `WF_RESULT_CACHE_MAX_BYTES` | 536870912（512MB） | 缓存总字节上限 | 按磁盘调 |
| `WF_RESULT_CACHE_OUTPUT_CHARS` | 1000000 | 缓存内单条输出字符上限（0=不裁） | 一般不改 |
| `WF_SCRIPT_STATE_DIR` | `<脚本目录父级>\.script_server_state` | 缓存 + 台账目录 | 显式指定本地盘 |
| `WF_CANCEL_ON_DISCONNECT` | 1 | 客户端断开是否取消执行 | 保持 1，但中间层超时须更长 |
| `WF_SCRIPT_ENV_DENY` | `WF_SCRIPT_TOKEN` | 额外禁止注入的变量（逗号分隔） | 默认即可 |
| `WF_BIND_HOST` | 空 | 仅用于安全判定与日志，不控制实际监听 | 一般不用 |
| `WEB_CONCURRENCY` / `UVICORN_WORKERS` | 未设 | 与 `--workers` 一起检测，>1 拒绝启动 | 不要设置 |
| `PYTHONIOENCODING` | `utf-8`（`serve.py` 注入） | 子进程 stdio 编码 | 保持 utf-8；**不要设 `PYTHONUTF8=1`** |

---

## 附录 B：命令速查

```powershell
# —— 服务（计划任务托管）——
powershell -NoProfile -ExecutionPolicy Bypass -File C:\ScriptServer\script-server\deploy\ops-http.ps1 status
powershell -NoProfile -ExecutionPolicy Bypass -File C:\ScriptServer\script-server\deploy\ops-http.ps1 restart
powershell -NoProfile -ExecutionPolicy Bypass -File C:\ScriptServer\script-server\deploy\ops-http.ps1 logs
schtasks /End /TN "WorkflowPlatform-ScriptServer"    # 停止
schtasks /Run /TN "WorkflowPlatform-ScriptServer"    # 启动

# —— 探活 ——
curl.exe -s http://127.0.0.1:9000/health
curl.exe -s -H "X-Script-Token: $t" http://127.0.0.1:9000/health/ready
curl.exe -s -H "X-Script-Token: $t" http://127.0.0.1:9000/tasks

# —— 执行 / 取消 / 扫描 / 排空 ——
curl.exe -s -X POST http://127.0.0.1:9000/run -H "X-Script-Token: $t" `
  -H "Content-Type: application/json" --data-binary "@run.json"
curl.exe -s -X POST -H "X-Script-Token: $t" http://127.0.0.1:9000/cancel/<task_id>
curl.exe -s -X POST -G --data-urlencode "dir_path=C:\ScriptServer\scripts" `
  -H "X-Script-Token: $t" http://127.0.0.1:9000/scan
curl.exe -s -X POST -H "X-Script-Token: $t" "http://127.0.0.1:9000/drain?timeout=600"

# —— 端口与进程 ——
netstat -ano | findstr :9000
Get-NetTCPConnection -LocalPort 9000 -State Listen | Select-Object OwningProcess
taskkill /F /T /PID <pid>

# —— 开机自启 / 看护 ——
powershell -NoProfile -ExecutionPolicy Bypass -File C:\ScriptServer\script-server\deploy\cutover-to-uvicorn-task.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File C:\ScriptServer\script-server\deploy\install-watchdog.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File C:\ScriptServer\script-server\deploy\install-watchdog.ps1 -Uninstall
Get-ScheduledTask -TaskName 'WorkflowPlatform-ScriptServer' | Format-List TaskName,State
Get-ScheduledTask -TaskName 'WorkflowPlatform-ScriptServer-Watchdog' | Format-List TaskName,State
```

---

## 附录 C：文件清单与布局对照

### C.1 必须拷贝 / 可选 / 不要拷

| 文件 | 必要性 | 说明 |
|---|---|---|
| `script_server.py` | **必须** | 服务全部代码 |
| `requirements.txt` | **必须** | fastapi + uvicorn |
| `.env.example` | 参考 | 复制为 `.env` |
| `start-script-server.bat` | 推荐 | 计划任务启停 + 等 health |
| `env-change-restart.bat` | 推荐 | 改完 `.env` 一键重启并校验 |
| `deploy\serve.py` | **必须** | 启动器（计划任务的动作） |
| `deploy\cutover-to-uvicorn-task.ps1` | **必须** | 注册主任务 + watchdog（管理员） |
| `deploy\install-watchdog.ps1`、`deploy\watchdog.ps1` | **必须** | 进程看护 |
| `deploy\ops-http.ps1`、`deploy\verify-10.ps1` | 推荐 | 日常运维 / 验收 |
| `INSTALL.md`、`MAINTENANCE.md` | 参考 | 本文与运维手册 |
| `start.bat`、`run_server.py` | 仅开发排障 | `start.bat` 会无差别杀 9000 端口进程 |
| `tests\`、`tools\`、`notes\`、`verify\`、`__pycache__\`、`.deploy-backup-*\` | **不要拷** | 开发/验证产物 |
| `jinteng\` | **不要拷** | 与本系统无关的独立凭据工具（约 18MB，本机在 `Documents\jinteng`） |

### C.2 两种布局的路径对照（避免"改了不生效"）

| 项 | 独立执行器（本文，新设备） | 仓库内布局（开发机） |
|---|---|---|
| 代码目录 | `C:\ScriptServer\script-server\` | `<仓库>\script-server\` |
| `.env` | `C:\ScriptServer\script-server\.env` | 仓库根 `.env`（`serve.py` 仍只读 `script-server\.env`） |
| 业务脚本 | `C:\ScriptServer\scripts\` | `<仓库>\scripts\` |
| 状态目录 | `C:\ScriptServer\.script_server_state\` | `<仓库>\.script_server_state\` |
| 服务日志 | `C:\ScriptServer\script-server\logs\` | `<仓库>\logs\` |
| 看护/切换日志 | `C:\ScriptServer\script-server\logs\watchdog.log`、`C:\ScriptServer\logs\cutover.log` | `<仓库>\logs\` |

---

## 附录 D：3.2.0 相较旧版的行为变化（升级老部署时逐条检查）

1. **空 token + 非回环监听 → 拒绝启动**（仅 `WF_SCRIPT_ALLOW_INSECURE=1` 可放行）；
2. **`--workers > 1` → 拒绝启动**；
3. **请求 `env` 注入限制**：`WF_SCRIPT_*`、`PYTHONPATH`、`PATH`、`COMSPEC` 等危险键 → HTTP 400（业务变量不受影响）；
4. **新增 `POST /drain`**：排空后 `/run` → 503 + `Retry-After: 10`；
5. **`/health/ready` 纳入状态目录与排空**：不可用时返回 `status=degraded`（HTTP 仍 200）；
6. **取消异常路径先杀进程**，不再制造永久孤儿；
7. `start.bat` 行为未变（仍是无差别 `taskkill`），生产请勿使用。

老部署升级前检查：是否配了 token（空 token 现在起不来）、是否有 `WEB_CONCURRENCY` 环境变量、
`.env` 里是否有会被拒绝的业务变量名、监控是否读 `status` 而不是只看 HTTP 200。

---

## 附录 E：3.3.0（2026-10-28）变更——幂等/重试/并发/审计

升级方式：替换 `script_server.py` + `deploy\serve.py`，然后重启主任务（`ops-http.ps1 restart`，或提权跑 `logs\apply-hardening.ps1` 那种脚本）。**平台侧无需改动即可兼容**（只新增字段/头，不改既有语义）。

| # | 变更 | 影响 |
|---|---|---|
| 1 | 幂等指纹加入**脚本内容 sha256** | 脚本改了以后同一 `task_run_id` 会重跑，不再返回旧结果；只有 exit 0 才缓存（不变） |
| 2 | 响应新增 `code`（200/500/502）与 `timed_out` | 平台可不再靠 `status_label` 文案判断超时；老字段全部保留 |
| 3 | 新增请求参数 `block_strategy`（`reject` 默认 / `queue`） | `reject` 行为与 3.2.0 一致，只是 429 现在**带 `Retry-After: 5`**；`queue` 让执行器排队等空槽（上限 `WF_QUEUE_MAX_WAIT_SECONDS`，默认 30s） |
| 4 | 409 也带 `Retry-After: 5` | "上次响应丢了"的场景，平台等一会儿再查，而不是判失败 |
| 5 | 新增 `logs\script-server-audit.log`（按天轮转、保留 14 天） | 一次运行一行 JSON：`run.start` / `run.dedup` / `run.finish` / `run.reject` / `run.cancel`，含 run_id、客户端 IP、脚本名、脚本与参数摘要、超时、耗时、code/exit_code |
| 6 | 运行日志不再落**明文 argv** | `out.log` 只记脚本名 + `args_len` + `args_sha`（参数内容从旧记录里查；敏感参数不再泄漏） |
| 7 | 未知请求字段不再静默忽略 | 仍接受（向前兼容），但会打 WARNING 列出字段名，便于发现平台字段名写错 |
| 8 | 新增在线验收 `deploy\verify-hardening.py` | 11 项：版本/首跑/幂等/内容指纹/失败码/超时码/并发 reject/并发 queue/审计内容/不透明文 |
| 9 | `/health` 计数器新增 `queued` | 能看到排队次数 |

验收（本机 2026-10-28 实测）：`verify-10.ps1` **10/10 PASS**、`verify-hardening.py` **11/11 PASS**、`verify-logging.py` **17/17 PASS**。

---

本文依据 `script_server.py` 3.3.0、`deploy\serve.py`、`.env.example`、`deploy\*` 脚本与 192.168.1.210 的实测记录编写。
托管方式、开机自启、watchdog 恢复与平台端到端链路在该机已实测；新设备仍请按第 3 章验收。
