"""Overdrive's settings file (overdrive.json in Decky's per-plugin settings dir) and the fan curve rules.

Every field is checked on load on its own: a missing, wrong-typed or unknown value falls back to its default
(numbers out of range are clamped), the other fields are kept. Nothing invalid is ever sent to the helper.

The file handling (BOM-tolerant read, atomic save with a .bak, the copy kept of an unreadable file) is in
settings_file.py. A file that exists but cannot be read or parsed is never overwritten silently: a copy is kept as
overdrive.json.unreadable-<UTC time> first.

The Armoury Crate record lives in the record shared with Traction (armoury_record.py). Files from before that still
hold armoury_replaced/recorded/running: they are read (recovered from the newest readable overdrive.json.bak or earlier
unreadable copy when the file is unusable) so main.py can move them into the shared record, and they are written back
unchanged until main.py says they were moved (armoury_migrated), so a failed move never loses them.
The curve rules mirror helper\\src\\Validation.cs (CheckCurve, MinSpeedAt) exactly, messages included.
"""
import math
import os
from typing import Optional

from . import settings_file
from .armoury_record import ARMOURY_SERVICES, START_TYPES  # noqa: F401  (the shared list; re-exported)
from .settings_file import UNREADABLE_TAG

MODES = ("silent", "balanced", "turbo", "manual")
CURVE_MODES = ("balanced", "turbo", "silent")          # firmware curves; manual uses balanced's
PRESETS = ("default", "recommended", "quiet", "cool", "custom")
# Written by Overdrive 0.1.0, now Traction's. Overdrive never uses them but keeps them in its file verbatim (when the
# loaded file had them) so Traction can carry them over on its first load.
PASSTHROUGH_KEYS = ("controller_mode", "grip_vibration")
FANS = ("cpu", "gpu")                                  # G-Helper AsusFan.CPU / AsusFan.GPU; written in this order
ALLOWED_FPS = (0, 30, 40, 45, 50, 60, 75, 90, 120)     # 0 = off (the helper maps it to the driver's value)
CPU_BOOST_ON_DEFAULT = 2                               # Aggressive
POWER_MIN, POWER_MAX, POWER_DEFAULT = 5, 50, 30
# Owner-chosen watts per mode (2026-10-23), sent as all three limits with the mode. With Armoury Crate stopped the
# Ally's firmware mode alone changed the fans but barely the speed. src/curve.ts MODE_POWER mirrors it.
MODE_POWER = {"silent": 13, "balanced": 17, "turbo": 35}
CURVE_POINTS, CURVE_TEMP_MIN, CURVE_TEMP_MAX, CURVE_SPEED_MAX = 8, 20, 110, 100
QUIET_DELTA, COOL_DELTA = -10, 15
# Owner-approved fixed curve for both fans (2026-10-23), derived from the Ally's factory curves and G-Helper's
# minimum (75 -> 6, 80 -> 12, 90 -> 24 %, all met). src/curve.ts RECOMMENDED_CURVE mirrors it.
RECOMMENDED_CURVE = {"temps": [40, 50, 60, 65, 70, 75, 80, 90], "speeds": [10, 15, 25, 35, 45, 55, 70, 90]}

# The pre-shared-record fields: read for the move into the shared record, written only until it is done.
ARMOURY_FIELDS = ("armoury_replaced", "armoury_recorded", "armoury_running")


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
        self.auto_tdp = False               # Auto TDP: the plugin follows the load and sets the watts itself
        self.cpu_boost_on_value = CPU_BOOST_ON_DEFAULT   # 1..6: what CPU Boost "On" writes
        # False until the owner changes something: an untouched install only reads, it never writes the device.
        self.user_set = False
        self._passthrough: dict = {}        # PASSTHROUGH_KEYS found in the loaded file, written back unchanged
        self.logger = None
        self._reset_note = ""               # why the file was reset (shown in get_state)
        self._recovered_note = ""           # where the Armoury record came back from
        self._unreadable_pending = False    # the unreadable file is still on disk with no copy kept yet
        self._armoury_kept = False          # the file held an Armoury record not moved to the shared one yet

    FIELDS = ("mode", "spl", "sppt", "fppt", "fps", "fan_preset", "fan_curve", "default_curve",
              "fan_curve_gpu", "default_curve_gpu", "auto_tdp",
              "cpu_boost_on_value", "user_set")

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
        raw, note, pending = settings_file.read_or_keep(path, s._log, now)
        if raw is not None:
            s._take(raw)
        elif note or pending:
            # It exists but is unusable: a copy was kept (or tried) before anything can overwrite it; get the Armoury
            # record back.
            s._reset_note = note
            s._unreadable_pending = pending
            s._recover_armoury()
        s._armoury_kept = s.holds_armoury()
        return s

    def holds_armoury(self) -> bool:
        """The old fields hold an Armoury record (anything but the empty one)."""
        return bool(self.armoury_replaced or self.armoury_recorded or self.armoury_running)

    def armoury_pending(self) -> bool:
        """The old Armoury fields hold a record that is not in the shared record yet."""
        return self._armoury_kept

    def armoury_migrated(self) -> None:
        """The old Armoury fields are in the shared record now (or it already existed): stop writing them."""
        self._armoury_kept = False

    @property
    def warning(self) -> str:
        """For get_state: the file was unreadable and reset (and where the Armoury record came from), else ""."""
        return " ".join(w for w in (self._reset_note, self._recovered_note) if w)

    def _log(self, msg: str) -> None:
        if self.logger is not None:
            self.logger.warning(msg)

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
            other = Settings(self.path)
            other._take(raw)
            for k in ARMOURY_FIELDS:
                setattr(self, k, getattr(other, k))
            if self.armoury_recorded or self.armoury_replaced:
                self._recovered_note = f"The Armoury Crate record was recovered from {entry}."
                self._log(self._recovered_note)
            return

    def _take(self, raw: dict) -> None:
        self._passthrough = {k: raw[k] for k in PASSTHROUGH_KEYS if k in raw}
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
        if isinstance(raw.get("auto_tdp"), bool):
            self.auto_tdp = raw["auto_tdp"]
        if _is_int(raw.get("cpu_boost_on_value")) and 1 <= raw["cpu_boost_on_value"] <= 6:
            self.cpu_boost_on_value = raw["cpu_boost_on_value"]
        if isinstance(raw.get("user_set"), bool):
            self.user_set = raw["user_set"]

    def to_dict(self) -> dict:
        d = {k: getattr(self, k) for k in self.FIELDS}
        d.update(self._passthrough)
        if self._armoury_kept:   # not moved to the shared record yet: never dropped
            d.update({k: getattr(self, k) for k in ARMOURY_FIELDS})
        return d

    def save(self) -> None:
        """Atomic: written next to the target, then renamed over it; then the same for <name>.bak.

        An unreadable file still on disk is copied first; when that copy cannot be made the save fails (OSError)
        instead of overwriting it."""
        if self._unreadable_pending:
            self._unreadable_pending, note = settings_file.keep_unreadable(self.path, self._log)
            if note:
                self._reset_note = note
        settings_file.save_json(self.path, self.to_dict(), self._log)
