import asyncio, base64, json, logging, os, socket, sys, time
from pathlib import Path
from typing import Any
import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only module")

if sys.platform == "win32":
    from decky_loader.localplatform import winprocess
    from decky_loader.localplatform.winprocess import is_elevated
    from decky_loader.plugin import plugin as plugin_mod
    from decky_loader.plugin import plugin_host
    from decky_loader.plugin.sandboxed_plugin import SandboxedPlugin
else:
    def is_elevated() -> bool:
        return False

# Reports the child's own token state as JSON into the file named by argv[1].
PROBE = r'''
import ctypes, json, sys
from ctypes import wintypes
a = ctypes.WinDLL("advapi32", use_last_error=True)
k = ctypes.WinDLL("kernel32", use_last_error=True)
k.GetCurrentProcess.restype = ctypes.c_void_p
a.OpenProcessToken.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
a.GetTokenInformation.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
a.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
a.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
a.GetSidSubAuthority.argtypes = [ctypes.c_void_p, wintypes.DWORD]
a.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)
a.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
h = ctypes.c_void_p()
assert a.OpenProcessToken(k.GetCurrentProcess(), 8, ctypes.byref(h))
def info(cls):
    n = wintypes.DWORD()
    a.GetTokenInformation(h, cls, None, 0, ctypes.byref(n))
    buf = ctypes.create_string_buffer(n.value)
    assert a.GetTokenInformation(h, cls, buf, n, ctypes.byref(n))
    return buf
def sid_str(p):
    s = ctypes.c_wchar_p()
    assert a.ConvertSidToStringSidW(p, ctypes.byref(s))
    return s.value
def first_ptr(buf):
    return ctypes.c_void_p.from_buffer(buf).value
il_sid = first_ptr(info(25))
rid = a.GetSidSubAuthority(il_sid, a.GetSidSubAuthorityCount(il_sid)[0] - 1)[0]
elev = wintypes.DWORD.from_buffer(info(20)).value
user = sid_str(first_ptr(info(1)))
owner = sid_str(first_ptr(info(4)))
with open(sys.argv[1], "w") as f:
    json.dump({"elevation": elev, "il": rid, "user": user, "owner": owner, "token_env": "DECKY_PLUGIN_SOCKET_TOKEN" in __import__("os").environ}, f)
'''


def _run_probe(tmp_path: Path, elevated: bool | None) -> dict[str, Any]:
    out = tmp_path / "probe.json"
    env = dict(os.environ)
    proc = winprocess.PluginProcess([sys.executable, "-c", PROBE, str(out)], env, elevated=bool(elevated))
    if elevated is None:
        proc.start_restricted()
    else:
        proc.start()
    assert proc.pid > 0
    deadline = time.monotonic() + 30
    while proc.is_alive() and time.monotonic() < deadline:
        time.sleep(0.05)
    alive = proc.is_alive()
    if alive:
        proc.kill()
    assert not alive, "probe child did not exit"
    return json.loads(out.read_text())


def test_is_elevated_returns_bool():
    assert isinstance(is_elevated(), bool)


def test_restricted_token_child_from_current_token(tmp_path: Path):
    # Works unelevated: a restricted copy of our own token needs no SeAssignPrimaryTokenPrivilege.
    res = _run_probe(tmp_path, None)
    assert res["il"] == 0x2000
    assert res["elevation"] == 0
    assert res["owner"] == res["user"]


@pytest.mark.skipif(not is_elevated(), reason="needs an elevated shell")
def test_non_root_child_is_medium_integrity(tmp_path: Path):
    res = _run_probe(tmp_path, False)
    assert res["elevation"] == 0
    assert res["il"] == 0x2000
    assert res["owner"] == res["user"]


@pytest.mark.skipif(not is_elevated(), reason="needs an elevated shell")
def test_root_child_stays_elevated(tmp_path: Path):
    res = _run_probe(tmp_path, True)
    assert res["elevation"] == 1
    assert res["il"] >= 0x3000


def test_terminate_stops_restricted_child():
    proc = winprocess.PluginProcess([sys.executable, "-c", "import time; time.sleep(60)"], dict(os.environ), elevated=False)
    proc.start_restricted()
    assert proc.is_alive()
    proc.terminate()
    deadline = time.monotonic() + 10
    while proc.is_alive() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not proc.is_alive()
    proc.kill() # no-op after exit, must not raise


def test_terminate_stops_popen_child():
    proc = winprocess.PluginProcess([sys.executable, "-c", "import time; time.sleep(60)"], dict(os.environ), elevated=True)
    proc.start()
    assert proc.is_alive()
    proc.kill()
    deadline = time.monotonic() + 10
    while proc.is_alive() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not proc.is_alive()


def _sandboxed() -> "SandboxedPlugin":
    return SandboxedPlugin("Näme \"q\"", False, ["root", "debug"], r"C:\p\main.py", "dir name", r"C:\plugins", None, "Au thor", 1)


def test_encode_params_roundtrip():
    sp = _sandboxed()
    b64 = plugin_host.encode_params(sp, 51234)
    assert all(c.isalnum() or c in "+/=" for c in b64)
    got, port = plugin_host.decode_params(b64)
    assert port == 51234
    for field in ("name", "passive", "flags", "file", "plugin_directory", "plugin_path", "version", "author", "api_version"):
        assert getattr(got, field) == getattr(sp, field), field
    assert json.loads(base64.b64decode(b64).decode("utf-8"))["port"] == 51234


