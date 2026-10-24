"""CPU Boost, Windows power mode and Auto SR.

Thin layer over the helper's commands. Every function returns {"ok", "message", ...}; a helper refusal or a missing
feature is reported, never raised.
"""
from typing import Optional

from .helper_client import is_transport_failure
from .settings import Settings

POWER_MODES = ("eco", "balanced", "best")


def transport_failure(reply: dict) -> bool:
    """A reply that failed on the way (helper restarted, did not answer, not running), not a device refusal. Works
    for any owner's name (helper_client builds the messages from it)."""
    return is_transport_failure(reply)


def _result(ok: bool, message: str = "", **extra) -> dict:
    return {"ok": ok, "message": message, **extra}


def _data(reply: dict) -> Optional[dict]:
    data = reply.get("data") if reply.get("ok") else None
    return data if isinstance(data, dict) else None


def _msg(reply: dict, fallback: str) -> str:
    return str(reply.get("message") or fallback)


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

    sr = {"installed": False, "on": None}
    d = _data(await client.call("get_auto_sr", {}))
    if d is not None and d.get("installed"):
        v = d.get("value")
        sr = {"installed": True, "on": True if v == 1028 else False if v == 1024 else None}

    return {"ok": True, "message": "", "cpu_boost": boost, "power_mode": power, "auto_sr": sr}


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


async def set_auto_sr(client, on: bool) -> dict:
    r = await client.call("set_auto_sr", {"on": bool(on)})
    if not r.get("ok"):
        return _result(False, _msg(r, "Auto SR was not changed."))
    return _result(True, "", on=bool(on))
