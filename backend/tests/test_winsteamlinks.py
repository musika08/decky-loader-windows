import os, sys
from pathlib import Path
import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only module")

if sys.platform == "win32":
    from decky_loader.localplatform import winsteamlinks


def _steam(tmp_path: Path) -> Path:
    steam = tmp_path / "Steam"
    (steam / "config").mkdir(parents=True)
    (steam / "steam.exe").write_text("")
    return steam


def test_creates_both_junctions(tmp_path: Path):
    steam, home = _steam(tmp_path), tmp_path / "home"
    home.mkdir()
    winsteamlinks.ensure_steam_home_links(str(home), str(steam))
    for name in ("root", "steam"):
        link = home / ".steam" / name
        assert os.path.isjunction(link)
        assert (link / "config").is_dir()


def test_existing_plain_dir_is_moved_aside(tmp_path: Path):
    steam, home = _steam(tmp_path), tmp_path / "home"
    movies = home / ".steam" / "root" / "config" / "uioverrides" / "movies"
    movies.mkdir(parents=True)
    (movies / "deck_startup.webm").write_text("x")
    winsteamlinks.ensure_steam_home_links(str(home), str(steam))
    assert os.path.isjunction(home / ".steam" / "root")
    assert (home / ".steam" / "root.decky-old" / "config" / "uioverrides" / "movies" / "deck_startup.webm").is_file()


def test_idempotent(tmp_path: Path):
    steam, home = _steam(tmp_path), tmp_path / "home"
    home.mkdir()
    winsteamlinks.ensure_steam_home_links(str(home), str(steam))
    winsteamlinks.ensure_steam_home_links(str(home), str(steam))
    assert not (home / ".steam" / "root.decky-old").exists()
    assert os.path.isjunction(home / ".steam" / "root")


def test_foreign_junction_left_alone(tmp_path: Path):
    steam, home, other = _steam(tmp_path), tmp_path / "home", tmp_path / "other"
    other.mkdir()
    (home / ".steam").mkdir(parents=True)
    import _winapi
    _winapi.CreateJunction(str(other), str(home / ".steam" / "root"))
    winsteamlinks.ensure_steam_home_links(str(home), str(steam))
    assert os.path.realpath(home / ".steam" / "root") == os.path.realpath(other)


def test_dot_steam_junction_left_alone(tmp_path: Path):
    steam, home, elsewhere = _steam(tmp_path), tmp_path / "home", tmp_path / "elsewhere"
    home.mkdir()
    elsewhere.mkdir()
    import _winapi
    _winapi.CreateJunction(str(elsewhere), str(home / ".steam"))
    winsteamlinks.ensure_steam_home_links(str(home), str(steam))
    assert os.listdir(elsewhere) == []


def test_setup_skips_when_elevated(monkeypatch: pytest.MonkeyPatch):
    called: list[object] = []
    monkeypatch.setattr(winsteamlinks.ctypes.windll.shell32, "IsUserAnAdmin", lambda: 1)
    monkeypatch.setattr(winsteamlinks, "ensure_steam_home_links", lambda *a: called.append(a))
    winsteamlinks.setup_steam_home_links()
    assert called == []
