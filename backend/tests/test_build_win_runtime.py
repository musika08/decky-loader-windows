import importlib.util, json, os, subprocess, sys
from pathlib import Path, PurePath
from types import ModuleType
from typing import Any
import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only build")

TOOL = Path(__file__).resolve().parents[2] / "tools" / "build_win_runtime.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("build_win_runtime", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bw = _load()


def _tree(d: Path) -> set[str]:
    return {str(p.relative_to(d)) for p in d.rglob("*") if p.is_file()}


def _touch(root: Path, *rels: str) -> None:
    for rel in rels:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(rel.encode())


# Layout of <folder>\python

def _fake_base(tmp_path: Path) -> Path:
    base = tmp_path / "Python313"
    _touch(base, "python.exe", "pythonw.exe", "python3.dll", "python313.dll", "vcruntime140.dll", "vcruntime140_1.dll",
           "LICENSE.txt", "NEWS.txt", r"Scripts\pip.exe", r"Doc\x.html", r"include\Python.h", r"libs\python313.lib",
           r"tcl\tcl8.6\init.tcl",
           r"DLLs\_socket.pyd", r"DLLs\_ssl.pyd", r"DLLs\libssl-3.dll", r"DLLs\sqlite3.dll", r"DLLs\tcl86t.dll",
           r"DLLs\tk86t.dll", r"DLLs\_tkinter.pyd", r"DLLs\_testcapi.pyd", r"DLLs\_ctypes_test.pyd",
           r"Lib\os.py", r"Lib\json\__init__.py", r"Lib\json\__pycache__\__init__.cpython-313.pyc",
           r"Lib\__pycache__\os.cpython-313.pyc", r"Lib\site-packages\pip\__init__.py", r"Lib\test\test_os.py",
           r"Lib\idlelib\idle.py", r"Lib\tkinter\__init__.py", r"Lib\turtledemo\demo.py", r"Lib\ensurepip\__init__.py",
           r"Lib\unittest\test\keep.py", r"Lib\asyncio\events.py")
    return base


def test_runtime_layout_and_pth(tmp_path: Path):
    base = _fake_base(tmp_path)
    dest = tmp_path / "out" / "python"
    bw.copy_runtime(str(base), str(dest), (3, 13))
    assert _tree(dest) == {
        "python.exe", "pythonw.exe", "python3.dll", "python313.dll", "vcruntime140.dll", "vcruntime140_1.dll",
        "LICENSE.txt", "python313._pth",
        r"DLLs\_socket.pyd", r"DLLs\_ssl.pyd", r"DLLs\libssl-3.dll", r"DLLs\sqlite3.dll",
        r"Lib\os.py", r"Lib\json\__init__.py", r"Lib\asyncio\events.py",
        r"Lib\unittest\test\keep.py",  # only the top-level stdlib test package is left out
    }
    # Lib, DLLs and the app folder only, no "import site", CRLF like the python.org one
    assert (dest / "python313._pth").read_bytes() == b"Lib\r\nDLLs\r\n..\\app\r\n"


def test_runtime_copy_fails_without_the_signed_files(tmp_path: Path):
    base = _fake_base(tmp_path)
    (base / "pythonw.exe").unlink()
    with pytest.raises(bw.BuildError, match="pythonw.exe is missing"):
        bw.copy_runtime(str(base), str(tmp_path / "out" / "python"), (3, 13))


def test_pth_and_dll_names_follow_the_version():
    assert bw.pth_name((3, 13)) == "python313._pth" and bw.dll_name((3, 14)) == "python314.dll"
    assert bw.psf_files((3, 13)) == ["python.exe", "pythonw.exe", "python3.dll", "python313.dll"]


# Dependency closure

DEV_ONLY = {"pytest", "pyinstaller", "pyinstaller-hooks-contrib", "pyright", "poetry", "poetry-core", "pip", "setuptools",
            "pefile", "altgraph", "iniconfig", "pluggy", "nodeenv", "virtualenv", "dulwich", "keyring", "cleo", "build"}


