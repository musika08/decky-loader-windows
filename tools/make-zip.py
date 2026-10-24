"""Build the Traction release zip: traction-<version>.zip with a top folder `traction/`.

Usage: python tools/make-zip.py [output_dir]   (default: dist-release/ in the repo)
Needs a finished build: dist/ (npm run build) and bin/ (helper build).
"""
import hashlib
import json
import os
import sys
import zipfile

FOLDER = "traction"
FILES = ("plugin.json", "main.py", "package.json", "README.md", "LICENSE", "THIRD-PARTY-NOTICES.md")
DIRS = ("dist", "py_modules", "bin")


def build_zip(root, out_dir):
    with open(os.path.join(root, "package.json"), encoding="utf-8") as f:
        version = json.load(f)["version"]
    entries = []
    for name in FILES:
        path = os.path.join(root, name)
        if not os.path.isfile(path):
            raise FileNotFoundError(f"missing {name}")
        entries.append((path, f"{FOLDER}/{name}"))
    for d in DIRS:
        base = os.path.join(root, d)
        if not os.path.isdir(base):
            raise FileNotFoundError(f"missing folder {d}/ (run the build first)")
        found = False
        for cur, subdirs, names in os.walk(base):
            subdirs[:] = sorted(s for s in subdirs if s != "__pycache__")
            for n in sorted(names):
                if n.endswith(".pyc"):
                    continue
                full = os.path.join(cur, n)
                rel = os.path.relpath(full, root).replace(os.sep, "/")
                entries.append((full, f"{FOLDER}/{rel}"))
                found = True
        if not found:
            raise FileNotFoundError(f"folder {d}/ is empty (run the build first)")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"{FOLDER}-{version}.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for path, arc in entries:
            z.write(path, arc)
    with open(out, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    return out, digest


def main(argv):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_dir = argv[1] if len(argv) > 1 else os.path.join(root, "dist-release")
    try:
        out, digest = build_zip(root, out_dir)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(out)
    print(f"sha256 {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
