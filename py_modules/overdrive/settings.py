"""Overdrive's settings file (overdrive.json in Decky's per-plugin settings dir) and the fan curve rules.

Every field is checked on load on its own: a missing, wrong-typed or unknown value falls back to its default
(numbers out of range are clamped), the other fields are kept. Nothing invalid is ever sent to the helper.

The file is read as UTF-8 with or without a BOM (Windows PowerShell 5.1 `Set-Content -Encoding UTF8` adds one) and
always written without one. A file that exists but cannot be read or parsed is never overwritten silently: a copy is
kept as overdrive.json.unreadable-<UTC time> first, and the Armoury Crate record (which services to put back) is
recovered from the newest readable overdrive.json.bak or earlier unreadable copy, so a fallback to defaults can
never leave stopped services with no record to restore them from. Every successful save also writes the .bak.
The curve rules mirror helper\\src\\Validation.cs (CheckCurve, MinSpeedAt) exactly, messages included.
"""
import datetime
import json
import math
import os
import stat
from typing import Optional

MODES = ("silent", "balanced", "turbo", "manual")
CURVE_MODES = ("balanced", "turbo", "silent")          # firmware curves; manual uses balanced's
PRESETS = ("default", "recommended", "quiet", "cool", "custom")
FANS = ("cpu", "gpu")                                  # G-Helper AsusFan.CPU / AsusFan.GPU; written in this order
ALLOWED_FPS = (0, 30, 40, 45, 50, 60, 75, 90, 120)     # 0 = off (the helper maps it to the driver's value)
POWER_MIN, POWER_MAX, POWER_DEFAULT = 5, 50, 30
# Owner-chosen watts per mode (2026-10-23), sent as all three limits with the mode. With Armoury Crate stopped the
# Ally's firmware mode alone changed the fans but barely the speed. src/curve.ts MODE_POWER mirrors it.
MODE_POWER = {"silent": 13, "balanced": 17, "turbo": 35}
CURVE_POINTS, CURVE_TEMP_MIN, CURVE_TEMP_MAX, CURVE_SPEED_MAX = 8, 20, 110, 100
QUIET_DELTA, COOL_DELTA = -10, 15
# Owner-approved fixed curve for both fans (2026-10-23), derived from the Ally's factory curves and G-Helper's
# minimum (75 -> 6, 80 -> 12, 90 -> 24 %, all met). src/curve.ts RECOMMENDED_CURVE mirrors it.
RECOMMENDED_CURVE = {"temps": [40, 50, 60, 65, 70, 75, 80, 90], "speeds": [10, 15, 25, 35, 45, 55, 70, 90]}

# helper\src\ArmouryServices.cs (G-Helper AsusService.cs services + servicesAC). The helper refuses a restore that
# names anything else, so a hand-edited name is dropped here instead of blocking the switch.
ARMOURY_SERVICES = (
    "ArmouryCrateControlInterface", "ArmouryCrateProArtService", "AsHidService", "ASUSOptimization",
    "AsusAppService", "ASUSLinkNear", "ASUSLinkRemote", "ASUSSoftwareManager", "ASUSLiveUpdateAgent", "ASUSSwitch",
    "ASUSSystemAnalysis", "ASUSSystemDiagnosis", "ASUSXGMobileService", "AsusCertService",
    "ArmouryCrateSEService", "ArmouryCrateService", "LightingService",
)
START_TYPES = ("Automatic", "AutomaticDelayedStart", "Manual", "Disabled")
# The record a fallback must never lose. With the switch off, a non-empty record is the provisional one.
ARMOURY_FIELDS = ("armoury_replaced", "armoury_recorded", "armoury_running")
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


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def clamp(v: int, lo: int, hi: int) -> int:
    return lo if v < lo else hi if v > hi else v


def clamp_power(spl: int, sppt: int, fppt: int) -> "tuple[int, int, int]":
    """Validation.ClampPower: each to 5..50 W, then fPPT >= sPPT >= SPL by raising the later ones."""
    spl, sppt, fppt = (clamp(x, POWER_MIN, POWER_MAX) for x in (spl, sppt, fppt))
    sppt = max(sppt, spl)
    fppt = max(fppt, sppt)
    return spl, sppt, fppt