def test_closure_of_the_real_venv_has_the_runtime_deps_and_no_dev_deps():
    closure = bw.requirement_closure("decky_loader")
    names = set(closure)
    for needed in ("decky-loader", "aiohttp", "aiohttp-jinja2", "aiohttp-cors", "certifi", "psutil", "pillow", "pystray",
                   "watchdog", "multidict", "yarl", "frozenlist", "aiosignal", "attrs", "idna", "propcache",
                   "aiohappyeyeballs", "jinja2", "markupsafe", "packaging", "setproctitle", "six"):
        assert needed in names, needed
    assert not names & DEV_ONLY, names & DEV_ONLY


class FakeDist:
    def __init__(self, name: str, requires: list[str] | None = None, files: list[str] | None = None, root: str = ""):
        self.metadata = {"Name": name}
        self.requires = requires
        self.version = "1.0"
        self.files = None if files is None else [PurePath(f) for f in files]
        self.root = root

    def locate_file(self, f: Any) -> Path:
        return Path(self.root) / str(f)


def test_closure_follows_markers_and_extras():
    dists = {
        "app": FakeDist("app", ["lib-a (>=1)", "lib-win ; sys_platform == 'win32'", "lib-linux ; sys_platform == 'linux'",
                                "lib-b[fast]", "lib-old ; python_version < '3.0'"]),
        "lib-a": FakeDist("lib-a", ["lib-test ; extra == 'test'"]),
        "lib-win": FakeDist("lib-win"),
        "lib-b": FakeDist("lib-b", ["lib-speed ; extra == 'fast'", "lib-doc ; extra == 'doc'", "Lib_A"]),
        "lib-speed": FakeDist("lib-speed"),
    }
    seen: list[str] = []

    def distribution(name: str) -> FakeDist:
        seen.append(name)
        return dists[bw.canonicalize_name(name)]

    closure = bw.requirement_closure("app", distribution=distribution, environment={"sys_platform": "win32"})
    assert sorted(closure) == ["app", "lib-a", "lib-b", "lib-speed", "lib-win"]
    assert len(seen) == len(set(bw.canonicalize_name(s) for s in seen))  # each looked up once


def test_closure_fails_on_a_missing_requirement():
    import importlib.metadata

    def distribution(name: str) -> FakeDist:
        if name == "app":
            return FakeDist("app", ["gone"])
        raise importlib.metadata.PackageNotFoundError(name)

    with pytest.raises(bw.BuildError, match="gone is required"):
        bw.requirement_closure("app", distribution=distribution)


def test_distribution_files_stay_inside_site_packages(tmp_path: Path):
    sp = tmp_path / "venv" / "Lib" / "site-packages"
    _touch(sp, r"lib\__init__.py", r"lib\__pycache__\x.pyc", r"lib-1.0.dist-info\METADATA", r"lib-1.0.dist-info\RECORD")
    files = ["lib/__init__.py", "lib/__pycache__/x.pyc", "lib-1.0.dist-info/METADATA", "lib-1.0.dist-info/RECORD",
             "../../Scripts/lib.exe",  # console scripts are outside site-packages
             str(sp / "lib.pth"),      # editable installs list absolute paths
             str(tmp_path / "elsewhere" / "x.py")]
    dist = FakeDist("lib", files=files, root=str(sp))
    got = [str(rel) for _src, rel in bw.distribution_files(dist, str(sp))]
    assert got == [r"lib\__init__.py", r"lib-1.0.dist-info\METADATA", r"lib-1.0.dist-info\RECORD", "lib.pth"]
    with pytest.raises(bw.BuildError, match="no RECORD"):
        bw.distribution_files(FakeDist("x", files=None), str(sp))


def test_copy_distribution_refuses_a_missing_recorded_file(tmp_path: Path):
    sp = tmp_path / "sp"
    _touch(sp, r"lib\__init__.py")
    dist = FakeDist("lib", files=["lib/__init__.py", "lib/gone.py"], root=str(sp))
    with pytest.raises(bw.BuildError, match="missing"):
        bw.copy_distribution(dist, str(sp), str(tmp_path / "app"))


