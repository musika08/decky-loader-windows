import json, sys
from pathlib import Path
from typing import Any
import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only plugin start path")


def _start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, flags: list[str], loader_elevated: bool) -> tuple[dict[str, str], bool]:
    from decky_loader.plugin import plugin as plugin_mod
    from decky_loader.localplatform import winprocess

    started: list[Any] = []

    class FakeProcess:
        def __init__(self, argv: list[str], env: dict[str, str], elevated: bool) -> None:
            self.env, self.elevated = env, elevated
        def start(self) -> None:
            started.append(self)

    monkeypatch.setattr(plugin_mod, "get_homebrew_path", lambda: str(tmp_path / "home"))
    monkeypatch.setattr(winprocess, "PluginProcess", FakeProcess)
    monkeypatch.setattr(winprocess, "is_elevated", lambda: loader_elevated)
    monkeypatch.setenv("TMP", r"C:\Users\me\AppData\Local\Temp")
    monkeypatch.setenv("TEMP", r"C:\Users\me\AppData\Local\Temp")

    pdir = tmp_path / "plugins" / "P"
    pdir.mkdir(parents=True)
    (pdir / "plugin.json").write_text(json.dumps({"name": "P", "author": "me", "flags": flags, "api_version": 1}))
    (pdir / "main.py").write_text("class Plugin: pass\n")

    async def noop(*_: Any) -> None:
        pass

    w = plugin_mod.PluginWrapper(str(pdir / "main.py"), "P", str(tmp_path / "plugins"), noop)
    w._start_windows() # pyright: ignore [reportPrivateUsage]
    assert len(started) == 1
    return started[0].env, started[0].elevated


@pytest.mark.parametrize("flags, loader_elevated", [([], True), (["root"], True), ([], False)])
def test_hosts_inherit_the_loaders_temp_and_get_a_fresh_pyinstaller_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                        flags: list[str], loader_elevated: bool):
    # Installed mode no longer points the loader at an admin-only temp dir, so no host needs its TMP changed
    env, elevated = _start(tmp_path, monkeypatch, flags, loader_elevated)
    assert env["TMP"] == env["TEMP"] == r"C:\Users\me\AppData\Local\Temp"
    assert env["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert elevated is ("root" in flags)
