"""The Armoury Crate record shared by Overdrive and Traction, and the lock that lets only one of them switch at a time.

Shared with the Traction plugin: standard library only, plus settings_file.

The record (<folder>\\state.json) holds what armoury_restore puts back: `replaced` (the switch is on), `recorded` (each
listed service's start type before anything was stopped) and `running` (which of them were running). It is checked on
read like Overdrive's settings were: only listed service names and start types are kept. Every save is atomic and also
writes state.json.bak. A record that exists but cannot be read is never overwritten silently: it is recovered from the
newest readable state.json.bak or earlier unreadable copy, and a copy of the unreadable file is kept before the next
save (state.json.unreadable-<UTC time>).

The lock is an exclusive OS lock on one byte of <folder>\\switch.lock (msvcrt.locking). Windows drops it when the
process holding it dies, so a plugin killed in the middle of a switch never leaves it held.
"""
import contextlib
import logging
import os
import time
from typing import Optional

from . import settings_file
from .settings_file import UNREADABLE_TAG

try:
    import msvcrt
except ImportError:          # not Windows (tests run on Windows; this keeps the module importable elsewhere)
    msvcrt = None
    try:
        import fcntl
    except ImportError:
        fcntl = None

RECORD_DIR = r"C:\ProgramData\decky\armoury"
RECORD_NAME = "state.json"
LOCK_NAME = "switch.lock"
LOCK_TIMEOUT = 2.0
LOCK_RETRY = 0.05

# helper\src\ArmouryServices.cs (G-Helper AsusService.cs services + servicesAC). The helper refuses a restore that
# names anything else, so a hand-edited name is dropped here instead of blocking the switch.
ARMOURY_SERVICES = (
    "ArmouryCrateControlInterface", "ArmouryCrateProArtService", "AsHidService", "ASUSOptimization",
    "AsusAppService", "ASUSLinkNear", "ASUSLinkRemote", "ASUSSoftwareManager", "ASUSLiveUpdateAgent", "ASUSSwitch",
    "ASUSSystemAnalysis", "ASUSSystemDiagnosis", "ASUSXGMobileService", "AsusCertService",
    "ArmouryCrateSEService", "ArmouryCrateService", "LightingService",
)
START_TYPES = ("Automatic", "AutomaticDelayedStart", "Manual", "Disabled")

# Another process reading state.json at the moment it is replaced makes the rename fail on Windows (the reader's
# handle does not share delete); such a read takes microseconds, so the rename and the read are retried briefly.
_REPLACE_TRIES, _REPLACE_WAIT = 20, 0.025
_READ_TRIES, _READ_WAIT = 5, 0.02
_UNLOADED = object()


class ArmouryLocked(Exception):
    """Another plugin (or another record object) holds the switch lock."""


def clean_types(rec) -> dict:
    """Start types limited to listed services and known start types."""
    if not isinstance(rec, dict):
        return {}
    return {k: v for k, v in rec.items() if k in ARMOURY_SERVICES and v in START_TYPES}


def clean_running(run, types: dict) -> list:
    """Running services limited to names with a recorded type, each once."""
    seen = []
    if isinstance(run, list):
        for name in run:
            if isinstance(name, str) and name in types and name not in seen:
                seen.append(name)
    return seen


def _try_lock(fd: int) -> bool:
    os.lseek(fd, 0, os.SEEK_SET)
    try:
        if msvcrt is not None:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        elif fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(fd: int) -> None:
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        if msvcrt is not None:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        elif fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass   # closing the file releases it anyway


