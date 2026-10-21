"""Symlink fallback for plugin processes on Windows.

Creating symlinks needs Developer Mode or elevation (winerror 1314). Plugins call
os.symlink without knowing that, so this replaces it inside the plugin process with a
version that falls back to a junction (directories) or a hardlink, then a copy (files).
"""
import os
import shutil
import _winapi  # pyright: ignore [reportMissingImports]
from logging import getLogger
from typing import Any, Callable

ERROR_PRIVILEGE_NOT_HELD = 1314

_REAL_SYMLINK: Callable[..., Any] = os.symlink
_log = getLogger("winsymlink")
_logged = False


def symlink_with_fallback(src: str, dst: str, target_is_directory: bool = False, *,
                          dir_fd: int | None = None,
                          _symlink: Callable[..., Any] = _REAL_SYMLINK,
                          _link: Callable[..., Any] = os.link) -> None:
    global _logged
    try:
        if dir_fd is None:
            _symlink(src, dst, target_is_directory)
        else:
            _symlink(src, dst, target_is_directory, dir_fd=dir_fd)
        return
    except OSError as e:
        if getattr(e, "winerror", None) != ERROR_PRIVILEGE_NOT_HELD or dir_fd is not None:
            raise
        original = e

    src_s, dst_s = os.fspath(src), os.fspath(dst)
    abs_src = src_s if os.path.isabs(src_s) else os.path.join(os.path.dirname(os.path.abspath(dst_s)), src_s)
    abs_src = os.path.normpath(abs_src)
    is_dir = target_is_directory or os.path.isdir(abs_src)
    if not is_dir and not os.path.exists(abs_src):
        raise original
    if os.path.lexists(dst_s):
        raise FileExistsError(17, "Cannot create a file when that file already exists", dst_s)

    if not _logged:
        _logged = True
        _log.info("symlink unavailable, using junction/hardlink/copy")

    if is_dir:
        _winapi.CreateJunction(abs_src, os.path.abspath(dst_s))
        return
    try:
        _link(abs_src, dst_s)
    except OSError:
        shutil.copy2(abs_src, dst_s)


def _patched(src: str, dst: str, target_is_directory: bool = False, *, dir_fd: int | None = None) -> None:
    symlink_with_fallback(src, dst, target_is_directory, dir_fd=dir_fd, _symlink=_REAL_SYMLINK)

_patched._decky_fallback = True  # pyright: ignore [reportFunctionMemberAccess]


def install_symlink_fallback() -> None:
    if getattr(os.symlink, "_decky_fallback", False):
        return
    os.symlink = _patched
