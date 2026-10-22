import os
import sys
import importlib.util
from PyInstaller.building.build_main import Analysis
from PyInstaller.building.api import COLLECT, EXE, PYZ
from PyInstaller.utils.hooks import copy_metadata

noconsole_str = os.getenv('DECKY_NOCONSOLE', '0')
noconsole = noconsole_str == '1'
# Onedir (PluginLoader.exe plus its _internal folder) is what Windows installed mode installs. Onefile stays the default.
onedir = os.getenv('DECKY_ONEDIR', '0') == '1'
repo = os.getenv('DECKY_REPO', 'SteamDeckHomebrew/decky-loader')
branch = os.getenv('DECKY_BRANCH', 'main')
with open('build_config.py', 'w') as f:
    f.write(f"""
import os
os.environ['DECKY_PACKAGED_BUILD'] = '1'

# Build info
os.environ['DECKY_REPO'] = {repr(repo)}
os.environ['DECKY_BRANCH'] = {repr(branch)}

# Windows-specific
os.environ['DECKY_NOCONSOLE'] = {repr(noconsole_str)}
""")

name = "PluginLoader"
# if noconsole:
#    name += "_noconsole"

hiddenimports = ['logging.handlers', 'sqlite3', 'http.server', 'socketserver', 'configparser', 'decky_plugin', 'decky']
if sys.platform == "win32":
    # Plugins run inside this exe and there is usually no usable system python to fall back on,
    # so bundle the whole stdlib (minus GUI/dev-only packages) plus common plugin dependencies.
    skip = {"tkinter", "turtle", "turtledemo", "idlelib", "test", "ensurepip", "venv", "lib2to3", "pydoc_data"}
    def _importable(name):
        try:
            return importlib.util.find_spec(name) is not None
        except Exception:
            return False
    hiddenimports += sorted(n for n in sys.stdlib_module_names if n not in skip and _importable(n))
    hiddenimports += ["psutil", "PIL", "pystray"]

a = Analysis(
    ['main.py'],
    datas=[
        ('decky_loader/locales', 'decky_loader/locales'),
        ('decky_loader/static', 'decky_loader/static'),
        # xdg-open.cmd / chmod.cmd put on plugin hosts' PATH by winplugincompat
        ('decky_loader/localplatform/winbin', 'decky_loader/localplatform/winbin'),
    ] + copy_metadata('decky_loader'),
    runtime_hooks=['build_config.py'],
    hiddenimports=hiddenimports,
)
pyz = PYZ(a.pure, a.zipped_data)

if onedir:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name=name,
        upx=True,
        console=not noconsole,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        name=name,
        upx=True,
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        name=name,
        upx=True,
        console=not noconsole,
    )