def test_copy_app_takes_the_package_from_the_checkout_and_only_metadata_from_the_editable_install(tmp_path: Path):
    backend = tmp_path / "backend"
    _touch(backend, "main.py", r"decky_loader\__init__.py", r"decky_loader\static\index.js", r"decky_loader\locales\en-US.json",
           r"decky_loader\localplatform\winbin\chmod.cmd", r"decky_loader\__pycache__\x.pyc", r"tests\test_x.py")
    sp = tmp_path / "sp"
    info = "decky_loader-1.2.3.dist-info"
    _touch(sp, info + r"\METADATA", info + r"\RECORD", info + r"\direct_url.json", info + r"\entry_points.txt",
           r"dep\__init__.py", r"dep-1.0.dist-info\METADATA")
    own = FakeDist("decky-loader", files=[str(sp / "decky_loader.pth"), str(sp / info / "METADATA"), str(sp / info / "RECORD"),
                                          str(sp / info / "direct_url.json"), str(sp / info / "entry_points.txt"),
                                          str(tmp_path / "Scripts" / "decky-loader.cmd")], root=str(sp))
    dep = FakeDist("dep", files=["dep/__init__.py", "dep-1.0.dist-info/METADATA"], root=str(sp))
    bw.copy_app(str(backend), str(tmp_path / "app"), own, [dep], str(sp))
    assert _tree(tmp_path / "app") == {
        "main.py", r"decky_loader\__init__.py", r"decky_loader\static\index.js", r"decky_loader\locales\en-US.json",
        r"decky_loader\localplatform\winbin\chmod.cmd", info + r"\METADATA", info + r"\entry_points.txt",
        r"dep\__init__.py", r"dep-1.0.dist-info\METADATA"}


def test_copy_app_needs_the_built_frontend(tmp_path: Path):
    backend = tmp_path / "backend"
    _touch(backend, "main.py", r"decky_loader\__init__.py")
    with pytest.raises(bw.BuildError, match="build the frontend first"):
        bw.copy_app(str(backend), str(tmp_path / "app"), FakeDist("decky-loader", files=[]), [], str(tmp_path))


# Signatures and versions

PSF = "CN=Python Software Foundation, O=Python Software Foundation, L=Beaverton, S=Oregon, C=US"
MS = "CN=Microsoft Windows Software Compatibility Publisher, O=Microsoft Corporation, L=Redmond, S=Washington, C=US"


def test_subject_cn():
    assert bw.subject_cn(PSF) == "Python Software Foundation"
    assert bw.subject_cn('O=x, CN="Quoted, Name"') == "Quoted"  # naive split: never matches the PSF by accident
    assert bw.subject_cn("O=Python Software Foundation") is None


def test_signature_problems():
    ok = {"a": ("Valid", PSF), "v": ("Valid", MS)}
    assert bw.signature_problems(ok, ["a"], ["v"]) == []
    assert bw.signature_problems({"a": ("Valid", MS)}, ["a"], []) != []                                  # wrong signer
    assert bw.signature_problems({"a": ("NotSigned", "")}, ["a"], []) != []
    assert bw.signature_problems({"a": ("HashMismatch", PSF)}, ["a"], []) != []
    assert bw.signature_problems({"a": ("Valid", "CN=Python Software Foundation Fake")}, ["a"], []) != []
    assert bw.signature_problems({}, ["a"], []) != []                                                    # not checked
    assert bw.signature_problems({"v": ("UnknownError", MS)}, [], ["v"]) != []


