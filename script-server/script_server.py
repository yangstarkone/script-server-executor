# -*- coding: utf-8 -*-
"""
脚本执行服务 — Windows / Linux 宿主机上的 HTTP 脚本执行代理

用途:
  接收调度系统（Docker Worker / 平台）的 HTTP 请求，执行 Python 脚本并返回结果。
  支持取消正在运行的脚本（POST /cancel/{task_id}）。
  客户端断开时默认取消执行（可关闭，见 WF_CANCEL_ON_DISCONNECT）。

幂等与重试（2026-10-28 / 3.3.0，3.3.1 修订）:
  - 幂等键 = task_run_id（可选，调用方提供）；不传则每次都是新任务、不做任何去重。
  - 命中条件 = 同一 task_run_id + 同一请求指纹。指纹含 脚本路径、args、env、超时、
    **脚本内容 sha256** → 脚本改动后同一 task_run_id 会重新执行，不会返回旧结果。
  - 只有**成功（exit 0）**的结果才落缓存；失败/超时/取消一律不缓存，
    所以平台“失败工作流里的重试”会真正重跑，不会被幂等挡住。
  - 同一 task_run_id 仍在运行 → 409 + Retry-After（通常是上一次响应丢了；等一会儿或查 /tasks）。
  - 幂等真正防护的是“响应丢失但其实已成功”的重投：返回历史结果，绝不二次执行。

启动:
  python deploy\\serve.py           （推荐；读 .env 并托管 uvicorn）
  uvicorn script_server:app --host 0.0.0.0 --port 9000 --workers 1   （裸启动，不读 .env）
  （注意：--workers 必须为 1，因为用进程内全局 dict 追踪进程；
    多 worker 时 /tasks、/cancel、409 各自为政，代码会在启动时检查并告警。）

脚本目录:
  默认 ./scripts/（相对于本文件所在目录），可通过 WF_SCRIPTS_DIR 环境变量自定义。
  file_path 支持相对路径（task.py → WF_SCRIPTS_DIR/task.py）与绝对路径（C:\\...）；
  绝对路径默认只允许 WF_SCRIPTS_DIR 内部（fail-closed），其它目录需显式配置
  WF_ALLOWED_SCRIPT_ROOTS，/scan 同理（WF_SCAN_ALLOWED_ROOTS）。

鉴权（重要）:
  WF_SCRIPT_TOKEN 环境变量为空 = 不校验（兼容单机旧部署，启动会打 WARNING）；
  非空时 /run、/cancel、/scan、/tasks、/health/ready 必须带请求头 X-Script-Token
  （或 Authorization: Bearer）。/health 保持公开，只返回状态/版本/计数，不执行脚本。
  生产环境可设 WF_SCRIPT_REQUIRE_TOKEN=1：token 为空时直接拒绝启动（fail-fast）。

请求 (POST /run):
  {
    "file_path": "task.py",           // 必填，绝对路径或相对路径
    "args": "--verbose",              // 可选，CLI 参数（Windows 下按“空格/双引号”切分，反斜杠原样保留；
                                      //       空引号 "" 会保留为空参数，不会被吞掉）
    "env": {"KEY": "value"},          // 可选，注入为环境变量
    "timeout_seconds": 300,           // 可选，默认 300，范围 1-7200
    "task_run_id": "xxx",             // 可选，幂等键 + 取消追踪
    "block_strategy": "reject"        // 可选，并发满时：reject=立即 429（默认） / queue=排队等空槽
  }

响应 (HTTP 200):
  {task_id, code, exit_code, stdout, stderr, success, partial, status_label, error_kind,
   cancelled, timed_out, deduplicated, truncated, duration_seconds}
  code（平台判定用，与 exit_code 互补）:
    200 = 成功；206 = 部分成功（脚本 exit 1）；500 = 失败/取消/执行器内部错误；
    502 = 超时（进程已终止）
  error_kind（3.3.2 新增，最精确的一维分类，建议平台优先用它）:
    ok / partial / script_error / timeout / cancelled / executor_error
    其中 executor_error 表示执行器自身故障（spawn 失败、内部异常），与脚本自己失败区分开。
  HTTP 层错误仍用状态码: 400 参数、401 鉴权、403 路径、409 同 task_id 运行中、429 繁忙/排队已满、503 排空。
  请求体字段严格校验（3.3.2）：常见错拼（timeout/timeout_ms/filepath/argv/task_id…）直接 400 并给出正确字段名；
  其它未知字段默认告警并忽略，设 WF_STRICT_FIELDS=1 可改为 400（确认调用方无多余字段后开启）。

取消 (POST /cancel/{task_id]):
  杀掉正在运行的脚本进程树（先 taskkill /F /T，失败再 kill 主进程）。

持久化/对账（WF_SCRIPT_STATE_DIR，默认 <scripts 目录父级>/.script_server_state）:
  - results/<hash>.json：已完成结果缓存（TTL WF_RESULT_CACHE_TTL，默认 24h）
  - running.json：运行中任务台账；进程重启后启动时对账，清理孤儿脚本进程
  - 缓存/台账只用于“同一 task_run_id 不重复执行”，不替代平台的任务状态机。

稳定性相关环境变量:
  WF_MAX_CONCURRENCY=4            并发执行上限，超出返回 429（带 Retry-After）
    WF_QUEUE_MAX_WAIT_SECONDS=30    block_strategy=queue 时最多排队等待秒数（同时不超过本次 timeout）
  WF_QUEUE_MAX_DEPTH=16           block_strategy=queue 的排队上限（0=不限制；满了立即 429，不再等待）
  WF_MAX_OUTPUT_BYTES=8388608     单流 stdout/stderr 捕获上限（超出截断并置 truncated）
  WF_RESULT_CACHE_TTL=86400       已完成结果缓存秒数
  WF_RESULT_CACHE_MAX_FILES=2000  结果缓存文件数上限
  WF_RESULT_CACHE_MAX_BYTES=536870912  结果缓存总字节上限（超出删最旧）
  WF_RESULT_CACHE_OUTPUT_CHARS=1000000  缓存里每条输出字符上限
  WF_PARTIAL_CODE=206             脚本 exit 1（部分成功）返回的 code：206（默认）/ 500（回退旧行为）
  WF_STRICT_FIELDS=0              /run 请求体里出现未知字段时：0=告警忽略，1=直接 400
  WF_READY_PROBE_TTL_SECONDS=300  /health/ready 写探测文件的缓存秒数（0=每次都写，旧行为）
  WF_ALLOWED_SCRIPT_ROOTS=        【追加】绝对路径白名单根目录（分号分隔）。
                                  默认只允许 WF_SCRIPTS_DIR 内的绝对路径（fail-closed）；
                                  需要执行 scripts 目录之外的绝对路径时，必须显式把根目录列在这里。
  WF_ALLOWED_SCRIPT_EXTENSIONS=".py,.pyw"
  WF_SCAN_ALLOWED_ROOTS=          【追加】/scan 允许扫描的目录（分号分隔）。
                                  默认只允许 WF_SCRIPTS_DIR；空值不再表示"不限制"。
  WF_SCAN_MAX_FILES=2000          /scan 单次返回文件数上限（超出置 truncated=true）
  WF_SCAN_MAX_DEPTH=8             /scan 递归深度上限（相对请求目录）
  WF_SCAN_ABSOLUTE_PATHS=0        /scan 默认返回相对路径（path 可直接回传 /run）；1=默认返回绝对路径
  WF_CANCEL_ON_DISCONNECT=1       客户端断开是否取消执行（默认 1）
  WF_SCRIPT_ENV_DENY="WF_SCRIPT_TOKEN"  不向业务脚本传递的服务级环境变量（逗号分隔）
  WF_SCRIPT_REQUIRE_TOKEN=0       为 1 时 token 为空直接拒绝启动
  WF_SCRIPT_ALLOW_INSECURE=0      为 1 时才允许“空 token + 非回环监听”（默认拒绝启动）
  WF_BIND_HOST=                   实际监听地址（分号/逗号分隔），仅用于安全判定与日志
  WF_SCRIPT_ENV_DENY              【追加】额外禁止注入的变量名（逗号分隔）

安全默认（2026-10-26 / 3.2.0）:
  - token 为空且监听地址不是回环地址时拒绝启动（除非 WF_SCRIPT_ALLOW_INSECURE=1）；
  - /run 的 env 拒绝注入服务级/危险变量（WF_SCRIPT_*、PYTHONPATH/PATH/COMSPEC/SystemRoot 等）；
  - --workers 必须为 1，检测到多 worker 直接拒绝启动；
  - POST /drain：排空模式，拒绝新任务并等待运行中任务结束（升级/重启前使用）。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import locale
import logging
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
import traceback
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, List, Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, field_validator

# ── 日志 ────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)
# 业务审计（一次运行一行 JSON）：由 deploy\serve.py 挂到 logs\script-server-audit.log
audit_logger = logging.getLogger("audit")

VERSION = "3.3.2"


def _audit(event: str, **fields) -> None:
    """写一行审计 JSON。审计失败绝不影响执行（吞掉异常）。"""
    payload = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "event": event,
        "pid": os.getpid(),
    }
    payload.update({k: v for k, v in fields.items() if v not in (None, "")})
    try:
        audit_logger.info(json.dumps(payload, ensure_ascii=False, default=str))
    except Exception:
        pass


def _short_hash(text: str) -> str:
    """参数摘要：审计里不落明文 argv（测评 P1：argv 泄漏），只留可对比的短哈希。"""
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:16]


def _client_ip(request: Optional[Request]) -> str:
    try:
        return request.client.host if request is not None and request.client else ""
    except Exception:
        return ""


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("环境变量 %s=%r 不是整数，使用默认值 %s", name, raw, default)
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


# ── 鉴权 ────────────────────────────────────────────
# WF_SCRIPT_TOKEN 为空 = 不校验（仅限回环地址；对外监听会拒绝启动，见下方安全默认）。
# 非空时 /run、/cancel、/scan、/tasks、/health/ready、/drain 必须带 X-Script-Token 头（或 Bearer）。
_SCRIPT_TOKEN = (os.environ.get("WF_SCRIPT_TOKEN") or "").strip()
_REQUIRE_TOKEN = _env_bool("WF_SCRIPT_REQUIRE_TOKEN", False)
if _REQUIRE_TOKEN and not _SCRIPT_TOKEN:
    raise RuntimeError(
        "WF_SCRIPT_REQUIRE_TOKEN=1 但 WF_SCRIPT_TOKEN 为空；拒绝以无鉴权方式启动"
    )


# ── 启动安全护栏（2026-10-26 P1-5）────────────────────
# 空 token 只允许监听回环地址；对外监听必须显式 WF_SCRIPT_ALLOW_INSECURE=1。
def _parse_bind_hosts() -> List[str]:
    hosts: List[str] = []
    raw = (os.environ.get("WF_BIND_HOST") or "").strip()
    if raw:
        hosts += [x.strip() for x in raw.replace(",", ";").split(";") if x.strip()]
    for i, arg in enumerate(sys.argv):
        if arg == "--host" and i + 1 < len(sys.argv):
            hosts.append(sys.argv[i + 1])
        elif arg.startswith("--host="):
            hosts.append(arg.split("=", 1)[1])
    if not hosts and __name__ == "__main__":
        hosts = ["0.0.0.0"]  # 直接 python script_server.py 时默认监听 0.0.0.0
    return hosts or ["127.0.0.1"]


def _is_loopback_host(host: str) -> bool:
    h = (host or "").strip().strip("[]").lower()
    return bool(h) and (h in ("localhost", "::1", "0:0:0:0:0:0:0:1") or h.startswith("127."))


_ALLOW_INSECURE = _env_bool("WF_SCRIPT_ALLOW_INSECURE", False)
_BIND_HOSTS = _parse_bind_hosts()
if not _SCRIPT_TOKEN and not _ALLOW_INSECURE:
    _public_hosts = [h for h in _BIND_HOSTS if not _is_loopback_host(h)]
    if _public_hosts:
        raise RuntimeError(
            "WF_SCRIPT_TOKEN 为空，但监听地址不是回环地址（%s）；拒绝以无鉴权方式对外启动。"
            "请设置 WF_SCRIPT_TOKEN（>=32 位随机串）并设置 WF_SCRIPT_REQUIRE_TOKEN=1；"
            "仅在隔离网络临时调试时才显式设置 WF_SCRIPT_ALLOW_INSECURE=1。"
            % ", ".join(dict.fromkeys(_public_hosts))
        )
if not _SCRIPT_TOKEN:
    logger.warning(
        "WF_SCRIPT_TOKEN 为空：本实例不校验鉴权（仅建议用于 127.0.0.1 调试）。"
        "生产环境请设置 token（≥32 位随机串）并设置 WF_SCRIPT_REQUIRE_TOKEN=1。"
    )


# ── 单 worker 护栏（2026-10-26 P1-3 / P2-15）────────────
# 进程内台账/并发槽/取消能力都依赖单进程；多 worker 会互杀在跑脚本、丢失取消能力。
def _parse_worker_count() -> int:
    count = 1
    for var in ("WEB_CONCURRENCY", "UVICORN_WORKERS"):
        raw = (os.environ.get(var) or "").strip()
        if raw.isdigit():
            count = max(count, int(raw))
    for i, arg in enumerate(sys.argv):
        if arg == "--workers" and i + 1 < len(sys.argv) and sys.argv[i + 1].isdigit():
            count = max(count, int(sys.argv[i + 1]))
        elif arg.startswith("--workers=") and arg.split("=", 1)[1].isdigit():
            count = max(count, int(arg.split("=", 1)[1]))
    return count


_WORKERS = _parse_worker_count()
if _WORKERS > 1:
    raise RuntimeError(
        "检测到 --workers=%d：script-server 必须单 worker 运行（--workers 1）。"
        "多 worker 会让 /tasks、/cancel、409 与并发上限各自为政，且启动对账会互杀在跑脚本。" % _WORKERS
    )


async def require_token(request: Request) -> None:
    """FastAPI 依赖：校验 X-Script-Token（未配置 token 时直接放行）。

    3.3.1：鉴权失败写审计并计数——token 等于主机控制权，失败尝试必须可追溯。
    """
    if not _SCRIPT_TOKEN:
        return
    import secrets

    supplied = (request.headers.get("X-Script-Token") or "").strip()
    if not supplied:
        auth = request.headers.get("Authorization") or ""
        if auth.startswith("Bearer "):
            supplied = auth[7:].strip()
    if not supplied:
        _bump("auth_failed")
        _audit("auth.fail", client=_client_ip(request), path=request.url.path,
               reason="missing_token")
        raise HTTPException(status_code=401, detail="invalid or missing X-Script-Token")
    if not secrets.compare_digest(supplied, _SCRIPT_TOKEN):
        _bump("auth_failed")
        _audit("auth.fail", client=_client_ip(request), path=request.url.path,
               reason="bad_token", token_len=len(supplied))
        raise HTTPException(status_code=401, detail="invalid or missing X-Script-Token")


# ── 脚本目录 ────────────────────────────────────────
# 统一 resolve()：路径比较必须基于真实路径，否则符号链接/相对片段会让 is_relative_to 失准。
_SCRIPTS_DIR = (
    Path(
        os.environ.get("WF_SCRIPTS_DIR")
        or Path(__file__).resolve().parent / "scripts"
    )
    .expanduser()
    .resolve()
)
_SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)

# ── 状态目录（结果缓存 + 运行中台账）───────────────
_STATE_DIR = Path(
    os.environ.get("WF_SCRIPT_STATE_DIR")
    or (_SCRIPTS_DIR.parent / ".script_server_state")
)
_RESULTS_DIR = _STATE_DIR / "results"
_RUNNING_FILE = _STATE_DIR / "running.json"
_STATE_OK = True
try:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
except OSError as _state_exc:  # 只读盘/权限问题：降级为无幂等模式，不影响执行
    _STATE_OK = False
    logger.error("状态目录不可写（%s）：幂等缓存/重启对账已禁用。原因: %s", _STATE_DIR, _state_exc)

# ── 资源上限 / 策略 ────────────────────────────────
_MAX_CONCURRENCY = max(1, _env_int("WF_MAX_CONCURRENCY", 4))
_QUEUE_MAX_WAIT = max(1, _env_int("WF_QUEUE_MAX_WAIT_SECONDS", 30))
# 排队上限：默认 16；0 = 不限制（不建议，排队请求会一直占着连接等空槽）。
_QUEUE_MAX_DEPTH = max(0, _env_int("WF_QUEUE_MAX_DEPTH", 16))
# 部分成功（脚本 exit 1）的 code：用 206 与“失败 500”区分（3.3.2 修 P2-3）。
# 平台若仍按 code==500 判定失败，可设 WF_PARTIAL_CODE=500 回退旧行为，无需改代码。
_PARTIAL_CODE = _env_int("WF_PARTIAL_CODE", 206)
if _PARTIAL_CODE not in (200, 206, 500):
    logger.warning("WF_PARTIAL_CODE=%s 不在 {200,206,500}，回退默认 206", _PARTIAL_CODE)
    _PARTIAL_CODE = 206
# 未知字段策略：0=告警忽略（兼容旧/新平台多传字段），1=400（P2-2，确认调用方后开启）
_STRICT_FIELDS = _env_bool("WF_STRICT_FIELDS", False)
# /health/ready 的写探测有成本（平台每 60s 轮询一次），结果按 TTL 缓存；0=每次都写
_READY_PROBE_TTL = max(0, _env_int("WF_READY_PROBE_TTL_SECONDS", 300))
# /scan 上限：文件数 / 递归深度 / 是否默认返回绝对路径
_SCAN_MAX_FILES = max(1, _env_int("WF_SCAN_MAX_FILES", 2000))
_SCAN_MAX_DEPTH = max(1, _env_int("WF_SCAN_MAX_DEPTH", 8))
_SCAN_ABSOLUTE_PATHS = _env_bool("WF_SCAN_ABSOLUTE_PATHS", False)
_MAX_OUTPUT_BYTES = max(64 * 1024, _env_int("WF_MAX_OUTPUT_BYTES", 8 * 1024 * 1024))
_RESULT_CACHE_TTL = max(60, _env_int("WF_RESULT_CACHE_TTL", 24 * 3600))
_RESULT_CACHE_MAX_FILES = max(10, _env_int("WF_RESULT_CACHE_MAX_FILES", 2000))
_RESULT_CACHE_MAX_BYTES = max(16 * 1024 * 1024, _env_int("WF_RESULT_CACHE_MAX_BYTES", 512 * 1024 * 1024))
_RESULT_CACHE_OUTPUT_CHARS = max(0, _env_int("WF_RESULT_CACHE_OUTPUT_CHARS", 1_000_000))
_CANCEL_ON_DISCONNECT = (os.environ.get("WF_CANCEL_ON_DISCONNECT") or "1").strip().lower() not in (
    "0", "false", "no", "off",
)


def _split_list(raw: str) -> List[str]:
    return [x.strip() for x in (raw or "").split(";") if x.strip()]


# 绝对路径白名单根目录：默认 = scripts 目录（fail-closed）。
# 修复（2026-10-23 评估 P1-04）：原实现里 _ALLOWED_ROOTS 为空表示"绝对路径无条件放行"，
# 实测 POST /run {"file_path": "<scripts 目录外的绝对路径>"} 直接 200 执行。
# 现在默认只允许 _SCRIPTS_DIR；确实需要跑别的目录时显式列 WF_ALLOWED_SCRIPT_ROOTS。
_EXTRA_ALLOWED_ROOTS = [
    Path(x).expanduser().resolve() for x in _split_list(os.environ.get("WF_ALLOWED_SCRIPT_ROOTS", ""))
]
_ALLOWED_ROOTS = [_SCRIPTS_DIR, *_EXTRA_ALLOWED_ROOTS]
_ALLOWED_EXTS = tuple(
    x.strip().lower()
    for x in (os.environ.get("WF_ALLOWED_SCRIPT_EXTENSIONS") or ".py,.pyw").split(",")
    if x.strip()
)
# /scan 目录白名单：同样 fail-closed（默认只允许 scripts 目录）
_EXTRA_SCAN_ROOTS = [
    Path(x).expanduser().resolve() for x in _split_list(os.environ.get("WF_SCAN_ALLOWED_ROOTS", ""))
]
_SCAN_ROOTS = [_SCRIPTS_DIR, *_EXTRA_SCAN_ROOTS]
_ENV_DENY = tuple(x.strip() for x in (os.environ.get("WF_SCRIPT_ENV_DENY") or "WF_SCRIPT_TOKEN").split(",") if x.strip())

# ── 请求 env 注入限制（2026-10-26 P1-1）───────────────
# 只放行业务变量；服务级变量、解释器/命令搜索路径变量一律拒绝，防止：
#   1) PYTHONPATH 指向脚本目录外 → sitecustomize.py 被执行（绕过路径白名单）；
#   2) PATH/COMSPEC 被劫持 → 脚本内部调用的外部命令被替换；
#   3) WF_SCRIPT_TOKEN / WF_SCRIPT_* 被注入子进程。
_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_HARD_DENY_ENV = frozenset({
    "WF_SCRIPT_TOKEN", "WF_SCRIPT_REQUIRE_TOKEN", "WF_SCRIPT_ALLOW_INSECURE", "WF_SCRIPT_ENV_DENY",
    "WF_SCRIPT_STATE_DIR", "WF_SCRIPTS_DIR", "WF_ALLOWED_SCRIPT_ROOTS", "WF_SCAN_ALLOWED_ROOTS",
    "WF_ALLOWED_SCRIPT_EXTENSIONS", "WF_BIND_HOST", "WEB_CONCURRENCY", "UVICORN_WORKERS",
    "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONUSERBASE", "PYTHONWARNINGS",
    "PYTHONINSPECT", "PYTHONEXECUTABLE", "PYTHONSAFEPATH",
    "PATH", "PATHEXT", "COMSPEC", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR",
    "LD_PRELOAD", "LD_LIBRARY_PATH", "DYLD_INSERT_LIBRARIES", "DYLD_LIBRARY_PATH",
    "NODE_OPTIONS", "JAVA_TOOL_OPTIONS", "CLASSPATH", "GIT_SSH_COMMAND",
    "BASH_ENV", "IFS", "PS4", "PROMPT_COMMAND",
})


def _sanitize_env_extra(env_extra: dict) -> dict:
    """校验请求里的 env 键；发现服务级/危险变量直接 400（fail-closed）。"""
    if not env_extra:
        return {}
    deny_upper = {str(x).upper() for x in _ENV_DENY if x}
    cleaned: dict = {}
    seen: dict = {}
    rejected: list = []
    for key, value in env_extra.items():
        if not isinstance(key, str) or not _ENV_KEY_RE.match(key):
            rejected.append(str(key))
            continue
        # 3.3.1：NUL 会让 CreateProcess 抛 ValueError（变成服务端异常），入口直接拒绝
        if not isinstance(value, str) or "\x00" in value:
            rejected.append(key)
            continue
        up = key.upper()
        if up in _HARD_DENY_ENV or up.startswith("WF_SCRIPT_") or up in deny_upper:
            rejected.append(key)
            continue
        if up in seen:
            raise HTTPException(400, f"env 变量在 Windows 下重复（不区分大小写）: {seen[up]} / {key}")
        seen[up] = key
        cleaned[key] = value
    if rejected:
        raise HTTPException(
            400,
            "不允许通过 env 注入的变量: " + ", ".join(sorted(set(rejected)))
            + "（服务级/解释器/命令搜索路径变量不允许由请求覆盖；业务变量请改用其它名字）",
        )
    return cleaned


def _strip_env(env: dict, names) -> None:
    """按名字（Windows 下不区分大小写）从 env 字典中删除变量。"""
    upper = {str(n).upper() for n in names if n}
    for key in [k for k in env if isinstance(k, str) and k.upper() in upper]:
        env.pop(key, None)

# ── 退出码 ──────────────────────────────────────────
_EXIT_LABELS = {0: "成功", 1: "部分成功", 2: "网络错误", 3: "脚本错误", 4: "脚本超时", 5: "任务取消"}
_KILL_FAILED_LABEL = "终止失败"


def _ok(code: int) -> bool:
    """仅退出码 0 为完全成功；1 是“部分成功”，由调用方单独标记。"""
    return code == 0


def _label(code: int) -> str:
    return _EXIT_LABELS.get(code, f"未知({code})")


# ── 运行指标（/health 暴露，便于无人值守排障）────────
_METRICS = {
    "requests": 0,
    "spawned": 0,
    "run_success": 0,
    "run_partial": 0,
    "run_failed": 0,
    "run_timeout": 0,
    "run_cancelled": 0,
    "run_kill_failed": 0,
    "rejected_busy": 0,
    "queued": 0,
    "queue_success": 0,
    "queue_timeout": 0,
    "queue_full_rejected": 0,
    "conflict_409": 0,
    "deduplicated": 0,
    "spawn_errors": 0,
    "kills": 0,
    "kill_failed": 0,
    "disconnect_cancelled": 0,
    "orphans_killed_at_startup": 0,
    "auth_failed": 0,
    "path_denied": 0,
    "drain_rejected": 0,
    "unknown_fields": 0,
    "invalid_fields": 0,
}


def _bump(key: str, n: int = 1) -> None:
    _METRICS[key] = _METRICS.get(key, 0) + n


# 排空模式（2026-10-26 P1-3）：升级/重启前调用 POST /drain，拒绝新任务并等待存量任务结束。
_DRAINING = False


# ── 任务台账 ────────────────────────────────────────

class _TaskHandle:
    """运行中任务的句柄（不用 dataclass：模块被 importlib 动态加载时 dataclass 会解析失败）。"""

    def __init__(
        self,
        task_id: str,
        script_path: Path,
        cmd: List[str],
        timeout_seconds: int,
        started_at: float,
        started_monotonic: float,
        args_len: int = 0,
        args_sha: str = "",
    ) -> None:
        self.task_id = task_id
        self.script_path = script_path
        self.cmd = cmd
        self.args_len = args_len
        self.args_sha = args_sha
        self.timeout_seconds = timeout_seconds
        self.started_at = started_at
        self.started_monotonic = started_monotonic
        self.process: Optional[asyncio.subprocess.Process] = None
        self.pid_create_time: Optional[float] = None
        self.cancel_event = asyncio.Event()
        self.cancel_reason = ""
        self.done = False
        self.slot_held = False
        self.kill_failed = False

    def request_cancel(self, reason: str) -> None:
        if self.done:
            return
        if not self.cancel_reason:
            self.cancel_reason = reason
        try:
            self.cancel_event.set()
        except RuntimeError:
            # event 绑定在别的 event loop（多 TestClient/多 loop 场景），尽力而为
            pass


task_manager: Dict[str, _TaskHandle] = {}
_state_lock = threading.RLock()
_slots_lock = threading.Lock()
_free_slots = _MAX_CONCURRENCY


def _try_acquire_slot() -> bool:
    global _free_slots
    with _slots_lock:
        if _free_slots <= 0:
            return False
        _free_slots -= 1
        return True


def _release_slot() -> None:
    global _free_slots
    with _slots_lock:
        _free_slots = min(_MAX_CONCURRENCY, _free_slots + 1)


def _reset_slots() -> None:
    global _free_slots
    with _slots_lock:
        _free_slots = _MAX_CONCURRENCY


# ── 排队登记（3.3.1）────────────────────────────────
# 只有 block_strategy=queue 且没抢到槽的请求才登记；/tasks 能直接看到“在等槽”的请求，
# 不再出现“槽满被拒前完全不可见”的黑盒。上限由 WF_QUEUE_MAX_DEPTH 控制。
_queue_lock = threading.Lock()
_queue_waiters: Dict[str, dict] = {}


def _queue_enter(task_id: str, script: str) -> Optional[str]:
    """登记一个排队请求；队列已满返回 None。"""
    key = uuid.uuid4().hex
    with _queue_lock:
        if _QUEUE_MAX_DEPTH and len(_queue_waiters) >= _QUEUE_MAX_DEPTH:
            return None
        _queue_waiters[key] = {"task_id": task_id, "script": script, "since": time.monotonic()}
    return key


def _queue_leave(key: Optional[str]) -> None:
    if not key:
        return
    with _queue_lock:
        _queue_waiters.pop(key, None)


def _queue_depth() -> int:
    with _queue_lock:
        return len(_queue_waiters)


def _queue_is_head(key: Optional[str]) -> bool:
    """只有队头才能抢空槽 → 严格 FIFO（3.3.2 修 P2-6）。

    dict 保持插入顺序（CPython 3.7+），队头 = 最早登记的等待者。
    """
    if not key:
        return True
    with _queue_lock:
        first = next(iter(_queue_waiters), None)
    return first == key


def _queue_snapshot() -> List[dict]:
    now = time.monotonic()
    with _queue_lock:
        return [
            {
                "task_id": item["task_id"],
                "script": item["script"],
                "waited_seconds": round(now - item["since"], 1),
                "position": index + 1,
            }
            for index, item in enumerate(_queue_waiters.values())
        ]


def _active_handles() -> List[_TaskHandle]:
    with _state_lock:
        return [h for h in task_manager.values() if not h.done]


def _prune_finished_handles() -> None:
    """清理已结束的台账。注意：只有 /run 已经完成收尾（done=True）或 kill 失败后
    进程自行退出的记录才移除——避免“进程刚退出、缓存还没写”的重复执行窗口（复审 N5）。"""
    removed: list[_TaskHandle] = []
    with _state_lock:
        for task_id, handle in list(task_manager.items()):
            if handle.process is None:
                if handle.done:
                    removed.append(handle)
                    task_manager.pop(task_id, None)
                continue
            if handle.process.returncode is None:
                continue
            if handle.done or handle.kill_failed:
                removed.append(handle)
                task_manager.pop(task_id, None)
    for handle in removed:
        if handle.slot_held:
            handle.slot_held = False
            _release_slot()


def _drop_handle(task_id: str, handle: _TaskHandle) -> bool:
    with _state_lock:
        if task_manager.get(task_id) is handle:
            task_manager.pop(task_id, None)
            return True
    return False


# ── 持久化：running.json / 结果缓存 ─────────────────

_atomic_write_lock = threading.Lock()


def _atomic_write_json(path: Path, payload) -> None:
    """原子写 JSON。临时文件名必须唯一：多线程/多 event loop 下会并发写（测试/多客户端）。"""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    with _atomic_write_lock:
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)


def _snapshot_running() -> dict:
    with _state_lock:
        return {
            task_id: {
                "pid": handle.process.pid if handle.process is not None else None,
                "create_time": handle.pid_create_time,
                "started_at": handle.started_at,
                "script": str(handle.script_path),
                "timeout_seconds": handle.timeout_seconds,
                # 3.3.1：台账不再落完整 argv（可能含敏感参数），只留可对照的摘要
                "args_len": handle.args_len,
                "args_sha": handle.args_sha,
            }
            for task_id, handle in task_manager.items()
        }


async def _persist_running() -> None:
    if not _STATE_OK:
        return
    try:
        snapshot = _snapshot_running()
        await asyncio.to_thread(_atomic_write_json, _RUNNING_FILE, snapshot)
    except Exception as exc:  # 持久化失败不能影响任务执行
        logger.warning("写 running.json 失败: %s", exc)


def _cache_path(task_id: str) -> Path:
    digest = hashlib.sha1(task_id.encode("utf-8")).hexdigest()
    return _RESULTS_DIR / f"{digest}.json"


def _trim_output(text: str) -> str:
    limit = _RESULT_CACHE_OUTPUT_CHARS
    if limit <= 0 or len(text) <= limit:
        return text
    head = int(limit * 0.6)
    tail = limit - head
    return text[:head] + "\n…（缓存截断）…\n" + text[-tail:]


def _cache_get_sync(task_id: str, fingerprint: str) -> Optional[dict]:
    if not _STATE_OK:
        return None
    path = _cache_path(task_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        logger.warning("读取结果缓存失败 %s: %s", path.name, exc)
        return None
    cached_at = float(data.get("cached_at") or 0)
    if time.time() - cached_at > _RESULT_CACHE_TTL:
        try:
            path.unlink()
        except OSError:
            pass
        return None
    stored_fingerprint = data.get("fingerprint")
    if stored_fingerprint is not None and stored_fingerprint != fingerprint:
        # 同一 task_id 但请求参数不同：不能复用旧结果（复审 N9）
        logger.warning("任务 %s 命中缓存但请求指纹不一致，重新执行", task_id)
        return None
    response = data.get("response")
    return response if isinstance(response, dict) else None


async def _cache_get(task_id: str, fingerprint: str) -> Optional[dict]:
    return await asyncio.to_thread(_cache_get_sync, task_id, fingerprint)


def _cache_cleanup_sync() -> None:
    if not _STATE_OK:
        return
    now = time.time()
    entries = []
    try:
        for p in _RESULTS_DIR.glob("*.json"):
            try:
                st = p.stat()
            except OSError:
                continue
            entries.append((st.st_mtime, st.st_size, p))
    except OSError:
        return
    # 1) TTL 过期删除
    alive = []
    for mtime, size, p in entries:
        if now - mtime > _RESULT_CACHE_TTL:
            try:
                p.unlink()
            except OSError:
                pass
        else:
            alive.append((mtime, size, p))
    # 2) 数量/总字节上限：从最旧开始删，直到都满足
    alive.sort(key=lambda item: item[0])
    total_bytes = sum(item[1] for item in alive)
    while alive and (len(alive) > _RESULT_CACHE_MAX_FILES or total_bytes > _RESULT_CACHE_MAX_BYTES):
        _, size, p = alive.pop(0)
        try:
            p.unlink()
        except OSError:
            pass
        total_bytes -= size


_cache_put_counter = 0


def _cache_put_sync(task_id: str, response: dict, fingerprint: str) -> None:
    trimmed_stdout = _trim_output(response.get("stdout") or "")
    trimmed_stderr = _trim_output(response.get("stderr") or "")
    cut = trimmed_stdout != (response.get("stdout") or "") or trimmed_stderr != (response.get("stderr") or "")
    payload = {
        "cached_at": time.time(),
        "task_id": task_id,
        "fingerprint": fingerprint,
        "response": {
            **response,
            "stdout": trimmed_stdout,
            "stderr": trimmed_stderr,
            # 缓存裁剪过输出时也必须让调用方知道（复审 N9）
            "truncated": bool(response.get("truncated")) or cut,
        },
    }
    _atomic_write_json(_cache_path(task_id), payload)


async def _cache_put(task_id: str, response: dict, fingerprint: str) -> None:
    global _cache_put_counter
    if not _STATE_OK:
        return
    try:
        await asyncio.to_thread(_cache_put_sync, task_id, response, fingerprint)
        _cache_put_counter += 1
        if _cache_put_counter % 25 == 0:
            await asyncio.to_thread(_cache_cleanup_sync)
    except Exception as exc:
        logger.warning("写结果缓存失败（task_id=%s）: %s", task_id, exc)


def _file_signature(path: Path, with_digest: bool = True) -> dict:
    """脚本内容指纹：脚本改过之后，同一 task_run_id 不能复用旧结果（修测评「幂等缓存陈旧」）。

    with_digest=False 时只 stat 不读文件（3.3.2 修 P2-7）：没传 task_run_id 的请求根本不做幂等，
    没必要每次把整份脚本读一遍算 sha256。
    """
    try:
        st = path.stat()
        sha = ""
        if with_digest:
            digest = hashlib.sha256()
            with path.open("rb") as fh:
                for chunk in iter(lambda: fh.read(256 * 1024), b""):
                    digest.update(chunk)
            sha = digest.hexdigest()
        return {"sha256": sha, "size": st.st_size, "mtime_ns": st.st_mtime_ns}
    except OSError as exc:
        logger.warning("计算脚本内容指纹失败（%s）: %s", path, exc)
        return {"sha256": "", "size": 0, "mtime_ns": 0}


def _request_fingerprint(req: "RunRequest", script_path: Path, script_sig: dict) -> str:
    """请求指纹：同 task_run_id 但脚本内容/路径/args/env/超时变了就不能复用旧结果。"""
    payload = json.dumps(
        {
            "file_path": str(script_path),
            "script": script_sig,
            "args": req.args,
            "env": dict(sorted((req.env or {}).items())),
            "timeout_seconds": req.timeout_seconds,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ── 模型 ────────────────────────────────────────────

class RunRequest(BaseModel):
    # 允许未知字段（旧/新平台可能多传），但绝不静默忽略（3.3.2 修 P2-2）：
    #   - 常见错拼/近似字段名（timeout、filepath、argv…）→ 400 并提示正确字段；
    #   - 其它未知字段 → WARNING + 审计记录 + 计数；WF_STRICT_FIELDS=1 时改为 400。
    model_config = ConfigDict(extra="allow")

    file_path: str
    args: str = ""
    env: dict[str, str] = {}
    timeout_seconds: int = 300
    task_run_id: str = ""  # 可选的任务ID，用于取消追踪与幂等
    block_strategy: Literal["reject", "queue"] = "reject"  # 并发满时的处理策略

    @field_validator("timeout_seconds")
    @classmethod
    def validate_timeout(cls, v: int) -> int:
        if v < 1 or v > 7200:
            raise ValueError("超时时间必须在 1-7200 秒之间")
        return v


# 常见错拼/近似字段 → 正确字段（3.3.2 P2-2）：这类错误过去被静默忽略，
# 典型后果是 {"timeout": 1} 被忽略后按默认 300s 跑，平台以为 1s 就超时。
_FIELD_ALIASES = {
    "timeout": "timeout_seconds",
    "timeout_sec": "timeout_seconds",
    "timeout_seconds_": "timeout_seconds",
    "timeout_ms": "timeout_seconds（本接口只接受秒，请换算）",
    "file": "file_path",
    "filepath": "file_path",
    "filePath": "file_path",
    "script": "file_path",
    "script_path": "file_path",
    "scriptPath": "file_path",
    "path": "file_path",
    "argv": "args",
    "arguments": "args",
    "command_args": "args",
    "environment": "env",
    "envs": "env",
    "task_id": "task_run_id",
    "run_id": "task_run_id",
    "taskId": "task_run_id",
    "block": "block_strategy",
    "strategy": "block_strategy",
}


class RunResponse(BaseModel):
    task_id: str
    code: int = 200          # 200 成功 / 206 部分成功 / 500 失败或执行器错误 / 502 超时
    exit_code: int
    stdout: str
    stderr: str
    success: bool
    partial: bool = False
    status_label: str
    error_kind: str = "ok"   # ok / partial / script_error / timeout / cancelled / executor_error
    cancelled: bool = False
    timed_out: bool = False
    deduplicated: bool = False
    truncated: bool = False
    duration_seconds: float = 0.0


def _audit_finish(response: "RunResponse", **fields) -> None:
    """一次运行结束的审计行（成功/失败/超时/取消统一形状，便于日志检索与对账）。"""
    _audit(
        "run.finish",
        run_id=response.task_id,
        code=response.code,
        exit_code=response.exit_code,
        status=response.status_label,
        success=response.success,
        partial=response.partial,
        error_kind=response.error_kind,
        cancelled=response.cancelled,
        timed_out=response.timed_out,
        truncated=response.truncated,
        duration_ms=round(response.duration_seconds * 1000, 1),
        **fields,
    )


# ── 辅助 ────────────────────────────────────────────

def _check_unknown_fields(req: "RunRequest", client_ip: str) -> List[str]:
    """校验请求体里的未知字段（3.3.2 修 P2-2）。

    返回仍旧被忽略的未知字段名（用于审计），并可能抛 400。
    """
    extra = sorted((req.model_extra or {}).keys())
    if not extra:
        return []
    hints = {name: _FIELD_ALIASES[name] for name in extra if name in _FIELD_ALIASES}
    if hints:
        _bump("invalid_fields")
        _audit("run.reject", reason="unknown_field", client=client_ip,
               run_id=(req.task_run_id or "").strip(), fields=extra, hints=hints)
        detail = "；".join(f"{k} → {v}" for k, v in hints.items())
        raise HTTPException(
            400,
            f"请求字段无法识别（疑似写错）：{detail}。请改用上表右侧的字段名。",
        )
    if _STRICT_FIELDS:
        _bump("invalid_fields")
        _audit("run.reject", reason="unknown_field", client=client_ip,
               run_id=(req.task_run_id or "").strip(), fields=extra, strict=True)
        raise HTTPException(400, f"请求包含未知字段：{', '.join(extra)}（WF_STRICT_FIELDS=1）")
    _bump("unknown_fields")
    logger.warning(
        "POST /run 收到未识别字段（已忽略）: %s；若本应是有效字段，请升级调用方"
        "（设 WF_STRICT_FIELDS=1 可让未知字段直接 400）", ", ".join(extra),
    )
    return extra

def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import ctypes

            process_query_limited_information = 0x1000
            still_active = 259
            handle = ctypes.windll.kernel32.OpenProcess(
                process_query_limited_information, False, int(pid)
            )
            if not handle:
                return False
            try:
                code = ctypes.c_ulong()
                ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
                return bool(ok) and code.value == still_active
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _pid_create_time(pid: int) -> Optional[float]:
    """进程创建时间（Unix 秒）。用于启动对账时防 PID 复用误杀无关进程。"""
    if pid <= 0:
        return None
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            process_query_limited_information = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(
                process_query_limited_information, False, int(pid)
            )
            if not handle:
                return None
            try:
                creation = wintypes.FILETIME()
                exit_time = wintypes.FILETIME()
                kernel_time = wintypes.FILETIME()
                user_time = wintypes.FILETIME()
                ok = ctypes.windll.kernel32.GetProcessTimes(
                    handle,
                    ctypes.byref(creation),
                    ctypes.byref(exit_time),
                    ctypes.byref(kernel_time),
                    ctypes.byref(user_time),
                )
                if not ok:
                    return None
                ticks = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
                return ticks / 10_000_000.0 - 11644473600.0
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            return None
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
        fields = stat.rsplit(")", 1)[1].split()
        start_ticks = float(fields[19])
        btime = None
        with open("/proc/stat", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("btime "):
                    btime = float(line.split()[1])
                    break
        if btime is None:
            return None
        return btime + start_ticks / os.sysconf("SC_CLK_TCK")
    except Exception:
        return None


def _kill_pid_tree_sync(pid: int) -> bool:
    """同步杀掉 pid 的整个进程树（供 to_thread 调用）。"""
    if not _pid_alive(pid):
        return True
    try:
        if sys.platform == "win32":
            result = subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True, timeout=10,
            )
            return result.returncode == 0
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        except Exception:
            os.kill(pid, signal.SIGKILL)
        return True
    except Exception as exc:
        logger.error("kill pid tree %s 失败: %s", pid, exc)
        return False


async def _kill_process_tree(process: Optional[asyncio.subprocess.Process]) -> bool:
    """终止进程树；返回是否确认主进程已退出。"""
    if process is None:
        return True
    if process.returncode is not None:
        return True
    _bump("kills")
    pid = process.pid
    logger.info("正在终止进程树: PID %s", pid)
    try:
        await asyncio.to_thread(_kill_pid_tree_sync, pid)
    except Exception as exc:
        logger.error("终止进程树 %s 异常: %s", pid, exc)
    # 兜底：确保主进程退出
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        except Exception as exc:
            logger.warning("process.kill() 失败 pid=%s: %s", pid, exc)
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except asyncio.TimeoutError:
        logger.error("PID %s 在 kill 后 5s 仍未退出", pid)
        _bump("kill_failed")
        return False
    except Exception:
        pass
    if process.returncode is not None:
        logger.info("进程树已终止: PID %s (returncode=%s)", pid, process.returncode)
    return process.returncode is not None


def _check_extension(path: Path) -> None:
    if _ALLOWED_EXTS and path.suffix.lower() not in _ALLOWED_EXTS:
        raise HTTPException(
            403,
            f"不允许的脚本类型: {path.suffix or '(无扩展名)'}；允许: {', '.join(_ALLOWED_EXTS)}",
        )


def _resolve_path(raw: str) -> Path:
    """解析脚本路径。

    相对路径：基于 _SCRIPTS_DIR 拼接，禁止逃逸（Path.is_relative_to）。
    绝对路径：默认只允许 _SCRIPTS_DIR 内（fail-closed）；其它目录需配置
    WF_ALLOWED_SCRIPT_ROOTS（分号分隔多个根）。
    两类路径都会校验扩展名（默认 .py/.pyw）。
    """
    p = Path(raw)
    if not p.is_absolute():
        p = (_SCRIPTS_DIR / p).resolve()
        if not p.is_relative_to(_SCRIPTS_DIR.resolve()):
            raise HTTPException(403, "脚本路径不在允许的目录内")
        _check_extension(p)
        return p

    p = p.resolve()
    if not any(p.is_relative_to(root) for root in _ALLOWED_ROOTS):
        raise HTTPException(
            403,
            "脚本绝对路径不在允许的根目录内：默认只允许 WF_SCRIPTS_DIR 下的绝对路径。"
            "如需执行其它目录的脚本，请设置 WF_ALLOWED_SCRIPT_ROOTS（分号分隔多个根）后重启 script-server。",
        )
    _check_extension(p)
    return p


def _split_args_windows(raw: str) -> List[str]:
    """Windows 参数切分：空格/制表符分隔，双引号或单引号包裹可含空格，引号本身去掉，
    反斜杠原样保留。

    不能用 shlex.split(posix=True)：它会把 C:\\temp\\new 变成 C:tempnew。
    同时兼容旧 shlex 的单引号分组行为（复审 N7）。

    3.3.2 修 P2-1：`a "" b` 必须得到 ['a', '', 'b']。旧实现只在 current 非空时入栈，
    空引号产生的空 token 会被静默丢弃，导致脚本收到的 argv 少一位、后续参数整体错位。
    """
    args: List[str] = []
    current: List[str] = []
    quote: Optional[str] = None
    started = False  # 当前 token 是否已经开始（含空引号，用于区分“空参数”与“没有参数”）
    for ch in raw:
        if quote is not None:
            if ch == quote:
                quote = None
            else:
                current.append(ch)
            continue
        if ch in ("'", '"'):
            quote = ch
            started = True
            continue
        if ch in " \t\r\n":
            if started:
                args.append("".join(current))
                current = []
                started = False
            continue
        current.append(ch)
        started = True
    if started:
        args.append("".join(current))
    if quote is not None:
        raise HTTPException(400, "args 解析失败：引号未闭合")
    return args


def _split_args(raw: str) -> List[str]:
    if not raw:
        return []
    if sys.platform != "win32":
        try:
            return shlex.split(raw)
        except ValueError as exc:
            raise HTTPException(400, f"args 解析失败: {exc}")
    return _split_args_windows(raw)


def _decode_output(data: bytes | None) -> str:
    """智能解码子进程输出，自动适配 UTF-8 / GBK。

    顺序很重要：子脚本默认被注入 PYTHONIOENCODING=utf-8，所以先按 UTF-8 严格解码；
    老脚本若自己按 GBK（Windows 中文默认）写字节，UTF-8 严格解码会失败再回落 GBK。
    反过来先试 GBK 会把 UTF-8 字节解成乱码却不报错。
    """
    if not data:
        return ""

    candidates = [
        ("utf-8", None),      # 现代默认（script-server 给子进程注入 PYTHONIOENCODING=utf-8）
        ("gbk", None),        # Windows 中文旧脚本
        ("gb18030", None),    # 完整 GB18030
        ("utf-8", "replace"), # 兜底
    ]
    for enc, errors in candidates:
        try:
            return data.decode(enc, errors=errors) if errors else data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue

    return data.decode("utf-8", errors="replace")


def _get_task_id(req: RunRequest) -> str:
    """优先使用客户端传来的 task_run_id，否则自动生成。"""
    return (req.task_run_id or "").strip() or uuid.uuid4().hex


# ── 流式输出捕获（有上限，避免大输出打爆内存）────────

class _StreamSink:
    def __init__(self) -> None:
        self.buffer = bytearray()
        self.total = 0
        self.truncated = False


async def _read_stream(reader: asyncio.StreamReader, sink: _StreamSink, cap: int) -> None:
    while True:
        try:
            chunk = await reader.read(65536)
        except asyncio.CancelledError:
            raise
        except Exception:
            return
        if not chunk:
            return
        sink.total += len(chunk)
        if len(sink.buffer) < cap:
            sink.buffer.extend(chunk[: cap - len(sink.buffer)])
        if sink.total > cap:
            sink.truncated = True


async def _drain_readers(tasks: List[asyncio.Task], timeout: float = 10.0) -> None:
    if not tasks:
        return
    try:
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning("读取子进程输出超时（可能有孙进程持有管道），已取消读取")
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _watch_disconnect(request: Optional[Request], handle: _TaskHandle) -> None:
    if not _CANCEL_ON_DISCONNECT or request is None:
        return
    while not handle.done:
        await asyncio.sleep(1.0)
        if handle.done:
            return
        try:
            if await request.is_disconnected():
                logger.info("客户端断开，取消任务 %s", handle.task_id)
                _bump("disconnect_cancelled")
                handle.request_cancel("client_disconnect")
                return
        except asyncio.CancelledError:
            raise
        except Exception:
            return


# ── 启动对账 ────────────────────────────────────────

async def _reconcile_orphans() -> None:
    """进程重启后清理上一进程遗留的脚本进程（running.json 台账）。"""
    if not _STATE_OK:
        return
    try:
        raw = await asyncio.to_thread(_RUNNING_FILE.read_text, "utf-8")
    except FileNotFoundError:
        return
    except Exception as exc:
        logger.warning("读取 running.json 失败: %s", exc)
        return
    try:
        records = json.loads(raw)
    except ValueError:
        logger.warning("running.json 损坏，已忽略")
        records = {}
    killed = 0
    for task_id, rec in (records or {}).items():
        rec = rec or {}
        try:
            pid = int(rec.get("pid") or 0)
        except (TypeError, ValueError):
            continue
        if pid <= 0 or not _pid_alive(pid):
            continue
        # 防 PID 复用误杀：创建时间必须与台账记录一致（2s 容差）。
        try:
            stored_create = float(rec.get("create_time"))
        except (TypeError, ValueError):
            stored_create = None
        current_create = await asyncio.to_thread(_pid_create_time, pid)
        if stored_create is None or current_create is None:
            logger.warning(
                "启动对账：无法校验 PID %s 的创建时间（台账或系统不支持），跳过清理以防误杀", pid
            )
            continue
        if abs(current_create - stored_create) > 2.0:
            logger.warning(
                "启动对账：PID %s 已被系统复用（创建时间不匹配），跳过清理", pid
            )
            continue
        logger.warning("发现上次进程遗留的脚本进程（task=%s pid=%s），正在清理", task_id, pid)
        if await asyncio.to_thread(_kill_pid_tree_sync, pid):
            killed += 1
    if killed:
        _bump("orphans_killed_at_startup", killed)
        logger.warning("启动对账：清理了 %s 个孤儿脚本进程", killed)
    try:
        await asyncio.to_thread(_RUNNING_FILE.unlink)
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning("删除 running.json 失败: %s", exc)


# ── App ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理：启动对账 + 关闭清理。"""
    logger.info(
        "script-server %s 启动: scripts=%s state=%s concurrency=%s cancel_on_disconnect=%s",
        VERSION, _SCRIPTS_DIR, _STATE_DIR, _MAX_CONCURRENCY, _CANCEL_ON_DISCONNECT,
    )
    _reset_slots()
    if not _STATE_OK:
        logger.warning("状态目录不可用：幂等与重启对账降级关闭")
    else:
        await _reconcile_orphans()
    yield
    handles = _active_handles()
    if not handles:
        await _persist_running()
        _reset_slots()
        return
    logger.info("正在清理 %s 个运行中的任务...", len(handles))

    async def _kill_one(handle: _TaskHandle) -> None:
        handle.request_cancel("shutdown")
        try:
            await _kill_process_tree(handle.process)
        except Exception as exc:
            logger.error("终止任务 %s 失败: %s", handle.task_id, exc)
        handle.done = True

    try:
        await asyncio.wait_for(
            asyncio.gather(*[_kill_one(h) for h in handles], return_exceptions=True),
            timeout=8.0,
        )
    except asyncio.TimeoutError:
        logger.error("关闭清理超时，仍有任务可能未终止")
    with _state_lock:
        task_manager.clear()
    _reset_slots()
    await _persist_running()


