# -*- coding: utf-8 -*-
"""3.3.1 P1 修复验收（在线 10 项 + 离线 4 项）。

覆盖：
  1) 内部异常不再裸 500（HTTP 200 + code=500 + exit_code=3，spawn_errors 计数）
  2) env / args 含 NUL -> 400
  3) queue 可观测（/tasks 的 queue.depth/waiting）+ 有界（WF_QUEUE_MAX_DEPTH）
  4) running.json 不落明文 argv
  5) 审计：auth.fail / path.deny、纯 JSON 行
  6) queue_timeout / queue_full_rejected / auth_failed / path_denied 计数

用法：python deploy\\verify-p1.py     退出码 0 = 全部通过
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = "http://127.0.0.1:9000"
SCRIPTS = ROOT.parent / "scripts"
STATE = Path(os.environ.get("WF_SCRIPT_STATE_DIR") or (ROOT.parent / ".script_server_state"))
AUDIT = ROOT / "logs" / "script-server-audit.log"
STAMP = time.strftime("%H%M%S")
FAILED: list[str] = []
RESULTS: list[dict] = []
TOKEN = ""
AUDIT_OFFSET = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append({"name": name, "pass": bool(ok), "detail": detail})
    print(("[PASS] " if ok else "[FAIL] ") + name + (("  -> " + detail) if detail else ""), flush=True)
    if not ok:
        FAILED.append(name)


def _read_env_token() -> str:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return ""
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("WF_SCRIPT_TOKEN="):
            return line.strip().split("=", 1)[1].strip()
    return ""


def _expected_version() -> str:
    try:
        m = re.search(r'^VERSION\s*=\s*"([^"]+)"', (ROOT / "script_server.py").read_text(encoding="utf-8"), re.M)
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


def run_async(body: dict, out: list, timeout: float = 120) -> threading.Thread:
    def _go():
        out.append(api("POST", "/run", body, timeout=timeout))
    th = threading.Thread(target=_go, daemon=True)
    th.start()
    return th


def counters() -> dict:
    _s, _h, payload = api("GET", "/health", token="")
    return payload.get("counters") or {}


def audit_since(offset: int) -> str:
    try:
        data = AUDIT.read_bytes()[offset:]
        return data.decode("utf-8", "replace")
    except OSError:
        return ""


def occupy_slots(tag: str, n: int = 4) -> list[tuple[str, threading.Thread]]:
    started = []
    for i in range(n):
        tid = f"p1-{tag}-{STAMP}-{i}"
        th = run_async({"file_path": "sleep5.py", "timeout_seconds": 30, "task_run_id": tid}, [])
        started.append((tid, th))
    deadline = time.time() + 10
    while time.time() < deadline:
        _s, _h, payload = api("GET", "/tasks")
        if int(payload.get("count") or 0) >= n:
            break
        time.sleep(0.15)
    return started


def release_slots(started: list[tuple[str, threading.Thread]]) -> None:
    for tid, _th in started:
        try:
            api("POST", f"/cancel/{tid}", timeout=30)
        except Exception:
            pass
    for _tid, th in started:
        th.join(timeout=30)


# ── 在线部分 ────────────────────────────────────────

def test_version() -> None:
    expected = _expected_version()
    st, _h, payload = api("GET", "/health", token="")
    check(f"/health 版本 = {expected}", st == 200 and payload.get("version") == expected,
          f"HTTP {st} version={payload.get('version')}")
    check("/health 暴露 queue.depth/limit",
          isinstance((payload.get("queue") or {}).get("depth"), int)
          and isinstance((payload.get("queue") or {}).get("limit"), int),
          f"queue={payload.get('queue')}")


def test_nul() -> None:
    st, _h, _b = api("POST", "/run", {"file_path": "hello.py", "env": {"EVIL": "a\x00b"}})
    check("env 值含 NUL -> 400（不再 500）", st == 400, f"HTTP {st}")
    st, _h, _b = api("POST", "/run", {"file_path": "hello.py", "args": "a\x00b"})
    check("args 含 NUL -> 400（不再 500）", st == 400, f"HTTP {st}")


def test_auth_audit() -> None:
    before = counters().get("auth_failed", 0)
    st, _h, _b = api("POST", "/run", {"file_path": "hello.py"}, token="definitely-wrong")
    after = counters().get("auth_failed", 0)
    check("错 token -> 401 且 auth_failed 计数 +1", st == 401 and after == before + 1,
          f"HTTP {st} {before}->{after}")
    time.sleep(0.2)
    new_text = audit_since(AUDIT_OFFSET)
    check("审计新增 auth.fail 事件", '"event": "auth.fail"' in new_text or '"event":"auth.fail"' in new_text,
          f"audit_bytes={len(new_text)}")


def test_path_audit() -> None:
    before = counters().get("path_denied", 0)
    st, _h, _b = api("POST", "/run", {"file_path": "..\\..\\script_server.py"})
    after = counters().get("path_denied", 0)
    check("越界路径 -> 403 且 path_denied +1", st == 403 and after == before + 1, f"HTTP {st} {before}->{after}")
    time.sleep(0.2)
    check("审计新增 path.deny 事件", '"event": "path.deny"' in audit_since(AUDIT_OFFSET))


def test_audit_pure_json() -> None:
    text = audit_since(AUDIT_OFFSET)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    bad = []
    for ln in lines:
        try:
            json.loads(ln)
        except ValueError:
            bad.append(ln[:60])
    check("审计新增行全部是纯 JSON", bool(lines) and not bad, f"lines={len(lines)} bad={len(bad)} {bad[:2]}")


def test_running_json_redacted() -> None:
    marker = "SECRET-" + uuid.uuid4().hex[:8]
    rid = f"p1-leak-{STAMP}"
    out: list = []
    th = run_async({"file_path": "sleep5.py", "timeout_seconds": 20, "task_run_id": rid,
                    "args": f"--secret {marker}"}, out)
    time.sleep(0.9)
    text = ""
    try:
        text = (STATE / "running.json").read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    check("running.json 不含明文 argv", marker not in text, f"marker_found={marker in text}")
    check("running.json 含 args_sha/args_len", '"args_sha"' in text and '"args_len"' in text,
          text[:160])
    api("POST", f"/cancel/{rid}")
    th.join(timeout=30)


def test_queue_visible() -> None:
    started = occupy_slots("queue")
    try:
        qid = f"p1-q-{STAMP}"
        qout: list = []
        qth = run_async({"file_path": "hello.py", "timeout_seconds": 30,
                         "block_strategy": "queue", "task_run_id": qid}, qout)
        seen = False
        depth = 0
        deadline = time.time() + 4
        while time.time() < deadline:
            _s, _h, payload = api("GET", "/tasks")
            q = payload.get("queue") or {}
            depth = int(q.get("depth") or 0)
            waiting = q.get("waiting") or []
            if depth >= 1 and any(item.get("task_id") == qid for item in waiting):
                seen = True
                break
            time.sleep(0.1)
        check("queue 排队中可在 /tasks 看到（depth/waiting）", seen, f"depth={depth} seen={seen}")
    finally:
        release_slots(started)
    qth.join(timeout=40)
    check("queue 排到空槽后成功", bool(qout) and qout[0][2].get("success") is True,
          f"{qout[0][0] if qout else 'no-response'}")


def test_queue_timeout_counter() -> None:
    started = occupy_slots("qtmo")
    try:
        before = counters().get("queue_timeout", 0)
        st, _h, _b = api("POST", "/run", {"file_path": "hello.py", "timeout_seconds": 1,
                                         "block_strategy": "queue"})
        after = counters().get("queue_timeout", 0)
        check("queue 等待超时 -> 429 且 queue_timeout +1", st == 429 and after == before + 1,
              f"HTTP {st} {before}->{after}")
    finally:
        release_slots(started)


# ── 离线部分（同进程 import，隔离目录，不碰生产状态）──

def test_offline() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="p1-"))
    scripts = tmp / "scripts"
    state = tmp / "state"
    scripts.mkdir()
    (scripts / "hello.py").write_text("print('hello')\n", encoding="utf-8")

    saved = {k: os.environ.get(k) for k in ("WF_SCRIPTS_DIR", "WF_SCRIPT_STATE_DIR", "WF_BIND_HOST",
                                           "WF_SCRIPT_TOKEN", "WF_MAX_CONCURRENCY")}
    os.environ["WF_SCRIPTS_DIR"] = str(scripts)
    os.environ["WF_SCRIPT_STATE_DIR"] = str(state)
    os.environ["WF_BIND_HOST"] = "127.0.0.1"
    os.environ["WF_SCRIPT_TOKEN"] = TOKEN or "x" * 48
    os.environ["WF_MAX_CONCURRENCY"] = "2"
    sys.path.insert(0, str(ROOT))
    try:
        import script_server as ss

        # 1) 队列上限（纯内存）
        ss._QUEUE_MAX_DEPTH = 2
        k1 = ss._queue_enter("a", "a.py")
        k2 = ss._queue_enter("b", "b.py")
        k3 = ss._queue_enter("c", "c.py")
        check("queue 上限生效（第 3 个返回 None）", k1 and k2 and k3 is None,
              f"depth={ss._queue_depth()}")
        for k in (k1, k2):
            ss._queue_leave(k)
        ss._QUEUE_MAX_DEPTH = 16

        # 2) running.json 快照不含 cmd
        h = ss._TaskHandle(
            task_id="t-offline", script_path=scripts / "hello.py", cmd=["python", "hello.py", "--x"],
            timeout_seconds=5, started_at=0.0, started_monotonic=0.0,
            args_len=3, args_sha="abc123",
        )
        ss.task_manager["t-offline"] = h
        snap = ss._snapshot_running()
        ss.task_manager.pop("t-offline", None)
        check("_snapshot_running 不含 cmd、含 args_sha",
              "cmd" not in snap["t-offline"] and snap["t-offline"].get("args_sha") == "abc123",
              str(snap["t-offline"]))

        # 3) 内部异常 -> 200 + code=500 + exit_code=3，spawn_errors 计数，槽归还
        async def boom(*_a, **_k):
            raise OSError("simulated spawn failure")

        orig = ss.asyncio.create_subprocess_exec
        ss.asyncio.create_subprocess_exec = boom
        before_spawn = ss._METRICS.get("spawn_errors", 0)
        try:
            resp = asyncio.run(ss.run(ss.RunRequest(file_path="hello.py"), None))
        finally:
            ss.asyncio.create_subprocess_exec = orig
        after_spawn = ss._METRICS.get("spawn_errors", 0)
        check("内部异常返回 code=500 / exit_code=3（不再裸 500）",
              resp.code == 500 and resp.exit_code == 3 and resp.success is False,
              f"code={resp.code} exit={resp.exit_code}")
        check("内部异常计入 spawn_errors", after_spawn == before_spawn + 1, f"{before_spawn}->{after_spawn}")
        check("内部异常后并发槽已归还", ss._free_slots == ss._MAX_CONCURRENCY,
              f"free={ss._free_slots}/{ss._MAX_CONCURRENCY}")
        check("内部异常后台账已清理", "t-offline" not in ss.task_manager and not ss.task_manager,
              f"tasks={list(ss.task_manager)}")
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    global TOKEN, AUDIT_OFFSET
    TOKEN = _read_env_token()
    if not TOKEN:
        print("[FATAL] .env 里没有 WF_SCRIPT_TOKEN")
        return 1
    AUDIT_OFFSET = AUDIT.stat().st_size if AUDIT.exists() else 0
    test_version()
    test_nul()
    test_auth_audit()
    test_path_audit()
    test_queue_visible()
    test_queue_timeout_counter()
    test_running_json_redacted()
    test_audit_pure_json()
    test_offline()
    summary = {"stamp": STAMP, "total": len(RESULTS), "passed": len(RESULTS) - len(FAILED),
               "failed": FAILED, "results": RESULTS}
    (ROOT / "logs" / "verify-p1-result.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("=" * 70)
    print(f"结果：{summary['passed']}/{summary['total']} PASS" + ("" if not FAILED else " | FAIL: " + ", ".join(FAILED)))
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
