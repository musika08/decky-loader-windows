r"""Mirror the Steam Deck boot movie override to the name Windows Big Picture uses.

Boot-animation plugins write `<Steam>\config\uioverrides\movies\deck_startup.webm`. Steam only
checks that override on a Deck; on Windows Big Picture's default is `/movies/bigpicture_startup.webm`
and its override is `.../uioverrides/movies/bigpicture_startup.webm`. Inside the plugin process,
os.symlink/os.remove/os.unlink are wrapped so placing or removing the Deck movie does the same
to the Big Picture name.
"""
import os
import shutil
from logging import getLogger
from typing import Any, Callable

_log = getLogger("winsteammovies")

DECK_MOVIE = "deck_startup.webm"
BIGPICTURE_MOVIE = "bigpicture_startup.webm"


def _str(path: object) -> str | None:
    if not isinstance(path, (str, bytes, os.PathLike)):
        return None
    try:
        p: object = os.fspath(path)  # pyright: ignore [reportUnknownArgumentType, reportUnknownVariableType]
    except TypeError:
        return None
    return p if isinstance(p, str) else None


def _alias_for(path: Any, movies_dir: str) -> str | None:
    p = _str(path)
    if p is None or os.path.basename(p).lower() != DECK_MOVIE:
        return None
    parent = os.path.dirname(os.path.abspath(p))
    if os.path.normcase(os.path.realpath(parent)) != os.path.normcase(os.path.realpath(movies_dir)):
        return None
    return os.path.join(parent, BIGPICTURE_MOVIE)


def _remove_quietly(path: str, remove: Callable[..., Any]) -> None:
    try:
        remove(path)
    except FileNotFoundError:
        pass


def wrap(movies_dir: str, symlink: Callable[..., Any], remove: Callable[..., Any],
         unlink: Callable[..., Any]) -> tuple[Callable[..., Any], Callable[..., Any], Callable[..., Any]]:
    def _symlink(src: Any, dst: Any, *args: Any, **kwargs: Any) -> None:
        symlink(src, dst, *args, **kwargs)
        alias = _alias_for(dst, movies_dir)
        if alias is None:
            return
        try:
            _remove_quietly(alias, remove)
            target = _str(src)
            if target is None:
                return
            if not os.path.isabs(target):
                target = os.path.join(os.path.dirname(alias), target)
            try:
                symlink(target, alias)
            except OSError:
                shutil.copy2(target, alias)
        except OSError as e:
            _log.warning(f"could not mirror {DECK_MOVIE} to {BIGPICTURE_MOVIE}: {e}")

    def _wrap_remove(real: Callable[..., Any]) -> Callable[..., Any]:
        def _remove(path: Any, *args: Any, **kwargs: Any) -> None:
            real(path, *args, **kwargs)
            alias = _alias_for(path, movies_dir)
            if alias is not None:
                try:
                    _remove_quietly(alias, real)
                except OSError as e:
                    _log.warning(f"could not remove {BIGPICTURE_MOVIE}: {e}")
        return _remove

    return _symlink, _wrap_remove(remove), _wrap_remove(unlink)


def install_movie_mirror(steam_dir: str) -> None:
    '''Call after install_symlink_fallback so mirrored links get the same fallback.'''
    if getattr(os.symlink, "_decky_movie_mirror", False):
        return
    movies_dir = os.path.join(steam_dir, "config", "uioverrides", "movies")
    s, r, u = wrap(movies_dir, os.symlink, os.remove, os.unlink)
    for f in (s, r, u):
        f._decky_movie_mirror = True  # pyright: ignore [reportFunctionMemberAccess]
    os.symlink, os.remove, os.unlink = s, r, u