app = FastAPI(title="Script Runner", version=VERSION, lifespan=lifespan)


def _uptime_seconds() -> float:
    return round(time.time() - _STARTED_AT, 1)


_STARTED_AT = time.time()


# /health/ready 的写探测：平台每 60s 轮询一次，每次都写盘没有意义（3.3.2 修 P2-5）。
# 结果按 WF_READY_PROBE_TTL_SECONDS 缓存；0 = 每次都写（旧行为）。
_ready_probe_lock = threading.Lock()
_ready_probe_cache: Dict[str, float] = {"ok": -1.0, "at": 0.0}


def _scripts_writable() -> tuple[bool, bool]:
    """返回 (scripts 目录可写, 是否命中缓存)。"""
    now = time.monotonic()
    with _ready_probe_lock:
        cached = _ready_probe_cache["ok"]
        if cached >= 0 and (now - _ready_probe_cache["at"]) < _READY_PROBE_TTL:
            return cached > 0, True
        ok = False
        try:
            probe = _SCRIPTS_DIR / f".write_probe_{os.getpid()}"
            probe.write_text("x", encoding="utf-8")
            probe.unlink()
            ok = True
        except OSError:
            ok = False
        _ready_probe_cache["ok"] = 1.0 if ok else 0.0
        _ready_probe_cache["at"] = now
        return ok, False


