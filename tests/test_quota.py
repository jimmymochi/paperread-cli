import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from easyread import quota, settings_api


class QuotaTest(unittest.TestCase):
    def setUp(self):
        quota._cache.clear()

    def test_agy_remaining_is_not_used_and_invalid_rows_are_ignored(self):
        rows = quota.parse_agy("Gemini\tWeekly Limit Remaining\t87%\t2030-01-01T00:00:00Z\n"
                               "Other\tLimit\t0%\tunknown\nBad\tLimit\t101%\tx\n"
                               "Bad\tLimit\tunknown\tx\nnoise")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["remaining_percent"], 87)
        self.assertEqual(rows[0]["resets_at"], 1893456000)
        self.assertEqual(rows[1]["remaining_percent"], 0)
        self.assertIsNone(rows[1]["resets_at"])

    def test_codex_prefers_buckets_and_uses_actual_window_duration(self):
        rows = quota.parse_codex({"rateLimits": {"primary": {"usedPercent": 99}}, "rateLimitsByLimitId": {
            "codex": {"primary": {"usedPercent": 22, "windowDurationMins": 10080, "resetsAt": 1900000000},
                      "secondary": None},
            "other": {"primary": {"usedPercent": 110, "windowDurationMins": 60},
                      "secondary": {"usedPercent": None}},
        }})
        self.assertEqual([r["remaining_percent"] for r in rows], [78, 0])
        self.assertEqual(rows[0]["window_minutes"], 10080)
        self.assertEqual(rows[1]["group"], "other")

    def test_missing_and_nonfinite_codex_limits_are_not_zero(self):
        for used in (None, True, "20", float("nan"), float("inf")):
            self.assertEqual(quota.parse_codex({"rateLimits": {"primary": {"usedPercent": used}}}), [])
        self.assertEqual(quota.parse_codex({"rateLimits": None}), [])
        self.assertEqual(quota.parse_codex({"rateLimitsByLimitId": {"a": None}}), [])

    def test_cache_refresh_failure_keeps_labeled_snapshot(self):
        snapshot = [{"group": "Gemini", "remaining_percent": 80}]
        with mock.patch.object(quota.config, "load", return_value={"agy": {}}), \
             mock.patch.object(quota.agy, "path", return_value="agy"), \
             mock.patch.object(quota, "_agy", return_value=snapshot) as query, \
             mock.patch.object(quota.time, "monotonic", return_value=100):
            first = quota.read("agy")
            second = quota.read("agy", fresh=True)
            self.assertEqual(first, second)
            self.assertEqual(query.call_count, 1)
            second["rows"].clear()
            self.assertEqual(len(quota.read("agy")["rows"]), 1)
            with mock.patch.object(quota.time, "monotonic", return_value=120), \
                 mock.patch.object(quota, "_agy", side_effect=TimeoutError):
                stale = quota.read("agy", fresh=True)
            self.assertEqual(stale["status"], "stale")
            self.assertEqual(stale["reason"], "timeout")
            self.assertEqual(stale["updated_at"], first["updated_at"])
            self.assertEqual(stale["rows"][0]["remaining_percent"], 80)

    def test_cli_missing_and_unsupported_do_not_start_process(self):
        with mock.patch.object(quota.config, "load", return_value={"agy": {}}), \
             mock.patch.object(quota.agy, "path", return_value=None), \
             mock.patch.object(quota.subprocess, "Popen") as spawn:
            self.assertEqual(quota.read("agy")["reason"], "cli_missing")
            self.assertEqual(settings_api.account_quota({"engine": "unknown"})["reason"], "unsupported")
            spawn.assert_not_called()

    def test_agy_timeout_cleans_up_owned_process(self):
        proc = mock.Mock()
        proc.communicate.side_effect = subprocess.TimeoutExpired("agy", 25)
        with mock.patch.object(quota.subprocess, "Popen", return_value=proc), \
             mock.patch.object(quota.agy, "stop") as stop:
            with self.assertRaises(subprocess.TimeoutExpired):
                quota._agy("agy", ".")
            stop.assert_called_once_with(proc)
            proc.stdout.close.assert_called_once()

    def test_codex_rpc_handshake_only_reads_limits(self):
        # A real child process validates newline framing, handshake, and cleanup.
        script = '''import json,sys
initialize=json.loads(sys.stdin.readline())
assert initialize['method']=='initialize'
print(json.dumps({'id':initialize['id'],'result':{}}),flush=True)
assert json.loads(sys.stdin.readline())['method']=='initialized'
request=json.loads(sys.stdin.readline())
assert request['method']=='account/rateLimits/read'
print(json.dumps({'method':'unrelated/notification','params':{}}),flush=True)
print(json.dumps({'id':request['id'],'result':{'rateLimits':{'primary':{'usedPercent':25,'windowDurationMins':60}}}}),flush=True)
assert sys.stdin.read()==''
'''
        real_popen = subprocess.Popen
        children = []
        def spawn(args, **kwargs):
            self.assertEqual(args[:4], ["codex", "app-server", "--listen", "stdio://"])
            proc = real_popen([sys.executable, "-u", str(fake)], **kwargs)
            children.append(proc)
            return proc
        with tempfile.TemporaryDirectory() as work:
            fake = Path(work) / "fake.py"
            fake.write_text(script, encoding="utf-8")
            with mock.patch.object(quota.subprocess, "Popen", side_effect=spawn), \
                 mock.patch.object(quota.codex_lean, "args", return_value=[]):
                rows = quota._codex("codex", work)
            self.assertEqual(rows[0]["remaining_percent"], 75)
            self.assertEqual(children[0].poll(), 0)
            self.assertTrue(children[0].stdout.closed)


if __name__ == "__main__":
    unittest.main()
