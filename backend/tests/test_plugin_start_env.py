import json, sys
from pathlib import Path
from typing import Any
import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only plugin start path")


def _start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, flags: list[str], loader_elevated: bool) -> dict[str, str]:
    from decky_loader.plugin import plugin as plugin_mod
    from decky_loader.localplatform import winprocess, wininstall

    started: list[Any] = []

    class FakeProcess:
        def __init__(self, argv: list[str], env: dict[str, str], elevated: bool) -> None:
            self.env, self.elevated = env, elevated
        def start(self) -> None:
            started.append(self)

    monkeypatch.setattr(plugin_mod, "get_homebrew_path", lambda: str(tmp_path / "home"))
    monkeypatch.setattr(winprocess, "PluginProcess", FakeProcess)
    monkeypatch.setattr(winprocess, "is_elevated", lambda: loader_elevated)
    monkeypatch.setattr(wininstall, "user_temp_dir", lambda: r"C:\Users\me\AppData\Local\Temp")
    monkeypatch.setenv("TMP", r"C:\ProgramData\decky\tmp")
    monkeypatch.setenv("TEMP", r"C:\ProgramData\decky\tmp")

    pdir = tmp_path / "plugins" / "P"
    pdir.mkdir(parents=True)
    (pdir / "plugin.json").write_text(json.dumps({"name": "P", "author": "me", "flags": flags, "api_version": 1}))
    (pdir / "main.py").write_text("class Plugin: pass\n")

    async def noop(*_: Any) -> None:
        pass

    w = plugin_mod.PluginWrapper(str(pdir / "main.py"), "P", str(tmp_path / "plugins"), noop)
    w._start_windows() # pyright: ignore [reportPrivateUsage]
    assert len(started) == 1
    return started[0].env


def test_restricted_host_gets_the_users_own_temp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    env = _start(tmp_path, monkeypatch, [], loader_elevated=True)
    assert env["TMP"] == env["TEMP"] == r"C:\Users\me\AppData\Local\Temp"


def test_root_host_keeps_the_admin_only_temp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    env = _start(tmp_path, monkeypatch, ["root"], loader_elevated=True)
    assert env["TMP"] == env["TEMP"] == r"C:\ProgramData\decky\tmp"


def test_unelevated_loader_leaves_temp_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    env = _start(tmp_path, monkeypatch, [], loader_elevated=False)
    assert env["TMP"] == env["TEMP"] == r"C:\ProgramData\decky\tmp"
