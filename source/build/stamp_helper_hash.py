#!/usr/bin/env python3
"""Stamp the SHA-256 of kit/rapreaper_helper.lua into the server and the installer.

BUILD TOOL (not shipped). The server refuses to use a helper script whose checksum is not the one stamped
here, so a changed helper is noticed like a changed bridge file. In the source tree both constants read
UNSTAMPED (no check), so that the helper can be edited and tested; the release build stamps them.

    python3 build/stamp_helper_hash.py            # stamp
    python3 build/stamp_helper_hash.py --check    # only report whether the stamps match the file
    python3 build/stamp_helper_hash.py --unstamp  # back to UNSTAMPED (for development)
"""
import hashlib
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
HELPER = os.path.join(ROOT, "kit", "rapreaper_helper.lua")
TARGETS = [os.path.join(ROOT, "kit", "rapreaper_server.py"), os.path.join(ROOT, "rapreaper_install.py")]
PATTERN = re.compile(r'^HELPER_SHA256 = (?:"([0-9a-f]{64})"|UNSTAMPED)', re.M)


def main(argv):
    with open(HELPER, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    check_only = "--check" in argv
    unstamp = "--unstamp" in argv
    bad = 0
    for path in TARGETS:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        m = PATTERN.search(text)
        if not m:
            print("no HELPER_SHA256 line in %s" % path)
            bad += 1
            continue
        have = m.group(1)
        if unstamp:
            text = PATTERN.sub("HELPER_SHA256 = UNSTAMPED", text, count=1)
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            print("%s: unstamped" % os.path.relpath(path, ROOT))
            continue
        if have == digest:
            print("%s: stamp matches" % os.path.relpath(path, ROOT))
            continue
        if check_only:
            print("%s: %s does not match the helper (%s...)" % (os.path.relpath(path, ROOT), ("stamp %s..." % have[:12]) if have else "UNSTAMPED", digest[:12]))
            bad += 1
            continue
        text = PATTERN.sub('HELPER_SHA256 = "%s"' % digest, text, count=1)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        print("%s: stamped %s" % (os.path.relpath(path, ROOT), digest))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
