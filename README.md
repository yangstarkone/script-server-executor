# ScriptServer

Windows 上的独立脚本执行器（script-server，FastAPI + uvicorn，VERSION 3.3.2），
供工作流平台「设备管理」派发脚本；由 Windows 计划任务直接托管 `python deploy\serve.py`（单 worker，`0.0.0.0:9000`）。

## 文档

- 安装与验收：[script-server/INSTALL.md](script-server/INSTALL.md)
- 新设备部署（简版）：[script-server/deploy/INSTALL-EXECUTOR.md](script-server/deploy/INSTALL-EXECUTOR.md)
- 安装/验收记录（按设备）：[script-server/INSTALL-RECORD.md](script-server/INSTALL-RECORD.md)
- 运维手册：[script-server/MAINTENANCE.md](script-server/MAINTENANCE.md)
- 部署脚本：[script-server/deploy/](script-server/deploy/)
- 测评报告（2026-10-27）：[script-server/EXECUTOR-EVAL-20261027.md](script-server/EXECUTOR-EVAL-20261027.md)
- 跨会话项目记忆：[PROJECT_LOG.md](PROJECT_LOG.md)

## 敏感信息

`.env` 保存本机执行 token，已加入 `.gitignore`，不入库。新设备从 `.env.example` 复制后自行生成 token：

```powershell
python -c "import secrets;print(secrets.token_hex(24))"
```

`.script_server_state/`、`logs/`、`__pycache__/` 等运行时产物同样不入库。

文档里的地址/主机名/目录是**占位值**，不对应任何真实环境：

| 占位值 | 含义 |
|---|---|
| `192.0.2.10` | 执行机（RFC 5737 文档地址段，平台与执行器同机时二者相同） |
| `192.0.2.20` | 平台 / 调用方 |
| `192.0.2.30` | 历史执行机（已停用） |
| `EXECUTOR-HOST` / `OLD-EXECUTOR-HOST` | 执行机主机名 |
| `C:\Users\<account>` | 运行账户的用户目录 |
| `D:\workflow-scripts` | `WF_ALLOWED_SCRIPT_ROOTS` 示例目录 |

真实环境同步文档后，先跑一次脱敏脚本再提交：

```powershell
python tools/sanitize.py --check   # 只检查（CI 也会跑；有残留返回 1）
python tools/sanitize.py --write   # 就地替换
```

精确映射放在 `tools/sanitize.local.json`（已在 `.gitignore` 中，**不要提交**），格式：

```json
[["<内网 IPv4 的正则>", "192.0.2.10"], ["<主机名>", "EXECUTOR-HOST"]]
```

没有这个文件时脚本只做通用规则（私有 IPv4、`C:\Users\<账户>`、token 长串、非 GitHub 邮箱）。
