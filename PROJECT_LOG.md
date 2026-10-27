# PROJECT_LOG

> 本文件是项目的跨会话记忆，供人和 AI agent 共用。
> **读法**：只读「当前状态」+「待确认问题」+ 最近 3 条日志，不要通读全文。
> **详细安装/运维记录**：`script-server\INSTALL-RECORD.md`（按设备分节，第 10 节 = 本机）。

## 当前状态

- 目标：在 `C:\ScriptServer` 运行 standalone script-server 执行器（FastAPI/uvicorn，VERSION 3.2.0），供平台「设备管理」派发脚本。
- 本机：`YANGSTARONE` / `192.168.1.220` / 账户 `Administrator`；包从旧机 `WUYUAN`（192.168.1.222）整体拷贝而来。
- 运行方式：**不用 PM2**；Windows 计划任务 `WorkflowPlatform-ScriptServer`（AtStartup / S4U / Highest）直接托管 `python deploy\serve.py`（uvicorn 单 worker，`0.0.0.0:9000`）。
- 能跑通：`deploy\verify-10.ps1` **10/10 PASS**（计划任务托管实例，2026-10-26 实测两次；Stop/Start 冷启动等价测试通过）。
- 不能跑通：无。
- 未验证：**真实重启后的开机自启**（未重启本机）；平台侧「设备管理」登记与「检测」在线（需在平台操作）。
- 已知问题：当前网络位置=公用配置文件且**防火墙关闭**，未加 9000 放行规则；`.env` 里的 `WF_ALLOWED_SCRIPT_ROOTS=C:\workflow-scripts-center` 在本机不存在该目录（仅白名单，暂无害）。
- 下一步：① 真实重启一次并按 `INSTALL-RECORD.md` §6 清单核对；② 平台登记 `base_url=http://192.168.1.220:9000` + `.env` 的 token；③ 需要的话启用防火墙并只放行平台 IP/网段。
- 最后更新：2026-10-26

## 待确认问题

- 是否保留与旧机相同的 `WF_SCRIPT_TOKEN`？（沿用拷贝值即可让平台用同一 token 登记本机，若要求每台一 token 需同步改平台）
- 本机是否需要 `C:\workflow-scripts-center`（旧机白名单目录）？需要的话要建目录并同步脚本，否则平台派发的绝对路径脚本会 404/403。

---

## 日志（新→旧）

### 2026-10-26 在新机 YANGSTARONE 装好 script-server 执行器（无 PM2）

- 目标：把旧机拷来的 `C:\ScriptServer` 在本机装成可开机自启的执行器。
- 结论：走 `INSTALL-RECORD.md` §8.3 的"计划任务直接托管 uvicorn"模型；PM2 不参与。拷贝包缺的 `serve.py` / 注册脚本 / 验收脚本按 §8.3 描述重建（详见 `script-server\INSTALL-RECORD.md` §10）。
- 改：新增 `script-server\deploy\serve.py`、`deploy\register-autostart-task.ps1`、`deploy\install-autostart-uvicorn.bat`、`deploy\verify-10.ps1`；`script-server\INSTALL-RECORD.md` 追加第 10 节；登记计划任务 `WorkflowPlatform-ScriptServer`。
- 坑：① 拷贝包里只有 PM2 时代 deploy 文件（无 serve.py），照 §9 装不起来 → 只能重建；② 在 DSH 受限 shell 里启动服务后 `/run` 必 500 `WinError 5`（asyncio 建子进程命名管道被拒），换计划任务托管即正常；③ `/scan` 的 `dir_path` 是 query 参数，传 JSON body 返回空列表；④ 拷来的 `.script_server_state\results` 会让同 `task_run_id` 在 24h 内命中旧机缓存（假成功）→ 已移走备份。
- 遗留：真实重启自启未验证；防火墙规则与平台登记未做。
