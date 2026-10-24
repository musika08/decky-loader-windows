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


def old_overdrive(plugins_dir: str) -> bool:
    """True when Overdrive is installed with a version below 0.2.0 (an unparsable version counts as old)."""
    v = overdrive_version(plugins_dir)
    if v is None:
        return False
    return v == "" or v < MIN_VERSION
