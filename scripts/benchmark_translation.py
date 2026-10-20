"""Reproducible, isolated translation benchmark. --live consumes one CLI turn.

Only synthetic academic text is used. Outputs stay in the chosen benchmark
directory; user papers, settings, and credentials are never copied or changed.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import statistics
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch


TEXTS = [
    """Efficient Measurement of Ground Motion
Abstract
We evaluate a reproducible method for measuring ground motion at 24 stations.
The median sampling interval is 0.02 seconds. The observed improvement may
reflect lower measurement noise; it does not establish a causal relationship.
We report a standard error of 0.15 mm, rather than a standard deviation.
1 Methods
For N observations x_i, the sample mean is x_bar = (1/N) sum_i x_i.
The standard error is SE = s / sqrt(N), where s is the sample standard deviation.
All stations use the same calibration procedure. Missing observations are
excluded before aggregation; they are not replaced with zeros.
""",
    """2 Results
The baseline error is 2.40 mm and the revised error is 2.04 mm, a reduction
of 15 percent. This reduction is not statistically significant at the 0.05
level. At least 18 stations satisfy the predefined quality criterion.
The finding applies to the observed stations and should not be generalized
to regions with different geological conditions without further evaluation.
Table 1. Measurement errors by method.
Method    Error (mm)    Stations
Baseline  2.40          24
Revised   2.04          24
3 Conclusion
The method may improve measurement precision, but the limited sample does
not support a claim of universal superiority. Future studies should evaluate
independent data and report uncertainty alongside point estimates.
""",
]


def fixture(root, count=2, translated=False):
    from easyread.store import Workspace, write_json_atomic
    root.mkdir(parents=True, exist_ok=True)
    extract = root / "extract"
    extract.mkdir(exist_ok=True)
    blocks = []
    for n in range(1, count + 1):
        text = TEXTS[(n - 1) % 2]
        (extract / f"page-{n:03d}.txt").write_text(text, encoding="utf-8")
        chars = [[ch, (i % 80) / 100, (i // 80) / 100, (i % 80 + .8) / 100, (i // 80 + .8) / 100]
                 for i, ch in enumerate(text)]
        write_json_atomic(extract / f"page-{n:03d}.chars.json", chars)
        if translated:
            for i, line in enumerate(text.splitlines()):
                blocks.append({"id": f"p{n}-{i}", "type": "para", "page": n, "en": line, "zh": "測試譯文"})
    paper = {"meta": {"title_en": "Efficient Measurement of Ground Motion", "target": "zh-TW", "page_count": count,
                      "pages": [{"n": n} for n in range(1, count + 1)]}, "blocks": blocks,
             "glossary": [], "references": [], "translation": {"done_pages": []}}
    write_json_atomic(root / "paper.json", paper)
    return Workspace(root)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--label", required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument("--live", action="store_true")
    p.add_argument("--replay", type=Path)
    args = p.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    os.environ["EASYREAD_HOME"] = str(out / "home")
    sys.path.insert(0, str(args.source.resolve()))
    from easyread import agy, config, engines, pdfwork, prompts, translate, usage
    from easyread.store import write_json_atomic

    # Measure identical, real local layout work on a 20-page synthetic paper.
    layout_ws = fixture(out / f"layout-{args.label}", 20, True)
    durations = []
    for _ in range(7):
        started = time.perf_counter()
        layout = pdfwork.locate(layout_ws.root)
        durations.append(time.perf_counter() - started)
    result = {"label": args.label, "local_layout_median_seconds": statistics.median(durations),
              "local_layout_samples": durations,
              "layout_sha256": hashlib.sha256(json.dumps(layout, sort_keys=True).encode()).hexdigest()}

    if args.live or args.replay:
        ws = fixture(out / f"paper-{args.label}")
        cfg = copy.deepcopy(config.DEFAULTS)
        cfg.update(engine="agy", batch_pages=2, concurrency=0, target="zh-TW")
        meter = usage.Meter("agy")
        chunks, first_preview, replay_clock = [], [], [0.0]
        original = agy.stream
        started = time.perf_counter()

        def record_stream(c, prompt, cancel=None, meter=None):
            for piece in original(c, prompt, cancel, meter):
                chunks.append({"seconds": time.perf_counter() - started, "text": piece})
                yield piece

        def replay_stream(c, prompt, cancel=None, meter=None):
            saved = json.loads(args.replay.read_text(encoding="utf-8"))
            for chunk in saved["chunks"]:
                chunks.append(chunk)
                replay_clock[0] = chunk["seconds"]
                yield chunk["text"]
            if meter:
                u = saved["usage"]
                meter.add(input=u["input"], cached=u.get("cached", 0), output=u["output"])

        def preview(*event):
            if not first_preview and any((e.get("rows") or []) for e in event if isinstance(e, dict)):
                first_preview.append(replay_clock[0] if args.replay else time.perf_counter() - started)

        prompt = prompts.translate(ws, [1, 2], "text", "")
        kwargs = {}
        import inspect
        if "preview" in inspect.signature(translate.translate_pages).parameters:
            kwargs["preview"] = preview
        with patch.object(agy, "stream", record_stream if args.live else replay_stream):
            failed = translate.translate_pages(ws, cfg, [1, 2], threading.Event(), lambda *a: None, meter, **kwargs)
        elapsed = time.perf_counter() - started
        paper = ws.load("paper")
        result.update(elapsed_seconds=elapsed, first_readable_seconds=first_preview[0] if first_preview else elapsed,
                      first_delta_seconds=chunks[0]["seconds"] if chunks else None, usage=meter.snapshot(),
                      failed=failed, prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
                      prompt_chars=len(prompt), blocks=len(paper.get("blocks", [])),
                      done_pages=paper.get("translation", {}).get("done_pages"),
                      translation_sha256=hashlib.sha256(json.dumps(paper, sort_keys=True, ensure_ascii=False).encode()).hexdigest())
        if args.replay:
            result["kind"] = "recorded-stream-replay-no-model-call"
            result["replay_processing_seconds"] = elapsed
        write_json_atomic(out / f"{args.label}-stream.json", {"chunks": chunks, "usage": result["usage"]})
    write_json_atomic(out / f"{args.label}.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
