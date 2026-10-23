"""Stand-ins for bin\\asus-helper.exe in tests; neither touches hardware.

- Run as a script: a child process that speaks the helper's JSON-lines protocol (used by the HelperClient tests).
- FakeClient: an in-process HelperClient double with scripted replies (used by the reapply and main tests).

Script options (argv):
  --exit-at-start CODE   exit with CODE before reading anything
  --exit-on CMD          exit with code 3 when CMD arrives, without replying
  --hang-on CMD          never reply to CMD (keeps reading other lines)
  --slow-on CMD          reply to CMD after 1 s
  --log PATH             append every request line to PATH
"""
import json
import sys
import time

CURVE = {"temps": [30, 40, 50, 60, 70, 80, 90, 100], "speeds": [0, 5, 10, 20, 30, 40, 50, 60]}


def main(argv):
    opts = {"exit_on": None, "hang_on": None, "slow_on": None, "log": None}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--exit-at-start":
            sys.exit(int(argv[i + 1]))
        if a == "--exit-on":
            opts["exit_on"] = argv[i + 1]
        elif a == "--hang-on":
            opts["hang_on"] = argv[i + 1]
        elif a == "--slow-on":
            opts["slow_on"] = argv[i + 1]
        elif a == "--log":
            opts["log"] = argv[i + 1]
        i += 2

    out = sys.stdout
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        if opts["log"]:
            with open(opts["log"], "a", encoding="utf-8") as f:
                f.write(line + "\n")
        try:
            req = json.loads(line)
        except ValueError:
            out.write(json.dumps({"id": None, "ok": False, "message": "Malformed request", "data": None}) + "\n")
            out.flush()
            continue
        cmd = req.get("cmd")
        args = req.get("args") or {}
        if cmd == opts["exit_on"]:
            sys.exit(3)
        if cmd == opts["hang_on"]:
            continue
        if cmd == opts["slow_on"]:
            time.sleep(1.0)
        data = None
        if cmd == "hello":
            data = {"version": "0.1.0", "ally": True}
        elif cmd == "set_mode":
            data = dict(args)
        elif cmd == "get_fans":
            data = {"mode": args.get("mode", "balanced"), **CURVE}
        elif cmd == "sensors":
            data = {"cpuTempC": 55, "fanRpm": 3100, "errors": []}
        elif cmd in ("set_fans", "set_fps_limit"):
            data = dict(args)
        else:
            data = {}
        out.write(json.dumps({"id": req.get("id"), "ok": True, "message": "", "data": data}) + "\n")
        out.flush()


class FakeClient:
    """Records (cmd, args) and answers like the helper. handlers[cmd] = async fn(args) -> reply overrides a reply."""

    def __init__(self, curves=None):
        self.calls = []
        self.timeouts = {}
        self.handlers = {}
        self.unavailable_reason = ""
        self.started = False
        self.stopped = False
        self.curves = curves or {m: dict(CURVE) for m in ("balanced", "turbo", "silent")}
        self.armoury = {"startTypes": {"ArmouryCrateSEService": "AutomaticDelayedStart",
                                       "LightingService": "Automatic"},
                        "running": ["ArmouryCrateSEService"]}

    def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True

    def writes(self):
        return [c for c in self.calls if c[0] not in ("get_fans", "sensors", "status", "armoury_state", "hello")]

    async def call(self, cmd, args=None, timeout=10.0):
        self.calls.append((cmd, args))
        self.timeouts.setdefault(cmd, []).append(timeout)
        if cmd in self.handlers:
            return await self.handlers[cmd](args)
        args = args or {}
        if cmd == "get_fans":
            return ok({"mode": args.get("mode"), **self.curves[args.get("mode", "balanced")]})
        if cmd == "armoury_state":
            return ok({"installed": True, "startTypes": dict(self.armoury["startTypes"]),
                       "running": list(self.armoury["running"])})
        if cmd == "armoury_stop":
            return ok({"startTypes": dict(self.armoury["startTypes"]), "running": list(self.armoury["running"])})
        if cmd == "armoury_restore":
            return ok({"skipped": []})
        if cmd == "sensors":
            return ok({"cpuTempC": 55, "fanRpm": 3100, "errors": []})
        return ok(dict(args))


def ok(data):
    return {"id": 1, "ok": True, "message": "", "data": data}


def fail(message, data=None):
    return {"id": 1, "ok": False, "message": message, "data": data}


if __name__ == "__main__":
    main(sys.argv[1:])
