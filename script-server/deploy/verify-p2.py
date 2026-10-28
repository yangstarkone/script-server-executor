# -*- coding: utf-8 -*-
"""3.3.2 P2 修复验收（在线；对任何已部署实例都可跑）。

覆盖测评报告 P2 清单里“能在黑盒层面验证”的六项：
  1) args 空引号不再丢参数（`a "" b` -> 3 个参数，中间是空串）
  2) 近义字段（timeout / filepath / argv …）-> 400 并提示正确字段名；其它未知字段告警+计数
  3) exit 1 -> code=206 + partial=true + error_kind=partial；exit 2 -> code=500 + script_error
  4) /scan 递归 + 相对路径（默认不泄露主机目录），include_absolute=true 可回退
  5) /health/ready 写探测按 TTL 缓存，不再每次轮询写盘
  6) 排队严格 FIFO（/tasks 暴露 position，队头先拿槽）+ queue_success 计数

用法：
  python deploy\\verify-p2.py                                  # 默认 http://127.0.0.1:9000
  python deploy\\verify-p2.py --base http://127.0.0.1:9001 --scripts-dir D:\\tmp\\scripts
退出码 0 = 全部通过。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import quote as url_quote
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AUDIT = ROOT / "logs" / "script-server-audit.log"
STAMP = time.strftime("%H%M%S")
FAILED: list[str] = []
RESULTS: list[dict] = []

BASE = "http://127.0.0.1:9000"
TOKEN = ""
SCRIPTS_DIR = ROOT.parent / "scripts"
PROBE_DIR_NAME = f".verify-p2-{os.getpid()}"
SCAN_DIR_NAME = f"verify-p2-scan-{os.getpid()}"


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append({"name": name, "pass": bool(ok), "detail": detail})
    print(("[PASS] " if ok else "[FAIL] ") + name + (("  -> " + detail) if detail else ""), flush=True)
    if not ok:
        FAILED.append(name)


def _read_env() -> dict:
    values: dict[str, str] = {}
    env_file = ROOT / ".env"
    if not env_file.exists():
        return values
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _expected_version() -> str:
    try:
        text = (ROOT / "script_server.py").read_text(encoding="utf-8")
        m = re.search(r'^VERSION\s*=\s*"([^"]+)"', text, re.M)
        return m.group(1) if m else ""
    except OSError:
        return ""


def api(method: str, path: str, body: dict | None = None, token: str | None = None,
        timeout: float = 120):
    if token is None:
        token = TOKEN
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        headers["X-Script-Token"] = token
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            try:
                return resp.status, dict(resp.headers), json.loads(raw)
            except ValueError:
                return resp.status, dict(resp.headers), {"raw": raw}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, dict(exc.headers), json.loads(raw)
        except ValueError:
            return exc.code, dict(exc.headers), {"raw": raw}


def counters() -> dict:
    _s, _h, payload = api("GET", "/health", token="")
    return payload.get("counters") or {}


def wait_for(predicate, timeout: float = 10.0, interval: float = 0.1):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return predicate()


def write_probe(name: str, body: str) -> str:
    """在目标实例的 scripts 目录里写一个探针脚本，返回可直接回传 /run 的相对路径。

    放在隐藏子目录里：/scan 会跳过隐藏目录，探针不会干扰平台看到的脚本清单。
    """
    path = SCRIPTS_DIR / PROBE_DIR_NAME / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return f"{PROBE_DIR_NAME}/{name}"


def write_visible_probe(name: str, body: str) -> str:
    """写一个「能被 /scan 扫到」的探针（/scan 跳过隐藏目录，所以这里用可见目录名）。"""
    path = SCRIPTS_DIR / SCAN_DIR_NAME / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return f"{SCAN_DIR_NAME}/{name}"


# ── 1) args 空引号 ─────────────────────────────────

def test_args_empty_quote() -> None:
    probe = write_probe("echo_args.py", "import sys, json\nprint(json.dumps(sys.argv[1:]))\n")
    status, _h, payload = api("POST", "/run", {"file_path": probe, "args": 'a "" b',
                                               "timeout_seconds": 30})
    try:
        echoed = json.loads((payload.get("stdout") or "[]").strip().splitlines()[-1])
    except (ValueError, IndexError):
        echoed = None
    check('args `a "" b` -> 3 个参数且中间是空串',
          status == 200 and echoed == ["a", "", "b"], f"HTTP {status} argv={echoed}")

    status, _h, payload = api("POST", "/run", {"file_path": probe, "args": '""',
                                               "timeout_seconds": 30})
    try:
        echoed = json.loads((payload.get("stdout") or "[]").strip().splitlines()[-1])
    except (ValueError, IndexError):
        echoed = None
    check('args `""` -> 1 个空参数（不是 0 个）', status == 200 and echoed == [""],
          f"HTTP {status} argv={echoed}")


# ── 2) 未知字段 ────────────────────────────────────

def test_unknown_fields() -> None:
    before = counters().get("invalid_fields", 0)
    status, _h, payload = api("POST", "/run", {"file_path": "does-not-matter.py", "timeout": 1})
    after = counters().get("invalid_fields", 0)
    detail = str(payload.get("detail") or "")
    check("近义字段 timeout -> 400 且提示 timeout_seconds",
          status == 400 and "timeout_seconds" in detail and after == before + 1,
          f"HTTP {status} detail={detail[:80]} counter {before}->{after}")

    before = counters().get("unknown_fields", 0)
    status, _h, payload = api("POST", "/run", {"file_path": "does-not-matter.py",
                                               "totally_unknown": 1})
    after = counters().get("unknown_fields", 0)
    check("其它未知字段：仍受理但计入 unknown_fields",
          status in (400, 404, 200) and after == before + 1, f"HTTP {status} {before}->{after}")


# ── 3) partial / 脚本失败 分类 ──────────────────────

def test_result_kinds() -> None:
    partial = write_probe("exit1.py", "import sys\nprint('partial-probe')\nsys.exit(1)\n")
    status, _h, payload = api("POST", "/run", {"file_path": partial, "timeout_seconds": 30})
    check("exit 1 -> code=206 + partial=true + error_kind=partial",
          status == 200 and payload.get("code") == 206 and payload.get("partial") is True
          and payload.get("error_kind") == "partial" and payload.get("exit_code") == 1,
          f"HTTP {status} code={payload.get('code')} partial={payload.get('partial')} "
          f"kind={payload.get('error_kind')} exit={payload.get('exit_code')}")

    failed = write_probe("exit2.py", "import sys\nprint('fail-probe')\nsys.exit(2)\n")
    status, _h, payload = api("POST", "/run", {"file_path": failed, "timeout_seconds": 30})
    check("exit 2 -> code=500 + error_kind=script_error（与 partial 区分）",
          status == 200 and payload.get("code") == 500 and payload.get("partial") is False
          and payload.get("error_kind") == "script_error",
          f"HTTP {status} code={payload.get('code')} kind={payload.get('error_kind')}")

    status, _h, payload = api("POST", "/run", {"file_path": "hello.py", "timeout_seconds": 30})
    if status == 200 and payload.get("code") == 200:
        check("成功 -> error_kind=ok", payload.get("error_kind") == "ok",
              f"kind={payload.get('error_kind')}")
    else:
        check("成功 -> error_kind=ok", False, f"HTTP {status} code={payload.get('code')}")


# ── 4) /scan ───────────────────────────────────────

def test_scan() -> None:
    write_visible_probe("nested_dir/deep.py", "print('deep')\n")
    status, _h, payload = api("POST", f"/scan?dir_path={url_quote(str(SCRIPTS_DIR))}")
    files = payload.get("files") or []
    names = [item.get("relative") for item in files]
    target = f"{SCAN_DIR_NAME}/nested_dir/deep.py"
    entry = next((item for item in files if item.get("relative") == target), None)
    check("/scan 递归且返回相对路径",
          status == 200 and target in names and entry is not None,
          f"HTTP {status} count={payload.get('count')} found={target in names}")
    check("/scan 默认 path 不含主机盘符（可直接回传 /run）",
          entry is not None and entry.get("path") == target,
          f"path={entry.get('path') if entry else None}")

    status, _h, payload = api(
        "POST", f"/scan?dir_path={url_quote(str(SCRIPTS_DIR))}&include_absolute=true")
    files = payload.get("files") or []
    entry = next((item for item in files if item.get("relative") == target), None)
    absolute_ok = entry is not None and os.path.isabs(str(entry.get("path")))
    check("/scan include_absolute=true 返回绝对路径（旧调用方兼容）",
          status == 200 and absolute_ok, f"HTTP {status} path={entry.get('path') if entry else None}")


# ── 5) /health/ready 探测缓存 ───────────────────────

def test_ready_probe_cache() -> None:
    status, _h, payload = api("GET", "/health/ready")
    checks = payload.get("checks") or {}
    scripts = checks.get("scripts_dir") or {}
    check("/health/ready 暴露 probe TTL 与缓存标记",
          status == 200 and "writable_from_cache" in scripts and "probe_ttl_seconds" in scripts,
          f"HTTP {status} scripts_dir={ {k: scripts.get(k) for k in ('writable', 'writable_from_cache', 'probe_ttl_seconds')} }")
    api("GET", "/health/ready")
    leftovers = list(SCRIPTS_DIR.glob(".write_probe_*"))
    check("/health/ready 不再残留探测文件", not leftovers, f"leftovers={[p.name for p in leftovers]}")


# ── 6) 队列 FIFO / queue_success ───────────────────

def test_queue_fifo() -> None:
    _s, _h, tasks = api("GET", "/tasks")
    limit = int(tasks.get("concurrency_limit") or 4)
    sleeper = write_probe("sleeper.py", "import time\ntime.sleep(8)\n")

    occupiers: list[threading.Thread] = []

    def occupy(i: int) -> None:
        api("POST", "/run", {"file_path": sleeper, "timeout_seconds": 30,
                             "task_run_id": f"p2occ-{STAMP}-{i}"}, timeout=60)

    for i in range(limit):
        th = threading.Thread(target=occupy, args=(i,), daemon=True)
        th.start()
        occupiers.append(th)
    busy = wait_for(lambda: int((api("GET", "/tasks")[2].get("count") or 0)) >= limit, timeout=15)
    check(f"占满并发槽 {limit}/{limit}", bool(busy), f"count={api('GET', '/tasks')[2].get('count')}")

    before_success = counters().get("queue_success", 0)
    queued: list[dict] = []

    def enqueue(tag: str) -> None:
        api("POST", "/run", {"file_path": "hello.py", "timeout_seconds": 25,
                             "block_strategy": "queue", "task_run_id": f"p2q-{tag}-{STAMP}"},
            timeout=60)

    first = threading.Thread(target=enqueue, args=("a",), daemon=True)
    first.start()
    wait_for(lambda: sum(1 for _ in (api("GET", "/tasks")[2].get("queue") or {}).get("waiting") or []) >= 1,
             timeout=5)
    second = threading.Thread(target=enqueue, args=("b",), daemon=True)
    second.start()
    wait_for(lambda: sum(1 for _ in (api("GET", "/tasks")[2].get("queue") or {}).get("waiting") or []) >= 2,
             timeout=5)

    waiting = (api("GET", "/tasks")[2].get("queue") or {}).get("waiting") or []
    queued = waiting
    positions = [int(item.get("position") or 0) for item in waiting]
    order_ok = (len(waiting) >= 2 and positions[:2] == [1, 2]
                and waiting[0].get("task_id") == f"p2q-a-{STAMP}")
    check("排队暴露 FIFO position，且队头=先到的请求", order_ok,
          f"waiting={[(item.get('task_id'), item.get('position')) for item in waiting]}")

    for i in range(limit):
        api("POST", f"/cancel/p2occ-{STAMP}-{i}", timeout=30)
    for th in occupiers:
        th.join(timeout=40)
    first.join(timeout=60)
    second.join(timeout=60)

    after_success = counters().get("queue_success", 0)
    check("排队的请求都拿到槽执行完（queue_success 计数增加）",
          after_success >= before_success + 1, f"queue_success {before_success}->{after_success}")


def cleanup() -> None:
    shutil.rmtree(SCRIPTS_DIR / PROBE_DIR_NAME, ignore_errors=True)
    shutil.rmtree(SCRIPTS_DIR / SCAN_DIR_NAME, ignore_errors=True)


def main() -> int:
    global BASE, TOKEN, SCRIPTS_DIR
    parser = argparse.ArgumentParser(description="3.3.2 P2 修复验收")
    parser.add_argument("--base", default=os.environ.get("SS_BASE", BASE))
    parser.add_argument("--scripts-dir", default=os.environ.get("SS_SCRIPTS_DIR", ""))
    parser.add_argument("--token", default=os.environ.get("SS_TOKEN", ""))
    args = parser.parse_args()

    env = _read_env()
    BASE = args.base.rstrip("/")
    TOKEN = args.token or env.get("WF_SCRIPT_TOKEN", "")
    SCRIPTS_DIR = Path(args.scripts_dir or env.get("WF_SCRIPTS_DIR") or (ROOT.parent / "scripts"))
    if not TOKEN:
        print("[FATAL] 没有 token：请设置 SS_TOKEN 或在 .env 里配置 WF_SCRIPT_TOKEN")
        return 1
    if not SCRIPTS_DIR.is_dir():
        print(f"[FATAL] scripts 目录不存在: {SCRIPTS_DIR}")
        return 1

    expected = _expected_version()
    status, _h, health = api("GET", "/health", token="")
    check(f"/health 版本 = {expected}", status == 200 and health.get("version") == expected,
          f"HTTP {status} version={health.get('version')}")

    try:
        test_args_empty_quote()
        test_unknown_fields()
        test_result_kinds()
        test_scan()
        test_ready_probe_cache()
        test_queue_fifo()
    finally:
        cleanup()

    summary = {"stamp": STAMP, "base": BASE, "total": len(RESULTS),
               "passed": len(RESULTS) - len(FAILED), "failed": FAILED, "results": RESULTS}
    out = ROOT / "logs" / "verify-p2-result.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("=" * 70)
    print(f"结果：{summary['passed']}/{summary['total']} PASS"
          + ("" if not FAILED else " | FAIL: " + ", ".join(FAILED)))
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
