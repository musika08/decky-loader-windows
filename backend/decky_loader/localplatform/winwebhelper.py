r'''Restart Steam's CEF webhelper on Windows, the counterpart of `killall steamwebhelper` on Linux.

Ends every steamwebhelper.exe in the loader's own session (the interactive user's: installed mode runs from a logon
task with the user's interactive token); Steam starts a fresh one. Processes in other sessions, such as another
signed-in user's Steam, are never touched. Best effort: failures are logged, never raised.
'''
import ctypes, os
from logging import getLogger
from typing import Callable

from .wininstall import (INVALID_HANDLE_VALUE, PROCESS_QUERY_LIMITED_INFORMATION, PROCESS_TERMINATE, SYNCHRONIZE,
                         TERMINATE_WAIT_MS, TH32CS_SNAPPROCESS, _kernel32, wait_pid_exit) # pyright: ignore [reportPrivateUsage]

logger = getLogger("winwebhelper")

WEBHELPER_IMAGE = "steamwebhelper.exe"

def process_names() -> list[tuple[int, int, str]]:
    '''(pid, parent pid, exe file name) for every process, from one Toolhelp snapshot. Raises OSError on failure.'''
    from ctypes import wintypes
    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260)]
    k = _kernel32()
    k.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    k.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
    k.Process32FirstW.restype = wintypes.BOOL
    k.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
    k.Process32NextW.restype = wintypes.BOOL

    snap = k.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap is None or snap == INVALID_HANDLE_VALUE:
        raise ctypes.WinError(ctypes.get_last_error()) # pyright: ignore [reportAttributeAccessIssue, reportUnknownMemberType]
    out: list[tuple[int, int, str]] = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = k.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            out.append((int(entry.th32ProcessID), int(entry.th32ParentProcessID), str(entry.szExeFile)))
            ok = k.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k.CloseHandle(snap)
    return out

def session_of(pid: int) -> int | None:
    '''The Terminal Services session `pid` runs in, or None if it cannot be determined.'''
    from ctypes import wintypes
    k = _kernel32()
    k.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    k.ProcessIdToSessionId.restype = wintypes.BOOL
    session = wintypes.DWORD()
    if not k.ProcessIdToSessionId(pid, ctypes.byref(session)):
        return None
    return int(session.value)

def own_session() -> int | None:
    return session_of(os.getpid())

def terminate_webhelper(pid: int) -> None:
    '''Terminate `pid` after checking, on the opened handle, that it still is a steamwebhelper.exe (the pid
    could have been reused since the snapshot). Raises OSError if it cannot be opened, checked or ended.'''
    from ctypes import wintypes
    k = _kernel32()
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.OpenProcess.restype = ctypes.c_void_p
    k.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    k.QueryFullProcessImageNameW.restype = wintypes.BOOL
    k.TerminateProcess.argtypes = [ctypes.c_void_p, wintypes.UINT]
    k.TerminateProcess.restype = wintypes.BOOL
    k.WaitForSingleObject.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    k.WaitForSingleObject.restype = wintypes.DWORD
    h = k.OpenProcess(PROCESS_TERMINATE | SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        raise ctypes.WinError(ctypes.get_last_error()) # pyright: ignore [reportAttributeAccessIssue, reportUnknownMemberType]
    try:
        buf = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buf))
        if not k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error()) # pyright: ignore [reportAttributeAccessIssue, reportUnknownMemberType]
        if os.path.basename(buf.value).lower() != WEBHELPER_IMAGE:
            raise OSError(f"pid {pid} is no longer {WEBHELPER_IMAGE}")
        if not k.TerminateProcess(h, 1):
            raise ctypes.WinError(ctypes.get_last_error()) # pyright: ignore [reportAttributeAccessIssue, reportUnknownMemberType]
        k.WaitForSingleObject(h, TERMINATE_WAIT_MS)
    finally:
        k.CloseHandle(h)

def webhelper_pids(processes: list[tuple[int, int, str]], session: int,
                   session_of: Callable[[int], int | None] = session_of) -> list[int]:
    '''steamwebhelper.exe pids in `session`, the top-level one (whose parent is not a webhelper) first, so its
    children usually go down with it. Processes whose session cannot be read are skipped.'''
    helpers = {pid: parent for pid, parent, name in processes if name.lower() == WEBHELPER_IMAGE}
    ours = [pid for pid in helpers if session_of(pid) == session]
    return sorted(ours, key=lambda pid: helpers[pid] in helpers)

def restart_webhelper(*, processes: Callable[[], list[tuple[int, int, str]]] = process_names,
                      session_of: Callable[[int], int | None] = session_of,
                      own_session: Callable[[], int | None] = own_session,
                      terminate: Callable[[int], None] = terminate_webhelper,
                      wait_exit: Callable[[int], bool] = wait_pid_exit) -> bool:
    '''End this session's steamwebhelper.exe processes. True if any was running and all of them are gone.'''
    session = own_session()
    if session is None:
        logger.warning("Could not determine Decky's own session, so steamwebhelper was not restarted")
        return False
    try:
        pids = webhelper_pids(processes(), session, session_of)
    except OSError as e:
        logger.warning(f"Could not list processes, so steamwebhelper was not restarted: {e}")
        return False
    if not pids:
        logger.info(f"No {WEBHELPER_IMAGE} is running in session {session}")
        return False
    logger.info(f"Restarting steamwebhelper: ending {len(pids)} process(es) in session {session}")
    stopped = True
    for pid in pids:
        try:
            terminate(pid)
        except OSError as e:
            # Children usually exit with the top-level webhelper, which makes opening them fail
            if not wait_exit(pid):
                logger.warning(f"Could not end {WEBHELPER_IMAGE} {pid}: {e}")
                stopped = False
    return stopped
