"""Stand-in for bin\\traction-helper.exe in tests; it touches no hardware.

Run as a script it is a child process that speaks the helper's JSON-lines protocol (used by the HelperClient tests).

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


if __name__ == "__main__":
    main(sys.argv[1:])