def test_plugin_host_main_reads_token_from_env(monkeypatch: pytest.MonkeyPatch):
    seen: dict[str, Any] = {}

    def fake_initialize(self: Any, socket: Any) -> None:
        seen.update(name=self.name, port=socket.port, token=socket.token, env=os.environ.get(plugin_host.TOKEN_ENV))

    monkeypatch.setattr(SandboxedPlugin, "initialize", fake_initialize)
    monkeypatch.setenv(plugin_host.TOKEN_ENV, "s3cret")
    plugin_host.main(plugin_host.encode_params(_sandboxed(), 40001))
    # The token is removed from the environment so plugin subprocesses do not inherit it
    assert seen == {"name": _sandboxed().name, "port": 40001, "token": "s3cret", "env": None}


def test_plugin_host_main_requires_token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(plugin_host.TOKEN_ENV, raising=False)
    with pytest.raises(SystemExit):
        plugin_host.main(plugin_host.encode_params(_sandboxed(), 40001))


class FakeProcess:
    instances: list["FakeProcess"] = []

    def __init__(self, argv: list[str], env: dict[str, str], elevated: bool) -> None:
        self.argv, self.env, self.elevated = argv, env, elevated
        self.started = False
        FakeProcess.instances.append(self)

    def start(self) -> None:
        self.started = True


def _start_wrapper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, flags: list[str], elevated_loader: bool) -> FakeProcess:
    plugins = tmp_path / "plugins"
    pdir = plugins / "MyPlugin"
    pdir.mkdir(parents=True)
    (pdir / "plugin.json").write_text(json.dumps({"name": "My Plugin", "author": "me", "flags": flags, "api_version": 1}))
    (pdir / "main.py").write_text("class Plugin: pass\n")
    monkeypatch.setattr(plugin_mod, "get_homebrew_path", lambda: str(tmp_path / "home"))
    monkeypatch.setattr(winprocess, "is_elevated", lambda: elevated_loader)
    monkeypatch.setattr(winprocess, "PluginProcess", FakeProcess)
    FakeProcess.instances = []

    async def noop(*_: Any) -> None:
        pass

    async def run() -> None:
        w = plugin_mod.PluginWrapper(str(pdir / "main.py"), "MyPlugin", str(plugins), noop)
        w.start()
        w._listener_task.cancel() # pyright: ignore [reportPrivateUsage]
        try:
            await w._listener_task # pyright: ignore [reportPrivateUsage]
        except BaseException:
            pass
        token = w._socket.token # pyright: ignore [reportPrivateUsage, reportAttributeAccessIssue, reportUnknownMemberType]
        proc = FakeProcess.instances[0]
        assert w.proc is proc # pyright: ignore [reportUnnecessaryComparison]
        assert proc.env[plugin_host.TOKEN_ENV] == token
        assert all(token not in a for a in proc.argv)
        assert proc.argv[-2] == "--plugin-host"
        _, port = plugin_host.decode_params(proc.argv[-1])
        assert port == w._socket.port # pyright: ignore [reportPrivateUsage, reportAttributeAccessIssue, reportUnknownMemberType]

    asyncio.run(run())
    return FakeProcess.instances[0]


def test_root_plugin_unelevated_logs_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    with caplog.at_level(logging.WARNING):
        proc = _start_wrapper(tmp_path, monkeypatch, ["root"], elevated_loader=False)
    assert proc.started and proc.elevated
    assert any("unelevated" in r.getMessage() for r in caplog.records)


def test_non_root_plugin_starts_restricted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    with caplog.at_level(logging.WARNING):
        proc = _start_wrapper(tmp_path, monkeypatch, [], elevated_loader=True)
    assert proc.started and not proc.elevated
    assert not any("unelevated" in r.getMessage() for r in caplog.records)


def test_plugin_host_end_to_end_restricted(tmp_path: Path):
    from decky_loader.localplatform.localsocket import PortSocket
    from decky_loader.localplatform.winsupervisor import self_command

    pdir = tmp_path / "plugins" / "Echo"
    pdir.mkdir(parents=True)
    (pdir / "main.py").write_text(
        "import os\n"
        "class Plugin:\n"
        "    async def double(self, x):\n"
        "        return [x * 2, os.environ.get('DECKY_PLUGIN_SOCKET_TOKEN')]\n")
    home = tmp_path / "home"
    for sub in ("logs", "settings", "data"):
        (home / sub / "Echo").mkdir(parents=True) # PluginWrapper creates these in the loader
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
    sp = SandboxedPlugin("Echo", False, [], str(pdir / "main.py"), "Echo", str(tmp_path / "plugins"), "1.0", "me", 1)
    env = dict(os.environ)
    env["UNPRIVILEGED_PATH"] = str(home)
    env[plugin_host.TOKEN_ENV] = "tok-123"
    proc = winprocess.PluginProcess(self_command() + ["--plugin-host", plugin_host.encode_params(sp, port)], env, elevated=False)
    proc.start_restricted()

    async def call(token: str) -> str | None:
        client = PortSocket(port, token)
        try:
            await client.write_single_line(json.dumps({"type": 0, "method": "double", "args": [21], "id": "1"}))
            return await asyncio.wait_for(client.read_single_line(), 10)
        except ConnectionResetError:
            return "" # a rejected client can see a reset instead of EOF when the server closes with unread data
        finally:
            await client.close_socket_connection()

    try:
        assert asyncio.run(call("wrong")) == ""
        res = json.loads(asyncio.run(call("tok-123")) or "null")
        assert res["success"] and res["res"] == [42, None] and res["id"] == "1"
    finally:
        proc.kill()
