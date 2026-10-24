"""Overdrive's reapply: what is put back on the device (mode, fans, FPS limit) and the fan curve rules.

The loop itself (polling for resume and AC changes, the shared lock and generation, retries) is ReapplierCore in
reapply_core.py, which Traction shares.
"""
import logging
import time
from typing import Callable, Optional

from .controls import transport_failure
from .reapply_core import (POLL, RESUME_GAP, TRANSPORT_RETRIES, Dropped, ReapplierCore, _StepClient,  # noqa: F401
                           ac_online_win32)
from .settings import CURVE_MODES, FANS, MODE_POWER, Settings, curve_mode, preset_curve

ADJUSTED = "Adjusted to the safe minimum"


def fan_plan(s: Settings, fan: str = "cpu") -> "tuple[Optional[dict], str, str]":
    """(curve to write after the mode write or None, note, warning) for one fan ("cpu" or "gpu"), the stored mode
    and the preset. Each fan's preset curve is derived from that fan's own default curve.

    Writing the performance mode puts the firmware curve of both fans back (G-Helper's fan reset), so the "default"
    preset needs no curve write unless the device curve sits below G-Helper's minimum (ruling P6: then the raised
    curve is written and the note says so). "recommended" is one fixed curve for both fans, written as is. Under
    "custom", a fan without a custom curve keeps its default curve."""
    custom = s.custom_curve(fan)
    if s.fan_preset == "custom" and custom:
        return custom, "", ""
    preset = "default" if s.fan_preset == "custom" else s.fan_preset
    if preset == "recommended":   # a fixed curve: written as is, no device curve needed
        return preset_curve(preset, None)[0], "", ""
    base = (s.default_curves(fan) or {}).get(curve_mode(s.mode))
    which, tail = ("", "") if fan == "cpu" else ("GPU ", " to the GPU fan")
    if base is None:
        if preset == "default":
            return None, "", ""
        return None, "", f"The device's default {which}fan curve is not known yet, so the fan preset was not applied{tail}."
    curve, adjusted = preset_curve(preset, base)
    if curve is None:
        if preset == "default":
            return None, "", ""
        return None, "", f"The device's default {which}fan curve is not usable, so the fan preset was not applied{tail}."
    note = ADJUSTED if adjusted else ""
    if preset == "default" and not adjusted:
        return None, note, ""
    return curve, note, ""


def fan_note(s: Settings) -> str:
    """The panel's note for both fans: which ones were raised to G-Helper's minimum."""
    adjusted = [f for f in FANS if fan_plan(s, f)[1]]
    if not adjusted:
        return ""
    if adjusted == ["cpu"]:
        return ADJUSTED
    if adjusted == ["gpu"]:
        return "GPU fan adjusted to the safe minimum"
    return "Both fans adjusted to the safe minimum"


def fan_args(curve: dict, fan: str) -> dict:
    """set_fans arguments; the CPU fan is the helper's default, so only the GPU fan is named."""
    args = {"temps": curve["temps"], "speeds": curve["speeds"]}
    if fan != "cpu":
        args["fan"] = fan
    return args


async def rewrite_fans(client, s: Settings) -> "tuple[str, bool]":
    """After a mode write the firmware curve of both fans is back; put the chosen preset or custom curves on again
    (CPU, then GPU, as G-Helper's AutoFans). Used by user changes (main.py) and Auto TDP's restore. The caller holds
    the reapplier's lock. (message for the owner, a set_fans write failed)."""
    msgs = []
    failed = False
    for fan in FANS:
        curve, _, warn = fan_plan(s, fan)
        if curve is None:
            if warn:
                msgs.append(warn)
            continue
        r = await client.call("set_fans", fan_args(curve, fan))
        if not r.get("ok"):
            failed = True
            what = "fan curve" if fan == "cpu" else "GPU fan curve"
            msgs.append(f"The {what} was not put back: " + str(r.get("message")))
    return " ".join(msgs), failed


