"""shared-files.txt lists the files Overdrive and Traction share by copy: they must exist, and must not depend on
this plugin's package name or namespace."""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIST = os.path.join(ROOT, "shared-files.txt")
C_SHARP_NAMESPACES = ("Ally.Shared", "Ally.Shared.GHelper")


def listed():
    with open(LIST, encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip() and not line.startswith("#")]


def test_shared_files_exist_and_import_relatively():
    files = listed()
    assert files, "shared-files.txt is empty"
    assert len(files) == len(set(files)), "a file is listed twice"
    for rel in files:
        path = os.path.join(ROOT, rel)
        assert os.path.isfile(path), rel + " is listed but missing"
        with open(path, encoding="utf-8-sig") as f:
            text = f.read()
        if rel.endswith(".py"):
            assert not re.search(r"^\s*(from\s+overdrive\b|import\s+overdrive\b)", text, re.M), \
                rel + " imports the overdrive package by name; use relative imports"
        elif rel.endswith(".cs"):
            m = re.search(r"^\s*namespace\s+([\w.]+)", text, re.M)
            assert m and m.group(1) in C_SHARP_NAMESPACES, rel + " has namespace " + (m.group(1) if m else "none")


def test_moved_commands_refused_by_the_helper_table():
    # Overdrive's command table no longer serves the controller and touchscreen commands (Traction does).
    with open(os.path.join(ROOT, "helper", "src", "Protocol.cs"), encoding="utf-8-sig") as f:
        text = f.read()
    for cmd in ("controller_info", "set_controller_mode", "set_vibration", "get_touchscreen", "set_touchscreen"):
        assert '"' + cmd + '"' not in text, cmd
