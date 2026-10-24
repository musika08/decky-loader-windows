"""Overdrive older than 0.2.0 still drives the controller itself (spec section 3.4). Traction then writes nothing to
the controller and shows the update note.

Overdrive is found the way Decky finds plugins: the folder under the plugins folder whose plugin.json is named
"Overdrive" (decky-loader plugin/plugin.py reads json["name"]), whatever the folder is called. Decky gives each plugin
<settings root>\\<plugin folder>, so its settings file is <settings root>\\<that folder>\\overdrive.json.
"""
import os
import re
from typing import Optional

from . import settings_file

MIN_VERSION = (0, 2, 0)
OVERDRIVE_NAME = "Overdrive"
OVERDRIVE_SETTINGS = "overdrive.json"
UPDATE_NOTE = "Update Overdrive to 0.2.0 or newer to use Traction's controller settings."
_VERSION = re.compile(r"^\s*(\d+)\.(\d+)\.(\d+)")


def _entries(folder: str) -> list:
    if not folder:
        return []
    try:
        return sorted(os.listdir(folder))
    except OSError:
        return []


def find_overdrive(plugins_dir: str) -> Optional[str]:
    """The folder name (under plugins_dir) of the installed Overdrive: the first, by name, whose plugin.json has
    "name" == "Overdrive". Unreadable or odd plugin.json files are skipped. None when there is none."""
    for entry in _entries(plugins_dir):
        try:
            raw = settings_file._read_json(os.path.join(plugins_dir, entry, "plugin.json"))
        except (OSError, ValueError):
            continue
        if isinstance(raw, dict) and raw.get("name") == OVERDRIVE_NAME:
            return entry
    return None


def find_overdrive_settings(settings_root: str) -> str:
    """With no Overdrive installed: the first <settings root>\\*\\overdrive.json (by folder name) that exists, else
    ""."""
    for entry in _entries(settings_root):
        path = os.path.join(settings_root, entry, OVERDRIVE_SETTINGS)
        if os.path.lexists(path):
            return path
    return ""


def overdrive_version(plugins_dir: str):
    """(major, minor, patch) from the installed Overdrive's package.json; None when Overdrive is not installed; ""
    when it is but its package.json holds no x.y.z version (missing, unreadable, not an object, missing or other
    value)."""
    folder = find_overdrive(plugins_dir)
    if folder is None:
        return None
    try:
        raw = settings_file._read_json(os.path.join(plugins_dir, folder, "package.json"))
    except (OSError, ValueError):
        return ""
    version = raw.get("version") if isinstance(raw, dict) else None
    m = _VERSION.match(version) if isinstance(version, str) else None
    if m is None:
        return ""
    return tuple(int(x) for x in m.groups())


def _read_object(path: str):
    """The parsed JSON object; {} when the file is absent; None when it exists but cannot be read or is not an
    object."""
    try:
        raw = settings_file._read_json(path)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def read_old_record(overdrive_settings_path: str):
    """Overdrive's settings as far as its old Armoury Crate record goes: overdrive.json, or overdrive.json.bak when
    overdrive.json exists but cannot be read (as Overdrive itself recovers it). {} when there is nothing (no
    overdrive.json, or an unreadable one with no .bak: Overdrive resets that to no record). None when neither file
    can be read, so the record is unknown. Only read, never changed."""
    if not overdrive_settings_path:
        return {}
    raw = _read_object(overdrive_settings_path)
    if raw is not None:
        return raw
    return _read_object(overdrive_settings_path + ".bak")


def _holds(raw: dict) -> bool:
    rec, run = raw.get("armoury_recorded"), raw.get("armoury_running")
    return (raw.get("armoury_replaced") is True or (isinstance(rec, dict) and bool(rec))
            or (isinstance(run, list) and bool(run)))


def overdrive_holds_armoury_record(overdrive_settings_path: str) -> bool:
    """True when Overdrive's settings file still holds its own (pre-0.2.0) Armoury Crate record: armoury_replaced
    true, or a non-empty armoury_recorded / armoury_running. A missing file holds none. An unreadable one (fail closed)
    holds one when its .bak does, or when the .bak cannot be read either; a readable .bak without one, or no .bak,
    means none."""
    raw = read_old_record(overdrive_settings_path)
    return True if raw is None else _holds(raw)


def old_overdrive(plugins_dir: str) -> bool:
    """True when Overdrive is installed with a version below 0.2.0 (an unparsable version counts as old)."""
    v = overdrive_version(plugins_dir)
    if v is None:
        return False
    return v == "" or v < MIN_VERSION
