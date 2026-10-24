"""Fail (exit 1) when a shared file differs from shared/SOURCE.

  python tools/check-shared.py [--root <plugin folder>] [--overdrive <overdrive repo>]

Every file listed in shared/SOURCE must exist and have the recorded sha256. With --overdrive the file is also
compared with the same file at the recorded commit (git show <commit>:<path>).
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys

PY_DEST = "py_modules/traction/"
PY_SRC = "py_modules/overdrive/"
CS_DEST = "helper/src/shared/"
CS_SRC = "helper/src/"


def source_path(dest):
    """Path of a shared file in the Overdrive repo, from its path in Traction."""
    if dest.startswith(PY_DEST):
        return PY_SRC + dest[len(PY_DEST):]
    if dest.startswith(CS_DEST):
        return CS_SRC + dest[len(CS_DEST):]
    raise ValueError("not a shared path: " + dest)


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument("--overdrive")
    args = ap.parse_args(argv)

    try:
        with open(os.path.join(args.root, "shared", "SOURCE"), encoding="utf-8") as f:
            source = json.load(f)
        commit = source["overdrive_commit"]
        files = source["files"]
    except (OSError, ValueError, KeyError) as e:
        print("cannot read shared/SOURCE: %s" % e)
        return 1
    if not files:
        print("shared/SOURCE lists no files")
        return 1

    bad = []
    for dest, want in sorted(files.items()):
        path = os.path.join(args.root, *dest.split("/"))
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            bad.append("%s: missing" % dest)
            continue
        if hashlib.sha256(data).hexdigest() != want:
            bad.append("%s: differs from shared/SOURCE" % dest)
            continue
        if args.overdrive:
            res = subprocess.run(["git", "-C", args.overdrive, "show", "%s:%s" % (commit, source_path(dest))],
                                 capture_output=True, timeout=60)
            if res.returncode != 0:
                bad.append("%s: not in Overdrive %s" % (dest, commit[:7]))
            elif res.stdout != data:
                bad.append("%s: differs from Overdrive %s" % (dest, commit[:7]))
    for line in bad:
        print(line)
    if bad:
        print("%d shared file(s) differ; run tools/sync-shared.py" % len(bad))
        return 1
    print("shared files match Overdrive %s (%d files)" % (commit[:7], len(files)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
