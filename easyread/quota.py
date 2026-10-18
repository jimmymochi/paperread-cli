"""Read account quotas through the logged-in CLIs, without starting model turns.

AGY: --print /usage (tab-separated report). Codex: app-server JSON-RPC.
Only normalized quota fields are retained, in memory, for five minutes.
"""
from __future__ import annotations

import copy
import json
import math
import os
import queue
import re
import subprocess
import tempfile
import threading
import time
from datetime import datetime

from . import __version__, agy, codex_lean, config, engines, netcheck

_cache: dict = {}
_locks = {engine: threading.Lock() for engine in ("agy", "codex")}


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return value


def parse_agy(text: str) -> list[dict]:
    rows = []
    for line in text.splitlines():
        parts = line.strip().split("\t")
        if len(parts) != 4 or not re.fullmatch(r"\d+(?:\.\d+)?%", parts[2]):
            continue
        group, label, percent, reset = parts
        remaining = float(percent[:-1])
        if not group or not label or not 0 <= remaining <= 100:
            continue
        try:
            parsed = datetime.fromisoformat(reset.replace("Z", "+00:00"))
            resets_at = int(parsed.timestamp()) if parsed.tzinfo else None
        except (ValueError, OverflowError, OSError):
            resets_at = None
        rows.append({"group": group[:160], "label": label[:160], "remaining_percent": remaining,
                     "resets_at": resets_at, "window_minutes": None})
    return rows


def parse_codex(result: dict) -> list[dict]:
    buckets = result.get("rateLimitsByLimitId")
    if not isinstance(buckets, dict) or not buckets:
        legacy = result.get("rateLimits")
        buckets = {"codex": legacy} if isinstance(legacy, dict) else {}
    rows = []
    for bucket_id, bucket in buckets.items():
        if not isinstance(bucket, dict):
            continue
        for key in ("primary", "secondary"):
            window = bucket.get(key)
            if not isinstance(window, dict):
                continue
            used = number(window.get("usedPercent"))
            if used is None:
                continue
            duration = number(window.get("windowDurationMins"))
            reset = number(window.get("resetsAt"))
            rows.append({"group": str(bucket.get("limitName") or bucket.get("limitId") or bucket_id)[:160],
                         "label": key, "remaining_percent": max(0, min(100, 100 - used)),
                         "window_minutes": duration if duration and duration > 0 else None,
                         "resets_at": reset if reset and reset > 0 else None})
    return rows


def _agy(exe: str, work: str) -> list[dict]:
    proc = subprocess.Popen([exe, "--print", "/usage", "--print-timeout", "20s"], cwd=work,
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            text=True, encoding="utf-8", errors="replace", creationflags=agy.NO_WINDOW,
                            env=netcheck.proxy_env())
    try:
        output, _ = proc.communicate(timeout=25)
        if proc.returncode:
            raise ValueError("query_failed")
        return parse_agy(output)
    finally:
        agy.stop(proc)
        proc.stdout.close()


def _codex(exe: str, work: str) -> list[dict]:
    proc = subprocess.Popen([exe, "app-server", "--listen", "stdio://", *codex_lean.args()], cwd=work,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            text=True, encoding="utf-8", errors="replace", creationflags=agy.NO_WINDOW,
                            env=netcheck.proxy_env())
    events = queue.Queue()

    def read():
        try:
            for line in proc.stdout:
                try:
                    event = json.loads(line)
                    if isinstance(event, dict):
                        events.put(event)
                except ValueError:
                    pass
        finally:
            events.put(None)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    deadline = time.monotonic() + 25

    def send(message):
        proc.stdin.write(json.dumps(message) + "\n")
        proc.stdin.flush()

    def receive(wanted):
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError()
            try:
                event = events.get(timeout=left)
            except queue.Empty:
                raise TimeoutError() from None
            if event is None:
                raise ValueError("query_failed")
            if event.get("id") == wanted:
                if "error" in event or not isinstance(event.get("result"), dict):
                    raise ValueError("query_failed")
                return event["result"]

    try:
        send({"id": 1, "method": "initialize", "params": {
            "clientInfo": {"name": "paperread_quota", "title": "PaperRead CLI", "version": __version__}}})
        receive(1)
        send({"method": "initialized"})
        send({"id": 2, "method": "account/rateLimits/read", "params": {"excludeResetCreditDetails": True}})
        return parse_codex(receive(2))
    finally:
        try:
            proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            agy.stop(proc)
        reader.join(timeout=2)
        proc.stdout.close()


def read(engine: str, fresh: bool = False) -> dict:
    """No credential extraction, login changes, model requests, or automatic resets."""
    if engine not in _locks:
        return {"engine": engine, "status": "unavailable", "reason": "unsupported", "rows": []}
    cfg = config.load()
    exe = (agy.path if engine == "agy" else engines.codex_path)(cfg[engine])
    if not exe:
        return {"engine": engine, "status": "unavailable", "reason": "cli_missing", "rows": []}
    key = (engine, exe, str(config.HOME), os.environ.get("CODEX_HOME", ""))
    with _locks[engine]:
        old = _cache.get(key)
        # Explicit refreshes are coalesced for 10 seconds, including across tabs.
        if old and time.monotonic() - old[0] < (10 if fresh else 300):
            return copy.deepcopy(old[1])
        reason = ""
        try:
            with tempfile.TemporaryDirectory(prefix="paperread-quota-") as work:
                rows = (_agy if engine == "agy" else _codex)(exe, work)
            if not rows:
                reason = "unavailable"
        except (TimeoutError, subprocess.TimeoutExpired):
            rows, reason = [], "timeout"
        except (OSError, ValueError, subprocess.SubprocessError):
            rows, reason = [], "query_failed"
        now = int(time.time())
        value = {"engine": engine, "status": "ready" if rows else "unavailable", "rows": rows,
                 "updated_at": now if rows else None, "checked_at": now, "reason": reason}
        if reason and old and old[1].get("rows"):
            value.update(status="stale", rows=old[1]["rows"], updated_at=old[1]["updated_at"])
        _cache[key] = (time.monotonic(), value)
        return copy.deepcopy(value)
