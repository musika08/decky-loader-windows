"""The shared copy (py_modules/traction/*.py shared with Overdrive, helper/src/shared/**) must match shared/SOURCE."""
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECK = os.path.join(ROOT, "tools", "check-shared.py")


def run_check(root):
    return subprocess.run([sys.executable, "-I", CHECK, "--root", root], capture_output=True, text=True, timeout=60)


def test_shared_copy_matches_source():
    res = run_check(ROOT)
    assert res.returncode == 0, res.stdout + res.stderr


def test_tampered_copy_fails(tmp_path):
    shutil.copytree(os.path.join(ROOT, "shared"), str(tmp_path / "shared"))
    shutil.copytree(os.path.join(ROOT, "py_modules"), str(tmp_path / "py_modules"))
    shutil.copytree(os.path.join(ROOT, "helper", "src", "shared"), str(tmp_path / "helper" / "src" / "shared"))
    assert run_check(str(tmp_path)).returncode == 0, "an untouched copy must pass"
    target = tmp_path / "py_modules" / "traction" / "device.py"
    data = bytearray(target.read_bytes())
    data[0] ^= 0x01
    target.write_bytes(bytes(data))
    res = run_check(str(tmp_path))
    assert res.returncode == 1, res.stdout + res.stderr
    assert "device.py" in res.stdout


def test_traction_core_records_its_g_helper_origin():
    """Final review T6: TractionCore.cs ports AllyControl.cs's controller reports, so it carries the same G-Helper
    attribution and modification notice as the shared C# files."""
    with open(os.path.join(ROOT, "helper", "src", "TractionCore.cs"), encoding="utf-8-sig") as f:
        head = f.read().split("using System;", 1)[0]
    assert "// G-Helper - https://github.com/seerge/g-helper - app/Ally/AllyControl.cs" in head
    assert ("// Copyright seerge and contributors, licensed GPL-3.0. Commit "
            "c09017453d9ed0c52261b67a48d02bd55232e6ac.") in head
    assert "// Modified for Traction (ported to C# 5 / .NET Framework 4.8), 2026" in head