@app.get("/health")
async def health():
    """公开探活：只返回状态/版本/运行中数量/计数，不泄露内部路径。"""
    return {
        "status": "ok",
        "version": VERSION,
        "auth_required": bool(_SCRIPT_TOKEN),
        "draining": _DRAINING,
        "running_tasks": len(_active_handles()),
        "queue": {"depth": _queue_depth(), "limit": _QUEUE_MAX_DEPTH},
        "uptime_seconds": _uptime_seconds(),
        "counters": dict(_METRICS),
    }


@app.get("/health/ready", dependencies=[Depends(require_token)])
async def health_ready():
    """鉴权就绪检查：能执行脚本吗？（解释器/脚本目录/状态目录/缓存/排空）

    scripts 目录可写性用一次性写探测，结果按 WF_READY_PROBE_TTL_SECONDS 缓存（3.3.2 修 P2-5）。
    """
    scripts_ok = _SCRIPTS_DIR.is_dir()
    scripts_writable = False
    writable_from_cache = False
    if scripts_ok:
        scripts_writable, writable_from_cache = _scripts_writable()
    python_ok = Path(sys.executable).exists()
    cache_files = 0
    if _STATE_OK:
        try:
            cache_files = sum(1 for _ in _RESULTS_DIR.glob("*.json"))
        except OSError:
            pass
    checks = {
        "scripts_dir": {
            "path": str(_SCRIPTS_DIR),
            "exists": scripts_ok,
            "writable": scripts_writable,
            "writable_from_cache": writable_from_cache,
            "probe_ttl_seconds": _READY_PROBE_TTL,
        },
        "state_dir": {"path": str(_STATE_DIR), "enabled": _STATE_OK},
        "python": {"path": sys.executable, "exists": python_ok},
        "result_cache_files": cache_files,
        "concurrency": {"limit": _MAX_CONCURRENCY, "running": len(_active_handles())},
        "locale": locale.getpreferredencoding(),
    }
    # 修复 P1-4：状态目录不可用（幂等/对账已静默关闭）必须体现在就绪状态里；
    # 排空中也不再接受新任务，同样标记 degraded。
    ok = scripts_ok and scripts_writable and python_ok and _STATE_OK and not _DRAINING
    return {
        "status": "ok" if ok else "degraded",
        "version": VERSION,
        "auth_required": bool(_SCRIPT_TOKEN),
        "draining": _DRAINING,
        "running_tasks": len(_active_handles()),
        "queue": {"depth": _queue_depth(), "limit": _QUEUE_MAX_DEPTH},
        "uptime_seconds": _uptime_seconds(),
        "checks": checks,
        "counters": dict(_METRICS),
    }


