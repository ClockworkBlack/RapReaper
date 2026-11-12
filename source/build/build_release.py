#!/usr/bin/env python3
"""Build the RapReaper release: three zips, for Windows, for Macs with an Apple chip and for Intel Macs, each complete
with the Vocal Remover.

BUILD TOOL (not shipped in a usable form; the person needs none of this). What it does, in order:
  1. checks that the version numbers agree: VERSION in the server and the installer, local VERSION in the helper,
     the first line of README.txt and of mac/README.txt, the first line of NOTICE.txt; and the Vocal Remover's
     version in main.go and its README;
  2. parses every Python file and loads the Lua files (a syntax error stops the build);
  3. writes the Mac guide from the Windows one (make_mac_guide.py), fills the measured numbers into the guides and
     the Vocal Remover's README (from build/eval.json) and turns both guides into PDFs (build_guides.py), unless
     --no-pdf;
  4. builds the Vocal Remover for Windows (amd64), Apple-chip Macs (arm64) and Intel Macs (amd64) with Go, running its tests first, unless
     --no-go (then the binaries in build/out from an earlier build are used);
  5. stamps the helper's checksum into the server and the installer (stamp_helper_hash.py), and takes the stamp
     out again at the end (unless --keep-stamped);
  6. writes  dist/RapReaper-<version>-Windows.zip ,  -Mac.zip  and  -Mac-Intel.zip , each with a top-level
     folder RapReaper/, a MANIFEST.sha256 of every file in it (the updater checks a zip against it), the person's
     folders present but empty (a .keep file), the double-click files of that system only, and the Vocal Remover
     with that system's runtime; then  dist/SHA256SUMS.txt ;
  7. checks that nothing personal is in a zip and that each zip stays under the delivery size limit.

    python3 build/build_release.py              # everything
    python3 build/build_release.py --check      # only the checks
    python3 build/build_release.py --no-go --no-pdf
"""
import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
DIST = os.path.join(ROOT, "dist")
OUT = os.path.join(ROOT, "build", "out")
RUNTIME = os.path.join(ROOT, "build", "vr_runtime")
SIZE_LIMIT = 29 * 1024 * 1024          # one delivered file must stay under this (download size limit)

COMMON_TOP = ["LICENSE.txt", "SECURITY.txt", "NOTICE.txt", "DEVELOPMENT.md", "BUILDING.md", "rapreaper_install.py"]
WINDOWS_TOP = ["README.txt", "AUDIO INTERFACE SETUP.txt", "Install RapReaper.bat", "Check RapReaper.bat", "Update RapReaper.bat", "Make Full Packages.bat",
               "Uninstall RapReaper.bat", "Allow Vocal Remover.bat", "Allow ffmpeg.bat", "Allow Transcriber.bat",
               "Get ffmpeg and Transcriber.bat", "get_tools_b5130.ps1", "Get Vocal Remover Model.bat", "get_model_kimft2.ps1",
               "Get Local AI.bat", "get_local_ai.ps1", "Start Local AI.bat"]
MAC_TOP = ["Install RapReaper.command", "Check RapReaper.command", "Update RapReaper.command", "Make Full Packages.command", "Uninstall RapReaper.command",
           "Allow Vocal Remover.command", "Allow ffmpeg.command", "Allow Transcriber.command", "Get ffmpeg and Transcriber.command",
           "Get Vocal Remover Model.command", "Get Local AI.command", "Start Local AI.command"]
MAC_RENAMED = {"mac/README.txt": "README.txt", "mac/AUDIO INTERFACE SETUP.txt": "AUDIO INTERFACE SETUP.txt"}
SHIP_DIRS = ["kit", "sim", "tests", "docs", "LocalAI"]
BUILD_FILES = ["build_release.py", "stamp_helper_hash.py", "build_guides.py", "make_mac_guide.py", "build_roformer_onnx.py",
               "onnxwriter.py", "torch_ckpt.py", "eval_separation.py", "roformer_numpy.py", "eval.json", "fetch_runtime.py"]
