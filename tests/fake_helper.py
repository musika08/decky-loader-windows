"""Stand-ins for bin\\asus-helper.exe in tests; neither touches hardware.

- Run as a script: a child process that speaks the helper's JSON-lines protocol (used by the HelperClient tests).
- FakeClient: an in-process HelperClient double with scripted replies (used by the reapply and main tests).

Script options (argv):
  --exit-at-start CODE   exit with CODE before reading anything
  --exit-on CMD          exit with code 3 when CMD arrives, without replying
  --hang-on CMD[,CMD]    never reply to these commands (keeps reading other lines)
  --slow-on CMD          reply to CMD after 1 s (or --slow-secs S), reading nothing else meanwhile
  --log PATH             append every request line to PATH
"""
import json
import sys
import time

CURVE = {"temps": [30, 40, 50, 60, 70, 80, 90, 100], "speeds": [0, 5, 10, 20, 30, 40, 50, 60]}
GPU_CURVE = {"temps": [35, 45, 55, 65, 75, 85, 95, 105], "speeds": [0, 6, 12, 22, 32, 42, 52, 62]}


def main(argv):
    opts = {"exit_on": None, "hang_on": None, "slow_on": None, "slow_secs": 1.0, "log": None}
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
        elif a == "--slow-secs":
            opts["slow_secs"] = float(argv[i + 1])
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
        if opts["hang_on"] and cmd in opts["hang_on"].split(","):
            continue
        if cmd == opts["slow_on"]:
            time.sleep(opts["slow_secs"])
        data = None
        if cmd == "hello":
            data = {"version": "0.1.0", "ally": True}
        elif cmd == "set_mode":
            data = dict(args)
        elif cmd == "get_fans":
            data = {"mode": args.get("mode", "balanced"), "fan": args.get("fan", "cpu"),
                    **(GPU_CURVE if args.get("fan") == "gpu" else CURVE)}
        elif cmd == "sensors":
            data = {"cpuTempC": 55, "fanRpm": 3100, "gpuFanRpm": 2900, "errors": []}
        elif cmd in ("set_fans", "set_fps_limit"):
            data = dict(args)
        else:
            data = {}
        out.write(json.dumps({"id": req.get("id"), "ok": True, "message": "", "data": data}) + "\n")
        out.flush()


class FakeClient:
    """Records (cmd, args) and answers like the helper. handlers[cmd] = async fn(args) -> reply overrides a reply."""

    def __init__(self, curves=None, curves_gpu=None):
        self.calls = []
        self.timeouts = {}
        self.handlers = {}
        self.unavailable_reason = ""
        self.started = False
        self.stopped = False
        self.armoury_inflight = 0
        self.curves = curves or {m: dict(CURVE) for m in ("balanced", "turbo", "silent")}
        self.curves_gpu = curves_gpu or {m: dict(GPU_CURVE) for m in ("balanced", "turbo", "silent")}
        self.armoury = {"startTypes": {"ArmouryCrateSEService": "AutomaticDelayedStart",
                                       "LightingService": "Automatic"},
                        "running": ["ArmouryCrateSEService"]}

    def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True

    def writes(self):
        return [c for c in self.calls if c[0] not in ("get_fans", "sensors", "status", "armoury_state", "hello")]

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
        if cmd == "get_fans":
            gpu = args.get("fan") == "gpu"
            table = self.curves_gpu if gpu else self.curves
            return ok({"mode": args.get("mode"), "fan": "gpu" if gpu else "cpu", **table[args.get("mode", "balanced")]})
        if cmd == "armoury_state":
            return ok({"installed": True, "startTypes": dict(self.armoury["startTypes"]),
                       "running": list(self.armoury["running"])})
        if cmd == "armoury_stop":
            return ok({"startTypes": dict(self.armoury["startTypes"]), "running": list(self.armoury["running"])})
        if cmd == "armoury_restore":
            return ok({"skipped": []})
        if cmd == "sensors":
            return ok({"cpuTempC": 55, "fanRpm": 3100, "gpuFanRpm": 2900, "errors": []})
        return ok(dict(args))


def ok(data):
    return {"id": 1, "ok": True, "message": "", "data": data}


def fail(message, data=None):
    return {"id": 1, "ok": False, "message": message, "data": data}


if __name__ == "__main__":
    main(sys.argv[1:])
