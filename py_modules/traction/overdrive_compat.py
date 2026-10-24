"""Overdrive older than 0.2.0 still drives the controller itself (spec section 3.4). Traction then writes nothing to
the controller and shows the update note."""
import os
import re

from . import settings_file

MIN_VERSION = (0, 2, 0)
UPDATE_NOTE = "Update Overdrive to 0.2.0 or newer to use Traction's controller settings."
_VERSION = re.compile(r"^\s*(\d+)\.(\d+)\.(\d+)")


def overdrive_version(plugins_dir: str):
    """(major, minor, patch) from <plugins_dir>\\overdrive\\package.json; None when there is no such file; "" when it
    exists but holds no x.y.z version (unreadable, not an object, missing or other value)."""
    if not plugins_dir:
        return None
    path = os.path.join(plugins_dir, "overdrive", "package.json")
    try:
        raw = settings_file._read_json(path)
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return "" if os.path.lexists(path) else None
    version = raw.get("version") if isinstance(raw, dict) else None
    m = _VERSION.match(version) if isinstance(version, str) else None
    if m is None:
        return ""
    return tuple(int(x) for x in m.groups())


def overdrive_holds_armoury_record(overdrive_settings_path: str) -> bool:
    """True when Overdrive's settings file still holds its own (pre-0.2.0) Armoury Crate record: armoury_replaced
    true, or a non-empty armoury_recorded / armoury_running. A missing, unreadable or wrong file holds none."""
    try:
        raw = settings_file._read_json(overdrive_settings_path)
    except (OSError, ValueError):
        return False
    if not isinstance(raw, dict):
        return False
    rec, run = raw.get("armoury_recorded"), raw.get("armoury_running")
    return (raw.get("armoury_replaced") is True or (isinstance(rec, dict) and bool(rec))
            or (isinstance(run, list) and bool(run)))


def old_overdrive(plugins_dir: str) -> bool:
    """True when Overdrive is installed with a version below 0.2.0 (an unparsable version counts as old)."""
    v = overdrive_version(plugins_dir)
    if v is None:
        return False
    return v == "" or v < MIN_VERSION