EMPTY_DIRS = ["kit/state", "kit/captures", "kit/exports", "kit/logs", "kit/references", "kit/imports", "kit/chains/user", "kit/models",
              "kit/state/songs", "kit/state/edits", "kit/state/transcripts", "kit/reaper-daemon/logs",
              "VocalRemover/models", "VocalRemover/Output", "LocalAI/runtime", "LocalAI/models"]
EXCLUDE_DIR_NAMES = {"__pycache__", ".git", "dist", ".pytest_cache", "inbox", "outbox", "processing", "archive", "failed", "venv"}
EXCLUDE_FILE_RE = re.compile(r"(\.pyc$|\.wav$|\.mp3$|\.m4a$|\.flac$|\.onnx(\.part\d*)?$|\.npy$|^ggml.*\.bin$|\.log$|\.tmp$|\.broken-|\.gguf$|\.part$|"
                             r"\.rapreaper-backup-|^\.DS_Store$|^Thumbs\.db$|^settings\.json$|^private_names\.txt$|^install\.json$)", re.I)
KEEP_FILES = {"tests/data/real_speech.wav"}      # the one audio file that is part of the tests


def fail(msg):
    print("BUILD STOPPED: " + msg)
    sys.exit(1)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def version_of(path, pattern):
    with open(path, encoding="utf-8", errors="replace") as f:
        m = re.search(pattern, f.read(), re.M)
    return m.group(1) if m else None


def check():
    versions = {
        "server": version_of(os.path.join(ROOT, "kit", "rapreaper_server.py"), r'^VERSION = "([^"]+)"'),
        "installer": version_of(os.path.join(ROOT, "rapreaper_install.py"), r'^VERSION = "([^"]+)"'),
        "helper": version_of(os.path.join(ROOT, "kit", "rapreaper_helper.lua"), r'^local VERSION = "([^"]+)"'),
        "README.txt": version_of(os.path.join(ROOT, "README.txt"), r'^RapReaper ([0-9.]+) - '),
        "mac/README.txt": version_of(os.path.join(ROOT, "mac", "README.txt"), r'^RapReaper ([0-9.]+) - '),
        "NOTICE.txt": version_of(os.path.join(ROOT, "NOTICE.txt"), r'^RapReaper ([0-9.]+)'),
    }
    if len(set(versions.values())) != 1 or None in versions.values():
        fail("version numbers differ: %s" % versions)
    version = versions["server"]
    print("version %s (server, installer, helper, both READMEs and NOTICE agree)" % version)
    vr_version = version_of(os.path.join(ROOT, "VocalRemover", "source", "main.go"), r'^const version = "([^"]+)"')
    vr_readme = version_of(os.path.join(ROOT, "VocalRemover", "README.txt"), r'^VOCAL REMOVER ([0-9.]+)')
    if not vr_version or vr_version != vr_readme:
        fail("Vocal Remover version differs: main.go %s, README %s" % (vr_version, vr_readme))
    print("Vocal Remover %s" % vr_version)
    n = 0
    for path in python_files():
        try:
            with open(path, "rb") as f:
                ast.parse(f.read(), filename=path)
        except SyntaxError as e:
            fail("syntax error in %s: %s" % (os.path.relpath(path, ROOT), e))
        n += 1
    print("%d Python files compile" % n)
    lua = shutil.which("lua5.4") or shutil.which("lua")
    if lua:
        for rel in ("kit/rapreaper_helper.lua", "kit/reaper-daemon/bridge/bridge.lua", "sim/fake_reaper.lua"):
            p = os.path.join(ROOT, rel)
            if os.path.isfile(p):
                r = subprocess.run([lua, "-e", "assert(loadfile(%r))" % p], capture_output=True, text=True)
                if r.returncode != 0:
                    fail("Lua cannot load %s: %s" % (rel, r.stderr.strip()))
        print("the Lua files load")
    for rel in ("kit/state/settings.json", "kit/state/private_names.txt", "kit/state/journal.json", "kit/state/mix_log.jsonl"):
        if os.path.exists(os.path.join(ROOT, rel)):
            print("  (note: %s exists in the working copy; it will NOT be shipped)" % rel)
    return version, vr_version


def python_files():
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDE_DIR_NAMES and d != "vendor")
        for fn in filenames:
            if fn.endswith(".py"):
                yield os.path.join(dirpath, fn)