def test_authenticode_runs_system_powershell_with_the_paths(tmp_path: Path):
    calls: list[Any] = []

    def run(argv: list[str], **kw: Any) -> Any:
        calls.append((argv, kw))
        out = [{"Path": p, "Status": "Valid", "Subject": PSF} for p in kw["env"]["DECKY_SIGNED_PATHS"].split("|")]
        return subprocess.CompletedProcess(argv, 0, json.dumps(out if len(out) > 1 else out[0]), "")

    got = bw.authenticode([r"C:\a b\python.exe", r"C:\x\pythonw.exe"], run=run, system_dir=lambda: r"C:\Win\System32")
    assert got == {r"C:\a b\python.exe": ("Valid", PSF), r"C:\x\pythonw.exe": ("Valid", PSF)}
    assert calls[0][0][0] == r"C:\Win\System32\WindowsPowerShell\v1.0\powershell.exe"
    assert bw.authenticode([r"C:\one.exe"], run=run, system_dir=lambda: r"C:\S") == {r"C:\one.exe": ("Valid", PSF)}


def test_check_signed_fails_when_a_file_is_not_psf_signed(tmp_path: Path):
    base = _fake_base(tmp_path)

    def run(argv: list[str], **kw: Any) -> Any:
        paths = kw["env"]["DECKY_SIGNED_PATHS"].split("|")
        out = [{"Path": p, "Status": "Valid", "Subject": MS if p.endswith("pythonw.exe") or "vcruntime" in p else PSF}
               for p in paths]
        return subprocess.CompletedProcess(argv, 0, json.dumps(out), "")

    with pytest.raises(bw.BuildError, match="pythonw.exe"):
        bw.check_signed(str(base), (3, 13), run=run, system_dir=lambda: r"C:\S")


def test_the_real_base_runtime_is_psf_signed():
    cfg = bw.read_pyvenv_cfg(sys.prefix)
    if not cfg.get("home"):
        pytest.skip("not running from a venv")
    results = bw.check_signed(cfg["home"], (sys.version_info[0], sys.version_info[1]))
    assert all(status == "Valid" for status, _ in results.values())


def test_versions_must_agree():
    bw.check_versions({"version": "3.13.15"}, "3.13.15", "3.13.15")
    with pytest.raises(bw.BuildError, match="differ"):
        bw.check_versions({"version": "3.13.15"}, "3.13.15", "3.13.14")
    with pytest.raises(bw.BuildError, match="differ"):
        bw.check_versions({}, "3.13.15", "3.13.15")


# Checks on the staged folder

def _good_report(folder: Path) -> dict[str, Any]:
    return {"path": [str(folder / "python" / "Lib"), str(folder / "python" / "DLLs"), str(folder / "app")],
            "isolated": 1, "site_imported": False, "inspect": 0, "dev_mode": False, "utf8_mode": 0,
            "dont_write_bytecode": 0, "pycache_prefix": None, "warnoptions": [], "stdout_encoding": "cp1252"}


def test_isolation_report_runs_with_dash_i_and_a_hostile_environment(tmp_path: Path):
    seen: list[tuple[list[str], dict[str, Any]]] = []

    def run(argv: list[str], **kw: Any) -> Any:
        seen.append((argv, kw))
        return subprocess.CompletedProcess(argv, 0, json.dumps(_good_report(tmp_path)), "")

    assert bw.isolation_report(str(tmp_path), run=run)["path"] == _good_report(tmp_path)["path"]
    argv, kw = seen[0]
    assert argv[:3] == [str(tmp_path / "python" / "python.exe"), "-I", "-c"]
    env = kw["env"]
    assert env["PYTHONPATH"] == kw["cwd"] and env["PYTHONHOME"] == kw["cwd"]  # started elsewhere, pointing there
    for key, value in {"PYTHONINSPECT": "1", "PYTHONDEVMODE": "1", "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1",
                       "PYTHONWARNINGS": "default", "PYTHONIOENCODING": "iso8859-1", "PYTHONMALLOC": "debug"}.items():
        assert env[key] == value, key
    assert env["PYTHONSTARTUP"].endswith("startup.py") and env["PYTHONPYCACHEPREFIX"].startswith(kw["cwd"])
    assert kw["timeout"] == bw.PROBE_TIMEOUT


