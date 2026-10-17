"""Antigravity CLI integration. Uses the user's CLI login, with no API key or bridge."""
from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
from collections import deque
from pathlib import Path

from . import engines, netcheck
from .i18n import tr

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,119}\Z")
_catalog_lock = threading.Lock()
_catalog = {}


def find_cli(name: str, command: str = "") -> str | None:
    """Explicit paths win; desktop launches may not inherit the CLI installer PATH."""
    found = shutil.which(command or name)
    if found or (command and command != name):
        return found
    local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local")
    candidates = ([local / "agy/bin/agy.exe", Path.home() / ".local/bin/agy"] if name == "agy"
                  else [local / "Programs/OpenAI/Codex/bin/codex.exe"])
    return next((str(p) for p in candidates if p.is_file()), None)


def path(c: dict) -> str | None:
    return find_cli("agy", c.get("command", ""))


def parse_models(output: str) -> list[dict]:
    records, seen = [], set()
    for line in output.splitlines():
        slug, sep, name = line.strip().partition("\t")
        if sep and SLUG.fullmatch(slug) and slug not in seen:
            records.append({"id": slug, "name": name.strip() or slug})
            seen.add(slug)
    if not records:
        raise ValueError(tr("AGY 未回傳模型清單，請先在終端機執行 agy 並完成登入。"))
    return records


def models(c: dict, fresh: bool = False) -> dict:
    from . import config
    from .store import read_json, write_json_atomic
    exe = path(c)
    if not exe:
        return {"default": "", "models": [], "error": tr("尚未安裝 Antigravity CLI。安裝後執行 agy 完成登入，再按重新整理。")}
    cache = config.HOME / ".agy-models.json"
    key = (exe, str(cache))
    with _catalog_lock:
        old = _catalog.get(key)
        if not fresh and old and time.monotonic() - old[0] < 300:
            return old[1]
        error = ""
        try:
            result = subprocess.run([exe, "models"], capture_output=True, text=True, encoding="utf-8",
                                    errors="replace", timeout=20, creationflags=NO_WINDOW, env=netcheck.proxy_env())
            if result.returncode:
                raise ValueError(tr("AGY 模型清單讀取失敗。請在終端機執行 agy，確認登入與網路後重試。"))
            records = parse_models(result.stdout)
            try:
                write_json_atomic(cache, records)
            except OSError:
                pass
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            error = str(exc) if isinstance(exc, ValueError) else tr("AGY 模型清單暫時無法讀取，請確認 CLI 登入與網路後重試。")
            saved = read_json(cache, [])
            records = [m for m in saved if isinstance(m, dict) and isinstance(m.get("id"), str)
                       and SLUG.fullmatch(m["id"]) and isinstance(m.get("name"), str)] if isinstance(saved, list) else []
        value = {"default": "", "models": records, "error": error, "stale": bool(error and records)}
        _catalog[key] = (time.monotonic(), value)
        return value


def argv(c: dict, exe: str) -> list[str]:
    args = [exe, "--input-format", "stream-json", "--output-format", "stream-json",
            "--disable-slash-commands", "--sandbox"]
    model = c.get("model") or ""
    if model:
        if not isinstance(model, str) or not SLUG.fullmatch(model):
            raise engines.EngineError(tr("無效的 AGY 模型名稱，請重新選擇模型。"))
        args += ["--model", model]
    effort = c.get("reasoning_effort") or ""
    if effort:
        if effort not in ("low", "medium", "high", "xhigh", "max"):
            raise engines.EngineError(tr("AGY 不支援這個推理強度。"))
        args += ["--effort", effort]
    return args


def stop(proc):
    if proc.poll() is None:
        if os.name == "nt":
            try:
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                               capture_output=True, creationflags=NO_WINDOW, timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                proc.kill()
        else:
            proc.kill()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()


def stream(c: dict, prompt: str, cancel=None, meter=None):
    exe = path(c)
    if not exe:
        raise engines.EngineError(tr("找不到 Antigravity CLI。請先安裝並執行 agy 完成登入；不需要 API Key。"))
    if cancel is not None and cancel.is_set():
        raise engines.Cancelled()
    args = argv(c, exe)
    # A fresh empty directory keeps each request away from the user's project instructions/files.
    with tempfile.TemporaryDirectory(prefix="paperread-agy-") as work:
        try:
            proc = subprocess.Popen(args, cwd=work, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                                    creationflags=NO_WINDOW, env=netcheck.proxy_env())
        except OSError as exc:
            raise engines.EngineError(tr("無法啟動 Antigravity CLI：") + str(exc)) from exc
        events, errors = queue.Queue(), deque(maxlen=10)

        def read_stdout():
            try:
                for line in proc.stdout:
                    events.put(line)
            finally:
                events.put(None)

        def read_stderr():
            for line in proc.stderr:
                errors.append(line[-300:])

        readers = [threading.Thread(target=read_stdout, daemon=True), threading.Thread(target=read_stderr, daemon=True)]
        for reader in readers:
            reader.start()
        deadline = time.monotonic() + int(c.get("timeout") or 600)
        said = ""
        try:
            text = ("Answer using the supplied academic text. Do not invoke tools or read, create, "
                    "or modify local files. Return only the requested answer.\n\n" + prompt)
            proc.stdin.write(json.dumps({"event": "user", "message": {"content": text}}, ensure_ascii=False) + "\n")
            proc.stdin.flush()
            while time.monotonic() < deadline:
                if cancel is not None and cancel.is_set():
                    raise engines.Cancelled()
                try:
                    line = events.get(timeout=0.2)
                except queue.Empty:
                    continue
                if line is None:
                    raise engines.EngineError(tr("AGY 提前結束，請確認 CLI 登入狀態。") + "".join(errors)[-600:])
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                if event.get("event") == "step_update":
                    step = event.get("step_update") or {}
                    delta = step.get("text_delta") if step.get("step_type") == "agent_response" else ""
                    if delta:
                        said += delta
                        yield delta
                elif event.get("event") == "result":
                    result = event.get("result") or {}
                    if result.get("status") != "SUCCESS":
                        raise engines.EngineError(str(result.get("error") or tr("AGY 未成功完成請求。")))
                    answer = result.get("response") or ""
                    if not said and not answer:
                        raise engines.EngineError(tr("AGY 未回傳回答。"))
                    if meter is not None:
                        u = result.get("usage") or {}
                        meter.add(input=int(u.get("input_tokens") or 0), output=int(u.get("output_tokens") or 0))
                    rest = answer[len(said):] if answer.startswith(said) else (answer if not said else "")
                    if rest:
                        yield rest
                    return
            raise engines.EngineError(tr("AGY 回覆逾時，請稍後重試。"))
        except (BrokenPipeError, OSError) as exc:
            raise engines.EngineError(tr("AGY 連線中斷，請確認 CLI 登入狀態。")) from exc
        finally:
            stop(proc)
            for reader in readers:
                reader.join(timeout=2)
            for pipe in (proc.stdin, proc.stdout, proc.stderr):
                try:
                    pipe.close()
                except OSError:
                    pass


def complete(c: dict, prompt: str, cancel=None, meter=None) -> str:
    return "".join(stream(c, prompt, cancel, meter))
