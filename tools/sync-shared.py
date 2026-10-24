"""Copy the shared files from an Overdrive commit.

  python tools/sync-shared.py --overdrive <overdrive repo> --commit <sha>

Reads shared-files.txt at that commit (git show), writes each file (Python: py_modules/overdrive/<n>.py to
py_modules/traction/<n>.py; C#: helper/src/<p> to helper/src/shared/<p>) byte for byte, and writes shared/SOURCE:
{"overdrive_commit": sha, "files": {dest path: sha256}}.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys

PLUGIN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def git(repo, *args):
    res = subprocess.run(["git", "-C", repo] + list(args), capture_output=True, timeout=60)
    if res.returncode != 0:
        raise SystemExit("git %s failed: %s" % (" ".join(args), res.stderr.decode("utf-8", "replace").strip()))
    return res.stdout


def dest_path(src):
    if src.startswith("py_modules/overdrive/") and src.endswith(".py"):
        return "py_modules/traction/" + src[len("py_modules/overdrive/"):]
    if src.startswith("helper/src/") and src.endswith(".cs"):
        return "helper/src/shared/" + src[len("helper/src/"):]
    raise SystemExit("cannot place shared file: " + src)


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--overdrive", required=True)
    ap.add_argument("--commit", required=True)
    args = ap.parse_args(argv)

    commit = git(args.overdrive, "rev-parse", "--verify", args.commit + "^{commit}").decode().strip()
    listing = git(args.overdrive, "show", commit + ":shared-files.txt").decode("utf-8-sig")
    srcs = [l.strip() for l in listing.splitlines() if l.strip() and not l.strip().startswith("#")]
    if not srcs:
        raise SystemExit("shared-files.txt is empty at " + commit)

    files = {}
    for src in srcs:
        data = git(args.overdrive, "show", "%s:%s" % (commit, src))
        dest = dest_path(src)
        path = os.path.join(PLUGIN, *dest.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        files[dest] = hashlib.sha256(data).hexdigest()
        print("%s -> %s" % (src, dest))

    init = os.path.join(PLUGIN, "py_modules", "traction", "__init__.py")
    if not os.path.exists(init):
        open(init, "wb").close()

    os.makedirs(os.path.join(PLUGIN, "shared"), exist_ok=True)
    with open(os.path.join(PLUGIN, "shared", "SOURCE"), "w", encoding="utf-8", newline="\n") as f:
        json.dump({"overdrive_commit": commit, "files": files}, f, indent=2, sort_keys=True)
        f.write("\n")
    print("shared/SOURCE written for %s (%d files)" % (commit[:7], len(files)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
