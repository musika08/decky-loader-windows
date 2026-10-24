import importlib.util
import json
import os
import zipfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load():
    spec = importlib.util.spec_from_file_location("make_zip", os.path.join(ROOT, "tools", "make-zip.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "package.json").write_text(json.dumps({"version": "9.8.7"}))
    for n in ("plugin.json", "main.py", "README.md", "LICENSE", "THIRD-PARTY-NOTICES.md"):
        (repo / n).write_text("x")
    (repo / "dist").mkdir()
    (repo / "dist" / "index.js").write_text("js")
    (repo / "bin").mkdir()
    (repo / "bin" / "traction-helper.exe").write_bytes(b"MZ")
    (repo / "py_modules" / "pkg" / "__pycache__").mkdir(parents=True)
    (repo / "py_modules" / "pkg" / "mod.py").write_text("y")
    (repo / "py_modules" / "pkg" / "__pycache__" / "mod.cpython-311.pyc").write_bytes(b"c")
    (repo / "extra.txt").write_text("not shipped")
    return repo


def test_zip_layout(tmp_path):
    mod = _load()
    repo = _fake_repo(tmp_path)
    out, digest = mod.build_zip(str(repo), str(tmp_path / "out"))
    assert os.path.basename(out) == "traction-9.8.7.zip"
    assert len(digest) == 64
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
    assert all(n.startswith("traction/") for n in names)
    assert not any("__pycache__" in n or n.endswith(".pyc") for n in names)
    tops = {n.split("/")[1] for n in names}
    assert tops == {"plugin.json", "main.py", "package.json", "README.md", "LICENSE",
                    "THIRD-PARTY-NOTICES.md", "dist", "py_modules", "bin"}
    assert "traction/py_modules/pkg/mod.py" in names
    assert "traction/bin/traction-helper.exe" in names


def test_missing_build_fails(tmp_path):
    mod = _load()
    repo = _fake_repo(tmp_path)
    (repo / "dist" / "index.js").unlink()
    with pytest.raises(FileNotFoundError):
        mod.build_zip(str(repo), str(tmp_path / "out"))
