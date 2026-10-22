import pytest

from decky_loader.localplatform import localplatform


def test_live_reload_is_off_without_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("LIVE_RELOAD", raising=False)
    assert localplatform.get_live_reload() is False


@pytest.mark.parametrize("value, expected", [("1", True), ("0", False), ("yes", False)])
def test_live_reload_env_override(monkeypatch: pytest.MonkeyPatch, value: str, expected: bool):
    monkeypatch.setenv("LIVE_RELOAD", value)
    assert localplatform.get_live_reload() is expected
