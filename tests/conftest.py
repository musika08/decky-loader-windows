import logging
import os
import sys
import types

ROOT = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(ROOT)
sys.path.insert(0, os.path.join(ROOT, "py_modules"))
sys.path.insert(0, ROOT)

# Stand-in for the loader's `decky` module so main.py imports outside Decky.
# Tests never use the real settings or runtime directories: they pass paths under tmp_path.
if "decky" not in sys.modules:
    decky = types.ModuleType("decky")
    decky.logger = logging.getLogger("overdrive-test")
    sys.modules["decky"] = decky
