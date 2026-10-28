# -*- coding: utf-8 -*-
"""执行器加固项的在线验收（需要服务在跑；token 从 ..\\.env 读取）。

覆盖 2026-10-28 的四项加固：
  1) 脚本内容指纹：脚本改动后同一 task_run_id 必须重跑（不会返回旧结果）；
  2) 三态 code：成功 200 / 失败 500 / 超时 502（HTTP 仍是 200，见文档）；
  3) 并发策略：block_strategy=reject 立即 429+Retry-After；=queue 排队等空槽后成功；
  4) 审计日志：run.start / run.dedup / run.finish 各一行 JSON，且不含明文参数。

用法（普通窗口即可）：
  python deploy\\verify-hardening.py        退出码 0 = 全部通过
"""
from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # ...\script-server
BASE = "http://127.0.0.1:9000"
SCRIPTS_DIR = ROOT.parent / "scripts"
PROBE = SCRIPTS_DIR / "_hardening_probe.py"
FAIL_SCRIPT = SCRIPTS_DIR / "_hardening_fail.py"
AUDIT_LOG = ROOT / "logs" / "script-server-audit.log"
STAMP = time.strftime("%H%M%S")
ARG_MARKER = "ARG-MARKER-" + uuid.uuid4().hex[:8]   # 用来验证「参数不落明文日志」的标记串

FAILED: list[str] = []
TOKEN = ""


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("[PASS] " if ok else "[FAIL] ") + name + ((" " + detail) if detail else ""), flush=True)
    if not ok:
        FAILED.append(name)


def _read_env_token() -> str:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return ""
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("WF_SCRIPT_TOKEN="):
            return line.split("=", 1)[1].strip()
    return ""


def api(method: str, path: str, body: dict | None = None, timeout: float = 90.0):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if TOKEN:
        headers["X-Script-Token"] = TOKEN
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = {"raw": raw}
        return exc.code, dict(exc.headers), payload


