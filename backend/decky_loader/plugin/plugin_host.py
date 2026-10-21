'''Windows plugin host: the entry point of `<loader> --plugin-host <b64>`.

The loader starts each plugin as a separate program (so it can pick the child's token) instead of a
multiprocessing child. The parameters travel base64-encoded on the command line; the socket token travels only in
the environment, since any local user can read another process's command line.
'''
import base64, json, os, sys
from logging import basicConfig

from .sandboxed_plugin import SandboxedPlugin
from ..localplatform.localsocket import PortSocket
from ..localplatform.localplatform import get_log_level

TOKEN_ENV = "DECKY_PLUGIN_SOCKET_TOKEN"

def encode_params(sp: SandboxedPlugin, port: int) -> str:
    params = {
        "name": sp.name,
        "passive": sp.passive,
        "flags": sp.flags,
        "file": sp.file,
        "plugin_directory": sp.plugin_directory,
        "plugin_path": sp.plugin_path,
        "version": sp.version,
        "author": sp.author,
        "api_version": sp.api_version,
        "port": port,
    }
    return base64.b64encode(json.dumps(params, ensure_ascii=False).encode("utf-8")).decode("ascii")

def decode_params(b64: str) -> tuple[SandboxedPlugin, int]:
    p = json.loads(base64.b64decode(b64, validate=True).decode("utf-8"))
    sp = SandboxedPlugin(str(p["name"]), bool(p["passive"]), [str(f) for f in p["flags"]], str(p["file"]),
                         str(p["plugin_directory"]), str(p["plugin_path"]),
                         None if p["version"] is None else str(p["version"]), str(p["author"]), int(p["api_version"]))
    return sp, int(p["port"])

def main(b64: str) -> None:
    # Remove the token so the plugin's own subprocesses do not inherit it
    token = os.environ.pop(TOKEN_ENV, None)
    if not token:
        print(f"{TOKEN_ENV} is not set", file=sys.stderr)
        sys.exit(2)
    basicConfig(level=get_log_level(), format="[%(module)s][%(levelname)s]: %(message)s")
    sp, port = decode_params(b64)
    sp.initialize(PortSocket(port, token))
