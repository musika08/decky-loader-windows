import os, sys
from pathlib import Path
import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only module")

if sys.platform == "win32":
    from decky_loader.localplatform import winsteammovies


def _setup(tmp_path: Path):
    steam = tmp_path / "Steam"
    movies = steam / "config" / "uioverrides" / "movies"
    movies.mkdir(parents=True)
    anim = tmp_path / "anim.webm"
    anim.write_bytes(b"video")
    s, r, u = winsteammovies.wrap(str(movies), os.link, os.remove, os.unlink)
    return movies, anim, s, r, u


def test_placing_deck_movie_also_places_bigpicture(tmp_path: Path):
    movies, anim, symlink, _, _ = _setup(tmp_path)
    symlink(str(anim), str(movies / "deck_startup.webm"))
    assert (movies / "bigpicture_startup.webm").read_bytes() == b"video"


def test_replacing_updates_the_mirror(tmp_path: Path):
    movies, anim, symlink, remove, _ = _setup(tmp_path)
    other = tmp_path / "other.webm"
    other.write_bytes(b"other")
    symlink(str(anim), str(movies / "deck_startup.webm"))
    remove(str(movies / "deck_startup.webm"))
    symlink(str(other), str(movies / "deck_startup.webm"))
    assert (movies / "bigpicture_startup.webm").read_bytes() == b"other"


def test_removing_deck_movie_removes_mirror(tmp_path: Path):
    movies, anim, symlink, remove, unlink = _setup(tmp_path)
    symlink(str(anim), str(movies / "deck_startup.webm"))
    remove(str(movies / "deck_startup.webm"))
    assert not (movies / "bigpicture_startup.webm").exists()
    symlink(str(anim), str(movies / "deck_startup.webm"))
    unlink(str(movies / "deck_startup.webm"))
    assert not (movies / "bigpicture_startup.webm").exists()


def test_path_through_junction_is_recognised(tmp_path: Path):
    movies, anim, symlink, _, _ = _setup(tmp_path)
    import _winapi
    home_root = tmp_path / "root"
    _winapi.CreateJunction(str(tmp_path / "Steam"), str(home_root))
    symlink(str(anim), str(home_root / "config" / "uioverrides" / "movies" / "deck_startup.webm"))
    assert (movies / "bigpicture_startup.webm").exists()


def test_other_files_untouched(tmp_path: Path):
    movies, anim, symlink, remove, _ = _setup(tmp_path)
    symlink(str(anim), str(movies / "steam_os_suspend.webm"))
    symlink(str(anim), str(tmp_path / "deck_startup.webm"))
    remove(str(movies / "steam_os_suspend.webm"))
    assert not (movies / "bigpicture_startup.webm").exists()
