"""Stand-ins for bin\\traction-helper.exe in tests; neither touches hardware.

- Run as a script: a child process that speaks the helper's JSON-lines protocol (used by the HelperClient tests).
- FakeClient: an in-process HelperClient double with scripted replies (used by the controls, reapply, Controller Auto
  and main tests).

Script options (argv):
  --exit-at-start CODE   exit with CODE before reading anything
  --exit-on CMD          exit with code 3 when CMD arrives, without replying
  --hang-on CMD[,CMD]    never reply to these commands (keeps reading other lines)
  --log PATH             append every request line to PATH
"""
import json
import sys

FRAME_SAMPLE = {"supported": True, "fps": 60.0, "gpuPowerW": 10, "errors": []}
TOUCH = {"found": True, "enabled": True, "instanceId": "HID\\NVTK0603&COL01\\5&1234&0&0000"}
ARMOURY = {"installed": False, "startTypes": {}, "running": []}

DATA = {
    "hello": {"version": "0.1.0", "ally": True},
    "frame_sample": FRAME_SAMPLE,
    "foreground": {"exe": "game.exe"},
    "armoury_state": ARMOURY,
    "controller_info": {"found": True, "pid": "0x1B4C", "xbox": True},
    "get_touchscreen": TOUCH,
}


def reply(req):
    cmd = req.get("cmd")
    args = req.get("args") or {}
    if cmd in DATA:
        return {"id": req.get("id"), "ok": True, "message": "", "data": DATA[cmd]}
    if cmd == "set_controller_mode":
        return {"id": req.get("id"), "ok": True, "message": "", "data": {"mode": args.get("mode")}}
    if cmd == "set_vibration":
        return {"id": req.get("id"), "ok": True, "message": "", "data": {"strength": args.get("strength")}}
    if cmd == "set_touchscreen":
        return {"id": req.get("id"), "ok": True, "message": "", "data": {"enabled": args.get("enabled")}}
    if cmd in ("armoury_stop", "armoury_restore"):
        return {"id": req.get("id"), "ok": True, "message": "", "data": {"startTypes": {}, "running": []}}
    return {"id": req.get("id"), "ok": False, "message": "Refused: unknown command '%s'." % cmd, "data": None}


def main(argv):
    opts = {"exit_on": None, "hang_on": set(), "log": None}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--exit-at-start":
            sys.exit(int(argv[i + 1]))
        if a == "--exit-on":
            opts["exit_on"] = argv[i + 1]
        elif a == "--hang-on":
            opts["hang_on"] = set(argv[i + 1].split(","))
        elif a == "--log":
            opts["log"] = argv[i + 1]
        i += 2

    for line in sys.stdin:
        if not line.strip():
            continue
        if opts["log"]:
            with open(opts["log"], "a", encoding="utf-8") as f:
                f.write(line if line.endswith("\n") else line + "\n")
        try:
            req = json.loads(line)
        except ValueError:
            sys.stdout.write(json.dumps({"id": None, "ok": False, "message": "Malformed request", "data": None}) + "\n")
            sys.stdout.flush()
            continue
        cmd = req.get("cmd")
        if cmd == opts["exit_on"]:
            sys.exit(3)
        if cmd in opts["hang_on"]:
            continue
        sys.stdout.write(json.dumps(reply(req)) + "\n")
        sys.stdout.flush()


READS = ("hello", "frame_sample", "foreground", "armoury_state", "controller_info", "get_touchscreen")


class FakeClient:
    """Records (cmd, args) and answers like the helper. handlers[cmd] = async fn(args) -> reply overrides a reply."""

    def __init__(self):
        self.calls = []
        self.timeouts = {}
        self.handlers = {}
        self.unavailable_reason = ""
        self.started = False
        self.stopped = False
        self.armoury_inflight = 0
        self.armoury = {"startTypes": {"ArmouryCrateSEService": "AutomaticDelayedStart",
                                       "LightingService": "Automatic"},
                        "running": ["ArmouryCrateSEService"]}

    def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True

    def writes(self):
        return [c for c in self.calls if c[0] not in READS]

    @property
    def busy(self):
        return self.armoury_inflight > 0

    async def call(self, cmd, args=None, timeout=10.0):
        armoury = cmd in ("armoury_stop", "armoury_restore")
        if armoury:
            self.armoury_inflight += 1
        try:
            return await self._call(cmd, args, timeout)
        finally:
            if armoury:
                self.armoury_inflight -= 1

    async def _call(self, cmd, args, timeout):
        self.calls.append((cmd, args))
        self.timeouts.setdefault(cmd, []).append(timeout)
        if cmd in self.handlers:
            return await self.handlers[cmd](args)
        args = args or {}
        if cmd == "armoury_state":
            return ok({"installed": True, "startTypes": dict(self.armoury["startTypes"]),
                       "running": list(self.armoury["running"])})
        if cmd == "armoury_stop":
            return ok({"startTypes": dict(self.armoury["startTypes"]), "running": list(self.armoury["running"])})
        if cmd == "armoury_restore":
            return ok({"skipped": []})
        if cmd == "frame_sample":
            return ok(dict(FRAME_SAMPLE))
        if cmd == "foreground":
            return ok({"exe": "steam.exe"})
        if cmd == "controller_info":
            return ok({"found": False, "pid": "0x1B4C", "xbox": False})   # off by default: no extra writes
        if cmd == "get_touchscreen":
            return ok({"found": False, "enabled": False, "instanceId": ""})
        return ok(dict(args))


def ok(data):
    return {"id": 1, "ok": True, "message": "", "data": data}


def fail(message, data=None):
    return {"id": 1, "ok": False, "message": message, "data": data}


if __name__ == "__main__":
    main(sys.argv[1:])
