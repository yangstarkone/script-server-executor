# -*- coding: utf-8 -*-
"""日志分级/轮转的离线验收（不依赖运行中的服务，不需要 token）。

检查：
  1) 三份日志分开：access 只收 uvicorn.access、out 收 INFO+、error 只收 ERROR+；
  2) 中文按 UTF-8 写入且可读回；
  3) 生产默认是「按天轮转、保留 14 份」；
  4) 轮转与保留策略真的生效（用 when='S' 压缩时间验证）。

用法：python deploy\\verify-logging.py     退出码 0 = 全部通过
"""
from __future__ import annotations

import logging
import os
import shutil
import sys
import time
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import serve  # noqa: E402

# 测试目录放在 logs/ 下（DSH 沙箱只保证工作区内可写，系统 temp 目录会被拒绝）
TEST_ROOT = Path(__file__).resolve().parent.parent / "logs"
FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("[PASS] " if ok else "[FAIL] ") + name + ((" " + detail) if detail else ""), flush=True)
    if not ok:
        FAILED.append(name)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _close_all() -> None:
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
        h.close()
    acc = logging.getLogger("uvicorn.access")
    for h in list(acc.handlers):
        acc.removeHandler(h)
        h.close()


def test_split(tmp: Path) -> None:
    _close_all()
    ok = serve.setup_logging(tmp)
    check("setup_logging 返回 True", ok)
    check("access/out/error 三个文件立即创建",
          all((tmp / n).exists() for n in (serve.ACCESS_LOG, serve.SERVICE_LOG, serve.ERROR_LOG)))

    root = logging.getLogger()
    rot = [h for h in root.handlers if isinstance(h, TimedRotatingFileHandler)]
    check("root 上有 out+error 两个轮转 handler", len(rot) == 2, f"count={len(rot)}")
    check("生产默认按天轮转、保留 14 份",
          all(h.when == "MIDNIGHT" and h.backupCount == 14 for h in rot),
          ",".join(f"{Path(h.baseFilename).name}:{h.when}/{h.backupCount}" for h in rot))

    acc = logging.getLogger("uvicorn.access")
    check("访问日志 handler 挂在 uvicorn.access 且不向 root 传播",
          len(acc.handlers) == 1 and acc.propagate is False)

    logging.getLogger("uvicorn.access").info('127.0.0.1:1234 - "POST /run HTTP/1.1" 200')
    logging.getLogger("script_server").info("任务 abc 完成，退出码: 0")
    logging.getLogger("script_server").error("boom：连接数据库失败")
    for h in root.handlers + acc.handlers:
        h.flush()

    access = _read(tmp / serve.ACCESS_LOG)
    service = _read(tmp / serve.SERVICE_LOG)
    error = _read(tmp / serve.ERROR_LOG)
    check("access.log 只有请求行", "POST /run" in access and "任务 abc" not in access, repr(access[:90]))
    check("out.log 有 INFO 和 ERROR", "任务 abc" in service and "boom" in service)
    check("out.log 不含访问日志", "POST /run" not in service)
    check("error.log 只有 ERROR", "boom" in error and "任务 abc" not in error)
    check("中文 UTF-8 可读回", "任务 abc 完成，退出码: 0" in service)


def test_rotation(tmp: Path) -> None:
    _close_all()
    # when='S' + 保留 2 份：快速验证「轮转确实发生、超出份数会被删」
    ok = serve.setup_logging(tmp, when="S", backup_count=2)
    check("rotation 场景 setup_logging 返回 True", ok)
    for i in range(6):
        logging.getLogger("script_server").info("tick-%d", i)
        for h in logging.getLogger().handlers:
            h.flush()
        time.sleep(1.15)
    cur = tmp / serve.SERVICE_LOG
    rotated = sorted(tmp.glob(serve.SERVICE_LOG + ".*"))
    check("发生了轮转（产生 .后缀 历史文件）", len(rotated) >= 1, f"rotated={len(rotated)}")
    check("保留份数生效（<= backup_count=2）", len(rotated) <= 2, f"rotated={len(rotated)}")
    check("当前文件仍在写", cur.exists() and "tick-5" in _read(cur))
    if rotated:
        oldest = _read(rotated[0])
        check("最旧历史内容已被淘汰", "tick-0" not in oldest, repr(oldest[:80]))


def main() -> int:
    for name, fn in (("split", test_split), ("rot", test_rotation)):
        tmp = TEST_ROOT / f"_verify-logging-{name}-{os.getpid()}"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True, exist_ok=True)
        try:
            fn(tmp)
        finally:
            _close_all()                     # 先关句柄，Windows 上否则删不掉
            shutil.rmtree(tmp, ignore_errors=True)
    print("=" * 60)
    print("结果：全部通过" if not FAILED else "结果：失败 " + ", ".join(FAILED))
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
