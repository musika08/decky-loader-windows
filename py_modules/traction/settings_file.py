"""A plugin's JSON settings file: read, atomic save with a .bak, and the copy kept of an unreadable file.

Shared with the Traction plugin: standard library only, no imports from the rest of the package.

The file is read as UTF-8 with or without a BOM (Windows PowerShell 5.1 `Set-Content -Encoding UTF8` adds one) and
always written without one. A file that exists but cannot be read or parsed is never overwritten silently: a copy is
kept as <name>.unreadable-<UTC time> first. Every successful save also writes <name>.bak.
"""
import datetime
import json
import os
import stat
from typing import Callable, Optional

UNREADABLE_TAG = ".unreadable-"
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def _is_link(path: str) -> bool:
    """True for a symlink, junction or any other reparse point (never followed when copying or recovering)."""
    st = os.lstat(path)
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & _REPARSE_POINT)


def _read_json(path: str):
    """The parsed file (BOM accepted). FileNotFoundError when absent; OSError/ValueError when unreadable."""
    with open(path, "rb") as f:
        data = f.read()
    return json.loads(data.decode("utf-8-sig"))


def _write_atomic(path: str, text: str) -> None:
    """Written next to the target (UTF-8, no BOM), flushed to disk, then renamed over it."""
    tmp = path + ".new"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def keep_unreadable(path: str, log: Callable[[str], None], now=None, owner: str = "Overdrive"
                    ) -> "tuple[bool, str]":
    """Copy the unreadable file to <name>.unreadable-<UTC time> (never through a link). Raises OSError.

    Returns (still_pending, note): still_pending is False once there is nothing left to copy (copied, a link, or the
    file is gone); note is what to show the owner ("" when the file is gone)."""
    name = os.path.basename(path)
    try:
        link = _is_link(path)
    except FileNotFoundError:
        return False, ""
    if link:
        note = f"{owner} settings could not be read and were reset; {name} is a link, so no copy was kept."
        log(note)
        return False, note
    with open(path, "rb") as f:
        data = f.read()
    when = (now or datetime.datetime.now(datetime.timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    base = path + UNREADABLE_TAG + when
    for n in range(100):
        dst = base if n == 0 else f"{base}-{n}"
        try:   # O_EXCL: never overwrites an existing file and never writes through a planted link
            fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        except FileExistsError:
            continue
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        break
    else:
        raise OSError(f"no free name for a copy of {name}")
    note = (f"{owner} settings could not be read and were reset; a copy was kept as "
            f"{os.path.basename(dst)}.")
    log(note)
    return False, note


def read_or_keep(path: str, log: Callable[[str], None], now=None, owner: str = "Overdrive"
                 ) -> "tuple[Optional[dict], str, bool]":
    """(data, note, copy_pending). data is the parsed JSON object, or None when the file is absent (note "") or
    unusable (a copy is attempted first; note says what happened). copy_pending is True when the copy could not be
    made yet: the caller must call keep_unreadable again before it overwrites the file."""
    try:
        raw = _read_json(path)
    except FileNotFoundError:
        return None, "", False
    except (OSError, ValueError) as e:
        raw, why = None, str(e)
    else:
        why = "not a JSON object"
    if isinstance(raw, dict):
        return raw, "", False
    log(f"{owner} settings file {path} could not be read ({why}); using defaults")
    try:
        pending, note = keep_unreadable(path, log, now, owner)
    except OSError as e:
        log(f"Could not keep a copy of the unreadable settings file yet: {e}")
        return None, (f"{owner} settings could not be read and were reset; a copy could not be kept yet, "
                      "so they will not be saved until it can."), True
    return None, note, pending


def load_json(path: str, log: Callable[[str], None], now=None, owner: str = "Overdrive"
              ) -> "tuple[Optional[dict], str]":
    """(data, note): the parsed JSON object, or None with a note when the file was unusable (see read_or_keep)."""
    data, note, _ = read_or_keep(path, log, now, owner)
    return data, note


def save_json(path: str, data: dict, log: Optional[Callable[[str], None]] = None) -> None:
    """Atomic: written next to the target, then renamed over it; then the same for <name>.bak (a failure there is
    only logged). Raises OSError when the main file cannot be written."""
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    text = json.dumps(data, indent=2)
    _write_atomic(path, text)
    try:
        _write_atomic(path + ".bak", text)
    except OSError as e:
        if log is not None:
            log(f"Could not write the settings backup {path}.bak: {e}")