@app.post("/drain", dependencies=[Depends(require_token)])
async def drain(timeout: int = 30):
    """排空模式：拒绝新任务并等待运行中任务结束（升级/重启前调用；重启后解除）。

    timeout 为最长等待秒数（0-3600，默认 30）；返回时 drained=false 表示还有任务未结束，
    可继续等待后再次调用，或按业务决定强制重启。
    """
    global _DRAINING
    _DRAINING = True
    timeout = max(0, min(int(timeout), 3600))
    deadline = time.monotonic() + timeout
    while True:
        running = len(_active_handles())
        if running == 0 or time.monotonic() >= deadline:
            break
        await asyncio.sleep(0.5)
    running = len(_active_handles())
    logger.warning("进入排空模式：等待结束的运行中任务=%s（timeout=%ss）", running, timeout)
    return {
        "draining": True,
        "running_tasks": running,
        "drained": running == 0,
        "timeout_seconds": timeout,
    }


def _scan_entry(file: Path, scan_root: Path, include_absolute: bool) -> dict:
    """单个扫描条目：relative 相对“本次请求的目录”，path 可直接回传给 /run。"""
    try:
        resolved = file.resolve()
    except OSError:
        resolved = file
    if resolved.is_relative_to(scan_root):
        rel = resolved.relative_to(scan_root).as_posix()
    else:
        rel = file.name
    entry = {"name": file.name, "relative": rel}
    if include_absolute or _SCAN_ABSOLUTE_PATHS:
        entry["path"] = str(resolved)
    else:
        try:
            entry["path"] = resolved.relative_to(_SCRIPTS_DIR).as_posix()
        except ValueError:
            # 不在 scripts 目录内（额外扫描根）：绝对路径是唯一能回传 /run 的形式
            entry["path"] = str(resolved)
    try:
        st = resolved.stat()
        entry["size_bytes"] = st.st_size
        entry["mtime"] = round(st.st_mtime, 3)
    except OSError:
        entry["size_bytes"] = 0
        entry["mtime"] = 0.0
    return entry


