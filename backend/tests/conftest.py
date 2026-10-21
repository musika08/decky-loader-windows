import os, sys

# Make `decky_loader` importable when pytest is run from backend/ or the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