@pytest.mark.parametrize("change", [
    {"path": "outside"}, {"path": "missing DLLs"}, {"path": "empty entry"}, {"inspect": 1}, {"dev_mode": True},
    {"utf8_mode": 1}, {"dont_write_bytecode": 1}, {"pycache_prefix": r"C:\pcp"}, {"warnoptions": ["default"]},
    {"site_imported": True}, {"isolated": 0}])
def test_isolation_report_rejects_anything_the_environment_changed(tmp_path: Path, change: dict[str, Any]):
    report = _good_report(tmp_path)
    if change.get("path") == "outside":
        report["path"] = report["path"] + [r"C:\Users\me\site-packages"]
    elif change.get("path") == "missing DLLs":
        report["path"] = [report["path"][0], report["path"][2]]
    elif change.get("path") == "empty entry":
        report["path"] = [""] + report["path"]
    else:
        report.update(change)

    def run(argv: list[str], **kw: Any) -> Any:
        return subprocess.CompletedProcess(argv, 0, json.dumps(report), "")

    with pytest.raises(bw.BuildError, match="sys.path|followed the environment"):
        bw.isolation_report(str(tmp_path), run=run)


def test_isolation_report_notices_pythonstartup_running(tmp_path: Path):
    def run(argv: list[str], **kw: Any) -> Any:
        startup = kw["env"]["PYTHONSTARTUP"]
        exec(Path(startup).read_text(encoding="utf-8"))  # what an interpreter honouring it would do
        return subprocess.CompletedProcess(argv, 0, json.dumps(_good_report(tmp_path)), "")

    with pytest.raises(bw.BuildError, match="PYTHONSTARTUP"):
        bw.isolation_report(str(tmp_path), run=run)


def test_isolation_report_wraps_bad_output(tmp_path: Path):
    def run(argv: list[str], **kw: Any) -> Any:
        return subprocess.CompletedProcess(argv, 0, ">>> ", "")

    with pytest.raises(bw.BuildError, match="Unexpected isolation probe output"):
        bw.isolation_report(str(tmp_path), run=run)


def test_run_never_waits_on_stdin_and_times_out(tmp_path: Path):
    # An interpreter dropped into a REPL gets EOF at once instead of waiting for input
    proc = bw._run([sys.executable, "-I", "-c", "import sys; print(repr(sys.stdin.read()))"])  # pyright: ignore [reportPrivateUsage]
    assert proc.stdout.strip() == "''"
    with pytest.raises(bw.BuildError, match="Timed out"):
        bw._run([sys.executable, "-I", "-c", "import time; time.sleep(30)"], timeout=1)  # pyright: ignore [reportPrivateUsage]


def test_smoke_test_runs_check_imports_with_the_staged_runtime(tmp_path: Path):
    calls: list[list[str]] = []

    def run(argv: list[str], **kw: Any) -> Any:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "3.3.0\n", "")

    assert bw.smoke_test(str(tmp_path), run=run) == "3.3.0"
    py, main = str(tmp_path / "python" / "python.exe"), str(tmp_path / "app" / "main.py")
    assert calls[0] == [py, "-I", main, "--check-imports", *bw.CHECK_IMPORTS]
    assert calls[1] == [py, "-I", main, "--check-imports", *bw.LOADER_IMPORTS]
    assert calls[2][:3] == [py, "-I", "-c"]
    assert "psutil" in bw.CHECK_IMPORTS and "aiohttp" in bw.CHECK_IMPORTS and "winreg" in bw.CHECK_IMPORTS

    def failing(argv: list[str], **kw: Any) -> Any:
        return subprocess.CompletedProcess(argv, 1, "missing: psutil", "")

    with pytest.raises(bw.BuildError, match="missing: psutil"):
        bw.smoke_test(str(tmp_path), run=failing)


def test_compile_uses_dash_i(tmp_path: Path):
    calls: list[list[str]] = []

    def run(argv: list[str], **kw: Any) -> Any:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    bw.compile_folder(str(tmp_path), run=run)
    assert calls[0][:4] == [str(tmp_path / "python" / "python.exe"), "-I", "-m", "compileall"]
    assert "checked-hash" in calls[0]


