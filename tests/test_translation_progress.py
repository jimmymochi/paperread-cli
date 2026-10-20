import json
import threading
import time
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

from easyread import engines, pdfwork, usage
from easyread.translation_progress import Capture, JsonPreview, RequestSlots


@pytest.mark.parametrize("payload,expected", [
    ({"meta": {"title_zh": "not a paragraph"}, "blocks": [{"id": "p1", "type": "para", "page": 1, "en": "x", "zh": '譯文 \\frac{1}{2} "引文"。‖第二句'}]}, ['譯文 \\frac{1}{2} "引文"。第二句']),
    ({"zh": {"p1": "段落一", "p2": "段落二"}}, ["段落一", "段落二"]),
    ({"zh": "重譯結果"}, ["重譯結果"]),
    ({"blocks": [{"id": "p2", "page": 2, "zh": "wrong page"}]}, []),
])
def test_preview_at_every_chunk_boundary(payload, expected):
    text = "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"
    for split in range(len(text) + 1):
        preview = JsonPreview([1])
        preview.feed(text[:split])
        preview.feed(text[split:])
        assert [r["text"] for r in preview.rows] == expected
    preview = JsonPreview([1])
    for char in text:
        preview.feed(char)
    assert [r["text"] for r in preview.rows] == expected


def test_truncated_output_does_not_publish_incomplete_block():
    preview = JsonPreview([1])
    preview.feed('{"blocks":[{"id":"p1","page":1,"zh":"incomplete')
    assert preview.rows == []


def test_preview_does_not_change_request_result_or_token_count(tmp_path):
    cfg = {"engine": "agy", "agy": {}, "concurrency": 1}
    payload = '{"blocks":[{"id":"p1","page":1,"type":"para","zh":"譯文"}]}'
    calls, events, metrics = [], [], []
    def stream(options, prompt, cancel, meter):
        calls.append((options, prompt))
        yield payload[:-3]
        assert not events[-1]["rows"]
        yield payload[-3:-2]
        # The complete block is visible before the enclosing response ends.
        assert events[-1]["rows"][0]["text"] == "譯文"
        yield payload[-2:]
        meter.add(input=10, output=20)
    meter = usage.Meter("agy")
    with patch("easyread.agy.stream", stream):
        with Capture([1], events.append, metrics.append):
            assert engines.run(cfg, "same prompt", tmp_path, meter=meter) == payload
    assert calls == [({}, "same prompt")]
    assert meter.snapshot()["calls"] == 1
    assert meter.snapshot()["input"] == 10
    assert events[-1]["phase"] == "clear"
    assert metrics[0]["requests"][0]["first_preview_seconds"] >= 0


def test_shared_slots_bound_simultaneous_papers_and_cancel_waiters():
    slots = RequestSlots()
    lock, active, peak, papers = threading.Lock(), 0, 0, set()
    def work(paper):
        nonlocal active, peak
        with slots.slot(2, None):
            with lock:
                active += 1
                peak = max(peak, active)
                papers.add(paper)
            time.sleep(.03)
            with lock:
                active -= 1
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(work, ["one", "two"] * 4))
    assert peak == 2 and papers == {"one", "two"}
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(engines.Cancelled), slots.slot(1, cancel):
        pass
    assert not slots.waiters and slots.active == 0


def test_normalized_character_cache_keeps_ligatures_and_unicode_indices(tmp_path):
    chars = [[ch, 0, 0, 1, 1] for ch in "AaAa ﬁﬁ ﬂﬂ １２３繁體e\u0301"]
    (tmp_path / "page-001.chars.json").write_text(json.dumps(chars), encoding="utf-8")
    expected_text, expected_idx = [], []
    for i, c in enumerate(chars):
        for char in pdfwork._norm(c[0]):
            expected_text.append(char)
            expected_idx.append(i)
    text, idx, got = pdfwork._page_stream(tmp_path, 1)
    assert (text, idx, got) == ("".join(expected_text), expected_idx, chars)


def test_codex_observed_output_keeps_final_file_usage_and_drains_both_pipes(tmp_path):
    payload = '{"blocks":[{"id":"p1","page":1,"type":"para","zh":"result"}]}'
    script = tmp_path / "fake_codex.py"
    script.write_text(
        "import sys,json,time\nfrom pathlib import Path\n"
        "assert len(sys.stdin.read()) == 100000\n"
        "sys.stderr.write('diagnostic' * 20000)\nsys.stderr.flush()\n"
        f"text = {payload!r}\n"
        "Path(sys.argv[1]).write_text(text, encoding='utf-8')\n"
        "print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':text}}), flush=True)\n"
        "time.sleep(.05)\n"
        "print(json.dumps({'type':'turn.completed','usage':{'input_tokens':10,'output_tokens':20}}), flush=True)\n",
        encoding="utf-8")
    def popen(args, cwd):
        output = args[args.index("-o") + 1]
        return subprocess.Popen([sys.executable, str(script), output], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding="utf-8", creationflags=engines._NO_WINDOW)
    events, meter = [], usage.Meter("codex")
    with patch.object(engines, "codex_path", return_value="fake"), patch.object(engines, "_popen", popen), \
            patch.object(engines.netcheck, "problem", return_value=None), Capture([1], events.append):
        assert engines.run({"engine": "codex", "codex": {}, "concurrency": 1}, "x" * 100000, tmp_path, meter=meter) == payload
    assert meter.snapshot()["calls"] == 1
    assert meter.snapshot()["output"] == 20
    assert any(e["rows"] and e["rows"][0]["text"] == "result" for e in events)