def min_speed_at(temp: int) -> int:
    """Validation.MinSpeedAt (G-Helper Fans.cs): ceil((temp - 70) * 1.2), at least 0, in integer tenths."""
    tenths = (temp - 70) * 12
    return 0 if tenths <= 0 else (tenths + 9) // 10


def check_curve(temps, speeds) -> Optional[str]:
    """Validation.CheckCurve: None when valid, else the helper's reason string (first failing point wins)."""
    if (not isinstance(temps, list) or not isinstance(speeds, list) or len(temps) != CURVE_POINTS
            or len(speeds) != CURVE_POINTS or not all(_is_int(x) for x in temps + speeds)):
        return "The fan curve needs 8 points."
    for i in range(CURVE_POINTS):
        n = i + 1
        if temps[i] < CURVE_TEMP_MIN or temps[i] > CURVE_TEMP_MAX:
            return f"Point {n}: temperature must be between {CURVE_TEMP_MIN} and {CURVE_TEMP_MAX} °C."
        if speeds[i] < 0 or speeds[i] > CURVE_SPEED_MAX:
            return f"Point {n}: fan speed must be between 0 and {CURVE_SPEED_MAX} %."
        # Firmware curves repeat their last temperature: non-decreasing temps, one speed per temperature.
        if i > 0 and temps[i] < temps[i - 1]:
            return f"Point {n}: temperature can't be lower than point {i}."
        if i > 0 and speeds[i] < speeds[i - 1]:
            return f"Point {n}: fan speed can't be lower than point {i}."
        if i > 0 and temps[i] == temps[i - 1] and speeds[i] != speeds[i - 1]:
            return f"Point {n}: same temperature as point {i}, so the fan speed must be the same."
        m = min_speed_at(temps[i])
        if speeds[i] < m:
            return f"Point {n}: at {temps[i]} °C the fan must be at least {m} %."
    return None


def _device_curve(v) -> Optional[dict]:
    """A curve as the device returned it: 8 byte-sized ints each. Not required to pass check_curve (ruling P6)."""
    if not isinstance(v, dict):
        return None
    temps, speeds = v.get("temps"), v.get("speeds")
    if not isinstance(temps, list) or not isinstance(speeds, list):
        return None
    if len(temps) != CURVE_POINTS or len(speeds) != CURVE_POINTS:
        return None
    if not all(_is_int(x) and 0 <= x <= 255 for x in temps + speeds):
        return None
    return {"temps": list(temps), "speeds": list(speeds)}


def _valid_curve(v) -> Optional[dict]:
    c = _device_curve(v)
    if c is None or check_curve(c["temps"], c["speeds"]) is not None:
        return None
    return c


def preset_curve(name: str, base: Optional[dict]) -> "tuple[Optional[dict], bool]":
    """Curve for a preset derived from a device default curve, and whether it was raised to the safe minimum.

    default = the device curve; quiet = speeds - 10; cool = speeds + 15; each clamped to 0..100 and raised to
    G-Helper's minimum per point; temps unchanged. (None, False) when the base curve cannot be made valid.
    recommended = RECOMMENDED_CURVE whatever the base (it needs no device curve)."""
    if name == "recommended":
        return {"temps": list(RECOMMENDED_CURVE["temps"]), "speeds": list(RECOMMENDED_CURVE["speeds"])}, False
    base = _device_curve(base)
    if base is None or name not in ("default", "quiet", "cool"):
        return None, False
    delta = {"default": 0, "quiet": QUIET_DELTA, "cool": COOL_DELTA}[name]
    temps = base["temps"]
    shifted = [clamp(s + delta, 0, CURVE_SPEED_MAX) for s in base["speeds"]]
    speeds = [max(s, min_speed_at(t)) for s, t in zip(shifted, temps)]
    if check_curve(temps, speeds) is not None:
        return None, False
    adjusted = speeds != shifted
    return {"temps": list(temps), "speeds": speeds}, adjusted


