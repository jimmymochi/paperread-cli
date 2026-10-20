"""Replay a recorded CLI response through actual Jobs workers, without AI calls."""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

from benchmark_translation import fixture


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--stream", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--label", required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    os.environ["EASYREAD_HOME"] = str(out / "home")
    sys.path.insert(0, str(args.source.resolve()))
    from easyread import config
    from easyread.jobs import Jobs
    from easyread.library import Library
    from easyread.store import write_json_atomic

    saved = json.loads(args.stream.read_text(encoding="utf-8"))
    lib = Library(out / ("library-" + args.label))
    workspaces = [fixture(lib.root / f"paper{i:04}") for i in (1, 2)]
    cfg = copy.deepcopy(config.DEFAULTS)
    cfg.update(engine="agy", batch_pages=2, concurrency=0)
    lock, stats = threading.Lock(), {"active": 0, "peak": 0, "calls": 0, "prompts": [], "requests": []}
    started = time.perf_counter()
    def replay(options, prompt, cancel=None, meter=None):
        begin = time.perf_counter() - started
        with lock:
            stats["active"] += 1
            stats["peak"] = max(stats["peak"], stats["active"])
            stats["calls"] += 1
            stats["prompts"].append(hashlib.sha256(prompt.encode()).hexdigest())
        previous = 0
        try:
            for chunk in saved["chunks"]:
                time.sleep(max(0, chunk["seconds"] - previous) / 100)
                previous = chunk["seconds"]
                yield chunk["text"]
            if meter:
                meter.add(input=saved["usage"]["input"], output=saved["usage"]["output"])
        finally:
            with lock:
                stats["active"] -= 1
                stats["requests"].append([begin, time.perf_counter() - started])

    with patch("easyread.jobs.config.load", return_value=cfg), patch("easyread.agy.stream", replay):
        jobs = Jobs(lib)
        for ws in workspaces:
            jobs.enqueue(ws)
        while any(ws.load("job").get("state") not in ("done", "error", "partial") for ws in workspaces):
            if time.perf_counter() - started > 60:
                raise TimeoutError("benchmark did not finish")
            time.sleep(.02)
    result = {"kind": "recorded-stream-replay-100x-no-model-call", "label": args.label,
              "elapsed_seconds": time.perf_counter() - started, "peak_simultaneous_requests": stats["peak"],
              "calls": stats["calls"], "prompt_sha256": sorted(stats["prompts"]), "intervals_seconds": stats["requests"],
              "states": [ws.load("job")["state"] for ws in workspaces],
              "usage": [ws.load("job")["usage"] for ws in workspaces],
              "output_sha256": [hashlib.sha256(json.dumps(ws.load("paper"), sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                                for ws in workspaces]}
    write_json_atomic(out / f"{args.label}-parallel.json", result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
