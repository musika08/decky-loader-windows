# pyright: strict
r'''Build the Windows installer folder: a private copy of the PSF-signed CPython runtime plus Decky's source.

  backend\.venv\Scripts\python.exe tools\build_win_runtime.py --out <folder>

<folder>\python is the base interpreter the backend venv was made from (pyvenv.cfg `home`), trimmed (no tcl/tk, test,
idlelib, tkinter, turtledemo, ensurepip, site-packages) and pinned by a python3XX._pth file to Lib, DLLs and ..\app,
without `import site`, so it ignores PYTHONPATH, the user site and the current directory.
<folder>\app is backend\main.py, the decky_loader package (with static, locales and winbin) and the runtime dependency
closure of decky_loader, copied from the venv. Everything is precompiled by the staged runtime itself.

Install it with "<folder>\python\python.exe" "<folder>\app\main.py" --install. Fails (exit 1) if the interpreter is not
validly signed by the Python Software Foundation, if the venv and its base differ in version, or if the staged copy
does not pass the --check-imports smoke test.
'''
from __future__ import annotations

import argparse, ctypes, importlib.metadata, json, os, platform, shutil, subprocess, sys, tempfile
from pathlib import PurePath
from typing import Any, Callable, Iterable, cast

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(REPO, "backend")
PACKAGE = "decky_loader"

# Same list the PyInstaller build is checked with: stdlib modules plugins rely on, and the bundled dependencies
CHECK_IMPORTS = ("linecache", "netrc", "pdb", "keyword", "reprlib", "sqlite3", "psutil", "PIL", "pystray", "watchdog",
                 "aiohttp", "certifi", "winreg")
# What the supervisor, the loader and the plugin hosts import
LOADER_IMPORTS = ("decky_loader.main", "decky_loader.plugin.plugin_host", "decky_loader.localplatform.winsupervisor",
                  "decky_loader.localplatform.wininstall")

PSF_SIGNER = "Python Software Foundation"
RUNTIME_DIR = "python"
APP_DIR = "app"
# Relative to <folder>\python. No "import site": no site-packages, no user site, no .pth processing.
PTH_LINES = ("Lib", "DLLs", "..\\" + APP_DIR)
# Top-level stdlib folders that are never needed (GUI, tests, IDE, pip bootstrap) or are replaced by app\
STDLIB_SKIP = frozenset({"site-packages", "test", "idlelib", "tkinter", "turtledemo", "ensurepip"})

CommandRunner = Callable[..., "subprocess.CompletedProcess[str]"]


class BuildError(Exception):
    pass


def _run(argv: list[str], **kw: Any) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(argv, capture_output=True, text=True, timeout=600, **kw)


# Base runtime

def read_pyvenv_cfg(venv: str) -> dict[str, str]:
    cfg: dict[str, str] = {}
    with open(os.path.join(venv, "pyvenv.cfg"), encoding="utf-8") as f:
        for line in f:
            key, sep, value = line.partition("=")
            if sep:
                cfg[key.strip().lower()] = value.strip()
    return cfg


def dll_name(version: tuple[int, int]) -> str:
    return f"python{version[0]}{version[1]}.dll"


def pth_name(version: tuple[int, int]) -> str:
    return f"python{version[0]}{version[1]}._pth"


def psf_files(version: tuple[int, int]) -> list[str]:
    '''Runtime files that must carry a valid Python Software Foundation signature.'''
    return ["python.exe", "pythonw.exe", "python3.dll", dll_name(version)]


def runtime_files(version: tuple[int, int]) -> list[str]:
    '''Top-level files of <folder>\\python (vcruntime140_1.dll only exists on x64).'''
    return psf_files(version) + ["vcruntime140.dll", "vcruntime140_1.dll", "LICENSE.txt"]


def interpreter_version(python: str, run: CommandRunner = _run) -> str:
    proc = run([python, "-I", "-c", "import platform; print(platform.python_version())"])
    if proc.returncode != 0:
        raise BuildError(f"Could not run {python}: {proc.stderr.strip()}")
    return proc.stdout.strip()


