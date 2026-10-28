# -*- coding: utf-8 -*-
"""script-server 独立启动器（由 Windows 计划任务托管）。

用途：进程即 uvicorn 服务本体（单进程、单 worker），
不产生额外的守护进程/命名管道，因此不存在跨 Session 连接问题。

做的事：
  1) 读取同目录上一级的 .env（已存在的环境变量优先，便于临时覆盖）；
  2) 设定 WF_BIND_HOST / PYTHONIOENCODING（不设 PYTHONUTF8，避免改变业务脚本 open() 编码）；
  3) 日志拆成三份并**按天轮转、保留 14 天**（不依赖任何外部轮转工具）：
       logs\\script-server-access.log  访问日志（一次 HTTP 请求一行）
       logs\\script-server-out.log     服务/业务日志（INFO 及以上）
       logs\\script-server-error.log   仅 ERROR 及以上（真正需要盯的那份）
     轮转文件名为 <文件名>.YYYY-MM-DD，超过 14 个自动删最旧；
  4) 在当前进程内启动 uvicorn（workers=1，不 fork）。

用法：
  python serve.py                     # 默认 0.0.0.0:9000
  python serve.py --port 9001         # 临时端口（联调用）
  python serve.py --host 127.0.0.1

退出码：绑定失败/导入失败 = 1（计划任务会按“失败每分钟重启”再拉）。
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # ...\script-server
LOG_DIR = ROOT / "logs"
LOG_BACKUP_DAYS = 14                                   # 保留的历史日志份数（一天一份）
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

ACCESS_LOG = "script-server-access.log"
SERVICE_LOG = "script-server-out.log"
ERROR_LOG = "script-server-error.log"


def load_env(path: Path) -> None:
    """把 .env 里的键值放进 os.environ（已存在的键不覆盖）。值与 ecosystem 的解析规则一致：
    去掉首尾空白，成对引号会被剥掉；不写行尾注释处理。"""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


def _rotating_handler(path: Path, level: int, when: str, backup_count: int) -> TimedRotatingFileHandler:
    """按天轮转的文件 handler：到点把当前文件改名为 <name>.YYYY-MM-DD，保留 backup_count 份。"""
    handler = TimedRotatingFileHandler(
        path,
        when=when,
        interval=1,
        backupCount=backup_count,
        encoding="utf-8",
    )
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    return handler


def setup_logging(
    log_dir: Path = LOG_DIR,
    *,
    when: str = "midnight",
    backup_count: int = LOG_BACKUP_DAYS,
) -> bool:
    """配置访问/业务/错误三份日志。

    访问日志单独挂到 uvicorn.access 并关闭向 root 传播（否则每条请求会在 out.log 里重复一份）；
    业务与错误两份都挂 root：ERROR 会同时出现在 out.log（完整时间线）和 error.log（只读错误）。
    日志目录不可写时降级为 stderr，函数返回 False，**不阻止服务启动**。
    """
    root = logging.getLogger()
    for old in list(root.handlers):
        root.removeHandler(old)
        old.close()

    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        access = _rotating_handler(log_dir / ACCESS_LOG, logging.INFO, when, backup_count)
        service = _rotating_handler(log_dir / SERVICE_LOG, logging.INFO, when, backup_count)
        error = _rotating_handler(log_dir / ERROR_LOG, logging.ERROR, when, backup_count)
    except OSError as exc:
        logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
        logging.getLogger("serve").warning(
            "日志文件不可用（%s），已降级为 stderr：%s", log_dir, exc
        )
        return False

    root.setLevel(logging.INFO)
    root.addHandler(service)
    root.addHandler(error)

    access_logger = logging.getLogger("uvicorn.access")
    for old in list(access_logger.handlers):
        access_logger.removeHandler(old)
        old.close()
    access_logger.addHandler(access)
    access_logger.propagate = False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="script-server standalone launcher")
    parser.add_argument("--host", default=None, help="监听地址，默认取 WF_BIND_HOST 或 0.0.0.0")
    parser.add_argument("--port", type=int, default=None, help="监听端口，默认取 WF_HTTP_PORT 或 9000")
    args = parser.parse_args()

    load_env(ROOT / ".env")

    host = args.host or (os.environ.get("WF_BIND_HOST") or "").strip() or "0.0.0.0"
    port = args.port or int((os.environ.get("WF_HTTP_PORT") or "9000").strip() or 9000)
    os.environ["WF_BIND_HOST"] = host
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUNBUFFERED", "1")

    logging_ok = setup_logging(LOG_DIR)
    log = logging.getLogger("serve")

    sys.path.insert(0, str(ROOT))
    try:
        import uvicorn
        from script_server import VERSION, app
    except Exception:                                   # 依赖缺失/语法错误时给出可读原因
        log.exception("[serve] 启动失败：导入依赖/代码出错")
        return 1

    log.info(
        "[serve] start pid=%s version=%s host=%s port=%s scripts=%s state=%s at=%s",
        os.getpid(), VERSION, host, port,
        os.environ.get("WF_SCRIPTS_DIR", ""),
        os.environ.get("WF_SCRIPT_STATE_DIR", ""),
        time.strftime("%Y-%m-%d %H:%M:%S"),
    )
    if not logging_ok:
        log.warning("[serve] 文件日志未启用（见上一条 WARNING），本次仅输出到 stderr")

    try:
        # workers 不传 = 进程内单 worker；log_config=None 沿用本文件的 logging 配置
        uvicorn.run(app, host=host, port=port, log_config=None, access_log=True)
    except Exception:
        log.exception("[serve] uvicorn 运行异常退出")
        return 1
    log.info("[serve] exit at=%s", time.strftime("%Y-%m-%d %H:%M:%S"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
