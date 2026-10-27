# -*- coding: utf-8 -*-
"""script-server 独立启动器（由 Windows 计划任务托管）。

用途：进程即 uvicorn 服务本体（单进程、单 worker），
不产生额外的守护进程/命名管道，因此不存在跨 Session 连接问题。

做的事：
  1) 读取同目录上一级的 .env（已存在的环境变量优先，便于临时覆盖）；
  2) 设定 WF_BIND_HOST / PYTHONIOENCODING（不设 PYTHONUTF8，避免改变业务脚本 open() 编码）；
  3) 把 stdout/stderr 重定向到 logs\\script-server-out.log / -error.log（UTF-8、行缓冲）；
  4) 在当前进程内启动 uvicorn（workers=1，不 fork）。

用法：
  python serve.py                     # 默认 0.0.0.0:9000
  python serve.py --port 9001         # 临时端口（联调用）
  python serve.py --host 127.0.0.1

退出码：绑定失败/导入失败 = 1（计划任务会按“失败每分钟重启”再拉）。
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # ...\script-server
LOG_DIR = ROOT / "logs"


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

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    out = open(LOG_DIR / "script-server-out.log", "a", encoding="utf-8", errors="replace", buffering=1)
    err = open(LOG_DIR / "script-server-error.log", "a", encoding="utf-8", errors="replace", buffering=1)
    sys.stdout = out
    sys.stderr = err

    sys.path.insert(0, str(ROOT))
    try:
        import uvicorn
        from script_server import VERSION, app
    except Exception:                                   # 依赖缺失/语法错误时给出可读原因
        import traceback
        traceback.print_exc()
        print(f"[serve] 启动失败：{time.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
        return 1

    print(
        f"[serve] start pid={os.getpid()} version={VERSION} host={host} port={port} "
        f"scripts={os.environ.get('WF_SCRIPTS_DIR', '')} "
        f"state={os.environ.get('WF_SCRIPT_STATE_DIR', '')} "
        f"at={time.strftime('%Y-%m-%d %H:%M:%S')}",
        flush=True,
    )
    try:
        # workers 不传 = 进程内单 worker；log_config=None 沿用 script_server 的 logging 配置
        uvicorn.run(app, host=host, port=port, log_config=None, access_log=True)
    except Exception:
        import traceback
        traceback.print_exc()
        return 1
    print(f"[serve] exit at={time.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