def check_versions(cfg: dict[str, str], running: str, base: str) -> None:
    '''The venv (its pyvenv.cfg and the interpreter running this script) and its base interpreter must agree.'''
    versions = {"pyvenv.cfg": cfg.get("version", ""), "venv interpreter": running, "base interpreter": base}
    if len(set(versions.values())) != 1:
        raise BuildError("Python versions differ: " + ", ".join(f"{k} {v or '?'}" for k, v in versions.items()))


# Signatures

def _system_directory() -> str:
    buf = ctypes.create_unicode_buffer(260)
    n = int(ctypes.windll.kernel32.GetSystemDirectoryW(buf, len(buf))) # pyright: ignore [reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownArgumentType]
    if n == 0 or n >= len(buf):
        raise OSError("GetSystemDirectoryW failed")
    return buf.value


_SIGNATURE_SCRIPT = (
    "$ErrorActionPreference = 'Stop'; "
    "$items = @($env:DECKY_SIGNED_PATHS -split '\\|' | ForEach-Object { $s = Get-AuthenticodeSignature -LiteralPath $_; "
    "[pscustomobject]@{ Path = $_; Status = [string]$s.Status; "
    "Subject = $(if ($s.SignerCertificate) { $s.SignerCertificate.Subject } else { '' }) } }); "
    "ConvertTo-Json -InputObject $items -Compress"
)


def authenticode(paths: list[str], run: CommandRunner = _run, system_dir: Callable[[], str] = _system_directory) -> dict[str, tuple[str, str]]:
    '''{path: (status, signer subject)} from Get-AuthenticodeSignature (Windows PowerShell from System32).'''
    powershell = os.path.join(system_dir(), "WindowsPowerShell", "v1.0", "powershell.exe")
    env = {**os.environ, "DECKY_SIGNED_PATHS": "|".join(paths)}
    proc = run([powershell, "-NoProfile", "-NonInteractive", "-Command", _SIGNATURE_SCRIPT], env=env)
    if proc.returncode != 0:
        raise BuildError(f"Get-AuthenticodeSignature failed: {proc.stderr.strip()}")
    parsed: Any = json.loads(proc.stdout)
    # ConvertTo-Json -InputObject keeps a one-item array an array, but accept a bare object too
    rows = cast(list[dict[str, str]], parsed if isinstance(parsed, list) else [parsed])
    return {r["Path"]: (r["Status"], r["Subject"]) for r in rows}


def subject_cn(subject: str) -> str | None:
    '''The CN of an X.500 subject such as "CN=Python Software Foundation, O=..., C=US".'''
    for part in subject.split(","):
        key, sep, value = part.strip().partition("=")
        if sep and key.strip().upper() == "CN":
            return value.strip().strip('"')
    return None


def signature_problems(results: dict[str, tuple[str, str]], psf: Iterable[str], others: Iterable[str]) -> list[str]:
    '''`psf` files must be Valid and signed by the PSF; `others` (the VC runtime) must be Valid.'''
    problems: list[str] = []
    for path in psf:
        status, subject = results.get(path, ("missing", ""))
        if status != "Valid" or subject_cn(subject) != PSF_SIGNER:
            problems.append(f"{path}: {status}, signer {subject or 'none'} (need Valid, CN={PSF_SIGNER})")
    for path in others:
        status, subject = results.get(path, ("missing", ""))
        if status != "Valid":
            problems.append(f"{path}: {status}, signer {subject or 'none'} (need Valid)")
    return problems


def check_signed(folder: str, version: tuple[int, int], run: CommandRunner = _run,
                 system_dir: Callable[[], str] = _system_directory) -> dict[str, tuple[str, str]]:
    psf = [os.path.join(folder, n) for n in psf_files(version)]
    others = [os.path.join(folder, n) for n in ("vcruntime140.dll", "vcruntime140_1.dll") if os.path.isfile(os.path.join(folder, n))]
    results = authenticode(psf + others, run=run, system_dir=system_dir)
    problems = signature_problems(results, psf, others)
    if problems:
        raise BuildError("Signature check failed:\n  " + "\n  ".join(problems))
    return results


