r'''Windows log files. Windowed builds have no console, so the loader and the supervisor also log to a file.

Installed: `%ProgramData%\decky\logs` (admin-only, created like the installer creates its directories).
Portable: `<homebrew>\logs`, next to the plugin logs.
'''
import logging, os
from logging.handlers import RotatingFileHandler
from typing import Any, Callable

logger = logging.getLogger("winlogging")

LOG_DIR_NAME = "logs"
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 3

def _installed_root() -> str | None:
    from .localplatformwin import installed_root
    return installed_root()

def _homebrew() -> str:
    from .localplatformwin import get_unprivileged_path
    return get_unprivileged_path()

def _open_trusted(path: str) -> Any:
    from .wininstall import open_trusted_dir
    return open_trusted_dir(path)

def _ensure_trusted(path: str) -> Any:
    from .wininstall import ensure_trusted_dir
    return ensure_trusted_dir(path, remove_links=True)

def _close_dir(handle: Any) -> None:
    from .wininstall import close_handle
    close_handle(handle)

def _is_reparse_point(path: str) -> bool:
    from .wininstall import is_reparse_point
    return is_reparse_point(path)

def prepare_log_dir(*, installed_root: Callable[[], str | None] = _installed_root, homebrew: Callable[[], str] = _homebrew,
                    open_trusted: Callable[[str], Any] = _open_trusted, ensure_trusted: Callable[[str], Any] = _ensure_trusted,
                    close_dir: Callable[[Any], None] = _close_dir, reparse: Callable[[str], bool] = _is_reparse_point,
                    makedirs: Callable[[str], None] = lambda p: os.makedirs(p, exist_ok=True)) -> str | None:
    '''Create the log directory if needed and return it, or None (logged) if it cannot be used safely.

    Installed: the root must already be a trusted directory, and `logs` is reused only if it is one too; anything
    else there is moved aside (a link only removed) and the directory created with the protected descriptor.
    Portable: a junction or symbolic link at `logs` is never followed.'''
    root = installed_root()
    if root is not None:
        logs = os.path.join(root, LOG_DIR_NAME)
        # Held open (no share-delete) while `logs` is checked and created, so the root cannot be swapped meanwhile
        root_handle = open_trusted(root)
        if root_handle is None:
            logger.warning("%s is not owned and protected by administrators, not writing a log file", root)
            return None
        try:
            close_dir(ensure_trusted(logs))
        except OSError as e:
            logger.warning("Could not prepare %s, not writing a log file: %s", logs, e)
            return None
        finally:
            close_dir(root_handle)
        return logs

    try:
        logs = os.path.join(homebrew(), LOG_DIR_NAME)
        if reparse(logs):
            logger.warning("%s is a junction or symbolic link, not writing a log file", logs)
            return None
        makedirs(logs)
    except OSError as e:
        logger.warning("Could not prepare the log directory, not writing a log file: %s", e)
        return None
    return logs

def setup_file_logging(filename: str, fmt: str, *, target: logging.Logger | None = None,
                       reparse: Callable[[str], bool] = _is_reparse_point, **dir_kw: Any) -> str | None:
    '''Also send everything logged (through `target`, the root logger by default) to `<log dir>\\<filename>`, rotated
    at LOG_MAX_BYTES with LOG_BACKUP_COUNT old files kept, in format `fmt`. Returns the file's path, or None if no
    file could be used; console logging is left as it is either way.'''
    logs = prepare_log_dir(reparse=reparse, **dir_kw)
    if logs is None:
        return None
    path = os.path.join(logs, filename)
    if reparse(path):
        logger.warning("%s is a junction or symbolic link, not writing a log file", path)
        return None
    try:
        handler = RotatingFileHandler(path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8")
    except OSError as e:
        logger.warning("Could not open %s, not writing a log file: %s", path, e)
        return None
    handler.setFormatter(logging.Formatter(fmt))
    (target if target is not None else logging.getLogger()).addHandler(handler)
    return path
