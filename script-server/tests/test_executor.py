# -*- coding: utf-8 -*-
"""执行器单元测试（3.3.2 新增，对应测评 P2-8：无单测）。

覆盖的是「纯逻辑 + 单进程内可复现」的部分，不依赖线上服务：
  - args 切分（含空引号、反斜杠、未闭合引号）
  - 未知字段校验（近义字段 400 / 未知字段告警 / WF_STRICT_FIELDS=1 时 400）
  - 结果分类（partial 206 / 脚本错误 500 / 执行器错误 executor_error）
  - /scan 相对路径与递归
  - 队列 FIFO（只有队头能抢槽）
  - 指纹与就绪探测缓存

运行：python -m pytest tests -q        或    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_TMP = Path(tempfile.mkdtemp(prefix="ss-selftest-"))
_SCRIPTS = _TMP / "scripts"
_SCRIPTS.mkdir(parents=True, exist_ok=True)
(_SCRIPTS / "hello.py").write_text("print('hello')\n", encoding="utf-8")
(_SCRIPTS / "exit1.py").write_text("import sys\nprint('partial')\nsys.exit(1)\n", encoding="utf-8")
(_SCRIPTS / "exit2.py").write_text("import sys\nprint('fail')\nsys.exit(2)\n", encoding="utf-8")

# 必须在 import script_server 之前设好：这些常量在模块加载时读取
os.environ.setdefault("WF_SCRIPTS_DIR", str(_SCRIPTS))
os.environ.setdefault("WF_SCRIPT_STATE_DIR", str(_TMP / "state"))
os.environ.setdefault("WF_SCRIPT_TOKEN", "t" * 48)
os.environ.setdefault("WF_BIND_HOST", "127.0.0.1")
os.environ.setdefault("WF_MAX_CONCURRENCY", "2")

sys.path.insert(0, str(ROOT))
import script_server as ss  # noqa: E402


# ── args 切分 ───────────────────────────────────────

@pytest.mark.parametrize(
    "raw,expected",
    [
        ('a "" b', ["a", "", "b"]),
        ('""', [""]),
        ('a ""', ["a", ""]),
        ('"" b', ["", "b"]),
        ('a "b c" d', ["a", "b c", "d"]),
        ("a 'b c' d", ["a", "b c", "d"]),
        (r"C:\temp\new x", [r"C:\temp\new", "x"]),
        ('--flag="a b" --x', ["--flag=a b", "--x"]),
        ("", []),
        ("   ", []),
    ],
)
def test_split_args_windows(raw: str, expected: list) -> None:
    assert ss._split_args_windows(raw) == expected


def test_split_args_unclosed_quote_400() -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        ss._split_args_windows('a "b')
    assert exc.value.status_code == 400


# ── 未知字段 ────────────────────────────────────────

def test_near_miss_field_rejected_with_hint() -> None:
    from fastapi import HTTPException

    req = ss.RunRequest(file_path="hello.py", timeout=1)  # type: ignore[call-arg]
    with pytest.raises(HTTPException) as exc:
        ss._check_unknown_fields(req, "127.0.0.1")
    assert exc.value.status_code == 400
    assert "timeout_seconds" in str(exc.value.detail)


def test_unknown_field_ignored_but_counted() -> None:
    before = ss._METRICS.get("unknown_fields", 0)
    req = ss.RunRequest(file_path="hello.py", foo="bar")  # type: ignore[call-arg]
    assert ss._check_unknown_fields(req, "127.0.0.1") == ["foo"]
    assert ss._METRICS["unknown_fields"] == before + 1


def test_strict_fields_rejects_unknown() -> None:
    from fastapi import HTTPException

    req = ss.RunRequest(file_path="hello.py", foo="bar")  # type: ignore[call-arg]
    ss._STRICT_FIELDS = True
    try:
        with pytest.raises(HTTPException) as exc:
            ss._check_unknown_fields(req, "127.0.0.1")
        assert exc.value.status_code == 400
    finally:
        ss._STRICT_FIELDS = False


# ── 结果分类 ────────────────────────────────────────

def _run(name: str) -> ss.RunResponse:
    return asyncio.run(ss.run(ss.RunRequest(file_path=name), None))


def test_success_code() -> None:
    resp = _run("hello.py")
    assert (resp.code, resp.exit_code, resp.error_kind) == (200, 0, "ok")
    assert resp.success is True and resp.partial is False


def test_partial_uses_206() -> None:
    resp = _run("exit1.py")
    assert resp.code == ss._PARTIAL_CODE == 206
    assert resp.exit_code == 1 and resp.partial is True and resp.success is False
    assert resp.error_kind == "partial"


def test_partial_code_can_be_rolled_back(monkeypatch) -> None:
    monkeypatch.setattr(ss, "_PARTIAL_CODE", 500)
    assert _run("exit1.py").code == 500


def test_script_failure_code_and_kind() -> None:
    resp = _run("exit2.py")
    assert (resp.code, resp.exit_code, resp.error_kind) == (500, 2, "script_error")


def test_executor_error_kind(monkeypatch) -> None:
    async def boom(*_a, **_k):
        raise OSError("simulated spawn failure")

    monkeypatch.setattr(ss.asyncio, "create_subprocess_exec", boom)
    resp = _run("hello.py")
    assert (resp.code, resp.exit_code) == (500, 3)
    assert resp.error_kind == "executor_error"


# ── /scan ───────────────────────────────────────────

def test_scan_recursive_relative_paths() -> None:
    sub = _SCRIPTS / "pkg"
    sub.mkdir(exist_ok=True)
    (sub / "nested.py").write_text("print(1)\n", encoding="utf-8")
    payload = asyncio.run(ss.scan_directory(str(_SCRIPTS), True, False, None))
    rels = {item["relative"] for item in payload["files"]}
    assert "pkg/nested.py" in rels and "hello.py" in rels
    entry = next(item for item in payload["files"] if item["relative"] == "pkg/nested.py")
    # 默认不返回绝对路径，path 可直接回传 /run
    assert entry["path"] == "pkg/nested.py"
    assert ":" not in entry["path"]
    assert payload["recursive"] is True and payload["count"] == len(payload["files"])


def test_scan_absolute_opt_in() -> None:
    payload = asyncio.run(ss.scan_directory(str(_SCRIPTS), False, True, None))
    assert payload["files"]
    assert all(Path(item["path"]).is_absolute() for item in payload["files"])
    assert all("/" not in item["relative"] for item in payload["files"])


# ── 队列 FIFO ───────────────────────────────────────

def test_queue_fifo_order() -> None:
    k1 = ss._queue_enter("t1", "a.py")
    k2 = ss._queue_enter("t2", "b.py")
    try:
        assert ss._queue_is_head(k1) is True
        assert ss._queue_is_head(k2) is False
        positions = [item["position"] for item in ss._queue_snapshot()]
        assert positions == [1, 2]
        assert ss._queue_depth() == 2
    finally:
        ss._queue_leave(k1)
        ss._queue_leave(k2)
    assert ss._queue_depth() == 0
    assert ss._queue_is_head(None) is True


# ── 指纹 / 就绪探测 ─────────────────────────────────

def test_file_signature_skips_digest_when_not_needed() -> None:
    sig = ss._file_signature(_SCRIPTS / "hello.py", with_digest=False)
    assert sig["sha256"] == "" and sig["size"] > 0
    full = ss._file_signature(_SCRIPTS / "hello.py")
    assert len(full["sha256"]) == 64


def test_ready_probe_is_cached(monkeypatch) -> None:
    monkeypatch.setattr(ss, "_READY_PROBE_TTL", 300)
    first, cached1 = ss._scripts_writable()
    second, cached2 = ss._scripts_writable()
    assert first is True and second is True
    assert cached1 is False and cached2 is True


def test_ready_probe_ttl_zero_always_writes(monkeypatch) -> None:
    monkeypatch.setattr(ss, "_READY_PROBE_TTL", 0)
    ss._scripts_writable()
    _ok, cached = ss._scripts_writable()
    assert cached is False


# ── 端到端（TestClient，不起真实端口）───────────────

def test_http_surface_via_testclient() -> None:
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    headers = {"X-Script-Token": ss._SCRIPT_TOKEN}
    with fastapi_testclient.TestClient(ss.app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        body = health.json()
        assert body["version"] == ss.VERSION and "queue" in body
        assert client.get("/health/ready").status_code == 401
        ready = client.get("/health/ready", headers=headers)
        assert ready.status_code == 200 and ready.json()["status"] == "ok"
        bad = client.post("/run", json={"file_path": "hello.py", "timeout": 1}, headers=headers)
        assert bad.status_code == 400 and "timeout_seconds" in bad.text
        ran = client.post("/run", json={"file_path": "hello.py", "timeout_seconds": 30},
                          headers=headers)
        assert ran.status_code == 200 and ran.json()["code"] == 200
