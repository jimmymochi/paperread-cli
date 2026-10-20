import copy
import json
import queue
import threading
import time
from unittest.mock import patch

from easyread import config
from easyread.jobs import Jobs
from easyread.library import Library
from easyread.store import Workspace, write_json_atomic


def test_two_papers_translate_simultaneously_without_mixing_content(tmp_path):
    lib = Library(tmp_path)
    papers = []
    for number in (1, 2):
        root = lib.root / f"paper{number:04}"
        (root / "extract").mkdir(parents=True)
        for page in (1, 2):
            (root / "extract" / f"page-{page:03}.txt").write_text(f"SOURCE{number} page {page}.", encoding="utf-8")
        ws = Workspace(root)
        write_json_atomic(ws.paper_path, {"meta": {"title_en": f"SOURCE{number}", "pages": [{"n": 1}, {"n": 2}],
                                                 "page_count": 2, "target": "zh-TW"}, "blocks": [], "glossary": []})
        papers.append(ws)
    with patch("easyread.jobs.threading.Thread.start"):
        jobs = Jobs(lib)
    for ws in papers:
        jobs.enqueue(ws)
    barrier = threading.Barrier(2)
    calls = []
    def stream(options, prompt, cancel=None, meter=None):
        source = "SOURCE1" if "SOURCE1" in prompt else "SOURCE2"
        calls.append(source)
        barrier.wait(timeout=10)
        time.sleep(.04)
        yield json.dumps({"blocks": [{"id": f"p{p}-1", "page": p, "type": "para", "en": source, "zh": source + " 譯文"}
                                    for p in (1, 2)]})
        meter.add(input=10, output=20)
    cfg = copy.deepcopy(config.DEFAULTS)
    cfg["concurrency"] = 0
    def worker():
        try:
            jobs._bulk_loop()
        except queue.Empty:
            pass
    get = jobs.bulk.get
    with patch.object(jobs.bulk, "get", lambda: get(block=False)), patch("easyread.jobs.config.load", return_value=cfg), \
            patch("easyread.agy.stream", stream):
        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)
        assert not any(t.is_alive() for t in threads)
    assert sorted(calls) == ["SOURCE1", "SOURCE2"]
    for i, ws in enumerate(papers, 1):
        assert ws.load("job")["state"] == "done"
        assert ws.load("job")["usage"]["calls"] == 1
        assert ws.load("job")["usage"]["input"] == 10
        assert ws.load("paper")["translation"]["done_pages"] == [1, 2]
        assert all(b["en"] == f"SOURCE{i}" for b in ws.load("paper")["blocks"])
        assert jobs.preview(ws.id)["data"]["batches"] == []