def write_script(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


def test_version() -> None:
    status, _h, payload = api("GET", "/health", timeout=10)
    check("/health 200 且版本 3.3.0", status == 200 and payload.get("version") == "3.3.0",
          f"HTTP {status} version={payload.get('version')}")


def test_code_and_dedupe() -> tuple[bool, float]:
    body = {
        "file_path": "hello.py",
        "args": f"--name {ARG_MARKER}",
        "timeout_seconds": 30,
        "task_run_id": f"hard-ok-{STAMP}",
    }
    status, _h, first = api("POST", "/run", body)
    ok1 = (status == 200 and first.get("code") == 200 and first.get("exit_code") == 0
           and first.get("success") is True and first.get("deduplicated") is False)
    check("首次运行：code=200 exit=0", ok1,
          f"HTTP {status} code={first.get('code')} exit={first.get('exit_code')}")

    status, _h, second = api("POST", "/run", body)
    ok2 = (status == 200 and second.get("code") == 200 and second.get("deduplicated") is True)
    check("同 task_run_id 重投：deduplicated=True 且 code=200", ok2,
          f"HTTP {status} dedup={second.get('deduplicated')} code={second.get('code')}")
    return ok1 and ok2, float(second.get("duration_seconds") or 0)


def test_content_fingerprint() -> None:
    run_id = f"hard-content-{STAMP}"
    body = {"file_path": PROBE.name, "timeout_seconds": 30, "task_run_id": run_id}
    write_script(PROBE, "print('PROBE-A')\n")
    _s, _h, first = api("POST", "/run", body)
    write_script(PROBE, "print('PROBE-B')\n")
    _s, _h, second = api("POST", "/run", body)
    ok = (first.get("deduplicated") is False and "PROBE-A" in (first.get("stdout") or "")
          and second.get("deduplicated") is False and "PROBE-B" in (second.get("stdout") or ""))
    check("脚本内容改动后同 task_run_id 必须重跑（内容指纹生效）", ok,
          f"first={first.get('deduplicated')}/{first.get('stdout', '').strip()!r} "
          f"second={second.get('deduplicated')}/{second.get('stdout', '').strip()!r}")


def test_fail_code() -> None:
    write_script(FAIL_SCRIPT, "import sys\nprint('fail-probe')\nsys.exit(2)\n")
    status, _h, payload = api("POST", "/run",
                              {"file_path": FAIL_SCRIPT.name, "timeout_seconds": 20})
    ok = status == 200 and payload.get("code") == 500 and payload.get("success") is False
    check("失败运行：code=500（HTTP 仍 200）", ok,
          f"HTTP {status} code={payload.get('code')} exit={payload.get('exit_code')}")


def test_timeout_code() -> None:
    status, _h, payload = api("POST", "/run",
                              {"file_path": "sleep5.py", "timeout_seconds": 2,
                               "task_run_id": f"hard-tmo-{STAMP}"})
    ok = (status == 200 and payload.get("code") == 502
          and payload.get("timed_out") is True and payload.get("cancelled") is False)
    check("超时运行：code=502 且 timed_out=True", ok,
          f"HTTP {status} code={payload.get('code')} timed_out={payload.get('timed_out')}")


def test_concurrency() -> None:
    results: list = []
    lock = threading.Lock()

    def slow(i: int) -> None:
        body = {"file_path": "sleep5.py", "timeout_seconds": 20,
                "task_run_id": f"hard-busy-{STAMP}-{i}"}
        out = api("POST", "/run", body, timeout=60)
        with lock:
            results.append(out)

    threads = [threading.Thread(target=slow, args=(i,), daemon=True) for i in range(4)]
    for t in threads:
        t.start()

    # 等 4 个并发槽被占满（/tasks 需要 token）
    busy = 0
    for _ in range(25):
        _s, _h, tasks = api("GET", "/tasks", timeout=10)
        busy = int(tasks.get("count") or 0)
        if busy >= 4:
            break
        time.sleep(0.2)
    check("并发槽占满 4/4", busy >= 4, f"count={busy}")

    status, headers, payload = api("POST", "/run",
                                   {"file_path": "hello.py", "timeout_seconds": 20})
    retry_after = (headers.get("Retry-After") or headers.get("retry-after") or "")
    ok_reject = status == 429 and retry_after != ""
    check("并发满 + reject：429 且带 Retry-After", ok_reject,
          f"HTTP {status} Retry-After={retry_after!r}")

    started = time.monotonic()
    status_q, _h, payload_q = api("POST", "/run",
                                  {"file_path": "hello.py", "timeout_seconds": 30,
                                   "block_strategy": "queue"})
    waited = time.monotonic() - started
    ok_queue = status_q == 200 and payload_q.get("success") is True and waited >= 1.5
    check("并发满 + queue：排队等到空槽后成功", ok_queue,
          f"HTTP {status_q} waited={waited:.1f}s success={payload_q.get('success')}")

    for t in threads:
        t.join(timeout=40)


def test_audit_log() -> None:
    text = AUDIT_LOG.read_text(encoding="utf-8") if AUDIT_LOG.exists() else ""
    has_start = '"event": "run.start"' in text or '"event":"run.start"' in text
    has_dedup = '"event": "run.dedup"' in text or '"event":"run.dedup"' in text
    has_finish = '"event": "run.finish"' in text or '"event":"run.finish"' in text
    check("audit.log 有 run.start / run.dedup / run.finish",
          has_start and has_dedup and has_finish,
          f"start={has_start} dedup={has_dedup} finish={has_finish}")
    check("audit.log 不落明文参数", ARG_MARKER not in text,
          f"marker_found={ARG_MARKER in text}")


def main() -> int:
    global TOKEN
    TOKEN = _read_env_token()
    if not TOKEN:
        print("[FATAL] .env 里没有 WF_SCRIPT_TOKEN，无法调用需要鉴权的接口")
        return 1
    try:
        test_version()
        test_code_and_dedupe()
        test_content_fingerprint()
        test_fail_code()
        test_timeout_code()
        test_concurrency()
        test_audit_log()
    finally:
        for path in (PROBE, FAIL_SCRIPT):
            try:
                path.unlink()
            except OSError:
                pass
    print("=" * 60)
    print("结果：全部通过" if not FAILED else "结果：失败 " + ", ".join(FAILED))
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
