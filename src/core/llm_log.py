"""Structured logging of every LLM prompt, response, tool call, and opt-in network fetch.

Writes newline-delimited JSON to ~/.aegishiker/logs/llm.jsonl (and network.jsonl).
Can be disabled with AEGIS_LLM_LOG=0.
"""
import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import paths

logger = logging.getLogger(__name__)

MAX_BYTES = 25 * 1024 * 1024
BACKUP_COUNT = 3
DEFAULT_MAX_FIELD_CHARS = 50_000

_write_lock = threading.Lock()

try:
    import fcntl

    def _lock(f) -> None:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)

    def _unlock(f) -> None:
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)

except ImportError:  # pragma: no cover - Windows
    import msvcrt

    _LOCK_OFFSET = 1 << 30

    def _lock(f) -> None:
        fd = f.fileno()
        while True:
            os.lseek(fd, _LOCK_OFFSET, os.SEEK_SET)
            try:
                msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                return
            except OSError:
                continue

    def _unlock(f) -> None:
        fd = f.fileno()
        os.lseek(fd, _LOCK_OFFSET, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)


def is_enabled() -> bool:
    return os.environ.get("AEGIS_LLM_LOG", "1").strip().lower() not in ("0", "false", "no")


def log_path() -> Path:
    paths.refresh_app_dir()
    return Path(os.environ.get("AEGIS_LLM_LOG_PATH", str(paths.LLM_LOG_PATH)))


def network_log_path() -> Path:
    paths.refresh_app_dir()
    return paths.NETWORK_LOG_PATH


def _max_field_chars() -> int:
    try:
        return int(os.environ.get("AEGIS_LLM_LOG_MAX_CHARS", DEFAULT_MAX_FIELD_CHARS))
    except ValueError:
        return DEFAULT_MAX_FIELD_CHARS


def _truncate(value: Any) -> Any:
    limit = _max_field_chars()
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + f"…[truncated {len(value) - limit} chars]"
    if isinstance(value, dict):
        return {k: _truncate(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_truncate(v) for v in value]
    return value


def _rotate_if_needed(path: Path) -> None:
    try:
        if not path.exists() or path.stat().st_size < MAX_BYTES:
            return
        for i in range(BACKUP_COUNT - 1, 0, -1):
            older, newer = path.with_suffix(f".{i}.jsonl"), path.with_suffix(f".{i + 1}.jsonl")
            if older.exists():
                older.replace(newer)
        path.replace(path.with_suffix(".1.jsonl"))
    except OSError as e:
        logger.debug(f"Could not rotate log: {e}")


def write_record(record: Dict[str, Any], target_path: Optional[Path] = None) -> None:
    if not is_enabled():
        return
    try:
        path = target_path or log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        _rotate_if_needed(path)
        line = json.dumps(record, default=str, ensure_ascii=False) + "\n"
        with _write_lock:
            with open(path, "a", encoding="utf-8") as f:
                _lock(f)
                try:
                    f.write(line)
                    f.flush()
                finally:
                    _unlock(f)
    except Exception as e:
        logger.debug(f"Could not write log record: {e}")


def log_llm_call(
    kind: str,
    model: str,
    messages: List[Dict[str, Any]],
    response: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
    duration_ms: Optional[float] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    content = ""
    tool_calls: List[Dict[str, Any]] = []
    if response:
        message = response.get("message") or {}
        content = message.get("content") or ""
        for call in (message.get("tool_calls") or []):
            fn = call.get("function", {}) or {}
            tool_calls.append({"name": fn.get("name"), "arguments": fn.get("arguments")})

    record = {
        "ts": datetime.now().isoformat(timespec="milliseconds"),
        "pid": os.getpid(),
        "event": "llm_call",
        "kind": kind,
        "model": model,
        "duration_ms": round(duration_ms, 1) if duration_ms is not None else None,
        "prompt_chars": sum(len(str(m.get("content") or "")) for m in messages),
        "messages": _truncate(messages),
        "response": _truncate(content),
        "tool_calls": _truncate(tool_calls),
        "tools_offered": [(t.get("function", {}) or {}).get("name") for t in (tools or [])],
        "error": error,
    }
    if extra:
        record.update(_truncate(extra))
    write_record(record)


def log_tool_call(
    name: str,
    arguments: Dict[str, Any],
    result: Optional[str] = None,
    duration_ms: Optional[float] = None,
    error: Optional[str] = None,
) -> None:
    write_record({
        "ts": datetime.now().isoformat(timespec="milliseconds"),
        "pid": os.getpid(),
        "event": "tool_call",
        "tool": name,
        "arguments": _truncate(arguments),
        "result": _truncate(result),
        "duration_ms": round(duration_ms, 1) if duration_ms is not None else None,
        "error": error,
    })


def log_network_request(
    kind: str,
    url: str,
    status_code: Optional[int] = None,
    bytes_received: int = 0,
    error: Optional[str] = None,
) -> None:
    """Audit log for opt-in regional pack downloads."""
    write_record(
        {
            "ts": datetime.now().isoformat(timespec="milliseconds"),
            "pid": os.getpid(),
            "event": "network_request",
            "kind": kind,
            "url": url,
            "status_code": status_code,
            "bytes_received": bytes_received,
            "error": error,
        },
        target_path=network_log_path(),
    )


class Timer:
    def __enter__(self):
        self._start = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.ms = (time.perf_counter() - self._start) * 1000
        return False

    @property
    def elapsed_ms(self) -> float:
        return getattr(self, "ms", (time.perf_counter() - self._start) * 1000)


def read_records(limit: Optional[int] = None, path: Optional[str] = None) -> List[Dict[str, Any]]:
    p = Path(path) if path else log_path()
    if not p.exists():
        return []
    records: List[Dict[str, Any]] = []
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records[-limit:] if limit else records