# ---- the Vocal Remover ----------------------------------------------------------------------------------------

def build_vocalremover(no_go):
    src = os.path.join(ROOT, "VocalRemover", "source")
    os.makedirs(OUT, exist_ok=True)
    bins = {"windows": os.path.join(OUT, "VocalRemover.exe"),
            "mac": os.path.join(OUT, "VocalRemover-darwin-arm64"),
            "mac-intel": os.path.join(OUT, "VocalRemover-darwin-amd64")}
    if no_go:
        if not all(os.path.isfile(b) for b in bins.values()):
            fail("--no-go, but build/out has no Vocal Remover binaries")
        print("using the Vocal Remover binaries in build/out")
        return bins
    if not shutil.which("go"):
        fail("Go is not installed (needed to build the Vocal Remover); or pass --no-go with binaries in build/out")
    env = dict(os.environ, CGO_ENABLED="0", GOFLAGS="-mod=vendor", GOPROXY="off")
    r = subprocess.run(["go", "test", "./internal/dsp/", "./internal/onnxconv/"], cwd=src, env=env, capture_output=True, text=True)
    if r.returncode != 0:
        fail("Vocal Remover tests failed:\n" + r.stdout + r.stderr)
    print("Vocal Remover: Go tests pass")
    for goos, goarch, out in (("windows", "amd64", bins["windows"]), ("darwin", "arm64", bins["mac"]),
                              ("darwin", "amd64", bins["mac-intel"])):
        r = subprocess.run(["go", "build", "-trimpath", "-buildvcs=false", "-ldflags", "-s -w", "-o", out, "."],
                           cwd=src, env=dict(env, GOOS=goos, GOARCH=goarch), capture_output=True, text=True)
        if r.returncode != 0:
            fail("go build %s/%s failed:\n%s" % (goos, goarch, r.stderr))
        print("built %s (%.1f MB)" % (os.path.relpath(out, ROOT), os.path.getsize(out) / 1048576.0))
    return bins


# The Mac runtime libraries: ONNX Runtime 1.30.0 for Apple chips; 1.23.2, the last version Microsoft built for Intel Macs.
MAC_RUNTIME = {"mac": ("libonnxruntime-osx-arm64-1.30.0.dylib", "arm64"),
               "mac-intel": ("libonnxruntime-osx-x86_64-1.23.2.dylib", "x86_64")}


def vocalremover_files(system, bins):
    """[(archive path under RapReaper/, source path or bytes, mode)] for the Vocal Remover of one system."""
    vr = os.path.join(ROOT, "VocalRemover")
    files = []

    def add(arc, src, mode=0o644):
        files.append(("VocalRemover/" + arc, src, mode))

    add("README.txt", fill_vr_readme())
    for dirpath, dirnames, filenames in os.walk(os.path.join(vr, "licenses")):
        for fn in sorted(filenames):
            add("licenses/" + fn, os.path.join(dirpath, fn))
    for dirpath, dirnames, filenames in os.walk(os.path.join(vr, "source")):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
        rel_dir = os.path.relpath(dirpath, os.path.join(vr, "source")).replace(os.sep, "/")
        for fn in sorted(filenames):
            if fn.endswith((".pyc", ".onnx", ".npy")) or fn == ".DS_Store":
                continue
            rel = fn if rel_dir == "." else rel_dir + "/" + fn
            add("source/" + rel, os.path.join(dirpath, fn))
    if system == "windows":
        add("VocalRemover.exe", bins["windows"], 0o755)
        add("onnxruntime.dll", os.path.join(RUNTIME, "onnxruntime-win-1.30.0.dll"))
        for fn in ("msvcp140.dll", "msvcp140_1.dll", "vcruntime140.dll", "vcruntime140_1.dll"):
            add(fn, os.path.join(RUNTIME, "msvc", fn))
        add("gpu/onnxruntime.dll", os.path.join(RUNTIME, "onnxruntime-directml-1.24.4.dll"))
        add("gpu/DirectML.dll", os.path.join(RUNTIME, "DirectML-1.15.4.dll"))
        for fn in ("msvcp140.dll", "msvcp140_1.dll", "vcruntime140.dll", "vcruntime140_1.dll"):
            add("gpu/" + fn, os.path.join(RUNTIME, "msvc", fn))
        add("gpu/README.txt", ("The graphics-processor runtime, used only when the graphics processor is switched on:\n"
                               "onnxruntime.dll is Microsoft's ONNX Runtime 1.24.4 built with DirectML (NuGet package\n"
                               "Microsoft.ML.OnnxRuntime.DirectML) and DirectML.dll is Microsoft's DirectML 1.15.4 (NuGet\n"
                               "package Microsoft.AI.DirectML), both unchanged. Their licences are in ..\\licenses.\n").encode())
    else:
        lib_name, chip = MAC_RUNTIME[system]
        add("VocalRemover", bins[system], 0o755)
        add("libonnxruntime.dylib", os.path.join(RUNTIME, lib_name), 0o755)
        cmd = open(os.path.join(vr, "Vocal Remover.command"), encoding="utf-8").read()
        cmd = cmd.replace("__PROGRAM_SHA256__", sha256_file(bins[system])).replace(
            "__LIBRARY_SHA256__", sha256_file(os.path.join(RUNTIME, lib_name))).replace("__PACKAGE_CHIP__", chip)
        add("Vocal Remover.command", cmd.encode(), 0o755)
    for arc, src, _mode in files:
        if isinstance(src, str) and not os.path.isfile(src):
            fail("missing Vocal Remover file: %s" % src)
    return files


