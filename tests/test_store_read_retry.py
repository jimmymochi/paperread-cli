import io
from unittest.mock import patch

import pytest

from easyread.store import read_json


def test_transient_read_lock_retries_without_returning_empty_data():
    with patch("builtins.open", side_effect=[PermissionError(), PermissionError(), io.StringIO('{"value":42}')]) as opened, \
            patch("easyread.store.time.sleep") as sleep:
        assert read_json("paper.json", {}) == {"value": 42}
    assert opened.call_count == 3 and sleep.call_count == 2


def test_persistent_read_error_and_bad_json_are_not_reported_as_missing():
    with patch("builtins.open", side_effect=PermissionError()), patch("easyread.store.time.sleep"):
        with pytest.raises(PermissionError):
            read_json("paper.json", {})
    with patch("builtins.open", return_value=io.StringIO("broken")):
        with pytest.raises(ValueError):
            read_json("paper.json", {})
    with patch("builtins.open", side_effect=FileNotFoundError()):
        assert read_json("missing.json", {"default": True}) == {"default": True}
