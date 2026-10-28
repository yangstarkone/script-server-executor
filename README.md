# ScriptServer

Windows 上的独立脚本执行器（script-server，FastAPI + uvicorn，VERSION 3.2.0），
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
