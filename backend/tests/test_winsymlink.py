import os, sys
from pathlib import Path
import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only module")

if sys.platform == "win32":
    from decky_loader.localplatform import winsymlink


def _fail(code: int = 1314):
    def _raise(*_a: object, **_k: object) -> None:
        raise OSError(None, "x", None, code)
    return _raise


def test_dir_target_becomes_junction(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("hi")
    dst = tmp_path / "link"
    winsymlink.symlink_with_fallback(str(src), str(dst), True, _symlink=_fail())
    assert os.path.isjunction(dst)
    assert os.listdir(dst) == os.listdir(src)


def test_dir_detected_without_flag(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    dst = tmp_path / "link"
    winsymlink.symlink_with_fallback(str(src), str(dst), False, _symlink=_fail())
    assert os.path.isjunction(dst)


def test_relative_dir_target_resolves_against_link_parent(tmp_path: Path):
    themes = tmp_path / "themes"
    themes.mkdir()
    (themes / "t.css").write_text("x")
    (tmp_path / "a").mkdir()
    dst = tmp_path / "a" / "link"
    winsymlink.symlink_with_fallback("../themes", str(dst), True, _symlink=_fail())
    assert os.path.isjunction(dst)
    assert os.path.samefile(dst, themes)
    assert os.listdir(dst) == ["t.css"]


def test_file_target_becomes_hardlink(tmp_path: Path):
    src = tmp_path / "f.txt"
    src.write_text("data")
    dst = tmp_path / "g.txt"
    winsymlink.symlink_with_fallback(str(src), str(dst), False, _symlink=_fail())
    assert dst.read_text() == "data"
    assert os.stat(dst).st_nlink == 2


def test_file_cross_volume_falls_back_to_copy(tmp_path: Path):
    src = tmp_path / "f.txt"
    src.write_bytes(b"abc")
    dst = tmp_path / "g.txt"
    winsymlink.symlink_with_fallback(str(src), str(dst), False, _symlink=_fail(), _link=_fail(17))
    assert dst.read_bytes() == b"abc"
    assert os.stat(dst).st_nlink == 1


def test_other_errors_propagate(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    with pytest.raises(OSError) as e:
        winsymlink.symlink_with_fallback(str(src), str(tmp_path / "l"), True, _symlink=_fail(5))
    assert e.value.winerror == 5
    assert not (tmp_path / "l").exists()


def test_dir_fd_reraises_original(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    with pytest.raises(OSError) as e:
        winsymlink.symlink_with_fallback(str(src), str(tmp_path / "l"), True, dir_fd=3, _symlink=_fail())
    assert e.value.winerror == 1314
    assert not (tmp_path / "l").exists()


def test_missing_file_source_reraises_and_creates_nothing(tmp_path: Path):
    dst = tmp_path / "l"
    with pytest.raises(OSError) as e:
        winsymlink.symlink_with_fallback(str(tmp_path / "nope"), str(dst), False, _symlink=_fail())
    assert e.value.winerror == 1314
    assert not os.path.lexists(dst)


def test_existing_dst_raises_file_exists(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    dst = tmp_path / "l"
    dst.mkdir()
    with pytest.raises(FileExistsError):
        winsymlink.symlink_with_fallback(str(src), str(dst), True, _symlink=_fail())


def test_path_symlink_to_uses_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    src = tmp_path / "src"
    src.mkdir()
    dst = tmp_path / "link"
    monkeypatch.setattr(os, "symlink", os.symlink)  # restored on teardown
    monkeypatch.setattr(winsymlink, "_REAL_SYMLINK", _fail())
    winsymlink.install_symlink_fallback()
    Path(dst).symlink_to(src, target_is_directory=True)
    assert os.path.isjunction(dst)


def test_install_is_idempotent(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(os, "symlink", os.symlink)
    winsymlink.install_symlink_fallback()
    first = os.symlink
    winsymlink.install_symlink_fallback()
    assert os.symlink is first
