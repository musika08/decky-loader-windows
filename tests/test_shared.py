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
