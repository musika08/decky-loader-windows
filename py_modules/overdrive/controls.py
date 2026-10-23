"""CPU Boost, Windows power mode, touchscreen, Auto SR and the ASUS controller (mode + grip vibration).

Thin layer over the helper's commands. Every function returns {"ok", "message", ...}; a helper refusal or a missing
feature is reported, never raised.

get_touchscreen spawns PowerShell in the helper and can take up to 5 s, so read_controls keeps the last touchscreen
status for TOUCH_TTL seconds and a state poll never waits on it more than once per that time. set_touchscreen returns
at once (the change lands later), so the cache is updated with the requested value instead of asking again.
"""
import time
from typing import Optional

from .helper_client import RESTARTED
from .settings import CONTROLLER_MODES, Settings

TOUCH_TTL = 30.0
POWER_MODES = ("eco", "balanced", "best")

clock = time.monotonic          # tests replace it
_touch = {"at": None, "value": None}


def reset_cache() -> None:
    _touch["at"] = None
    _touch["value"] = None


def transport_failure(reply: dict) -> bool:
    """A reply that failed on the way (helper restarted, did not answer, not running), not a device refusal."""
    if reply.get("ok"):
        return False
    msg = str(reply.get("message") or "")
    return (msg == RESTARTED or msg.startswith("Overdrive helper did not answer")
            or msg.startswith("Overdrive helper is not running"))


def _result(ok: bool, message: str = "", **extra) -> dict:
    return {"ok": ok, "message": message, **extra}


def _data(reply: dict) -> Optional[dict]:
    data = reply.get("data") if reply.get("ok") else None
    return data if isinstance(data, dict) else None


def _msg(reply: dict, fallback: str) -> str:
    return str(reply.get("message") or fallback)


async def _touchscreen_status(client) -> dict:
    now = clock()
    if _touch["at"] is not None and now - _touch["at"] < TOUCH_TTL:
        return _touch["value"]
    d = _data(await client.call("get_touchscreen", {}, timeout=15.0))
    if d is None:
        value = {"found": False, "enabled": False}
    else:
        value = {"found": bool(d.get("found")), "enabled": bool(d.get("enabled"))}
    _touch["at"], _touch["value"] = clock(), value
    return value


async def read_controls(client) -> dict:
    """The state get_state shows for the new controls. A command the helper cannot serve marks its feature
    unsupported (or not found); nothing here raises."""
    boost = {"on": False, "supported": False}
    d = _data(await client.call("get_cpu_boost", {}))
    if d is not None:
        boost = {"on": bool(d.get("ac")) or bool(d.get("dc")), "supported": True}

    power = {"value": "other", "supported": False}
    d = _data(await client.call("get_power_mode", {}))
    if d is not None:
        mode = d.get("mode")
        power = {"value": mode if mode in POWER_MODES else "other", "supported": True}

    controller = {"found": False}
    d = _data(await client.call("controller_info", {}))
    if d is not None:
        controller = {"found": bool(d.get("found"))}

    touch = await _touchscreen_status(client)

    sr = {"installed": False, "on": None}
    d = _data(await client.call("get_auto_sr", {}))
    if d is not None and d.get("installed"):
        v = d.get("value")
        sr = {"installed": True, "on": True if v == 1028 else False if v == 1024 else None}

    return {"ok": True, "message": "", "cpu_boost": boost, "power_mode": power, "controller": controller,
            "touchscreen": {"found": touch["found"], "enabled": touch["enabled"]}, "auto_sr": sr}


async def set_cpu_boost(client, settings: Settings, on: bool) -> dict:
    """On writes the value saved when it was last turned off (default 2). Off saves the current non-zero value
    first, then writes 0."""
    if on:
        value = settings.cpu_boost_on_value
    else:
        d = _data(await client.call("get_cpu_boost", {}))
        if d is None:
            return _result(False, "CPU Boost could not be read.")
        current = max((v for v in (d.get("ac"), d.get("dc")) if isinstance(v, int) and not isinstance(v, bool)),
                      default=0)
        if current != 0:
            settings.cpu_boost_on_value = min(max(current, 1), 6)
            try:
                settings.save()
            except OSError as e:
                return _result(False, f"Could not save the CPU Boost value: {e}")
        value = 0
    r = await client.call("set_cpu_boost", {"value": value})
    if not r.get("ok"):
        return _result(False, _msg(r, "CPU Boost was not changed."))
    return _result(True, "", on=on)


async def set_power_mode(client, value: str) -> dict:
    if value not in POWER_MODES:
        return _result(False, "Unknown Windows power mode.")
    r = await client.call("set_power_mode", {"mode": value})
    if not r.get("ok"):
        return _result(False, _msg(r, "The Windows power mode was not changed."))
    return _result(True, "", value=value)


async def set_touchscreen(client, enabled: bool) -> dict:
    r = await client.call("set_touchscreen", {"enabled": bool(enabled)})
    if not r.get("ok"):
        return _result(False, _msg(r, "The touchscreen was not changed."))
    # The change lands a little later and asking again takes up to 5 s: remember what was asked for.
    _touch["at"], _touch["value"] = clock(), {"found": True, "enabled": bool(enabled)}
    return _result(True, "", enabled=bool(enabled))


async def set_auto_sr(client, on: bool) -> dict:
    r = await client.call("set_auto_sr", {"on": bool(on)})
    if not r.get("ok"):
        return _result(False, _msg(r, "Auto SR was not changed."))
    return _result(True, "", on=bool(on))


async def apply_controller(client, settings: Settings, mode: Optional[str] = None) -> dict:
    """Writes the controller mode ("auto" starts in Gamepad) and the
    grip vibration. Both are tried; every failure is reported."""
    mode = mode or settings.controller_mode
    if mode not in CONTROLLER_MODES:
        return _result(False, "Unknown controller mode.")
    problems = []
    r = await client.call("set_controller_mode", {"mode": "desktop" if mode == "desktop" else "gamepad"})
    if not r.get("ok"):
        problems.append(_msg(r, "The controller mode was not applied."))
        if transport_failure(r):   # the helper is gone: the vibration write would only fail the same way
            return _result(False, " ".join(problems), mode=mode)
    r = await client.call("set_vibration", {"strength": 100 if settings.grip_vibration else 0})
    if not r.get("ok"):
        problems.append(_msg(r, "The grip vibration was not applied."))
    return _result(not problems, " ".join(problems), mode=mode)