class ArmouryRecord:
    def __init__(self, folder: str = RECORD_DIR, logger: Optional[logging.Logger] = None):
        self.folder = folder
        self.path = os.path.join(folder, RECORD_NAME)
        self.lock_path = os.path.join(folder, LOCK_NAME)
        self.log = logger or logging.getLogger("armoury")
        self.replaced = False
        self.recorded: dict = {}
        self.running: list = []
        self.note = ""                      # the record was unreadable (and where it came back from), else ""
        self._seen = _UNLOADED              # the file's signature at the last load or save
        self._unreadable_pending = False    # an unreadable state.json is on disk with no copy kept yet

    # ---------- reading ----------

    def exists(self) -> bool:
        return os.path.exists(self.path)

    def _signature(self):
        try:
            st = os.stat(self.path)
        except OSError:
            return None
        return st.st_ino, st.st_mtime_ns, st.st_size

    def _warn(self, msg: str) -> None:
        self.log.warning(msg)

    def _set(self, raw: dict) -> None:
        self.replaced = raw.get("replaced") if isinstance(raw.get("replaced"), bool) else False
        self.recorded = clean_types(raw.get("recorded"))
        self.running = clean_running(raw.get("running"), self.recorded)

    def _read(self, path: str):
        """(data, why): data is the parsed file, None when unreadable (why says how), or _UNLOADED when absent."""
        why = ""
        for attempt in range(_READ_TRIES):
            try:
                return settings_file._read_json(path), ""
            except FileNotFoundError:
                return _UNLOADED, ""
            except PermissionError as e:   # being replaced right now
                why = str(e)
                if attempt + 1 < _READ_TRIES:
                    time.sleep(_READ_WAIT)
            except (OSError, ValueError) as e:
                return None, str(e)
        return None, why

    def load(self) -> str:
        """Read the record from disk (a missing file is the empty record). Returns the note: "" when it was read,
        else what happened (recovered from a copy, or reset)."""
        sig = self._signature()
        raw, why = self._read(self.path)
        self._unreadable_pending = False
        self.note = ""
        if raw is _UNLOADED:
            self._set({})
        elif isinstance(raw, dict):
            self._set(raw)
        else:
            self._warn(f"The Armoury Crate record {self.path} could not be read ({why or 'not a JSON object'})")
            self._set({})
            self._unreadable_pending = True
            self.note = self._recover()
        self._seen = sig
        return self.note

    def refresh(self) -> bool:
        """Load again only when the file changed since the last load or save (a stat otherwise). True when loaded."""
        if self._seen is not _UNLOADED and self._signature() == self._seen:
            return False
        self.load()
        return True

    def _recover(self) -> str:
        """Take the record from the newest readable state.json.bak or earlier unreadable copy."""
        try:
            entries = os.listdir(self.folder)
        except OSError:
            entries = []
        found = []
        for entry in entries:
            if entry != RECORD_NAME + ".bak" and not entry.startswith(RECORD_NAME + UNREADABLE_TAG):
                continue
            full = os.path.join(self.folder, entry)
            try:
                if settings_file._is_link(full):
                    continue
                found.append((os.stat(full).st_mtime, entry, full))
            except OSError:
                continue
        for _, entry, full in sorted(found, reverse=True):
            try:
                raw = settings_file._read_json(full)
            except (OSError, ValueError):
                continue
            if not isinstance(raw, dict):
                continue
            self._set(raw)
            if self.recorded or self.replaced:
                note = f"The Armoury Crate record was recovered from {entry}."
                self._warn(note)
                return note
            return ""
        note = "The Armoury Crate record could not be read and no readable copy was found."
        self._warn(note)
        return note

    # ---------- writing ----------

    def save(self) -> None:
        """Atomic (settings_file.save_json, with state.json.bak). An unreadable state.json still on disk is copied
        first; when that copy cannot be made the save fails instead of overwriting it. Raises OSError."""
        os.makedirs(self.folder, exist_ok=True)
        if self._unreadable_pending:
            self._unreadable_pending, _ = settings_file.keep_unreadable(self.path, self._warn,
                                                                        owner="Armoury Crate record")
        data = {"replaced": bool(self.replaced), "recorded": dict(self.recorded), "running": list(self.running)}
        for attempt in range(_REPLACE_TRIES):
            try:
                settings_file.save_json(self.path, data, self._warn)
                break
            except PermissionError:
                if attempt + 1 >= _REPLACE_TRIES:
                    raise
                time.sleep(_REPLACE_WAIT)
        self._seen = self._signature()

    # ---------- the switch lock ----------

    @contextlib.contextmanager
    def lock(self, timeout: float = LOCK_TIMEOUT):
        """Held for a whole switch (or the load-time check). Raises ArmouryLocked when it is not taken within
        `timeout` seconds (0: one try), OSError when the lock file cannot be opened."""
        os.makedirs(self.folder, exist_ok=True)
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o600)
        try:
            deadline = time.monotonic() + timeout
            while not _try_lock(fd):
                if time.monotonic() >= deadline:
                    raise ArmouryLocked(f"{self.lock_path} is held by another plugin")
                time.sleep(LOCK_RETRY)
        except BaseException:
            os.close(fd)
            raise
        try:
            yield self
        finally:
            _unlock(fd)
            os.close(fd)
