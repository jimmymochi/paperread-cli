"""Read-only previews and local timing. Never changes a model request or paper."""
from __future__ import annotations

import contextvars
import hashlib
import json
import threading
import time
from collections import deque
from contextlib import contextmanager

current = contextvars.ContextVar("translation_progress", default=None)


class JsonPreview:
    """Scan each character once; expose only complete JSON blocks/strings.

    This deliberately is not the final JSON validator. Malformed or truncated
    output still goes through engines.parse_json and all existing quality checks.
    """
    def __init__(self, pages, key=""):
        self.pages, self.key = pages, key
        self.text, self.pos, self.stack, self.string = "", 0, [], None
        self.escaped, self.started, self.rows = False, False, []

    def feed(self, delta):
        if len(self.text) + len(delta) > 2_000_000:
            return False
        self.text += delta
        before = len(self.rows)
        while self.pos < len(self.text):
            i, ch = self.pos, self.text[self.pos]
            self.pos += 1
            if self.string is not None:
                if self.escaped:
                    self.escaped = False
                elif ch == "\\":
                    self.escaped = True
                elif ch == '"':
                    try:
                        value = json.loads(self.text[self.string:i + 1])
                    except ValueError:
                        value = None
                    self.string = None
                    if self.stack and value is not None:
                        frame = self.stack[-1]
                        if frame["kind"] == "{" and frame["key"] is None:
                            frame["key"] = value
                        else:
                            path = self._path()
                            if path == ("zh",) or (len(path) == 2 and path[0] == "zh"):
                                self._row(self.key or str(path[-1]), self.pages[0], value)
                continue
            if not self.started:
                if ch != "{":
                    continue
                self.started = True
            if ch == '"':
                self.string = i
            elif ch in "{[":
                self.stack.append({"kind": ch, "path": self._path(), "start": i, "key": None, "index": 0})
            elif ch in "}]" and self.stack:
                frame = self.stack.pop()
                if frame["kind"] != {"}": "{", "]": "["}[ch]:
                    self.stack.clear()
                    return False
                path = frame["path"]
                if len(path) == 2 and path[0] == "blocks" and frame["kind"] == "{":
                    try:
                        b = json.loads(self.text[frame["start"]:i + 1])
                        value = b.get("zh") or b.get("caption_zh") or "\n".join(
                            it.get("zh", "") for it in b.get("items", []) if isinstance(it, dict))
                        self._row(str(b.get("id", "")), b.get("page", self.pages[0]), value)
                    except (ValueError, TypeError, AttributeError):
                        pass
            elif ch == "," and self.stack:
                self.stack[-1]["key"] = None
                self.stack[-1]["index"] += 1
        return len(self.rows) != before

    def _path(self):
        if not self.stack:
            return ()
        f = self.stack[-1]
        return f["path"] + ((f["index"] if f["kind"] == "[" else f["key"]),)

    def _row(self, key, page, text):
        if page in self.pages and isinstance(text, str) and text.strip() and len(self.rows) < 100:
            self.rows.append({"id": key, "page": page, "text": text.replace("‖", "").strip()[:16000]})


class RequestSlots:
    """FIFO global budget shared by papers; preserves every paper's lane order."""
    def __init__(self):
        self.condition, self.waiters, self.active = threading.Condition(), deque(), 0

    @contextmanager
    def slot(self, limit, cancel):
        ticket = object()
        with self.condition:
            self.waiters.append(ticket)
            try:
                while self.waiters[0] is not ticket or self.active >= limit:
                    if cancel is not None and cancel.is_set():
                        from .engines import Cancelled
                        raise Cancelled()
                    self.condition.wait(.1)
                if cancel is not None and cancel.is_set():
                    from .engines import Cancelled
                    raise Cancelled()
                self.active += 1
            finally:
                self.waiters.remove(ticket)
                self.condition.notify_all()
        try:
            yield
        finally:
            with self.condition:
                self.active -= 1
                self.condition.notify_all()


class Capture:
    def __init__(self, pages, emit=None, performance=None, gate=None, cancel=None, key=""):
        self.pages, self.emit, self.performance = pages, emit, performance
        self.gate, self.cancel, self.key = gate, cancel, key
        self.requests, self.parser, self.metric = [], None, None

    def __enter__(self):
        self.started = time.perf_counter()
        self.token = current.set(self)
        return self

    def __exit__(self, *exc):
        current.reset(self.token)
        self.publish("clear")
        if self.performance:
            elapsed = time.perf_counter() - self.started
            self.performance({"pages": self.pages, "seconds": round(elapsed, 4), "requests": self.requests,
                              "local_seconds": round(max(0, elapsed - sum(r["seconds"] + r["wait_seconds"] for r in self.requests)), 4)})

    @contextmanager
    def request(self, cfg, prompt, meter):
        from contextlib import nullcontext
        from .segments import AUTO_MAX, workers
        limit = workers(cfg.get("concurrency"), AUTO_MAX * 2)
        queued = time.perf_counter()
        with self.gate.slot(limit, self.cancel) if self.gate else nullcontext():
            self.parser = JsonPreview(self.pages, self.key)
            self.fed = False
            self.metric = {"engine": cfg.get("engine"), "prompt_chars": len(prompt),
                           "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                           "wait_seconds": round(time.perf_counter() - queued, 4)}
            self.request_started = time.perf_counter()
            self.publish("generating")
            try:
                yield
            finally:
                self.metric["seconds"] = round(time.perf_counter() - self.request_started, 4)
                self.requests.append(self.metric)
                self.publish("checking")

    def feed(self, text):
        if not self.metric or not text:
            return
        self.fed = True
        self.metric.setdefault("first_text_seconds", round(time.perf_counter() - self.request_started, 4))
        if self.emit and self.parser.feed(text):
            self.metric.setdefault("first_preview_seconds", round(time.perf_counter() - self.request_started, 4))
            self.publish("generating")

    def publish(self, phase):
        if self.emit:
            self.emit({"pages": self.pages, "key": self.key, "phase": phase,
                       "rows": list(self.parser.rows) if self.parser and phase != "clear" else []})