# ---- the measured numbers -------------------------------------------------------------------------------------

def load_eval():
    p = os.path.join(ROOT, "build", "eval.json")
    if not os.path.isfile(p):
        return {}
    with open(p, encoding="utf-8") as f:
        return json.load(f)


ROWS = [("mel", "Mel-Roformer (recommended)", "MelRoformer-KimFT2-int8.onnx"),
        ("ensemble", "Two networks (Mel + BS)", "ensemble(MelRoformer-KimFT2-int8.onnx+BS-Roformer-1297-int8.onnx)"),
        ("bs", "BS-Roformer (version 1.3's default)", "BS-Roformer-1297-int8.onnx")]
TRACKS = [("Little Chicago's Finest - My Own", "rap"), ("BKS - Bulldozer", "electronic"), ("Angels In Amplifiers - I'm Alright", "rock")]


def eval_cells(ev, key):
    cells, times = [], []
    for track, _kind in TRACKS:
        r = (ev.get(track) or {}).get(key)
        if r:
            cells.append("%.1f / %.1f" % (r["instrumental_accuracy_db"], r["vocal_accuracy_db"]))
            times.append(r["took_s"])
        else:
            cells.append("-")
    t = ("%.0f s" % (sum(times) / len(times))) if times else "-"
    return cells, t


def readme_table(ev):
    w = max(len(label) for _k, label, _key in ROWS) + 2
    lines = ["   %-*s %-16s %-16s %-16s %s" % (w, "mode", "rap track", "electronic", "rock", "time"),
             "   %-*s %-16s %-16s %-16s %s" % (w, "", "instr. / vocal", "instr. / vocal", "instr. / vocal", "(60 s of song)")]
    for _k, label, key in ROWS:
        cells, t = eval_cells(ev, key)
        lines.append("   %-*s %-16s %-16s %-16s %s" % (w, label, cells[0], cells[1], cells[2], t))
    lines.append("")
    lines.append("   (instrumental / vocal accuracy in dB; the test songs: Little Chicago's Finest - My Own,")
    lines.append("   BKS - Bulldozer, Angels In Amplifiers - I'm Alright, from MUSDB18-HQ)")
    return "\n".join(lines)


def guide_table(ev):
    out = []
    for _k, label, key in ROWS:
        cells, t = eval_cells(ev, key)
        out.append("| %s | %s | %s | %s | %s |" % (label, cells[0], cells[1], cells[2], t))
    return "\n".join(out)


def fill_vr_readme():
    text = open(os.path.join(ROOT, "VocalRemover", "README.txt"), encoding="utf-8").read()
    return text.replace("__TABLE__", readme_table(load_eval())).encode("utf-8")


