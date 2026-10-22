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


LINKS = (".steam/root", ".steam/steam", ".local/share/Steam")


def test_creates_all_junctions(tmp_path: Path):
    steam, home = _steam(tmp_path), tmp_path / "home"
    home.mkdir()
    winsteamlinks.ensure_steam_home_links(str(home), str(steam))
    for name in LINKS:
        link = home / name
        assert os.path.isjunction(link)
        assert (link / "config").is_dir()
    for parent in (".steam", ".local", ".local/share"):
        assert (home / parent).is_dir() and not os.path.isjunction(home / parent)


def test_existing_local_share_is_kept(tmp_path: Path):
    steam, home = _steam(tmp_path), tmp_path / "home"
    (home / ".local" / "share" / "other-app").mkdir(parents=True)
    winsteamlinks.ensure_steam_home_links(str(home), str(steam))
    assert (home / ".local" / "share" / "other-app").is_dir()
    assert os.path.isjunction(home / ".local" / "share" / "Steam")


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
    assert not (home / ".local" / "share" / "Steam.decky-old").exists()
    for name in LINKS:
        assert os.path.isjunction(home / name)


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
    assert os.path.isjunction(home / ".local" / "share" / "Steam")


@pytest.mark.parametrize("parent", [".local", ".local/share"])
def test_local_share_parent_junction_left_alone(tmp_path: Path, parent: str):
    steam, home, elsewhere = _steam(tmp_path), tmp_path / "home", tmp_path / "elsewhere"
    (home / parent).parent.mkdir(parents=True, exist_ok=True)
    elsewhere.mkdir()
    import _winapi
    _winapi.CreateJunction(str(elsewhere), str(home / parent))
    winsteamlinks.ensure_steam_home_links(str(home), str(steam))
    assert os.listdir(elsewhere) == []
    assert os.path.isjunction(home / ".steam" / "root")


def test_parent_that_is_a_file_left_alone(tmp_path: Path):
    steam, home = _steam(tmp_path), tmp_path / "home"
    home.mkdir()
    (home / ".local").write_text("x")
    winsteamlinks.ensure_steam_home_links(str(home), str(steam))
    assert (home / ".local").read_text() == "x"
    assert os.path.isjunction(home / ".steam" / "steam")


def test_existing_local_share_steam_dir_is_moved_aside(tmp_path: Path):
    steam, home = _steam(tmp_path), tmp_path / "home"
    old = home / ".local" / "share" / "Steam"
    old.mkdir(parents=True)
    (old / "note.txt").write_text("x")
    winsteamlinks.ensure_steam_home_links(str(home), str(steam))
    assert os.path.isjunction(old)
    assert (home / ".local" / "share" / "Steam.decky-old" / "note.txt").read_text() == "x"


def test_setup_skips_when_elevated(monkeypatch: pytest.MonkeyPatch):
    called: list[object] = []
    monkeypatch.setattr(winsteamlinks.ctypes.windll.shell32, "IsUserAnAdmin", lambda: 1)
    monkeypatch.setattr(winsteamlinks, "ensure_steam_home_links", lambda *a: called.append(a))
    winsteamlinks.setup_steam_home_links()
    assert called == []