# Layout

def _copy_file(src: str, dst: str) -> str:
    # Content and timestamps only, never the source ACL (the installer copies the same way into Program Files)
    shutil.copyfile(src, dst)
    shutil.copystat(src, dst)
    return dst


def _ignore_pycache(_dir: str, names: list[str]) -> set[str]:
    return {n for n in names if n == "__pycache__" or n.endswith((".pyc", ".pyo"))}


def stdlib_ignore(lib_root: str) -> Callable[[str, list[str]], set[str]]:
    root = os.path.normcase(os.path.abspath(lib_root))
    def ignore(directory: str, names: list[str]) -> set[str]:
        skip = _ignore_pycache(directory, names)
        if os.path.normcase(os.path.abspath(directory)) == root:
            skip |= {n for n in names if n in STDLIB_SKIP}
        return skip
    return ignore


def dlls_ignore(_dir: str, names: list[str]) -> set[str]:
    '''Tcl/Tk (tkinter is not shipped) and CPython's own test extension modules.'''
    lower = {n: n.lower() for n in names}
    return {n for n, l in lower.items()
            if l.startswith(("tcl", "tk", "_tkinter", "_test", "_ctypes_test")) or l == "__pycache__"}


def copy_runtime(base: str, dest: str, version: tuple[int, int]) -> None:
    r'''<dest> = <folder>\python: the interpreter files, DLLs and Lib from `base`, plus the ._pth file.'''
    os.makedirs(dest)
    for name in runtime_files(version):
        src = os.path.join(base, name)
        if os.path.isfile(src):
            _copy_file(src, os.path.join(dest, name))
        elif name in psf_files(version) or name == "vcruntime140.dll":
            raise BuildError(f"{src} is missing")
    shutil.copytree(os.path.join(base, "DLLs"), os.path.join(dest, "DLLs"), ignore=dlls_ignore, copy_function=_copy_file)
    lib = os.path.join(base, "Lib")
    shutil.copytree(lib, os.path.join(dest, "Lib"), ignore=stdlib_ignore(lib), copy_function=_copy_file)
    with open(os.path.join(dest, pth_name(version)), "w", encoding="utf-8", newline="\r\n") as f:
        f.write("\n".join(PTH_LINES) + "\n")


def requirement_closure(root: str, distribution: Callable[[str], Any] = importlib.metadata.distribution,
                        environment: dict[str, str] | None = None) -> dict[str, Any]:
    '''{canonical name: Distribution} for `root` and everything it requires, transitively, with environment markers
    (sys_platform == "win32" and the like) evaluated for this interpreter and extras followed.'''
    found: dict[str, Any] = {}
    done: set[tuple[str, str]] = set()
    todo: list[tuple[str, str]] = [(root, "")]
    while todo:
        name, extra = todo.pop()
        key = canonicalize_name(name)
        if (key, extra) in done:
            continue
        done.add((key, extra))
        if key not in found:
            try:
                found[key] = distribution(name)
            except importlib.metadata.PackageNotFoundError:
                raise BuildError(f"{name} is required but not installed in the venv") from None
        requires: list[str] = found[key].requires or []
        for raw in requires:
            req = Requirement(raw)
            if req.marker is not None and not req.marker.evaluate({**(environment or {}), "extra": extra}):
                continue
            todo.append((req.name, ""))
            todo.extend((req.name, e) for e in req.extras)
    return found


def _inside(path: str, folder: str) -> bool:
    path, folder = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(folder))
    return os.path.commonpath([path, folder]) == folder


def distribution_files(dist: Any, site_packages: str) -> list[tuple[str, PurePath]]:
    '''(source path, path relative to site-packages) for the installed files of `dist` inside site-packages, its
    dist-info included. Files outside it (..\\Scripts) and bytecode caches are left out. RECORD entries may be
    relative or, for editable installs, absolute.'''
    files = dist.files
    if files is None:
        raise BuildError(f"{dist.metadata['Name']} has no RECORD, so its files are unknown")
    out: list[tuple[str, PurePath]] = []
    for f in files:
        src = os.path.abspath(str(dist.locate_file(f)))
        if not _inside(src, site_packages):
            continue
        rel = PurePath(os.path.relpath(src, site_packages))
        if "__pycache__" in rel.parts or rel.suffix in (".pyc", ".pyo"):
            continue
        out.append((src, rel))
    return out