# ---- the zips -------------------------------------------------------------------------------------------------

def tree_files(system):
    """[(archive path under RapReaper/, source path or bytes, mode)] of the RapReaper tree for one system."""
    files = []

    def add(arc, src, mode=0o644):
        files.append((arc, src, mode))

    for name in COMMON_TOP:
        add(name, os.path.join(ROOT, name))
    if system == "windows":
        for name in WINDOWS_TOP:
            add(name, os.path.join(ROOT, name))
        add("RapReaper Guide (Windows).pdf", os.path.join(ROOT, "docs", "RapReaper Guide (Windows).pdf"))
    else:
        for name in MAC_TOP:
            add(name, os.path.join(ROOT, name), 0o755)
        for src, arc in MAC_RENAMED.items():
            add(arc, os.path.join(ROOT, src))
        add("RapReaper Guide (Mac).pdf", os.path.join(ROOT, "docs", "RapReaper Guide (Mac).pdf"))
    for d in SHIP_DIRS:
        base = os.path.join(ROOT, d)
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(x for x in dirnames if x not in EXCLUDE_DIR_NAMES)
            rel_dir = os.path.relpath(dirpath, ROOT).replace(os.sep, "/")
            if any(rel_dir == e or rel_dir.startswith(e + "/") for e in EMPTY_DIRS):
                continue
            for fn in sorted(filenames):
                rel = rel_dir + "/" + fn
                if rel in KEEP_FILES:
                    add(rel, os.path.join(dirpath, fn))
                    continue
                if EXCLUDE_FILE_RE.search(fn) or fn.endswith(".pdf"):
                    continue
                add(rel, os.path.join(dirpath, fn))
    for fn in BUILD_FILES:
        p = os.path.join(ROOT, "build", fn)
        if os.path.isfile(p):
            add("build/" + fn, p)
    for arc, src, _m in files:
        if isinstance(src, str) and not os.path.isfile(src):
            fail("missing file: %s" % os.path.relpath(src, ROOT))
    return files


def write_zip(out_path, files):
    """files: [(arc, source path or bytes, mode)]; writes RapReaper/<arc> plus MANIFEST.sha256 and the .keep files."""
    if os.path.exists(out_path):
        os.remove(out_path)
    manifest = []
    stamp = time.localtime()
    date = (stamp.tm_year, stamp.tm_mon, stamp.tm_mday, stamp.tm_hour, stamp.tm_min, stamp.tm_sec)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        def put(arc, data, mode):
            info = zipfile.ZipInfo("RapReaper/" + arc, date_time=date)
            info.external_attr = (0o100000 | mode) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)
            manifest.append((hashlib.sha256(data).hexdigest(), arc))

        for arc, src, mode in sorted(files, key=lambda t: t[0]):
            data = src if isinstance(src, bytes) else open(src, "rb").read()
            put(arc, data, mode)
        for d in EMPTY_DIRS:
            put(d + "/.keep", b"This folder is RapReaper's; it fills as you use it.\n", 0o644)
        text = "# RapReaper %s: SHA-256 of every file in this package (the updater checks a new zip against its own list)\n" % VERSION
        text += "".join("%s  %s\n" % (digest, arc) for digest, arc in sorted(manifest, key=lambda t: t[1]))
        info = zipfile.ZipInfo("RapReaper/MANIFEST.sha256", date_time=date)
        info.external_attr = (0o100000 | 0o644) << 16
        z.writestr(info, text)
    return len(manifest) + 1