def mode_args(s: Settings) -> dict:
    return mode_write(s.mode, (s.spl, s.sppt, s.fppt))


def mode_write(mode: str, manual_power=None) -> dict:
    """set_mode arguments: Manual with the user's limits, every other mode with its own watts (MODE_POWER)."""
    args = {"mode": mode}
    if mode == "manual":
        args.update(spl=manual_power[0], sppt=manual_power[1], fppt=manual_power[2])
    else:
        w = MODE_POWER[mode]
        args.update(spl=w, sppt=w, fppt=w)
    return args


class Reapplier(ReapplierCore):
    """Overdrive's reapply: the performance mode, both fans, and the FPS limit, in that order. The
    loop, the lock and the triggers are ReapplierCore's (reapply_core.py, shared with Traction)."""

    SKIP_MESSAGE = "Reapply skipped: no Overdrive settings chosen yet"
    DEFER_MESSAGE = "Reapply deferred while Armoury Crate services are being switched"
    LOGGER_NAME = "overdrive"

    def __init__(self, client, settings: Settings, clock=time, power_source: Optional[Callable] = None,
                 poll: float = POLL, logger: Optional[logging.Logger] = None, record=None):
        super().__init__(client, clock=clock, power_source=power_source, logger=logger, poll=poll, record=record)
        self.settings = settings
        self.fan_note = ""

    def _should_apply(self) -> bool:
        return self.settings.user_set

    def _is_transport_failure(self, reply: dict) -> bool:
        return transport_failure(reply)

    async def _before_apply(self) -> None:
        await self.read_default_curves()

    async def read_default_curves(self) -> None:
        """Read each mode's firmware curve of each fan once (reads only) and store it. Modes that could not be read
        (a partial result) are read again at the next reapply; curves already stored are never read again."""
        changed = False
        for fan in FANS:
            have = dict(self.settings.default_curves(fan) or {})
            missing = [m for m in CURVE_MODES if m not in have]
            if not missing:
                continue
            curves = {}
            for m in missing:
                args = {"mode": m} if fan == "cpu" else {"mode": m, "fan": fan}
                r = await self.client.call("get_fans", args)
                data = r.get("data") if r.get("ok") else None
                if (isinstance(data, dict) and isinstance(data.get("temps"), list)
                        and isinstance(data.get("speeds"), list)):
                    curves[m] = {"temps": list(data["temps"]), "speeds": list(data["speeds"])}
                else:
                    self.log.warning(f"Could not read the {m} {fan.upper()} fan curve: {r.get('message')}")
            if curves:
                have.update(curves)
                self.settings.set_default_curves(fan, have)
                changed = True
        if changed:
            try:
                self.settings.save()
            except OSError as e:
                self.log.warning(f"Could not save the default fan curves: {e}")

    async def _apply_steps(self, gen: int) -> "tuple[list[str], bool, bool]":
        s = self.settings
        problems = []
        transport = False
        mode_ok = False
        r = await self._step(gen, "set_mode", mode_args(s))
        transport |= transport_failure(r)
        if not r.get("ok"):
            problems.append(r.get("message") or "The performance mode was not applied.")
        else:
            mode_ok = True
            self.fan_note = fan_note(s)
            for fan in FANS:   # CPU then GPU, as G-Helper's AutoFans
                curve, _, warn = fan_plan(s, fan)
                if warn:
                    problems.append(warn)
                if curve is not None:
                    r = await self._step(gen, "set_fans", fan_args(curve, fan))
                    transport |= transport_failure(r)
                    if not r.get("ok"):
                        what = "fan curve" if fan == "cpu" else "GPU fan curve"
                        problems.append(r.get("message") or f"The {what} was not applied.")
        if s.fps != 0:   # 0 (off) is only sent when the owner picks it, so an Adrenalin limit is left alone
            r = await self._step(gen, "set_fps_limit", {"fps": s.fps})
            transport |= transport_failure(r)
            if not r.get("ok"):
                problems.append(r.get("message") or "The FPS limit was not applied.")
        return problems, transport, mode_ok
