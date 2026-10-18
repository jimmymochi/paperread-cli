import copy
import json
import subprocess
import sys
import threading
import time
from unittest.mock import patch

import pytest

from easyread import agy, chat, chat_models, config, detect, engines, usage


def test_catalog_parser_and_invalid_models():
    assert agy.parse_models("Fetching...\nmodel-high\tModel High\nmodel-high\tDuplicate") == [{"id": "model-high", "name": "Model High"}]
    with pytest.raises(ValueError):
        agy.parse_models("please login")
    for model in ("--help", "a\n--help", "a b"):
        with pytest.raises(engines.EngineError):
            agy.argv({"model": model}, "agy")
    args = agy.argv({"model": "model-high"}, "agy")
    assert args[-2:] == ["--model", "model-high"]
    assert "--model" not in agy.argv({}, "agy")
    assert "--dangerously-skip-permissions" not in args


def test_model_card_roundtrip_without_api_key():
    cfg = copy.deepcopy(config.DEFAULTS)
    cfg["chat"] = {"models": chat_models.sanitize([{"id": "agy", "engine": "agy", "model": "model-high"}]), "default": "agy"}
    selected, card = chat_models.engine_cfg(cfg, "agy")
    assert selected["engine"] == "agy"
    assert selected["agy"]["model"] == "model-high"
    assert not selected["openai"]["api_key"]
    assert engines.image_mode(selected) == "text"
    assert detect.ready(selected, {"agy": {"found": True}})
    assert chat_models.translation_id(selected) == "agy"
    with patch("easyread.detect.detect", return_value={"agy": {"found": True}}):
        assert chat_models.listing(selected)["models"][0]["source"] == "Antigravity CLI"


def test_catalog_cache_and_failure(tmp_path):
    agy._catalog.clear()
    with patch.object(config, "HOME", tmp_path), patch.object(agy, "path", return_value="fake-agy"), \
            patch.object(agy.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "m\tModel", "")) as run:
        assert agy.models({})["models"][0]["id"] == "m"
        assert agy.models({})["models"][0]["id"] == "m"
        assert run.call_count == 1
        run.side_effect = subprocess.TimeoutExpired("agy", 20)
        assert agy.models({}, fresh=True)["stale"]
    with patch.object(agy, "path", return_value=None):
        assert "尚未安裝" in agy.models({})["error"]


def fake_cli(tmp_path, body):
    file = tmp_path / "fake_agy.py"
    file.write_text("import sys,json,time\nrequest=json.loads(sys.stdin.readline())\n" + body, encoding="utf-8")
    return patch.object(agy, "argv", return_value=[sys.executable, "-X", "utf8", str(file)])


def test_stream_unicode_deduplicates_and_tracks_usage(tmp_path):
    body = '''assert "學術" in request["message"]["content"]
print(json.dumps({"event":"step_update","step_update":{"step_type":"thinking","text_delta":"SECRET_THOUGHT"}}),flush=True)
print(json.dumps({"event":"step_update","step_update":{"step_type":"agent_response","text_delta":"答"}}),flush=True)
print(json.dumps({"event":"result","result":{"status":"SUCCESS","response":"答案","usage":{"input_tokens":5,"output_tokens":2}}}),flush=True)
sys.stdin.read()
'''
    meter = usage.Meter("agy")
    with patch.object(agy, "path", return_value="fake"), fake_cli(tmp_path, body):
        assert agy.complete({}, "學術" * 18000, meter=meter) == "答案"
    assert meter.snapshot()["calls"] == 1
    assert meter.snapshot()["input"] == 5


@pytest.mark.parametrize("body, message", [
    ('print(json.dumps({"event":"result","result":{"status":"ERROR","error":"login required"}}),flush=True)\n', "login required"),
    ('sys.exit(2)\n', "提前結束"),
    ('time.sleep(20)\n', "逾時"),
])
def test_failures_not_reported_as_success(tmp_path, body, message):
    with patch.object(agy, "path", return_value="fake"), fake_cli(tmp_path, body):
        with pytest.raises(engines.EngineError, match=message):
            agy.complete({"timeout": 1}, "hello")


def test_cancel_terminates_child(tmp_path):
    cancel = threading.Event()
    real_popen = subprocess.Popen
    children = []
    def spawn(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        if isinstance(args[0], list) and args[0][0] == sys.executable:
            children.append(proc)
        return proc
    timer = threading.Timer(0.4, cancel.set)
    with patch.object(agy, "path", return_value="fake"), fake_cli(tmp_path, "time.sleep(30)\n"), patch.object(agy.subprocess, "Popen", side_effect=spawn):
        timer.start()
        started = time.monotonic()
        with pytest.raises(engines.Cancelled):
            agy.complete({}, "hello", cancel)
        timer.join()
    assert time.monotonic() - started < 10
    assert children and all(p.poll() is not None for p in children)


def test_chat_dispatches_native_agy():
    cfg = copy.deepcopy(config.DEFAULTS)
    with patch.object(agy, "stream", return_value=iter(["native", " agy"])) as call:
        assert "".join(chat.stream(cfg, "question", config.HOME, threading.Event())) == "native agy"
        assert call.call_args.args[0] == cfg["agy"]
