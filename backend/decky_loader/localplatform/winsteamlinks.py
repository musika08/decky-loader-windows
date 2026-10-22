r"""Linux-style Steam paths for plugins on Windows.

On Linux `~/.steam/root`, `~/.steam/steam` and `~/.local/share/Steam` lead to the Steam
install, and many plugins hardcode them (Animation Changer writes
`~/.steam/root/config/uioverrides/movies`). On Windows nothing creates them, so plugins write
into a plain folder Steam never reads. This makes all three junctions to the Steam install dir;
missing parent folders are created as plain folders, and a parent that is a link is left alone.

Runs only in unelevated plugin hosts: the profile is user-writable, so doing these
creates/renames from an elevated process would let a planted junction redirect them.
"""
import ctypes
import os
import time
import _winapi  # pyright: ignore [reportMissingImports]
from logging import getLogger

_log = getLogger("winsteamlinks")

# Parent folders under the home dir, link name
LINKS = ((".steam",), "root"), ((".steam",), "steam"), ((".local", "share"), "Steam")


def steam_install_dir() -> str | None:
    import winreg
    candidates: list[tuple[int, str, str]] = [
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
    ]
    for hive, key, value in candidates:
        try:
            with winreg.OpenKey(hive, key) as k:
                path = os.path.normpath(str(winreg.QueryValueEx(k, value)[0]))
        except OSError:
            continue
        if os.path.isfile(os.path.join(path, "steam.exe")):
            return path
    return None


def _same(a: str, b: str) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def _ensure_link(link: str, steam_dir: str) -> None:
    if os.path.isjunction(link) or os.path.islink(link):
        if not _same(link, steam_dir):
            _log.warning(f"{link} links elsewhere, leaving it alone")
        return
    if os.path.isdir(link):
        backup = f"{link}.decky-old"
        if os.path.lexists(backup):
            backup = f"{backup}-{int(time.time())}"
        os.rename(link, backup)
        _log.info(f"moved existing {link} to {backup}")
    elif os.path.lexists(link):
        _log.warning(f"{link} is a file, leaving it alone")
        return
    try:
        _winapi.CreateJunction(steam_dir, link)
    except FileExistsError:
        # Another plugin host created it at the same time
        return
    _log.info(f"linked {link} -> {steam_dir}")


def _ensure_parent(home: str, parts: tuple[str, ...]) -> str | None:
    '''Creates home/parts... as plain folders; None if any of them is a link or a file.'''
    path = home
    for part in parts:
        path = os.path.join(path, part)
        if os.path.isjunction(path) or os.path.islink(path):
            _log.warning(f"{path} is a link, leaving it alone")
            return None
        if os.path.lexists(path):
            if not os.path.isdir(path):
                _log.warning(f"{path} is a file, leaving it alone")
                return None
            continue
        try:
            os.mkdir(path)
        except FileExistsError:
            # Created at the same time by another plugin host; check it again
            if os.path.isjunction(path) or os.path.islink(path) or not os.path.isdir(path):
                return None
    return path


def ensure_steam_home_links(home: str, steam_dir: str) -> None:
    for parts, name in LINKS:
        parent = _ensure_parent(home, parts)
        if parent is not None:
            _ensure_link(os.path.join(parent, name), steam_dir)


def setup_steam_home_links() -> None:
    '''Best effort; never stops a plugin from loading.'''
    try:
        if ctypes.windll.shell32.IsUserAnAdmin(): # pyright: ignore [reportAttributeAccessIssue, reportUnknownMemberType]
            return
        steam_dir = steam_install_dir()
        if steam_dir is None:
            _log.warning("Steam install dir not found, skipping the Steam home links")
            return
        ensure_steam_home_links(os.path.expanduser("~"), steam_dir)
    except Exception as e:
        _log.warning(f"could not set up the Steam home links: {e}")
