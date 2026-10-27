# 在新设备上部署执行器（script-server）

> 适用：**只装执行器**的 Windows 设备（平台 backend/Redis 在别的机器）。
> 目标：`http://<新设备IP>:9000` 提供鉴权后的脚本执行能力，平台「设备管理」里能探活并派发任务。
> 版本：script-server 3.2.0（2026-10-26 起；3.1.0 的 P1 修复版）。
> 本文命令均以 `C:\ScriptServer\script-server` 为例，按实际路径替换。

## 0. 需要装什么（一句话）

| 组件 | 是否必须 | 用途 |
|---|---|---|
| **Python 3.10+**（3.12 实测） | 必须 | 运行执行器与业务脚本 |
| **Node.js 18+ + PM2** | **必须**（生产） | 进程守护/开机自启/日志；没有 PM2 时双击 bat 会闪退 |
| git / 整个平台仓库 | 不需要 | 新设备只需 `script-server\` 目录 + `scripts\` 目录 |
| Redis / 数据库 | 不需要 | 执行器不连 Redis，也不碰平台数据库 |

> PM2 安装（管理员或普通用户均可，装到用户全局）：
> ```powershell
> node -v                 # 没装 Node 先装 LTS
> npm install -g pm2
> pm2 -v                  # 能输出版本即可
> ```

## 1. 拷贝目录

在新设备上建目录，例如 `C:\ScriptServer\`，最终结构：

```text
C:\ScriptServer\
├─ script-server\            ← 从仓库复制整个目录（脚本 + 依赖 + 启动脚本 + 部署件）
│  ├─ script_server.py
│  ├─ requirements.txt
│  ├─ ecosystem.config.js    ← 独立执行器 PM2 配置（会自动读同目录 .env）
│  ├─ .env.example
│  ├─ start-script-server.bat
│  └─ deploy\
│     ├─ install-autostart.ps1
│     └─ INSTALL-EXECUTOR.md  ← 本文件
└─ scripts\                  ← 业务脚本目录（也可改 WF_SCRIPTS_DIR 指向别处）
   ├─ hello.py
   └─ ...
```

> 不要只拷 `script_server.py`：`requirements.txt`、`ecosystem.config.js`、`deploy\` 都要带上。

## 2. 安装 Python 依赖

```powershell
cd C:\ScriptServer\script-server
python -m pip install -r requirements.txt
# 内网/离线：先在有网机器 pip download -r requirements.txt -d wheels，再 pip install --no-index --find-links wheels -r requirements.txt
```

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

# 3) 脚本目录（不填默认 = C:\ScriptServer\scripts）
# WF_SCRIPTS_DIR=C:\ScriptServer\scripts

# 4) 并发上限（默认 4；执行机核少/脚本重时调低）
# WF_MAX_CONCURRENCY=4
```

> `.env` 含凭据：不要提交代码库/共享目录，Windows 上建议把目录 ACL 收紧到运行账户。
>
> **3.2.0 新行为（2026-10-26）**：① token 为空且监听非回环地址会**拒绝启动**（仅调试可 `WF_SCRIPT_ALLOW_INSECURE=1`）；
> ② 请求 `env` 不允许覆盖 `PYTHONPATH`/`PATH`/`WF_SCRIPT_*` 等（会 400）；③ `--workers` 必须为 1，多 worker 拒绝启动；
> ④ 升级前建议 `POST /drain?timeout=600` 排空在跑任务。详见 `FIX-P1-20261026.md`。

## 4. 启动（二选一）

### 4.1 双击 bat（推荐，带健康检查与错误提示）

双击 `script-server\start-script-server.bat`。成功会打印 `[4/4] Done` 和 `pm2 list`；
失败会**停住窗口**显示原因（`-nopause` 供脚本/自启调用）。

> **bat 闪退的常见原因**（本脚本已尽量拦住并 pause）：
> 1. 没装 PM2 / Node，或 `pm2` 不在 PATH → `npm install -g pm2`；
> 2. 在错误的目录双击（bat 必须在 `script-server\` 里）→ 找不到 `ecosystem.config.js`；
> 3. 端口 9000 被占（上次残留进程）→ `netstat -ano | findstr :9000` 后 `taskkill /F /T /PID <pid>`；
> 4. 缺 Python 包 / Python 不在 PATH → 按 §2 安装、勾选 Add to PATH；
> 5. 双击时窗口一闪而过（已修复）：可先用 cmd 打开再运行 bat 看报错。

### 4.2 命令行（等价，便于排错）

```powershell
cd C:\ScriptServer\script-server
pm2 delete script-server 2>$null
pm2 start ecosystem.config.js --only script-server
pm2 save
pm2 logs script-server --lines 50
```

## 5. 验证

```powershell
# 5.1 健康（不需要 token）：应看到 version=3.1.0
curl.exe http://127.0.0.1:9000/health

