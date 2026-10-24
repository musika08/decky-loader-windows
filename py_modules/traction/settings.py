"""Traction's settings file (settings.json in Decky's per-plugin settings dir) and the move of the controller settings
from Overdrive's file (spec section 3.3).

Every field is checked on load on its own: a missing, wrong-typed or unknown value falls back to its default, the other
fields are kept. The file handling (BOM-tolerant read, atomic save with a .bak, the copy kept of an unreadable file) is
settings_file.py's. Touch Screen is not stored: it is read from Windows.
"""
import os
from typing import Optional

from . import settings_file

OWNER = "Traction"
CONTROLLER_MODES = ("gamepad", "desktop", "auto")
FIELDS = ("controller_mode", "grip_vibration", "user_set")


def _mode(v) -> Optional[str]:
    return v if isinstance(v, str) and v in CONTROLLER_MODES else None


class Settings:
    def __init__(self, path: str):
        self.path = path
        self.controller_mode = "gamepad"    # "gamepad" | "desktop" | "auto"
        self.grip_vibration = True
        # False until the owner changes something: an untouched install only reads, it never writes the device.
        self.user_set = False
        self.logger = None
        self._reset_note = ""               # why the file was reset (shown in get_state)
        self._unreadable_pending = False    # the unreadable file is still on disk with no copy kept yet

    @classmethod
    def load(cls, path: str, logger=None, now=None) -> "Settings":
        s = cls(path)
        s.logger = logger
        raw, note, pending = settings_file.read_or_keep(path, s._log, now, owner=OWNER)
        if raw is not None:
            s._take(raw)
        else:
            s._reset_note = note
            s._unreadable_pending = pending
        return s

    @property
    def warning(self) -> str:
        """For get_state: the file was unreadable and reset, else ""."""
        return self._reset_note

    def _log(self, msg: str) -> None:
        if self.logger is not None:
            self.logger.warning(msg)

    def _take(self, raw: dict) -> None:
        mode = _mode(raw.get("controller_mode"))
        if mode is not None:
            self.controller_mode = mode
        if isinstance(raw.get("grip_vibration"), bool):
            self.grip_vibration = raw["grip_vibration"]
        if isinstance(raw.get("user_set"), bool):
            self.user_set = raw["user_set"]

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in FIELDS}

    def save(self) -> None:
        """Atomic, with a .bak. An unreadable file still on disk is copied first; when that copy cannot be made the
        save fails (OSError) instead of overwriting it."""
        if self._unreadable_pending:
            self._unreadable_pending, note = settings_file.keep_unreadable(self.path, self._log, owner=OWNER)
            if note:
                self._reset_note = note
        settings_file.save_json(self.path, self.to_dict(), self._log)


def migrate_from_overdrive(overdrive_settings_path: str, s: Settings) -> bool:
    """Traction's first load (its own file does not exist, readable or not): take `controller_mode` and
    `grip_vibration` from Overdrive's settings file, each when it holds a valid value, and mark them as the owner's
    choice (user_set) only when Overdrive's own user_set is true. True when either was taken; the caller saves then. Overdrive's file is only read: a missing,
    unreadable or wrong file changes nothing here (and no copy of it is made), so Traction keeps its defaults."""
    if os.path.lexists(s.path):
        return False
    try:
        raw = settings_file._read_json(overdrive_settings_path)
    except (OSError, ValueError):
        return False
    if not isinstance(raw, dict):
        return False
    taken = False
    mode = _mode(raw.get("controller_mode"))
    if mode is not None:
        s.controller_mode = mode
        taken = True
    if isinstance(raw.get("grip_vibration"), bool):
        s.grip_vibration = raw["grip_vibration"]
        taken = True
    if taken:
        # Final review T3: the owner's choice only when Overdrive had it as one (its own user_set); otherwise the
        # values are kept but an untouched install still only reads.
        s.user_set = raw.get("user_set") is True
    return taken