def copy_distribution(dist: Any, site_packages: str, dest: str) -> int:
    count = 0
    for src, rel in distribution_files(dist, site_packages):
        if not os.path.isfile(src):
            raise BuildError(f"{src} ({dist.metadata['Name']}) is listed in its RECORD but missing")
        dst = os.path.join(dest, *rel.parts)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        _copy_file(src, dst)
        count += 1
    return count


# decky_loader's dist-info files that describe the venv's editable install rather than the package
_EDITABLE_ONLY = frozenset({"RECORD", "direct_url.json"})


def copy_app(backend: str, dest: str, own_dist: Any, deps: list[Any], site_packages: str) -> None:
    r'''<dest> = <folder>\app: main.py, the decky_loader package from this checkout, its metadata (the version), and
    the dependencies.'''
    package = os.path.join(backend, PACKAGE)
    static = os.path.join(package, "static")
    if not os.path.isdir(static) or not os.listdir(static):
        raise BuildError(f"{static} is missing or empty: build the frontend first")
    os.makedirs(dest)
    _copy_file(os.path.join(backend, "main.py"), os.path.join(dest, "main.py"))
    shutil.copytree(package, os.path.join(dest, PACKAGE), ignore=_ignore_pycache, copy_function=_copy_file)
    has_metadata = False
    for src, rel in distribution_files(own_dist, site_packages):
        if len(rel.parts) == 2 and rel.parts[0].endswith(".dist-info") and rel.name not in _EDITABLE_ONLY:
            dst = os.path.join(dest, *rel.parts)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            _copy_file(src, dst)
            has_metadata = has_metadata or rel.name == "METADATA"
    if not has_metadata:
        raise BuildError(f"No METADATA found for {PACKAGE} in {site_packages}: install it into the venv (poetry install)")
    for dist in deps:
        copy_distribution(dist, site_packages, dest)


# Checks on the staged runtime

ISOLATION_PROBE = ("import json, sys; print(json.dumps({'path': sys.path, 'isolated': sys.flags.isolated, "
                   "'no_user_site': sys.flags.no_user_site, 'safe_path': sys.flags.safe_path, "
                   "'ignore_environment': sys.flags.ignore_environment, 'site_imported': 'site' in sys.modules, "
                   "'executable': sys.executable, 'prefix': sys.prefix}))")


def isolation_report(folder: str, run: CommandRunner = _run) -> dict[str, Any]:
    '''sys.path and flags of the staged runtime, started from an unrelated directory with PYTHONPATH set: every path
    must be inside `folder`.'''
    python = os.path.join(folder, RUNTIME_DIR, "python.exe")
    with tempfile.TemporaryDirectory() as elsewhere:
        env = {**os.environ, "PYTHONPATH": elsewhere}
        proc = run([python, "-c", ISOLATION_PROBE], cwd=elsewhere, env=env)
    if proc.returncode != 0:
        raise BuildError(f"The staged runtime does not start: {proc.stderr.strip()}")
    report: dict[str, Any] = json.loads(proc.stdout)
    outside = [p for p in report["path"] if not p or not _inside(p, folder)]
    if outside:
        raise BuildError(f"The staged runtime's sys.path reaches outside {folder}: {outside}")
    if report["site_imported"]:
        raise BuildError("The staged runtime imported site")
    return report


def compile_folder(folder: str, run: CommandRunner = _run) -> None:
    # checked-hash: the bytecode stays valid whatever timestamps the copy into Program Files ends up with
    python = os.path.join(folder, RUNTIME_DIR, "python.exe")
    proc = run([python, "-m", "compileall", "-q", "-j", "0", "--invalidation-mode", "checked-hash",
                os.path.join(folder, RUNTIME_DIR, "Lib"), os.path.join(folder, APP_DIR)])
    if proc.returncode != 0:
        raise BuildError(f"compileall failed:\n{proc.stdout}{proc.stderr}")