def curve_mode(mode: str) -> str:
    """The firmware curve a mode uses (the helper maps manual to balanced)."""
    return mode if mode in CURVE_MODES else "balanced"


class Settings:
    def __init__(self, path: str):
        self.path = path
        self.mode = "balanced"
        self.spl = POWER_DEFAULT
        self.sppt = POWER_DEFAULT
        self.fppt = POWER_DEFAULT
        self.fps = 0
        self.fan_preset = "default"
        self.fan_curve: Optional[dict] = None          # the custom CPU fan curve
        self.default_curve: Optional[dict] = None      # {mode: {temps, speeds}} read from the device on first run
        self.fan_curve_gpu: Optional[dict] = None      # the custom GPU fan curve
        self.default_curve_gpu: Optional[dict] = None  # the GPU fan's {mode: {temps, speeds}}, read like the CPU one
        self.armoury_replaced = False
        self.armoury_recorded: dict = {}
        self.armoury_running: list = []
        # False until the owner changes something: an untouched install only reads, it never writes the device.
        self.user_set = False
        self.logger = None
        self._reset_note = ""               # why the file was reset (shown in get_state)
        self._recovered_note = ""           # where the Armoury record came back from
        self._unreadable_pending = False    # the unreadable file is still on disk with no copy kept yet

    FIELDS = ("mode", "spl", "sppt", "fppt", "fps", "fan_preset", "fan_curve", "default_curve",
              "fan_curve_gpu", "default_curve_gpu",
              "armoury_replaced", "armoury_recorded", "armoury_running", "user_set")

    # Per-fan attribute names: (custom curve, default curves by mode).
    _FAN_ATTRS = {"cpu": ("fan_curve", "default_curve"), "gpu": ("fan_curve_gpu", "default_curve_gpu")}

    def custom_curve(self, fan: str) -> Optional[dict]:
        return getattr(self, self._FAN_ATTRS[fan][0])

    def set_custom_curve(self, fan: str, curve: Optional[dict]) -> None:
        setattr(self, self._FAN_ATTRS[fan][0], curve)

    def default_curves(self, fan: str) -> Optional[dict]:
        return getattr(self, self._FAN_ATTRS[fan][1])

    def set_default_curves(self, fan: str, curves: Optional[dict]) -> None:
        setattr(self, self._FAN_ATTRS[fan][1], curves)

    def has_custom(self) -> bool:
        return any(self.custom_curve(f) for f in FANS)

    @classmethod
    def load(cls, path: str, logger=None, now=None) -> "Settings":
        s = cls(path)
        s.logger = logger
        try:
            raw = _read_json(path)
        except FileNotFoundError:
            return s
        except (OSError, ValueError) as e:
            raw, why = None, str(e)
        else:
            why = "not a JSON object"
        if isinstance(raw, dict):
            s._take(raw)
            return s
        # It exists but is unusable: keep a copy before anything can overwrite it, and get the Armoury record back.
        s._log(f"Overdrive settings file {path} could not be read ({why}); using defaults")
        s._unreadable_pending = True
        try:
            s._keep_unreadable(now)
        except OSError as e:
            s._log(f"Could not keep a copy of the unreadable settings file yet: {e}")
            s._reset_note = ("Overdrive settings could not be read and were reset; a copy could not be kept yet, "
                             "so they will not be saved until it can.")
        s._recover_armoury()
        return s

    @property
    def warning(self) -> str:
        """For get_state: the file was unreadable and reset (and where the Armoury record came from), else ""."""
        return " ".join(w for w in (self._reset_note, self._recovered_note) if w)

    def _log(self, msg: str) -> None:
        if self.logger is not None:
            self.logger.warning(msg)

    def _keep_unreadable(self, now=None) -> None:
        """Copy the unreadable file to <name>.unreadable-<UTC time> (never through a link). Raises OSError."""
        if not self._unreadable_pending:
            return
        name = os.path.basename(self.path)
        try:
            link = _is_link(self.path)
        except FileNotFoundError:
            self._unreadable_pending = False
            return
        if link:
            self._unreadable_pending = False
            self._reset_note = (f"Overdrive settings could not be read and were reset; {name} is a link, so no "
                                "copy was kept.")
            self._log(self._reset_note)
            return
        with open(self.path, "rb") as f:
            data = f.read()
        when = (now or datetime.datetime.now(datetime.timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
        base = self.path + UNREADABLE_TAG + when
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
        self._unreadable_pending = False
        self._reset_note = (f"Overdrive settings could not be read and were reset; a copy was kept as "
                            f"{os.path.basename(dst)}.")
        self._log(self._reset_note)

    def _recover_armoury(self) -> None:
        """Take the Armoury record from the newest readable .bak or earlier unreadable copy; the other fields keep
        their defaults."""
        folder = os.path.dirname(self.path) or "."
        name = os.path.basename(self.path)
        try:
            entries = os.listdir(folder)
        except OSError:
            return
        found = []
        for entry in entries:
            if entry != name + ".bak" and not entry.startswith(name + UNREADABLE_TAG):
                continue
            full = os.path.join(folder, entry)
            try:
                if _is_link(full):
                    continue
                found.append((os.stat(full).st_mtime, entry, full))
            except OSError:
                continue
        for _, entry, full in sorted(found, reverse=True):
            try:
                raw = _read_json(full)
            except (OSError, ValueError):
                continue
            if not isinstance(raw, dict):
                continue
            other = Settings(self.path)
            other._take(raw)
            for k in ARMOURY_FIELDS:
                setattr(self, k, getattr(other, k))
            if self.armoury_recorded or self.armoury_replaced:
                self._recovered_note = f"The Armoury Crate record was recovered from {entry}."
                self._log(self._recovered_note)
            return

    def _take(self, raw: dict) -> None:
        if raw.get("mode") in MODES:
            self.mode = raw["mode"]
        power = []
        for key in ("spl", "sppt", "fppt"):
            v = raw.get(key)
            if _is_int(v):
                power.append(v)
            elif isinstance(v, float) and math.isfinite(v) and v == int(v):
                power.append(int(v))
            else:
                power.append(POWER_DEFAULT)
        self.spl, self.sppt, self.fppt = clamp_power(*power)
        if _is_int(raw.get("fps")) and raw["fps"] in ALLOWED_FPS:
            self.fps = raw["fps"]
        self.fan_curve = _valid_curve(raw.get("fan_curve"))
        self.fan_curve_gpu = _valid_curve(raw.get("fan_curve_gpu"))
        if raw.get("fan_preset") in PRESETS:
            self.fan_preset = raw["fan_preset"]
        if self.fan_preset == "custom" and not self.has_custom():
            self.fan_preset = "default"
        for key in ("default_curve", "default_curve_gpu"):
            dc = raw.get(key)
            if isinstance(dc, dict):
                curves = {m: _device_curve(dc.get(m)) for m in CURVE_MODES}
                curves = {m: c for m, c in curves.items() if c is not None}
                setattr(self, key, curves or None)
        if isinstance(raw.get("armoury_replaced"), bool):
            self.armoury_replaced = raw["armoury_replaced"]
        rec = raw.get("armoury_recorded")
        if isinstance(rec, dict):
            self.armoury_recorded = {k: v for k, v in rec.items() if k in ARMOURY_SERVICES and v in START_TYPES}
        run = raw.get("armoury_running")
        if isinstance(run, list):
            seen = []
            for name in run:
                if isinstance(name, str) and name in self.armoury_recorded and name not in seen:
                    seen.append(name)
            self.armoury_running = seen
        if isinstance(raw.get("user_set"), bool):
            self.user_set = raw["user_set"]

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.FIELDS}

    def save(self) -> None:
        """Atomic: written next to the target, then renamed over it; then the same for <name>.bak.

        An unreadable file still on disk is copied first; when that copy cannot be made the save fails (OSError)
        instead of overwriting it."""
        folder = os.path.dirname(self.path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        if self._unreadable_pending:
            self._keep_unreadable()
        text = json.dumps(self.to_dict(), indent=2)
        _write_atomic(self.path, text)
        try:
            _write_atomic(self.path + ".bak", text)
        except OSError as e:
            self._log(f"Could not write the settings backup {self.path}.bak: {e}")