# 5.2 就绪（需要 token）：应返回 status=ok + checks
curl.exe -H "X-Script-Token: <WF_SCRIPT_TOKEN>" http://127.0.0.1:9000/health/ready

# 5.3 真跑一个脚本
curl.exe -X POST http://127.0.0.1:9000/run `
  -H "X-Script-Token: <WF_SCRIPT_TOKEN>" -H "Content-Type: application/json" `
  -d '{\"file_path\":\"hello.py\",\"timeout_seconds\":30}'

# 5.4 从平台侧：设备管理 → 新增设备
#     name=任意（如 exec-02）, base_url=http://<新设备IP>:9000, token=<同一个 WF_SCRIPT_TOKEN>
#     保存后点「检测」：应显示在线（走 /health/ready），延迟正常
```

> 若平台在另一台机器，注意 Windows 防火墙放行入站 TCP 9000（只放行平台出口 IP 更安全）：
> ```powershell
> New-NetFirewallRule -DisplayName "script-server 9000" -Direction Inbound -Protocol TCP `
>   -LocalPort 9000 -RemoteAddress <平台IP/网段> -Action Allow
> ```

## 6. 开机自启（生产必须，需管理员）

```powershell
# 管理员 PowerShell
powershell -NoProfile -ExecutionPolicy Bypass -File C:\ScriptServer\script-server\deploy\install-autostart.ps1
# 卸载
powershell -NoProfile -ExecutionPolicy Bypass -File C:\ScriptServer\script-server\deploy\install-autostart.ps1 -Uninstall
```

它注册计划任务 `WorkflowPlatform-ScriptServer`：开机先 `pm2 resurrect`，60s 内 `/health` 不为 200 则回退
`start-script-server.bat -nopause`；失败每分钟重试。**装完务必真实重启一次机器验收**：

```powershell
# 重启后 3 分钟内
curl.exe http://127.0.0.1:9000/health
pm2 list
```

## 7. 路径边界（重要，默认 fail-closed）

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

> 注意：白名单是**目录**级。脚本内部能做什么（读写文件、调 API）由脚本自身权限决定，执行器只负责"只执行允许目录里的脚本"。

## 8. 常见问题

| 现象 | 处理 |
|---|---|
| bat 闪退 | 见 §4.1；用 cmd 运行 bat 看报错 |
| `/health` 返回旧字段/404 `/health/ready` | 跑的是旧代码：确认 `script_server.py` 是 3.1.0，`pm2 restart script-server` |
| 平台「检测」401/离线 | 设备里登记的 token 与 `.env` 的 `WF_SCRIPT_TOKEN` 不一致 |
| `POST /run` 403 | 路径不在允许目录：用相对路径或配 `WF_ALLOWED_SCRIPT_ROOTS` |
| `WinError 10048`（端口占用） | 残留进程：`netstat -ano \| findstr :9000` → `taskkill /F /T /PID <pid>` → `pm2 restart script-server` |
| 429 执行机繁忙 | 并发满：调大 `WF_MAX_CONCURRENCY`（注意 CPU/内存）或降低平台并发 |
| 日志中文乱码 | 确认 `.env`/ecosystem 有 `PYTHONIOENCODING=utf-8`（独立 ecosystem 已内置） |
| 状态目录不可写 | 默认 `<脚本目录上级>\.script_server_state`；可用 `WF_SCRIPT_STATE_DIR` 指到可写目录 |
| 想彻底停掉 | `pm2 stop script-server`（PM2 保留配置）；`pm2 delete script-server`（彻底移除） |

## 9. 升级已部署的执行器

```powershell
# 1) 备份
copy script-server\script_server.py script-server\script_server.py.bak
# 2) 覆盖新代码（script_server.py / requirements.txt / ecosystem / bat / deploy）
# 3) 依赖有变化时
python -m pip install -r requirements.txt
# 4) 重启 + 保存
pm2 restart script-server
pm2 save
# 5) 验证
curl.exe http://127.0.0.1:9000/health
```

> 升级会重启执行器：**在跑的任务会被中断**（启动对账会清理孤儿进程；平台侧会收到"取消/失败"）。
> 建议在无任务窗口升级，或先 `pm2 stop script-server` 等任务跑完。