def test_staged_main_must_refuse_to_run_without_dash_i(tmp_path: Path):
    def run_with(rc: int, err: str) -> Any:
        def run(argv: list[str], **kw: Any) -> Any:
            assert "-I" not in argv
            return subprocess.CompletedProcess(argv, rc, "", err)
        return run

    bw.check_refuses_without_isolation(str(tmp_path), run=run_with(2, "Decky's Python runtime must be started with -I"))
    with pytest.raises(bw.BuildError, match="ran without -I"):
        bw.check_refuses_without_isolation(str(tmp_path), run=run_with(0, ""))


def test_build_config_mirrors_the_pyinstaller_runtime_hook(tmp_path: Path):
    assert bw.build_config({}) == {"DECKY_PACKAGED_BUILD": "1", "DECKY_REPO": "SteamDeckHomebrew/decky-loader",
                                   "DECKY_BRANCH": "main", "DECKY_NOCONSOLE": "1"}
    assert bw.build_config({"DECKY_REPO": "me/fork", "DECKY_BRANCH": "win"})["DECKY_REPO"] == "me/fork"
    bw.write_build_config(str(tmp_path), {"DECKY_BRANCH": "win"})
    data = json.loads((tmp_path / "build_config.json").read_text(encoding="utf-8"))
    assert data["DECKY_BRANCH"] == "win"
    # The same keys and defaults as pyinstaller.spec's hook, and the name main.py reads
    spec = (Path(__file__).resolve().parents[1] / "pyinstaller.spec").read_text(encoding="utf-8")
    assert "os.getenv('DECKY_REPO', 'SteamDeckHomebrew/decky-loader')" in spec and "os.getenv('DECKY_BRANCH', 'main')" in spec
    from decky_loader.localplatform import winsupervisor
    assert bw.BUILD_CONFIG == winsupervisor.BUILD_CONFIG and set(data) == set(winsupervisor.BUILD_CONFIG_KEYS)


def test_every_extension_binary_is_signature_checked(tmp_path: Path):
    base = _fake_base(tmp_path)
    _touch(base, r"DLLs\py.ico", r"DLLs\python_lib.cat")
    names = sorted(os.path.basename(p) for p in bw.extension_binaries(str(base)))
    # Every .dll and .pyd that copy_runtime keeps; tcl/tk and test modules are not shipped, icons are not code
    assert names == ["_socket.pyd", "_ssl.pyd", "libssl-3.dll", "sqlite3.dll"]
    assert bw.DLLS_SIGNATURE_EXCEPTIONS == frozenset()
    seen: list[str] = []

    def run(argv: list[str], **kw: Any) -> Any:
        paths = kw["env"]["DECKY_SIGNED_PATHS"].split("|")
        seen.extend(paths)
        out = [{"Path": p, "Status": "NotSigned" if p.endswith("sqlite3.dll") else "Valid",
                "Subject": MS if "vcruntime" in p else PSF} for p in paths]
        return subprocess.CompletedProcess(argv, 0, json.dumps(out), "")

    with pytest.raises(bw.BuildError, match="sqlite3.dll: NotSigned"):
        bw.check_signed(str(base), (3, 13), run=run, system_dir=lambda: r"C:\S")
    assert str(base / "DLLs" / "_ssl.pyd") in seen


@pytest.mark.parametrize("stdout", ["", "not json", '[{"Path": "a"}]', "42"])
def test_authenticode_wraps_unexpected_output(stdout: str):
    def run(argv: list[str], **kw: Any) -> Any:
        return subprocess.CompletedProcess(argv, 0, stdout, "")

    with pytest.raises(bw.BuildError, match="Unexpected Get-AuthenticodeSignature output"):
        bw.authenticode([r"C:\a.exe"], run=run, system_dir=lambda: r"C:\S")


def test_build_refuses_a_non_empty_output_folder(tmp_path: Path):
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "x").write_bytes(b"")
    with pytest.raises(bw.BuildError, match="not empty"):
        bw.build(str(tmp_path / "out"))
    assert os.listdir(tmp_path / "out") == ["x"]