def smoke_test(folder: str, run: CommandRunner = _run) -> str:
    '''--check-imports with the plugin module list and with the loader's own entry points, then the version the
    loader will report (from the copied decky_loader metadata). Returns that version.'''
    python, main_py = os.path.join(folder, RUNTIME_DIR, "python.exe"), os.path.join(folder, APP_DIR, "main.py")
    for modules in (CHECK_IMPORTS, LOADER_IMPORTS):
        proc = run([python, main_py, "--check-imports", *modules])
        if proc.returncode != 0:
            raise BuildError(f"--check-imports failed: {proc.stdout.strip()} {proc.stderr.strip()}")
    proc = run([python, "-c", "import importlib.metadata as m; print(m.version('decky_loader'))"])
    if proc.returncode != 0:
        raise BuildError(f"The staged copy has no decky_loader metadata: {proc.stderr.strip()}")
    return proc.stdout.strip()


def folder_size(folder: str) -> int:
    return sum(os.path.getsize(os.path.join(d, f)) for d, _, files in os.walk(folder) for f in files)


def build(out: str, venv: str = sys.prefix, backend: str = BACKEND, run: CommandRunner = _run) -> dict[str, Any]:
    out = os.path.abspath(out)
    if os.path.lexists(out) and (not os.path.isdir(out) or os.listdir(out)):
        raise BuildError(f"{out} already exists and is not empty")
    if os.path.normcase(os.path.abspath(sys.prefix)) == os.path.normcase(os.path.abspath(sys.base_prefix)):
        raise BuildError("Run this with the backend venv's python (backend\\.venv\\Scripts\\python.exe)")
    cfg = read_pyvenv_cfg(venv)
    base = cfg.get("home", "")
    if not base or not os.path.isfile(os.path.join(base, "python.exe")):
        raise BuildError(f"pyvenv.cfg in {venv} does not name a base interpreter (home = {base or '?'})")
    version = (sys.version_info[0], sys.version_info[1])
    check_versions(cfg, platform.python_version(), interpreter_version(os.path.join(base, "python.exe"), run))
    check_signed(base, version, run)

    site_packages = os.path.join(venv, "Lib", "site-packages")
    closure = requirement_closure(PACKAGE)
    own = closure.pop(canonicalize_name(PACKAGE))
    deps = [closure[k] for k in sorted(closure)]

    os.makedirs(out, exist_ok=True)
    copy_runtime(base, os.path.join(out, RUNTIME_DIR), version)
    copy_app(backend, os.path.join(out, APP_DIR), own, deps, site_packages)
    compile_folder(out, run)
    signatures = check_signed(os.path.join(out, RUNTIME_DIR), version, run)
    isolation = isolation_report(out, run)
    loader_version = smoke_test(out, run)
    return {"folder": out, "base": base, "version": platform.python_version(), "loader_version": loader_version,
            "dependencies": [f"{d.metadata['Name']} {d.version}" for d in deps], "signatures": signatures,
            "isolation": isolation, "size": folder_size(out)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", required=True, help="the installer folder to create (must not exist, or be empty)")
    args = parser.parse_args(argv)
    try:
        result = build(args.out)
    except BuildError as e:
        print(f"build_win_runtime: {e}", file=sys.stderr)
        return 1
    print(f"Built {result['folder']} ({result['size'] / 1048576:.1f} MiB): decky_loader {result['loader_version']} "
          f"on Python {result['version']} from {result['base']}")
    print("Dependencies: " + ", ".join(result["dependencies"]))
    for path, (status, subject) in result["signatures"].items():
        print(f"Signature {os.path.basename(path)}: {status}, {subject}")
    print("Staged sys.path: " + json.dumps(result["isolation"]["path"]))
    print("Staged flags: " + ", ".join(f"{k}={result['isolation'][k]}" for k in
                                        ("isolated", "no_user_site", "safe_path", "ignore_environment", "site_imported")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