@app.post("/scan", dependencies=[Depends(require_token)])
async def scan_directory(dir_path: str = "", recursive: bool = True,
                         include_absolute: bool = False, request: Request = None):
    """扫描目录，返回脚本列表（3.3.2 修 P2-4）。

    - recursive：默认 True，递归子目录（跳过隐藏目录/__pycache__/.git/venv，不跟随符号链接），
      深度上限 WF_SCAN_MAX_DEPTH，文件数上限 WF_SCAN_MAX_FILES（超出 truncated=true）；
    - 默认只返回相对路径（不泄露主机目录结构），`path` 可直接回传给 /run；
    - include_absolute=true（或 WF_SCAN_ABSOLUTE_PATHS=1）时额外返回绝对路径，兼容旧调用方。
    """
    result = {"files": [], "count": 0, "truncated": False, "recursive": recursive}
    if not dir_path:
        return result
    p = Path(dir_path)
    if not p.exists():
        raise HTTPException(400, f"目录不存在: {dir_path}")
    if not p.is_dir():
        raise HTTPException(400, f"不是目录: {dir_path}")
    resolved = p.resolve()
    if not any(resolved.is_relative_to(root) for root in _SCAN_ROOTS):
        _bump("path_denied")
        _audit("path.deny", client=_client_ip(request), script=str(dir_path)[:200],
               reason="scan_root")
        raise HTTPException(
            403,
            "目录不在允许的扫描根内：默认只允许 WF_SCRIPTS_DIR。"
            "如需扫描其它目录，请设置 WF_SCAN_ALLOWED_ROOTS（分号分隔多个根）后重启 script-server。",
        )

    exts = tuple(_ALLOWED_EXTS) or (".py",)
    skip_dirs = {"__pycache__", ".git", ".svn", ".hg", "node_modules", ".venv", "venv", "site-packages"}
    files: List[dict] = []
    truncated = False
    if recursive:
        base_depth = len(resolved.parts)
        for dirpath, dirnames, filenames in os.walk(resolved, followlinks=False):
            current = Path(dirpath)
            if len(current.parts) - base_depth >= _SCAN_MAX_DEPTH:
                dirnames[:] = []  # 到深度上限：不再往下走
            else:
                dirnames[:] = sorted(
                    d for d in dirnames if not d.startswith(".") and d.lower() not in skip_dirs
                )
            for name in sorted(filenames):
                if name.startswith(".") or Path(name).suffix.lower() not in exts:
                    continue
                files.append(_scan_entry(current / name, resolved, include_absolute))
                if len(files) >= _SCAN_MAX_FILES:
                    truncated = True
                    break
            if truncated:
                break
    else:
        for f in sorted(resolved.glob("*")):
            try:
                if not f.is_file():
                    continue
            except OSError:
                continue
            if f.name.startswith(".") or f.suffix.lower() not in exts:
                continue
            files.append(_scan_entry(f, resolved, include_absolute))
            if len(files) >= _SCAN_MAX_FILES:
                truncated = True
                break
    files.sort(key=lambda item: item["relative"].lower())
    result.update({"files": files, "count": len(files), "truncated": truncated})
    return result