def check_zip(path):
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        bad = [n for n in names if re.search(r"settings\.json$|private_names|mix_log|journal\.json|sent_to_claude|sent_to_ai|\.wav$|\.onnx$|"
                                             r"\.gguf$|install\.json$|local_ai/|ai_app_settings", n)
               and not n.endswith("tests/data/real_speech.wav")]
        if bad:
            fail("personal or working files in %s: %s" % (os.path.basename(path), bad[:5]))
        # contents too: the build machine's folders, and the names of the people RapReaper was made with, never ship
        # (test data uses invented names); the words come from the environment so they are not in the package either
        words = [w.strip().lower().encode() for w in (os.environ.get("RAPREAPER_PRIVATE_WORDS") or "").split(",") if len(w.strip()) >= 4]
        # (the folder this build runs in, and the builder's home: taken from where the build runs, not written here)
        words += [os.path.dirname(ROOT).lower().encode() + b"/", os.path.expanduser("~").lower().encode() + b"/"]
        leaks = []
        for n in names:
            if n.endswith("/") or re.search(r"\.(exe|dll|dylib|so|pdf|wav|ogg|png)$", n, re.I):
                continue
            data = z.read(n)
            if data[:4] in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\x7fELF") or data[:2] == b"MZ":
                continue                  # (a compiled program: the text of its libraries is not ours)
            data = data.lower()
            leaks += ["%s (%s)" % (n, w.decode()) for w in words if w and w in data]
        if leaks:
            fail("personal or build-machine text in %s: %s" % (os.path.basename(path), leaks[:5]))
        tops = {n.split("/")[0] for n in names}
        if tops != {"RapReaper"}:
            fail("the zip must hold one RapReaper folder, has %s" % tops)
        manifest = z.read("RapReaper/MANIFEST.sha256").decode().splitlines()
        listed = {line.split("  ", 1)[1] for line in manifest if line and not line.startswith("#")}
        actual = {n[len("RapReaper/"):] for n in names if n != "RapReaper/MANIFEST.sha256" and not n.endswith("/")}
        if listed != actual:
            fail("manifest and zip differ: %s" % sorted(listed ^ actual)[:5])
    size = os.path.getsize(path)
    print("  %s: %d files, %.1f MB%s" % (os.path.basename(path), len(names), size / 1048576.0,
                                         "" if size <= SIZE_LIMIT else "  -- OVER THE %d MiB DELIVERY LIMIT" % (SIZE_LIMIT // 1048576)))
    return size <= SIZE_LIMIT


VERSION = None


def main(argv):
    global VERSION
    check_only = "--check" in argv
    no_go = "--no-go" in argv
    no_pdf = "--no-pdf" in argv
    allow_large = "--allow-large" in argv
    VERSION, vr_version = check()
    if check_only:
        return 0
    # the guides: the Mac one from the Windows one, the numbers filled in, both as PDFs
    r = subprocess.run([sys.executable, os.path.join(ROOT, "build", "make_mac_guide.py")], capture_output=True, text=True)
    if r.returncode != 0:
        fail("make_mac_guide.py failed:\n" + r.stdout + r.stderr)
    if not no_pdf:
        r = subprocess.run([sys.executable, os.path.join(ROOT, "build", "build_guides.py")], capture_output=True, text=True)
        if r.returncode != 0:
            fail("build_guides.py failed:\n" + r.stdout + r.stderr)
        print(r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "guides built")
    bins = build_vocalremover(no_go)
    stamp = os.path.join(ROOT, "build", "stamp_helper_hash.py")
    r = subprocess.run([sys.executable, stamp], capture_output=True, text=True)
    if r.returncode != 0:
        fail("stamping the helper's checksum failed:\n" + r.stdout + r.stderr)
    try:
        os.makedirs(DIST, exist_ok=True)
        results = {}
        for system, label in (("windows", "Windows"), ("mac", "Mac"), ("mac-intel", "Mac-Intel")):
            out = os.path.join(DIST, "RapReaper-%s-%s.zip" % (VERSION, label))
            files = tree_files(system) + vocalremover_files(system, bins)
            write_zip(out, files)
            results[out] = check_zip(out)
        with open(os.path.join(DIST, "SHA256SUMS.txt"), "w", encoding="utf-8") as f:
            f.write("# RapReaper %s (Vocal Remover %s): SHA-256 of the delivered files\n" % (VERSION, vr_version))
            for out in sorted(results):
                f.write("%s  %s\n" % (sha256_file(out), os.path.basename(out)))
        print("wrote dist/SHA256SUMS.txt")
        if not all(results.values()) and not allow_large:
            fail("a zip is over the delivery size limit (pass --allow-large to keep it anyway)")
    finally:
        if "--keep-stamped" not in argv:
            subprocess.run([sys.executable, stamp, "--unstamp"], capture_output=True, text=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