@app.post("/run", response_model=RunResponse, dependencies=[Depends(require_token)])
async def run(req: RunRequest, request: Request = None):
    _bump("requests")
    client_ip = _client_ip(request)
    if _DRAINING:
        _bump("drain_rejected")
        _audit("run.reject", reason="draining", client=client_ip,
               run_id=(req.task_run_id or "").strip(), script=Path(req.file_path or "").name)
        raise HTTPException(
            503,
            "执行器正在排空（升级/重启中），请稍后重试",
            headers={"Retry-After": "10"},
        )
    unknown_fields = _check_unknown_fields(req, client_ip)
    task_id = _get_task_id(req)
    explicit_id = bool((req.task_run_id or "").strip())
    started_monotonic = time.monotonic()
    started_wall = time.time()
    args_sha = _short_hash(req.args or "")

    # 1) 参数/路径校验（快速失败，不占用并发槽；先于幂等查询，避免缓存绕过校验）
    try:
        script_path = _resolve_path(req.file_path)
    except HTTPException as exc:
        if exc.status_code == 403:
            _bump("path_denied")
            _audit("path.deny", client=client_ip, script=str(req.file_path)[:200],
                   reason=str(exc.detail)[:160])
        raise
    except Exception as exc:
        raise HTTPException(400, f"脚本路径无效: {exc}")
    if not script_path.exists():
        raise HTTPException(400, f"脚本不存在: {script_path}")
    if not script_path.is_file():
        raise HTTPException(400, f"不是文件: {script_path}")
    if "\x00" in (req.args or ""):
        raise HTTPException(400, "args 包含 NUL 字符，无法传给子进程")
    args_list = _split_args(req.args)
    if sum(len(x) for x in args_list) > 65536:
        raise HTTPException(413, "args 过大（>64KB）")
    env_extra = dict(req.env or {})
    if sum(len(k) + len(v) for k, v in env_extra.items()) > 65536:
        raise HTTPException(413, "env 过大（>64KB）")
    env_extra = _sanitize_env_extra(env_extra)
    # 3.3.2（P2-7）：没传 task_run_id 就不做幂等，没必要每次把整份脚本读一遍算 sha256；
    # 只 stat，审计里用 size/mtime 做对照。
    script_sig = _file_signature(script_path, with_digest=explicit_id)
    script_sha = script_sig["sha256"][:16]
    script_ctx = {"script_sha": script_sha, "script_size": script_sig["size"],
                  "script_mtime_ns": script_sig["mtime_ns"]}

    # 2) 幂等：同一 task_run_id + 同一指纹的**成功**结果直接返回（失败不缓存，重试会真跑）
    request_fingerprint = _request_fingerprint(req, script_path, script_sig) if explicit_id else ""
    if explicit_id:
        cached = await _cache_get(task_id, request_fingerprint)
        if cached:
            _bump("deduplicated")
            logger.info("任务 %s 命中幂等缓存，直接返回历史结果", task_id)
            try:
                cached = {**cached, "deduplicated": True}
                cached.setdefault("code", 200)
                cached.setdefault("duration_seconds", 0.0)
                cached.setdefault("timed_out", False)
                response = RunResponse(**cached)
                _audit("run.dedup", run_id=task_id, client=client_ip, script=script_path.name,
                       **script_ctx, args_sha=args_sha, code=response.code,
                       exit_code=response.exit_code, status=response.status_label)
                return response
            except Exception as exc:
                logger.warning("缓存结果损坏，忽略并重新执行（task=%s）: %s", task_id, exc)

    # 3) 并发槽：reject=立即 429（带 Retry-After）；queue=排队等空槽，最多 WF_QUEUE_MAX_WAIT 秒
    #    排队有上限（WF_QUEUE_MAX_DEPTH，默认 16）并登记到 _queue_waiters，/tasks 可见；
    #    只有队头能抢槽 → 严格 FIFO，不会被后来的请求插队（3.3.2 修 P2-6）。
    queue_started = time.monotonic()
    queue_key: Optional[str] = None
    got_slot = _try_acquire_slot()
    if not got_slot and req.block_strategy == "queue":
        queue_key = _queue_enter(task_id, script_path.name)
        if queue_key is None:
            _bump("queue_full_rejected")
            _audit("run.reject", reason="queue_full", client=client_ip, run_id=task_id,
                   explicit=explicit_id, script=script_path.name,
                   queue_depth=_queue_depth(), queue_limit=_QUEUE_MAX_DEPTH)
            raise HTTPException(
                429,
                f"排队已满（上限 {_QUEUE_MAX_DEPTH}），请稍后重试",
                headers={"Retry-After": "5"},
            )
        deadline = queue_started + min(_QUEUE_MAX_WAIT, float(req.timeout_seconds))
        try:
            while time.monotonic() < deadline:
                if _queue_is_head(queue_key) and _try_acquire_slot():
                    got_slot = True
                    break
                await asyncio.sleep(0.05)
        finally:
            _queue_leave(queue_key)
        if not got_slot:
            _bump("queue_timeout")
    queue_wait = time.monotonic() - queue_started
    if not got_slot:
        _bump("rejected_busy")
        _audit("run.reject", reason="busy", client=client_ip, run_id=task_id,
               explicit=explicit_id, script=script_path.name, block_strategy=req.block_strategy,
               queue_wait_ms=round(queue_wait * 1000, 1))
        raise HTTPException(
            429,
            f"执行机繁忙（并发上限 {_MAX_CONCURRENCY}），请稍后重试",
            headers={"Retry-After": "5"},
        )
    if queue_wait > 0.1:
        _bump("queued")          # 兼容旧看板；口径见 queue_success
        _bump("queue_success")

    handle: Optional[_TaskHandle] = None
    keep_handle = False
    try:
        # 4) 同 task_id 运行中 -> 409 + Retry-After（检查与登记之间无 await，单 event loop 内原子）
        _prune_finished_handles()
        with _state_lock:
            existing = task_manager.get(task_id)
            if existing is not None:
                # 只要还在台账里（含“进程已退出但 /run 尚未收尾/写缓存”的窗口）一律 409：
                # 否则同 task_id 会在收尾窗口内被执行第二次（复审 N5）。
                _bump("conflict_409")
                pid = existing.process.pid if existing.process is not None else None
                raise HTTPException(
                    409,
                    f"任务 {task_id} 已在运行中（pid={pid}）；请等待或改用新的 task_run_id",
                    headers={"Retry-After": "5"},
                )
            handle = _TaskHandle(
                task_id=task_id,
                script_path=script_path,
                cmd=[sys.executable, str(script_path), *args_list],
                timeout_seconds=req.timeout_seconds,
                started_at=started_wall,
                started_monotonic=started_monotonic,
                args_len=len(req.args or ""),
                args_sha=args_sha,
            )
            task_manager[task_id] = handle
            handle.slot_held = True
        await _persist_running()
        _audit("run.start", run_id=task_id, explicit=explicit_id, client=client_ip,
               script=script_path.name, **script_ctx, args_len=len(req.args or ""),
               args_sha=args_sha, env_keys=sorted(env_extra), timeout_s=req.timeout_seconds,
               block_strategy=req.block_strategy, queue_wait_ms=round(queue_wait * 1000, 1),
               unknown_fields=unknown_fields)

        # 5) 子进程环境
        # 修复 P1-1：请求 env 已在 _sanitize_env_extra 里拒绝危险键；这里再做兜底剥离，
        # 保证服务 token / WF_SCRIPT_* 即使从服务端环境继承也绝不会传给业务脚本。
        env = {**os.environ, **env_extra}
        _strip_env(env, _ENV_DENY)
        for _key in [k for k in env if isinstance(k, str) and k.upper().startswith("WF_SCRIPT_")]:
            env.pop(_key, None)
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.setdefault("PYTHONUNBUFFERED", "1")
        cwd = str(script_path.parent)

        # 日志只记脚本名 + 参数摘要（不再落明文 argv，测评 P1；完整参数见审计 args_sha 对照）
        logger.info("启动任务 %s: %s args_len=%d args_sha=%s", task_id, script_path.name,
                    len(req.args or ""), args_sha)
        kwargs = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # 0x08000000
        process = await asyncio.create_subprocess_exec(
            *handle.cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
            **kwargs,
        )
        handle.process = process
        handle.pid_create_time = await asyncio.to_thread(_pid_create_time, process.pid)
        _bump("spawned")
        await _persist_running()

        # 6) spawn 期间已收到取消请求（/cancel 或客户端断开）
        if handle.cancel_event.is_set():
            killed = await _kill_process_tree(process)
            handle.done = True
            _bump("run_cancelled")
            response = RunResponse(
                task_id=task_id,
                code=500,
                exit_code=-1,
                stdout="",
                stderr="任务在启动阶段被取消",
                success=False,
                status_label="任务取消",
                error_kind="cancelled",
                cancelled=True,
                duration_seconds=round(time.monotonic() - started_monotonic, 3),
            )
            _audit_finish(response, client=client_ip, script=script_path.name,
                          **script_ctx, args_sha=args_sha, phase="spawn")
            return response

        # 7) 并发读取两路输出（有上限），等待“退出 / 取消 / 超时”任一发生
        out_sink = _StreamSink()
        err_sink = _StreamSink()
        out_reader = asyncio.create_task(_read_stream(process.stdout, out_sink, _MAX_OUTPUT_BYTES))
        err_reader = asyncio.create_task(_read_stream(process.stderr, err_sink, _MAX_OUTPUT_BYTES))
        readers = [out_reader, err_reader]

        wait_task = asyncio.create_task(process.wait())
        cancel_task = asyncio.create_task(handle.cancel_event.wait())
        timeout_task = asyncio.create_task(asyncio.sleep(req.timeout_seconds))
        disconnect_task = asyncio.create_task(_watch_disconnect(request, handle))
        try:
            done, _pending = await asyncio.wait(
                {wait_task, cancel_task, timeout_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for task in (cancel_task, timeout_task, disconnect_task):
                if not task.done():
                    task.cancel()

        # 判定顺序：正常成功退出优先；否则取消优先于超时/普通退出。
        # 原因：/cancel 会先杀进程，wait_task 与 cancel_task 可能同时就绪；
        # 若先判 wait_task 会把“被取消”误报成 exit=1/部分成功。
        if wait_task in done and process.returncode == 0:
            reason = "exited"
        elif cancel_task in done:
            reason = "cancelled"
        elif wait_task in done:
            reason = "exited"
        else:
            reason = "timeout"

        if reason != "exited":
            kill_ok = await _kill_process_tree(process)
            # kill 失败时不再二次等待 5s（复审 N6：否则失败路径被拖到 ~11s）
            if kill_ok and not wait_task.done():
                try:
                    await asyncio.wait_for(asyncio.shield(wait_task), timeout=5)
                except (asyncio.TimeoutError, Exception):
                    pass
        await _drain_readers(readers, timeout=10 if reason == "exited" or kill_ok else 2)

        stdout_text = _decode_output(bytes(out_sink.buffer))
        stderr_text = _decode_output(bytes(err_sink.buffer))
        truncated = out_sink.truncated or err_sink.truncated

        # 8) 结果判定
        if reason == "exited":
            returncode = process.returncode
            crashed = returncode != 0 and "Traceback" in stderr_text
            is_success = _ok(returncode) and not crashed
            is_partial = returncode == 1 and not crashed
            if is_success:
                status_label = _label(returncode)
                result_code = 200
                error_kind = "ok"
            elif is_partial:
                status_label = _label(returncode)
                result_code = _PARTIAL_CODE   # 206（可 WF_PARTIAL_CODE=500 回退旧行为）
                error_kind = "partial"
            elif crashed:
                status_label = "脚本错误"
                result_code = 500
                error_kind = "script_error"
            else:
                status_label = _label(returncode)
                result_code = 500
                error_kind = "script_error"
            logger.info("任务 %s 完成，退出码: %s", task_id, returncode)
            response = RunResponse(
                task_id=task_id,
                code=result_code,
                exit_code=returncode,
                stdout=stdout_text,
                stderr=stderr_text,
                success=is_success,
                partial=is_partial,
                status_label=status_label,
                error_kind=error_kind,
                truncated=truncated,
                duration_seconds=round(time.monotonic() - started_monotonic, 3),
            )
            _bump("run_success" if is_success else ("run_partial" if is_partial else "run_failed"))
            # 只缓存**成功**结果：失败/部分成功必须允许平台 retry 真正重跑
            # （复审 N1：缓存 exit 1/2 会让“重试失败任务”在 TTL 内空转并烧完重试次数）。
            if explicit_id and returncode == 0:
                await _cache_put(task_id, response.model_dump(), request_fingerprint)
        elif reason == "timeout":
            _bump("run_timeout")
            if not kill_ok:
                _bump("kill_failed")
            timeout_msg = f"脚本执行超时（{req.timeout_seconds}秒，进程已终止）"
            if not kill_ok:
                timeout_msg = (
                    f"脚本执行超时（{req.timeout_seconds}秒），但进程终止失败，可能仍在运行"
                )
            if stderr_text:
                timeout_msg += "\n" + stderr_text
            response = RunResponse(
                task_id=task_id,
                code=502,
                exit_code=-1,
                stdout=stdout_text,
                stderr=timeout_msg,
                success=False,
                status_label=_KILL_FAILED_LABEL if not kill_ok else "脚本超时",
                error_kind="timeout",
                timed_out=True,
                truncated=truncated,
                duration_seconds=round(time.monotonic() - started_monotonic, 3),
            )
            logger.warning("任务 %s 超时（%ss），kill_ok=%s", task_id, req.timeout_seconds, kill_ok)
            keep_handle = not kill_ok
        else:  # cancelled
            reason_text = handle.cancel_reason or "cancelled"
            _bump("run_cancelled")
            if not kill_ok:
                _bump("kill_failed")
            message = {
                "client_disconnect": "客户端断开连接，任务已取消",
                "user_cancel": "任务已按请求取消",
                "shutdown": "服务关闭，任务已取消",
            }.get(reason_text, f"任务已取消（{reason_text}）")
            if not kill_ok:
                message += "；但进程终止失败，可能仍在运行"
            if stderr_text:
                message += "\n" + stderr_text
            response = RunResponse(
                task_id=task_id,
                code=500,
                exit_code=-1,
                stdout=stdout_text,
                stderr=message,
                success=False,
                status_label=_KILL_FAILED_LABEL if not kill_ok else "任务取消",
                error_kind="cancelled",
                cancelled=True,
                truncated=truncated,
                duration_seconds=round(time.monotonic() - started_monotonic, 3),
            )
            logger.info("任务 %s 已取消（%s），kill_ok=%s", task_id, reason_text, kill_ok)
            keep_handle = not kill_ok

        _audit_finish(response, client=client_ip, script=script_path.name, **script_ctx,
                      args_sha=args_sha, kill_failed=(not kill_ok if reason != "exited" else False))
        return response

    except HTTPException:
        raise
    except Exception as exc:
        # 3.3.1 修复：异常处理顺序必须 Exception 先于 BaseException。
        # 之前 BaseException 写在前面，导致下面的 except Exception 永远不可达：
        # 服务端内部异常会冒到 uvicorn（裸 500、无 code/exit_code、spawn_errors 不计数）。
        _bump("spawn_errors")
        logger.error("任务 %s 异常: %s", task_id, traceback.format_exc())
        if handle is not None and handle.process is not None and handle.process.returncode is None:
            try:
                kill_ok = await _kill_process_tree(handle.process)
            except Exception:
                kill_ok = False
            if not kill_ok:
                handle.kill_failed = True
                keep_handle = True
                _bump("kill_failed")
                logger.error("任务 %s 异常且进程终止失败：保留台账与并发槽", task_id)
        response = RunResponse(
            task_id=task_id,
            code=500,
            exit_code=3,
            stdout="",
            stderr=traceback.format_exc(),
            success=False,
            status_label="执行器内部错误",
            error_kind="executor_error",
            duration_seconds=round(time.monotonic() - started_monotonic, 3),
        )
        _audit_finish(response, client=client_ip, script=script_path.name, **script_ctx,
                      args_sha=args_sha, error=type(exc).__name__)
        return response
    except BaseException as exc:
        # 修复 P1-2：请求任务被取消/进程中断（CancelledError/KeyboardInterrupt/SystemExit 等）
        # 不会进入下面的 except Exception；原实现直接走 finally：不杀子进程还删台账 → 永久孤儿。
        # 这里先杀进程树，成功才允许 finally 删台账；失败则保留台账与并发槽。
        logger.warning("任务 %s 被中断（%s），正在终止子进程树", task_id, type(exc).__name__)
        kill_ok = True
        if handle is not None and handle.process is not None and handle.process.returncode is None:
            try:
                kill_ok = await _kill_process_tree(handle.process)
            except BaseException:
                kill_ok = False
        if handle is not None and not kill_ok:
            handle.kill_failed = True
            keep_handle = True
            _bump("kill_failed")
            logger.error("任务 %s 中断清理失败：进程可能仍在运行，已保留台账与并发槽", task_id)
        for _task in list(locals().get("readers") or []) + [locals().get("wait_task")]:
            if _task is not None and not _task.done():
                _task.cancel()
        _audit("run.abort", run_id=task_id, phase="interrupted", error=type(exc).__name__,
               code=500, exit_code=-1)
        raise
    finally:
        if handle is not None:
            # 进程确认终止（或本来就没起来）才算 done；kill 失败时保持“运行中”，
            # 以便 /tasks 可见、/cancel 还能重试（并继续占用一个并发槽，复审 N4）。
            handle.done = not keep_handle
            if keep_handle:
                handle.kill_failed = True
            elif handle.slot_held:
                handle.slot_held = False
                _release_slot()
        else:
            _release_slot()
        if handle is not None and not keep_handle:
            _drop_handle(task_id, handle)
        try:
            await _persist_running()
        except Exception:
            pass


@app.post("/cancel/{task_id}", dependencies=[Depends(require_token)])
async def cancel_task(task_id: str):
    """主动取消指定任务（等待进程树确实退出后再返回）。"""
    handle = task_manager.get(task_id)
    if handle is None:
        raise HTTPException(404, f"任务不存在: {task_id}")

    process = handle.process
    if process is not None and process.returncode is not None:
        raise HTTPException(400, f"任务已完成: {task_id}")
    if process is None and handle.done:
        raise HTTPException(400, f"任务已完成: {task_id}")

    logger.info("收到取消请求: %s", task_id)
    _audit("run.cancel", run_id=task_id, phase="spawn" if process is None else "running")
    handle.request_cancel("user_cancel")
    if process is None:
        # 还在 spawn 阶段：/run 协程会在 spawn 后看到 cancel_event 并清理
        return {"task_id": task_id, "status": "cancelling", "message": "任务正在启动，已请求取消"}
    ok = await _kill_process_tree(process)
    if not ok:
        _bump("kill_failed")
        raise HTTPException(500, f"终止任务 {task_id} 失败，进程可能仍在运行，请重试")
    return {
        "task_id": task_id,
        "status": "cancelled",
        "message": "任务已取消",
    }


@app.get("/tasks", dependencies=[Depends(require_token)])
async def list_tasks():
    """列出所有运行中的任务。"""
    _prune_finished_handles()
    running = []
    now = time.time()
    for handle in _active_handles():
        running.append({
            "task_id": handle.task_id,
            "pid": handle.process.pid if handle.process is not None else None,
            "script": handle.script_path.name,
            "started_at": handle.started_at,
            "elapsed_seconds": round(now - handle.started_at, 1),
            "timeout_seconds": handle.timeout_seconds,
            "cancelling": bool(handle.cancel_reason),
        })

    return {
        "count": len(running),
        "tasks": running,
        "concurrency_limit": _MAX_CONCURRENCY,
        "queue": {
            "depth": _queue_depth(),
            "limit": _QUEUE_MAX_DEPTH,
            "max_wait_seconds": _QUEUE_MAX_WAIT,
            "waiting": _queue_snapshot(),
        },
        "counters": dict(_METRICS),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=9000)
