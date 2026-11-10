"""RapReaper installer (Windows and macOS).

Run it by double-clicking "Install RapReaper.bat" (Windows) or "Install RapReaper.command" (Mac), or by hand:
    py rapreaper_install.py            (Windows)        python3 rapreaper_install.py        (Mac)
It is safe to run again at any time; it only ever touches these things:

  1. Python packages: installs NumPy and SciPy (the measuring maths), lameenc (MP3 copies of an export) and ONNX
     Runtime (learning a song's effects) with pip. On a Mac they go into RapReaper's own small Python environment
     (kit/venv), so the Python on the Mac itself is left exactly as it is.
  2. REAPER: adds two marked blocks to REAPER's start-up script (Scripts/__startup.lua in its settings folder) so the
     bridge script in this folder starts with REAPER (and REAPER notes its start time in
     this folder), writes the bridge's permission file inside this folder, and copies
     RapReaper's one small JS effect (kit/effects/RapReaper/ducks.jsfx, the 'Ducks' plug-in)
     into REAPER's Effects folder. A copy of __startup.lua is kept before any change. If REAPER is closed and its preference
     'Set media items offline when application is not active' is on (RapReaper cannot
     measure with it on), the installer switches it off in reaper.ini, keeping a copy.
  3. Claude Desktop: adds one "rapreaper" entry to claude_desktop_config.json so Claude can
     start the RapReaper tool server. A copy of the file is kept before any change.

It does not need administrator rights, does not open any network port, and sends
nothing anywhere (pip downloads the two packages from the official Python index).

Options:
  --check        change nothing; report what is and is not set up
  --uninstall    remove the REAPER block and the Claude entry (this folder is left alone)
  --no-pip       do not install or upgrade Python packages
  --reaper-resource-dir PATH   for a portable REAPER install (the folder with reaper.ini);
                 it is remembered, so "Check" and "Uninstall" later look in the same place
  --update [ZIP] replace RapReaper's program files with those of a newer RapReaper zip (the RapReaper-<version>-Windows.zip,
                 -Mac.zip or -Mac-Intel.zip with the highest version in Downloads, when no file is given), keeping your chains, log,
                 settings, references, exports, snapshots and model files; then run the installer of the new version
  --allow-same-or-older        with --update: also accept a zip whose version is not newer than this one
  --allow-program KIND PATH    allow RapReaper to start one program (ffmpeg or transcriber), by path and checksum
  --allow-vocal-remover PATH   allow RapReaper to start this one Vocal Remover program (the installer allows the copy it
                               ships by itself; this is for another copy)
  --gpu on|off                 ask RapReaper's own separation to use the graphics processor (the Vocal Remover program
                               decides by its own test, from its window or with --gpu-test)
"""
from __future__ import annotations

import argparse
import hashlib
import contextlib
import datetime
import glob
import importlib.util
import io
import json
import os
import platform
import posixpath
import re
import shutil
import stat
import struct
import subprocess
import sys
import threading
import time
import unicodedata
import zipfile
import zlib

VERSION = "1.4.7"
HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.join(HERE, "kit")
BRIDGE_ROOT = os.path.join(KIT, "reaper-daemon")
SERVER = os.path.join(KIT, "rapreaper_server.py")
MIN_PY = (3, 12)          # what today's NumPy and SciPy need; RapReaper was tested on 3.13 and 3.14
INSTALL_NOTES = os.path.join(KIT, "state", "install.json")     # what the installer remembers between runs
MANIFEST = os.path.join(HERE, "MANIFEST.sha256")               # every shipped file and its checksum (written at build time)
STAMP = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")

sys.path.insert(0, KIT)
try:
    import rapreaper_platform as PF
except ImportError:                                  # a damaged unzip: say so in plain words (check_location would, but it is not reached)
    print("RapReaper's files are incomplete (kit/rapreaper_platform.py is missing). Unzip the whole RapReaper zip again "
          "and run the installer from the unzipped folder.")
    sys.exit(1)

PY_DOWNLOADS = "https://www.python.org/downloads/macos/" if PF.IS_MAC else "https://www.python.org/downloads/windows/"
PY_WHICH = ("the 'macOS 64-bit universal2 installer'" if PF.IS_MAC
            else "the 'Python install manager' or the 'Windows installer (64-bit)'")
PY_CMD = "py" if PF.IS_WINDOWS else "python3"

problems = []          # things the person still has to do
notes = []


def say(text=""):
    print(PF.wording(text))
    sys.stdout.flush()


def step(n, title):
    say()
    say("[%d/7] %s" % (n, title))


def ok(text):
    say("      OK   " + text)


def todo(text):
    say("      TO DO  " + text)
    problems.append(PF.wording(text))


def info(text):
    say("           " + text)


def is_windows():
    return PF.IS_WINDOWS


def backup(path):
    """Keep a dated copy of a file we are about to change. Returns the copy's path."""
    dest = "%s.rapreaper-backup-%s" % (path, STAMP)
    n = 1
    while os.path.exists(dest):
        n += 1
        dest = "%s.rapreaper-backup-%s-%d" % (path, STAMP, n)
    shutil.copy2(path, dest)
    return dest


def write_atomic(path, data):
    """Write a file in one piece (text as UTF-8, or bytes). On any failure nothing is left behind
    and the original file is untouched; the OSError is passed on. A file that is a link (a settings file kept in a
    dotfiles folder, say) stays a link: the file it points at is the one written. The file keeps its permissions
    (a settings file only its owner may read stays that way), and the new content is on the disk before it takes
    the old one's place."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    path = os.path.realpath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".rapreaper-tmp"
    try:
        mode = None
        try:
            mode = stat.S_IMODE(os.stat(path).st_mode)
        except OSError:
            pass
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        if mode is not None:
            try:
                os.chmod(tmp, mode)
            except OSError:
                pass
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


# --------------------------------------------------------------------------------------------
# 1. Python
# --------------------------------------------------------------------------------------------

def _apple_python(exe):
    low = exe.lower()
    return low.startswith("/usr/bin/python") or "/commandlinetools/" in low or "/xcode.app/" in low or low.startswith("/system/")


def check_python():
    step(1, "Python")
    exe = sys.executable or ""
    ver = "%d.%d.%d" % sys.version_info[:3]
    if PF.IS_MAC and _apple_python(exe) and sys.version_info < MIN_PY:
        todo("This is the Python that comes with macOS's developer tools (%s), which is too old for RapReaper's measuring "
             "packages. Install the current Python from %s (%s), then double-click the installer again: it finds the new "
             "Python by itself." % (ver, PY_DOWNLOADS, PY_WHICH))
        return False
    if sys.version_info < MIN_PY:
        todo("This is Python %s. RapReaper needs Python %d.%d or newer. Install the current Python from %s (%s) and run "
             "this installer again." % (ver, MIN_PY[0], MIN_PY[1], PY_DOWNLOADS, PY_WHICH))
        return False
    if sys.maxsize <= 2 ** 32:
        todo("This is a 32-bit Python. The measuring packages RapReaper needs (NumPy and SciPy) are only made for 64-bit "
             "Python. Install the 64-bit Python from %s and run this installer again." % PY_DOWNLOADS)
        return False
    low = exe.lower()
    if PF.IS_WINDOWS and "windowsapps" in low and "pythonsoftwarefoundation.python." in low:
        todo("This is the old Microsoft Store edition of Python, which keeps its own private copy of some folders, so "
             "REAPER and Claude would not see what it writes. Install Python from https://www.python.org/downloads/windows/ "
             "instead and run this installer again.")
        return False
    ok("Python %s  (%s)" % (ver, exe))
    return True


# --------------------------------------------------------------------------------------------
# 2. where this folder is
# --------------------------------------------------------------------------------------------

def _under(path, parent):
    if not parent:
        return False
    try:
        a = os.path.normcase(os.path.abspath(path))
        b = os.path.normcase(os.path.abspath(parent))
        return os.path.commonpath([a, b]) == b
    except ValueError:
        return False


def best_home():
    """Where the RapReaper folder is best kept: inside the person's own user folder, which other accounts cannot write
    to and which, on a Mac, no program needs special permission to use. On Windows, folders made directly under C:
    can be changed by every account on the PC, so C:\\RapReaper is only the fallback for user names REAPER's script
    engine cannot open."""
    if PF.IS_WINDOWS:
        prof = os.environ.get("USERPROFILE") or ""           # (always set on Windows; the tests leave it out on purpose)
        if prof and os.path.isabs(prof) and all(32 <= ord(ch) < 127 for ch in prof) and ";" not in prof:
            return os.path.join(prof, "RapReaper")
        return "C:\\RapReaper"
    return os.path.join(PF.home_dir() or os.path.expanduser("~"), "RapReaper")


REQUIRED_FILES = [os.path.join("kit", "rapreaper_server.py"), os.path.join("kit", "rapreaper_audio.py"),
                  os.path.join("kit", "rapreaper_music.py"), os.path.join("kit", "rapreaper_platform.py"),
                  os.path.join("kit", "rapreaper_programs.py"), os.path.join("kit", "rapreaper_effects.py"),
                  os.path.join("kit", "rapreaper_separate.py"), os.path.join("kit", "PLAYBOOK.md"),
                  os.path.join("kit", "rapreaper_helper.lua"), os.path.join("kit", "reaper-daemon", "reaperd.py"),
                  os.path.join("kit", "reaper-daemon", "bridge", "reaper_agent_bridge.lua"),
                  os.path.join("kit", "reaper-daemon", "bridge", "json.lua"),
                  os.path.join("kit", "reaper-daemon", "setup", "install.py"),
                  os.path.join("kit", "effects", "RapReaper", "ducks.jsfx")]


def check_location():
    step(2, "Where the RapReaper folder is")
    home = best_home()
    missing = [rel for rel in REQUIRED_FILES if not os.path.isfile(os.path.join(HERE, rel))]
    if missing:
        todo("Files are missing from this folder (%s). Unzip the whole RapReaper zip again and run the installer from "
             "the unzipped folder." % ", ".join(missing))
        return False
    bad = None
    prof = PF.home_dir() or ""
    if _unsafe_for_lua(HERE):
        bad = ("its path contains a line break or another invisible control character, which cannot be written into "
               "REAPER's start-up script")
    elif PF.IS_WINDOWS and any(ord(ch) > 127 for ch in HERE):
        bad = "its path contains accented or non-English letters, which REAPER's script engine cannot open"
    elif PF.IS_WINDOWS and HERE.startswith("\\\\"):
        bad = "it is on a network share"
    elif PF.IS_WINDOWS and (_under(HERE, os.environ.get("APPDATA")) or _under(HERE, os.environ.get("LOCALAPPDATA"))):
        bad = "it is inside AppData, where Windows gives the Claude app a private copy of files"
    elif PF.IS_WINDOWS and (any(_under(HERE, os.environ.get(k)) for k in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"))
                            or "onedrive" in HERE.lower()):
        bad = "it is inside OneDrive, which moves and locks files while they sync"
    elif PF.IS_WINDOWS and (_under(HERE, os.environ.get("TEMP")) or _under(HERE, os.environ.get("TMP"))):
        bad = "it is in a temporary folder that Windows empties"
    elif PF.IS_WINDOWS and (".zip" in HERE.lower()):
        bad = "it is still inside the zip file"
    elif PF.IS_MAC and prof and _under(HERE, os.path.join(prof, "Library")):
        if _under(HERE, os.path.join(prof, "Library", "Mobile Documents")):
            bad = "it is inside iCloud Drive, which moves files to and from the cloud while they are in use"
        elif _under(HERE, os.path.join(prof, "Library", "CloudStorage")):
            bad = "it is inside a cloud folder (Dropbox, Google Drive or OneDrive), which syncs and locks files while they are in use"
        else:
            bad = "it is inside the hidden Library folder, where the Claude app and REAPER are not meant to find it"
    elif PF.IS_MAC and prof and any(_under(HERE, os.path.join(prof, d)) for d in ("Downloads", "Desktop", "Documents")):
        bad = ("it is in your %s folder. macOS protects that folder: Terminal, REAPER and Claude would each have to be given "
               "permission to use it, and when one of them is refused, RapReaper fails without a clear reason"
               % next(d for d in ("Downloads", "Desktop", "Documents") if _under(HERE, os.path.join(prof, d))))
    elif PF.IS_MAC and (_under(HERE, os.environ.get("TMPDIR")) or HERE.startswith("/tmp") or HERE.startswith("/private/tmp")):
        bad = "it is in a temporary folder that macOS empties"
    elif ";" in HERE:
        bad = "its path contains a semicolon (;), which REAPER's script engine reads as the end of a folder name"
    if bad:
        todo("The RapReaper folder is at  %s  and that will not work: %s. Move the whole folder to  %s  "
             "and run the installer again from there." % (HERE, bad, home))
        return False
    ok(HERE)
    if PF.IS_WINDOWS and "downloads" in HERE.lower().split(os.sep):
        info("Note: this is your Downloads folder. RapReaper must stay where it is installed, so do not tidy it away;")
        info("      %s is a better home (move it there and run this installer again)." % home)
    elif prof and not _under(HERE, prof):
        info("Note: this folder is outside your own user folder. That is fine on a computer that only you use.")
        info("      If other people log in to this computer with their own account, they could change the files")
        if _under(home, prof):
            info("      in here; in that case move the whole folder to  %s  and run this installer again." % home)
        else:
            info("      in here; in that case ask for help making this folder private (Properties > Security).")
    return True


# --------------------------------------------------------------------------------------------
# 3. NumPy and SciPy
# --------------------------------------------------------------------------------------------

VENV = os.path.join(KIT, "venv")
PACKAGE_PINS = ["numpy>=1.26,<3", "scipy>=1.11,<2"]
LAMEENC_PIN = "lameenc>=1.6,<2"
ONNX_PIN = "onnxruntime>=1.17,<2"


def _venv_python():
    return os.path.join(VENV, "Scripts", "python.exe") if PF.IS_WINDOWS else os.path.join(VENV, "bin", "python3")


def _runs(python):
    """Can this Python be started? (A venv whose base Python was removed cannot.)"""
    try:
        r = subprocess.run([python, "-c", "import sys; print(sys.version_info[0])"], capture_output=True, text=True, timeout=60)
        return r.returncode == 0 and r.stdout.strip() == "3"
    except Exception:
        return False


def server_python():
    """The Python that runs the tool server (the one Claude is pointed at). On Windows the Python this installer runs
    with; on a Mac RapReaper's own environment inside the kit folder, once it exists."""
    if PF.IS_WINDOWS:
        return sys.executable
    vp = _venv_python()
    return vp if os.path.isfile(vp) else sys.executable


def _probe_packages(python=None):
    python = python or server_python()
    code = "import numpy, scipy, scipy.signal; print(numpy.__version__ + ' ' + scipy.__version__)"
    try:
        r = subprocess.run([python, "-c", code], capture_output=True, text=True, timeout=120)
    except Exception as e:                                       # pragma: no cover
        return None, str(e)
    if r.returncode == 0:
        return r.stdout.strip().split(), ""
    return None, (r.stderr or r.stdout).strip()[-600:]


def _pip(python, pins, timeout=1800):
    """pip install: ready-built wheels only (nothing is compiled or run from a source package), known-good version
    ranges, never --upgrade (a NumPy the person already has for another program is left as it is when it fits).
    Returns (succeeded, pip's last words)."""
    attempts = [[python, "-m", "pip", "install", "--only-binary=:all:"] + pins]
    if not os.path.isfile(_venv_python()):
        attempts.append([python, "-m", "pip", "install", "--only-binary=:all:", "--user"] + pins)
    last = ""
    for i, cmd in enumerate(attempts):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except Exception as e:                                   # pragma: no cover
            last = str(e)
            continue
        last = (r.stderr or r.stdout or "").strip()[-900:]
        if r.returncode != 0 and "No module named pip" in last and i == 0:
            # this Python came without pip: Python's own ensurepip adds it (from inside Python, no download), then once more
            try:
                subprocess.run([python, "-m", "ensurepip", "--upgrade"], capture_output=True, text=True, timeout=600)
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
                last = (r.stderr or r.stdout or "").strip()[-900:]
            except (OSError, subprocess.SubprocessError) as e:
                last = ("this Python has no pip, and adding it with Python's own 'ensurepip' did not work (%s). Install "
                        "Python again from %s (its installer includes pip) and run this installer again." % (e, PY_DOWNLOADS))
                continue
        if r.returncode == 0:
            return True, last
        if "externally-managed-environment" in last:
            break                                                # Homebrew's Python refuses: the venv is the way (below)
    return False, last


def _make_venv():
    """RapReaper's own Python environment (kit/venv) on a Mac: the packages go in there, so the Mac's own Python stays
    untouched and Homebrew's 'externally managed' rule is respected. Returns its python, or None."""
    vp = _venv_python()
    if os.path.isfile(vp) and _runs(vp):
        return vp
    if os.path.isdir(VENV):
        shutil.rmtree(VENV, ignore_errors=True)                 # a venv whose Python has gone: start afresh
    try:
        r = subprocess.run([sys.executable, "-m", "venv", VENV], capture_output=True, text=True, timeout=600)
    except Exception:                                            # pragma: no cover
        return None
    if r.returncode != 0 or not os.path.isfile(vp):
        return None
    subprocess.run([vp, "-m", "ensurepip", "--upgrade"], capture_output=True, text=True, timeout=600)
    return vp


def check_packages(use_pip, check_only):
    step(3, "Measuring tools (NumPy and SciPy)")
    python = server_python()
    if not PF.IS_WINDOWS and not os.path.isfile(_venv_python()) and not check_only and use_pip:
        info("Making RapReaper's own small Python environment (kit/venv), so nothing on this computer's Python changes ...")
        vp = _make_venv()
        if vp:
            python = vp
            ok("environment made: %s" % VENV)
        else:
            info("(could not make kit/venv; the packages will be installed for this Python instead)")
    have, _why = _probe_packages(python)
    if have:
        ok("NumPy %s, SciPy %s  (%s)" % (have[0], have[1], python))
        return True
    if check_only or not use_pip:
        todo("NumPy and SciPy are not installed for this Python. Run the installer without --check / --no-pip, or run:  "
             "\"%s\" -m pip install numpy scipy" % python)
        return False
    info("Not installed yet. Downloading them with pip (this needs the internet and can take a few minutes) ...")
    good, last = _pip(python, PACKAGE_PINS)
    if not good and "externally-managed-environment" in last and not os.path.isfile(_venv_python()):
        info("This Python does not allow packages to be added to it; making RapReaper's own environment (kit/venv) instead ...")
        vp = _make_venv()
        if vp:
            python = vp
            good, last = _pip(vp, PACKAGE_PINS)
    have, why = _probe_packages(python)
    if have:
        ok("installed NumPy %s, SciPy %s  (%s)" % (have[0], have[1], python))
        return True
    todo("NumPy and SciPy could not be installed. Check the internet connection and run the installer again. "
         "What pip said: %s" % (last or why or "(nothing)"))
    return False


def check_lameenc(use_pip, check_only):
    """lameenc writes MP3 copies of an export (export_mix formats mp3). Everything else works without it: a missing
    lameenc is a note, not a stop. It is a small, ready-built package (no compiler); nothing in it goes online."""
    python = server_python()
    code = "import lameenc; print(getattr(lameenc, '__version__', 'ok'))"

    def probe():
        try:
            r = subprocess.run([python, "-c", code], capture_output=True, text=True, timeout=120)
            return r.stdout.strip() if r.returncode == 0 else None
        except Exception:
            return None

    ver = probe()
    if not ver and use_pip and not check_only:
        info("lameenc (MP3 copies of an export) is not installed yet; downloading it with pip ...")
        _pip(python, [LAMEENC_PIN], timeout=900)
        ver = probe()
    if ver:
        ok("lameenc (export_mix can write MP3 copies)")
    else:
        info("      lameenc is not installed: export_mix writes WAV; for MP3 copies add it later with  \"%s\" -m pip install lameenc  "
             "or allow an ffmpeg with 'Allow ffmpeg.bat'." % python)
    # the optional programs the person may have allowed (ffmpeg, whisper.cpp): only reported, never searched for
    try:
        sys.path.insert(0, KIT)
        import rapreaper_programs as PR
        st = PR.status(KIT)
        for key, row in st.items():
            if row.get("allowed"):
                ok("%s is allowed (%s)" % (key, row["for"]))
            elif row.get("problem"):
                info("      %s: %s" % (key, row["problem"]))
    except Exception:
        pass
    return bool(ver)


def check_onnx(use_pip, check_only):
    """ONNX Runtime runs the vocal-separation network for learn_song_effects. Everything else works without it, so
    a missing ONNX Runtime is reported as a note, not as something that stops the install."""
    python = server_python()
    # (ORT_DISABLE_TELEMETRY first: ONNX Runtime on a Mac otherwise writes a device id and reports usage to Microsoft)
    code = "import os; os.environ['ORT_DISABLE_TELEMETRY'] = '1'; import onnxruntime; print(onnxruntime.__version__)"

    def probe():
        try:
            r = subprocess.run([python, "-c", code], capture_output=True, text=True, timeout=120)
            return r.stdout.strip() if r.returncode == 0 else None
        except Exception:
            return None

    ver = probe()
    if not ver and use_pip and not check_only:
        info("ONNX Runtime (for taking the voice out of a song) is not installed yet; downloading it with pip ...")
        _pip(python, [ONNX_PIN])
        ver = probe()
    if ver:
        ok("ONNX Runtime %s (RapReaper can run the separation networks itself)" % ver)
    else:
        info("      ONNX Runtime is not installed: RapReaper separates songs through the Vocal Remover program only. To add it "
             "later:  \"%s\" -m pip install onnxruntime" % python)
        if PF.IS_MAC and mac_chip() == "x86_64":
            info("      On an Intel Mac, ONNX Runtime comes for Python 3.10 to 3.13 only (its last Intel version is 1.23.2): with a "
                 "newer Python, install Python 3.13 from python.org and run the installer again.")
    # the model files: the Vocal Remover's models folder is the shared place since 1.4 (step 7 joins pieces into it)
    have = [n for n in ("MelRoformer-KimFT2-int8.onnx", "Kim_Vocal_2.onnx")
            if os.path.isfile(os.path.join(HERE, "VocalRemover", "models", n)) or os.path.isfile(os.path.join(KIT, "models", n))]
    if have:
        ok("separation model file(s) found: %s (step 7 checks each against its published checksum)" % ", ".join(have))
    else:
        info("      no separation model file yet (step 7 joins the downloaded pieces)")
    return True


# --------------------------------------------------------------------------------------------
# 4. REAPER
# --------------------------------------------------------------------------------------------

BRIDGE_BEGIN = "-- >>> reaper-agent-bridge for rapreaper (managed) >>>"
BRIDGE_END = "-- <<< reaper-agent-bridge for rapreaper (managed) <<<"


def _vendored_installer():
    """The bridge's own installer module, with one change made from outside it: the marker lines of the
    start-up block it writes name RapReaper, so that a RapMix installed on the same PC (whose bridge block
    carries the bridge's plain markers) is left alone, and the two can run side by side."""
    path = os.path.join(BRIDGE_ROOT, "setup", "install.py")
    spec = importlib.util.spec_from_file_location("reaper_daemon_install", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    plain_begin, plain_end, plain_block = mod.BEGIN, mod.END, mod.block_text

    def block_text(bridge_dir):
        return plain_block(bridge_dir).replace(plain_begin, BRIDGE_BEGIN, 1).replace(plain_end, BRIDGE_END, 1)

    mod.BEGIN, mod.END, mod.block_text = BRIDGE_BEGIN, BRIDGE_END, block_text
    return mod


def _install_notes():
    try:
        with open(INSTALL_NOTES, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _remember(**kw):
    """Keep a few facts between runs (kit/state/install.json): the version that was installed, the Python that runs the
    server, when an update was applied."""
    try:
        data = _install_notes()
        for k, v in kw.items():
            if v is None:
                data.pop(k, None)
            else:
                data[k] = v
        write_atomic(INSTALL_NOTES, json.dumps(data, indent=2) + "\n")
    except (OSError, ValueError):
        pass


def forget_reaper_dir():
    notes = _install_notes()
    if "reaper_resource_dir" in notes:
        del notes["reaper_resource_dir"]
        try:
            write_atomic(INSTALL_NOTES, json.dumps(notes, indent=2) + "\n")
        except (OSError, ValueError):
            pass


def reaper_resource_dir(arg, mod, remember=False):
    """REAPER's settings folder. One given with --reaper-resource-dir (a portable REAPER) is remembered in
    kit\\state\\install.json, so that a later run without the option (Check, Uninstall, or a second
    double-click on Install) looks in the same place."""
    if arg:
        res = os.path.abspath(arg)
        if remember and os.path.isdir(res):
            try:
                notes = _install_notes()
                notes["reaper_resource_dir"] = res
                write_atomic(INSTALL_NOTES, json.dumps(notes, indent=2) + "\n")
            except (OSError, ValueError):
                pass
        return res
    noted = _install_notes().get("reaper_resource_dir")
    if isinstance(noted, str) and noted and os.path.isdir(noted):
        info("using the REAPER folder given at install time: %s" % noted)
        return noted
    return PF.reaper_resource_dir()


GATES = {"allow_audio_writes": True,            # render a track to a WAV so it can be measured
         "allow_preference_writes": True,       # lets RapReaper switch OFF one REAPER preference (media offline when inactive)
         "allow_project_save": False,           # the bridge's own project-save permission stays off (RapReaper's save_project
                                                # tool saves only when the person asks, and asks before closing over unsaved changes)
         "allow_risk_level_3": False}           # the bridge's older catch-all switch stays off


def lua_escape(text):
    """`text` made safe inside a Lua double-quoted string: backslash, the double quote, line breaks and every other
    control character are written as Lua escapes. (A single quote needs none inside double quotes, and is left as it
    is, so that the block of an existing install in a folder such as O'Brien stays byte for byte the same; letters
    outside English, as UTF-8, need none either.)"""
    out = []
    for ch in text:
        o = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif o < 32 or o == 127:
            out.append("\\%03d" % o)
        else:
            out.append(ch)
    return "".join(out)


def _unsafe_for_lua(path):
    """True when a folder path cannot go into REAPER's start-up script: the bridge's own block (whose file is checked
    byte for byte, so it is not changed here) only escapes backslashes and double quotes, so a line break or another
    control character in the path would break the whole start-up script."""
    return any(ord(ch) < 32 or ord(ch) == 127 for ch in path)


BOM = b"\xef\xbb\xbf"
RAPREAPER_BEGIN = "-- >>> rapreaper (managed) >>>"
RAPREAPER_END = "-- <<< rapreaper (managed) <<<"


def rapreaper_block_text(kit_dir=None):
    """RapReaper's own lines for REAPER's start-up script: they note the moment of every REAPER start in a small
    file inside the RapReaper folder. With that, RapReaper can tell a REAPER that was started again after a crash
    (whose leftover 'busy' mark it may clear) from a REAPER that is merely busy (whose mark it must not touch)."""
    kit = os.path.abspath(kit_dir or KIT)
    stamp = os.path.join(kit, "state", "reaper_started.txt")
    helper = os.path.join(kit, "rapreaper_helper.lua")
    esc = lua_escape
    return (RAPREAPER_BEGIN + "\n"
            "-- Notes the moment of every REAPER start, so that RapReaper can tell a REAPER that was started again\n"
            "-- after a crash from one that is merely busy; then starts RapReaper's small helper (clip edits and\n"
            "-- plug-in settings blocks; see kit/rapreaper_helper.lua). Managed by rapreaper_install.py.\n"
            "do\n"
            '  local f = io.open("' + esc(stamp) + '", "w")\n'
            "  if f then f:write(tostring(os.time())); f:close() end\n"
            '  RAPREAPER_KIT_DIR = "' + esc(kit) + '"\n'
            '  local ok, err = pcall(dofile, "' + esc(helper) + '")\n'
            '  if not ok and reaper and reaper.ShowConsoleMsg then reaper.ShowConsoleMsg("RapReaper helper did not start: " .. tostring(err) .. "\\n") end\n'
            "end\n"
            + RAPREAPER_END + "\n")


def _startup_blocks(raw, mod):
    """Find the managed blocks in __startup.lua (RapReaper's own and the bridge's), working on bytes so that a
    file with a byte-order mark, Windows line endings or non-UTF-8 text in the person's own lines is handled
    without touching anything but the blocks. Returns (lines, [(first, last), ...] in file order, damaged)."""
    body = raw[len(BOM):] if raw.startswith(BOM) else raw
    lines = body.split(b"\n")
    spans, damaged = [], False
    for begin, end in ((RAPREAPER_BEGIN.encode("utf-8"), RAPREAPER_END.encode("utf-8")),
                       (mod.BEGIN.encode("utf-8"), mod.END.encode("utf-8"))):
        start = None
        for i, line in enumerate(lines):
            text = line.rstrip(b"\r")
            if text == begin:
                if start is not None:
                    damaged = True                   # a second start marker before the first block ended
                start = i
            elif text == end:
                if start is None:
                    damaged = True                   # an end marker without a start
                else:
                    spans.append((start, i))
                    start = None
        if start is not None:
            damaged = True                           # a start marker with no end
    spans.sort()
    if any(b[0] <= a[1] for a, b in zip(spans, spans[1:])):
        damaged = True                               # one block inside another
    return lines, spans, damaged


DAMAGED = ("REAPER's startup script (%s) has part of an old RapReaper block in it: a start or end marker line "
           "('-- >>> ... (managed) >>>' / '-- <<< ... (managed) <<<') without its "
           "partner. Nothing was changed. Open the file in Notepad and delete those marker lines and the lines "
           "between them (or delete the whole file if you never put anything in it yourself), then run this "
           "installer again.")


def _startup_with_block(raw, mod, block):
    """The file's bytes with exactly one, current, copy of the managed blocks (existing ones are replaced).
    `block` is RapReaper's own block followed by the bridge's. They go FIRST: a start-up script of the
    person's own may end with a top-level 'return', and Lua does not allow anything after that. (A first
    line starting with '#' stays first; Lua skips such a line only there.)"""
    lines, spans, _damaged = _startup_blocks(raw, mod)
    for a, b in reversed(spans):
        del lines[a:b + 1]
    body = b"\n".join(lines)
    if body.strip(b"\r\n \t") == b"":
        body = b""
    head = b""
    if body.startswith(b"#"):
        cut = body.find(b"\n")
        head, body = (body, b"") if cut < 0 else (body[:cut + 1], body[cut + 1:])
        if not head.endswith(b"\n"):
            head += b"\n"
    return (BOM if raw.startswith(BOM) else b"") + head + block.encode("utf-8") + body


def _startup_without_block(raw, mod):
    lines, spans, _damaged = _startup_blocks(raw, mod)
    for a, b in reversed(spans):
        del lines[a:b + 1]
    body = b"\n".join(lines)
    if body.strip(b"\r\n \t") == b"":
        return b""
    return (BOM if raw.startswith(BOM) else b"") + body


BRIDGE_SHA256 = {                      # the bridge files as published (NOTICE.txt); checked before anything is wired up
    os.path.join("bridge", "reaper_agent_bridge.lua"): "2f30318084e15096da6ecc462b1e5bd63fe91c9f143a1becdc803509c89ed731",
    os.path.join("bridge", "json.lua"): "b49b586232c17612565deeb1242a87c3befab897040cd2f9e3c02c6305aa2498",
    "reaperd.py": "258d9f42df2cc691ebcc96f121cbc6d86cedb316dcbb18535d90c166f1c4d76a",
    os.path.join("setup", "install.py"): "2c7f08cc7dd1b90ddd590305d3344d83a897e76837253ed2879a03badce12f87",
}


def bridge_files_ok():
    """Each vendored bridge file is byte for byte the published one. The bridge runs inside REAPER with every power
    a REAPER script has, so a changed file (a damaged download, a tampered zip, an editor's 'helpful' line-ending
    change) is a reason to stop and say so, not to wire it into REAPER's start-up. Returns the changed files."""
    bad = []
    for rel, want in BRIDGE_SHA256.items():
        path = os.path.join(BRIDGE_ROOT, rel)
        try:
            with open(path, "rb") as f:
                got = hashlib.sha256(f.read()).hexdigest()
        except OSError:
            got = "missing"
        if got != want:
            bad.append(rel)
    return bad


UNSTAMPED = "unstamped"
HELPER_SHA256 = UNSTAMPED           # stamped at build time (stamp_helper_hash.py), like the server's copy


def helper_file_ok():
    if HELPER_SHA256 == UNSTAMPED:
        return True
    try:
        with open(os.path.join(KIT, "rapreaper_helper.lua"), "rb") as f:
            return hashlib.sha256(f.read()).hexdigest() == HELPER_SHA256
    except OSError:
        return False


OFFLINE_KEY = b"offlineinact"        # reaper.ini, [REAPER]: 'Set media items offline when application is not active'
OFFLINE_WHY = ("With it on, REAPER unloads the audio whenever another window (such as Claude) is in front, and every "
               "measurement RapReaper makes comes back silent.")


def reaper_running():
    """True when REAPER seems to be open: its process is listed (Windows), or the bridge inside it wrote its heartbeat
    within the last half minute. REAPER writes its settings file when it closes, so a change made while it is open
    would be lost."""
    if is_windows():
        try:
            out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq reaper.exe", "/NH"], capture_output=True, text=True, timeout=20).stdout
            if "reaper.exe" in out.lower():
                return True
        except (OSError, subprocess.SubprocessError):
            pass
    elif sys.platform == "darwin":
        try:
            if subprocess.run(["pgrep", "-x", "REAPER"], capture_output=True, timeout=20).returncode == 0:
                return True
        except (OSError, subprocess.SubprocessError):
            pass
    hb = os.path.join(BRIDGE_ROOT, "bridge", "heartbeat.json")
    try:
        return time.time() - os.path.getmtime(hb) < 30.0
    except OSError:
        return False


def read_offline_preference(res):
    """(value or None, the ini's bytes, the matching line's index) for the preference in reaper.ini's [REAPER] section."""
    path = os.path.join(res, "reaper.ini")
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return None, None, None
    section = None
    for i, line in enumerate(raw.split(b"\n")):
        text = line.strip()
        if text.startswith(b"[") and text.endswith(b"]"):
            section = text[1:-1].strip().lower()
        elif section == b"reaper" and text.lower().startswith(OFFLINE_KEY + b"="):
            try:
                return int(text.split(b"=", 1)[1].strip() or b"0"), raw, i
            except ValueError:
                return None, raw, i
    return 0, raw, None          # REAPER's default when the line is absent: off


def setup_offline_preference(res, check_only):
    """Switch OFF the one REAPER preference RapReaper cannot work with, in reaper.ini, while REAPER is closed. (While
    REAPER is open, RapReaper's self_test and fix_setup tools switch it off through the bridge, and say so.)"""
    value, raw, idx = read_offline_preference(res)
    name = "'Set media items offline when application is not active'"
    if raw is None:
        info("reaper.ini was not found in %s, so REAPER's %s preference could not be checked. RapReaper's self-test "
             "checks it (and switches it off) when REAPER is open." % (res, name))
        return True
    if value in (0, None):
        ok("REAPER keeps audio loaded while other windows are in front (%s is off)" % name)
        return True
    if check_only:
        todo("REAPER's preference %s is ON. %s Run the installer again with REAPER closed (it switches it off), or run "
             "RapReaper's self-test from Claude with REAPER open (it does the same)." % (name, OFFLINE_WHY))
        return False
    if reaper_running():
        info("REAPER's preference %s is ON, and REAPER is open now, so this installer leaves reaper.ini alone (REAPER "
             "writes that file when it closes). RapReaper's self-test switches the preference off the first time it "
             "runs; or close REAPER and run this installer again." % name)
        return True
    lines = raw.split(b"\n")
    tail = b"\r" if lines[idx].endswith(b"\r") else b""
    lines[idx] = OFFLINE_KEY + b"=0" + tail
    path = os.path.join(res, "reaper.ini")
    try:
        copy = backup(path)
        write_atomic(path, b"\n".join(lines))
    except OSError as e:
        todo("REAPER's preference %s is ON and reaper.ini could not be changed (%s). In REAPER: Options > Preferences, "
             "type 'offline' in the search box, untick it, OK." % (name, e))
        return False
    try:
        notes_ = _install_notes()
        notes_["offline_preference_was"] = value
        notes_["reaper_ini_backup"] = copy
        write_atomic(INSTALL_NOTES, json.dumps(notes_, indent=2) + "\n")
    except (OSError, ValueError):
        pass
    ok("switched OFF REAPER's preference %s (it was on). %s A copy of reaper.ini was kept: %s" % (name, OFFLINE_WHY, copy))
    return True


# RapReaper's own JS effects (kit\effects\...) go into REAPER's Effects folder, where REAPER reads them from when it starts.
# Today that is one: RapReaper/ducks.jsfx, the 'Ducks' plug-in duck_section draws its dips on (see CHANGES-1.4.md, 1.4.4).
OWN_EFFECTS = ("RapReaper/ducks.jsfx",)


def setup_own_effects(res, check_only):
    """Copy RapReaper's JS effects into REAPER's Effects folder (only what differs is written). Returns True when
    they are all in place. The copies are compared byte for byte, so a run that changes nothing writes nothing."""
    all_ok = True
    for rel in OWN_EFFECTS:
        src = os.path.join(KIT, "effects", *rel.split("/"))
        dest = os.path.join(res, "Effects", *rel.split("/"))
        shown = os.path.join("Effects", *rel.split("/"))
        try:
            with open(src, "rb") as f:
                want = f.read()
        except OSError as e:
            todo("RapReaper's effect %s is missing from the kit (%s). Unzip RapReaper again from the original zip." % (rel, e))
            all_ok = False
            continue
        have = None
        if os.path.isfile(dest):
            try:
                with open(dest, "rb") as f:
                    have = f.read()
            except OSError:
                have = None
        if have == want:
            ok("REAPER has RapReaper's effect  %s" % shown)
            continue
        if check_only:
            todo("REAPER does NOT have RapReaper's effect %s%s (run the installer, then restart REAPER)."
                 % (shown, "" if have is None else " in its current form"))
            all_ok = False
            continue
        try:
            if have is not None:
                info("kept a copy of the effect file that was there: %s" % backup(dest))
            write_atomic(dest, want)
        except OSError as e:
            todo("RapReaper's effect could not be put into REAPER's Effects folder (%s): %s. If the folder is marked read-only, "
                 "clear that in its Properties and run this installer again." % (dest, e))
            all_ok = False
            continue
        ok("put RapReaper's effect into REAPER  (%s)  -- REAPER reads it when it next starts" % dest)
    return all_ok


def setup_reaper(resource_arg, check_only):
    step(4, "REAPER")
    changed = bridge_files_ok()
    if not helper_file_ok():
        changed.append("kit\\rapreaper_helper.lua")
    if changed:
        todo("The bridge script files in kit\\reaper-daemon are not the ones shipped with RapReaper (%s). RapReaper will not "
             "connect a changed bridge to REAPER. Unzip RapReaper again from the original zip and run the installer again."
             % ", ".join(changed))
        return False
    ok("bridge files are the published ones (checksums match NOTICE.txt)")
    mod = _vendored_installer()
    res = reaper_resource_dir(resource_arg, mod, remember=not check_only)
    startup = os.path.join(res, "Scripts", "__startup.lua")
    if not os.path.isdir(res):
        todo("REAPER's settings folder was not found (%s). Install REAPER from https://www.reaper.fm/download.php, start "
             "it once, close it, and run this installer again. (If you installed REAPER as a 'portable install', run:  "
             "%s rapreaper_install.py --reaper-resource-dir \"<your REAPER folder>\")" % (res, PY_CMD))
        return False
    if _unsafe_for_lua(os.path.abspath(BRIDGE_ROOT)):
        todo("The RapReaper folder's path contains a line break or another invisible control character, which cannot be "
             "written into REAPER's start-up script. Nothing was changed. Move the RapReaper folder to  %s  and run this "
             "installer again." % best_home())
        return False
    want_block = rapreaper_block_text() + mod.block_text(os.path.join(os.path.abspath(BRIDGE_ROOT), "bridge"))
    raw = b""
    if os.path.isfile(startup):
        try:
            with open(startup, "rb") as f:
                raw = f.read()
        except OSError as e:
            todo("REAPER's startup script could not be read (%s): %s. Nothing was changed." % (startup, e))
            return False
    _lines, spans, damaged = _startup_blocks(raw, mod)
    # in place = writing the blocks again would not change a single byte
    block_ok = (not damaged) and len(spans) == 2 and _startup_with_block(raw, mod, want_block) == raw
    try:
        cfg = mod.read_config(BRIDGE_ROOT) or {}
    except Exception:
        cfg = {}
    gates_ok = all(cfg.get(k) is v for k, v in GATES.items())
    if check_only:
        (ok if block_ok else todo)("bridge auto-start %s in %s" % ("is" if block_ok else "is NOT", startup))
        (ok if gates_ok else todo)("bridge permissions %s" % ("are set" if gates_ok else "are NOT set (run the installer)"))
    else:
        if damaged:
            todo(DAMAGED % startup)
            return False
        try:
            os.makedirs(os.path.join(KIT, "state"), exist_ok=True)        # where REAPER notes its start time
        except OSError:
            pass
        if not block_ok:
            try:
                if raw:
                    info("kept a copy of your existing startup script: %s" % backup(startup))
                write_atomic(startup, _startup_with_block(raw, mod, want_block))
            except OSError as e:
                todo("The bridge could not be added to REAPER's startup script (%s): %s. If the file is marked "
                     "read-only, clear that in its Properties and run this installer again." % (startup, e))
                return False
            ok("bridge will start with REAPER  (%s)" % startup)
        else:
            ok("bridge already starts with REAPER  (%s)" % startup)
        if not gates_ok:
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    mod.set_gates(BRIDGE_ROOT, GATES)
            except OSError as e:
                todo("The bridge's permission file could not be written (%s). Check that this folder is not read-only."
                     % e)
                return False
        ok("bridge permissions: measuring ON, 'media offline' preference fix ON, the bridge's own project saving OFF "
           "(RapReaper saves your project only when you ask it to, and asks before closing over unsaved changes)")
    sws = glob.glob(os.path.join(res, "UserPlugins", "reaper_sws*"))
    reapack = glob.glob(os.path.join(res, "UserPlugins", "reaper_reapack*"))
    if sws:
        ok("SWS extension found (%s)" % os.path.basename(sws[0]))
    else:
        # installed through ReaPack (REAPER's own package installer), which fetches it from SWS's GitHub releases;
        # the SWS website is not needed
        steps = ("in REAPER choose Extensions > ReaPack > Browse packages, type SWS in the filter, right-click "
                 "'SWS/S&M extension' > Install, click Apply, and restart REAPER when ReaPack asks.")
        if reapack:
            how = "ReaPack is installed: " + steps
        else:
            how = ("First install ReaPack, REAPER's free package installer, from its official site https://reapack.com "
                   "(by cfillion): download the file for your REAPER (%s) and put it in this folder: %s . Restart REAPER%s. Then %s"
                   % (PF.reapack_file(), os.path.join(res, "UserPlugins"),
                      (" (if macOS says the file cannot be opened, allow it once under System Settings > Privacy & Security, "
                       "then restart REAPER again)" if PF.IS_MAC else ""), steps))
        todo("The SWS extension is not installed. RapReaper needs it to measure audio without pop-up windows. " + how)
    fx_ok = setup_own_effects(res, check_only)
    pref_ok = setup_offline_preference(res, check_only)
    if not check_only:
        info("If REAPER is open now, close it and start it again so the bridge loads and RapReaper's effect is read (give it a "
             "minute after that).")
    return (block_ok and pref_ok and fx_ok) or not check_only


def remove_reaper(resource_arg):
    mod = _vendored_installer()
    res = reaper_resource_dir(resource_arg, mod)
    startup = os.path.join(res, "Scripts", "__startup.lua")
    if not os.path.isfile(startup):
        ok("nothing to remove from REAPER (no startup script)")
        return
    try:
        with open(startup, "rb") as f:
            raw = f.read()
    except OSError as e:
        todo("REAPER's startup script could not be read (%s): %s." % (startup, e))
        return
    _lines, spans, damaged = _startup_blocks(raw, mod)
    if damaged:
        todo(DAMAGED % startup)
        return
    if not spans:
        ok("nothing to remove from REAPER (no RapReaper block in %s)" % startup)
        return
    try:
        info("kept a copy: %s" % backup(startup))
        write_atomic(startup, _startup_without_block(raw, mod))
    except OSError as e:
        todo("The bridge block could not be removed from %s: %s." % (startup, e))
        return
    ok("removed RapReaper's blocks from %s (restart REAPER)" % startup)


# --------------------------------------------------------------------------------------------
# 5. Claude Desktop
# --------------------------------------------------------------------------------------------

def claude_config_dirs():
    return PF.claude_config_dirs()


def rapreaper_entry():
    return {"command": server_python(), "args": [SERVER], "env": {"RAPREAPER_HOME": KIT}}


def _load_config(path):
    """(config dict, problem text). A missing or empty file is an empty config."""
    if not os.path.isfile(path):
        return {}, None
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as e:
        return None, str(e)
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return None, "it is saved as UTF-16 text; save it as UTF-8 (in Notepad: Save As > Encoding: UTF-8)"
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None, "it is not saved as UTF-8 text (in Notepad: Save As > Encoding: UTF-8)"
    if not text.strip():
        return {}, None
    try:
        data = json.loads(text)
    except ValueError as e:
        return None, "it is not valid JSON (%s)" % e
    if not isinstance(data, dict):
        return None, "it does not contain a JSON object"
    return data, None


def setup_claude(check_only):
    step(5, "Claude Desktop")
    # A folder counts when it is there, when the person named it (RAPREAPER_CLAUDE_CONFIG_DIRS), or when it is the
    # private settings place of an installed Windows-Store-style Claude that has not been started yet.
    packaged = os.path.normcase(os.path.join("LocalCache", "Roaming", "Claude"))
    present = [d for d in claude_config_dirs()
               if os.path.isdir(d) or os.environ.get("RAPREAPER_CLAUDE_CONFIG_DIRS") or os.path.normcase(d).endswith(packaged)]
    if not present:
        todo("Claude Desktop does not seem to be installed, or has never been started on this computer. Get it from "
             "https://claude.com/download , start it and sign in once, close it, then run this installer again.")
        return False
    entry = rapreaper_entry()
    done = 0
    for d in present:
        path = os.path.join(d, "claude_desktop_config.json")
        cfg, why = _load_config(path)
        if cfg is None:
            todo("Claude's settings file could not be read (%s): %s. Nothing was changed. Open it in Claude (Settings > "
                 "Developer > Edit Config), fix or empty it, and run this installer again." % (path, why))
            continue
        servers = cfg.get("mcpServers")
        if servers is None:
            servers = {}
        if not isinstance(servers, dict):
            todo("In %s, \"mcpServers\" is not a JSON object. Nothing was changed." % path)
            continue
        if servers.get("rapreaper") == entry:
            ok("RapReaper is already in %s" % path)
            done += 1
            continue
        if check_only:
            todo("RapReaper is NOT in %s (run the installer)" % path)
            continue
        try:
            servers["rapreaper"] = entry
            cfg["mcpServers"] = servers
            text = json.dumps(cfg, indent=2, allow_nan=False) + "\n"
        except ValueError:
            todo("Claude's settings file (%s) contains a number that is not valid JSON (infinity or 'not a number'). "
                 "Nothing was changed. Open it in Claude (Settings > Developer > Edit Config), fix that value, and "
                 "run this installer again." % path)
            continue
        try:
            if os.path.isfile(path):
                info("kept a copy: %s" % backup(path))
            write_atomic(path, text)
        except OSError as e:
            todo("Claude's settings file could not be written (%s): %s. If it is marked read-only, clear that in its "
                 "Properties and run this installer again." % (path, e))
            continue
        ok("added RapReaper to %s" % path)
        done += 1
    if done and not check_only:
        info("Claude only reads this file when it starts: %s." % PF.quit_claude_how())
    return bool(done)


def remove_claude():
    for d in claude_config_dirs():
        path = os.path.join(d, "claude_desktop_config.json")
        cfg, why = _load_config(path)
        if not cfg or not isinstance(cfg.get("mcpServers"), dict) or "rapreaper" not in cfg["mcpServers"]:
            if cfg is None and os.path.isfile(path):
                todo("Claude's settings file could not be read (%s): %s. If RapReaper is listed in it, remove the "
                     "\"rapreaper\" entry by hand (Claude: Settings > Developer > Edit Config)." % (path, why))
            continue
        try:
            del cfg["mcpServers"]["rapreaper"]
            text = json.dumps(cfg, indent=2, allow_nan=False) + "\n"
        except ValueError:
            todo("Claude's settings file (%s) contains a number that is not valid JSON, so it was left untouched. "
                 "Remove the \"rapreaper\" entry by hand (Claude: Settings > Developer > Edit Config)." % path)
            continue
        try:
            info("kept a copy: %s" % backup(path))
            write_atomic(path, text)
        except OSError as e:
            todo("RapReaper could not be removed from %s: %s." % (path, e))
            continue
        ok("removed RapReaper from %s (restart Claude)" % path)


# --------------------------------------------------------------------------------------------
# 6. does the tool server start?
# --------------------------------------------------------------------------------------------

def _scrub(value):
    """`value` (text, or lists and dicts of it) without the person's home folder, user name or computer name: the home
    folder becomes %USERPROFILE% (Windows) or ~ (Mac), the names <you> and <this computer>. For what the installer shows
    and saves of the server's own error messages, which can carry full paths."""
    if isinstance(value, dict):
        return {_scrub(k): _scrub(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub(v) for v in value]
    if not isinstance(value, str):
        return value
    text = value
    homes = {h for h in (PF.home_dir(), os.environ.get("USERPROFILE"), os.environ.get("HOME"), os.path.expanduser("~"))
             if h and len(h.strip("\\/")) > 2}
    for home in sorted(homes, key=len, reverse=True):
        home = home.rstrip("\\/")
        forms = {home, home.replace("\\", "/"), home.replace("/", "\\"), home.replace("\\", "\\\\")}
        for form in sorted(forms, key=len, reverse=True):
            text = re.sub(re.escape(form) + r"(?![A-Za-z0-9_])", lambda _m: PF.HOME_WORD, text, flags=re.I)
    names = set()
    for n in (PF.user_name(), os.environ.get("USERNAME"), os.environ.get("USER"), os.environ.get("LOGNAME")):
        if n:
            names.add(n)
    for h in homes:
        base = os.path.basename(h.rstrip("\\/").replace("\\", "/"))
        if base:
            names.add(base)
    computers = set(PF.computer_names())
    if os.environ.get("COMPUTERNAME"):
        computers.add(os.environ["COMPUTERNAME"])
    for group, word in ((computers, "<this computer>"), (names, "<you>")):
        for n in sorted(group, key=len, reverse=True):
            if len(n) < 3:
                continue                # a one- or two-letter name would wipe out ordinary words
            text = re.sub(r"(?<![A-Za-z0-9])" + re.escape(n) + r"(?![A-Za-z0-9])", lambda _m: word, text, flags=re.I)
    return text


def self_check():
    step(6, "Starting the RapReaper tool server once, the way Claude will")
    env = dict(os.environ)
    env["RAPREAPER_HOME"] = KIT
    try:
        p = subprocess.Popen([server_python(), SERVER], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, env=env)
    except Exception as e:
        todo("The tool server could not be started: %s" % e)
        return False
    replies, errors = {}, []
    got = threading.Event()

    def read_replies():
        for line in iter(p.stdout.readline, b""):
            try:
                m = json.loads(line.decode("utf-8", "replace"))
            except ValueError:
                continue
            if isinstance(m, dict) and "id" in m:
                replies[m["id"]] = m
                got.set()

    def read_errors():
        for line in iter(p.stderr.readline, b""):
            errors.append(line.decode("utf-8", "replace"))

    for fn in (read_replies, read_errors):
        threading.Thread(target=fn, daemon=True).start()

    def ask(msg, seconds):
        """Send one message; for a request, wait for its reply (the way Claude does)."""
        try:
            p.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
            p.stdin.flush()
        except OSError:
            return None
        if "id" not in msg:
            return None
        waited = 0.0
        while msg["id"] not in replies and waited < seconds and p.poll() is None:
            got.clear()
            got.wait(0.2)
            waited += 0.2
        return replies.get(msg["id"])

    ask({"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "rapreaper-installer", "version": VERSION}}}, 60)
    ask({"jsonrpc": "2.0", "method": "notifications/initialized"}, 0)
    listed = ask({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, 60)
    if PF.IS_WINDOWS:
        info("Asking it for its status (the first start after an install can take a minute or two on Windows, while the")
        info("virus scanner checks the maths library once) ...")
    else:
        info("Asking it for its status ...")
    called = ask({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "status", "arguments": {}}}, 300)
    try:
        p.stdin.close()
        p.wait(timeout=15)
    except Exception:
        p.kill()
    tools = ((listed or {}).get("result") or {}).get("tools") or []
    status, answer_text = {}, ""
    try:
        answer_text = str(called["result"]["content"][0]["text"])
        status = json.loads(answer_text)
    except Exception:
        pass
    if not tools or not isinstance(status, dict) or "rapreaper_version" not in status:
        # what came back, word for word (the server's privacy filter has already taken paths and names out), kept in
        # a file too, so that whoever helps can read it without asking for a screenshot
        if called is None:
            what = ("no answer to the 'status' question within 5 minutes. On a slow PC the very first start can take that "
                    "long while Windows checks new files; run the installer once more (it is safe), and if it happens again "
                    "send the file named below to whoever helps you." if tools else "no list of tools")
        elif (called or {}).get("error"):
            what = "an error: %s" % json.dumps(called["error"])[:600]
        else:
            what = "this answer: %s" % (answer_text[:900] or json.dumps(called)[:600])
        # the server's own messages (Python's error text) are not filtered by the server, so the home folder, the user
        # name and the computer's name are taken out here, before they are shown or saved
        what = _scrub(what)
        try:
            os.makedirs(os.path.join(KIT, "state"), exist_ok=True)
            write_atomic(os.path.join(KIT, "state", "installer_selfcheck.json"),
                         json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "tools": len(tools),
                                     "status_reply": _scrub(called), "server_messages": _scrub("".join(errors)[-4000:])},
                                    indent=1))
        except (OSError, ValueError, TypeError):
            pass
        todo("The tool server started but did not answer properly: %s  Its messages: %s  (All of it is saved in "
             "kit\\state\\installer_selfcheck.json; your user folder, user name and computer name were taken out of it, "
             "but it may still contain the names of other folders, so look it over before you send it to anyone.)"
             % (what, _scrub("".join(errors).strip()[-500:]) or "(none)"))
        return False
    ok("it answers: version %s, %d tools, measuring: %s" % (status.get("rapreaper_version"), len(tools), status.get("measuring")))
    privacy = str(status.get("privacy") or "")
    if privacy:
        ok("privacy: %s" % privacy.split(". The privacy tool")[0])
        info("Everything RapReaper sends to Claude is recorded, as sent, in kit\\logs\\sent_to_claude.log on this PC; which label "
             "stands for which file is in kit\\state\\private_names.txt. Ask Claude \"show RapReaper's privacy settings\" to see or "
             "change the level (standard / strict / maximum).")
    if status.get("reaper") == "connected":
        ok("REAPER is open and connected right now")
    else:
        info("REAPER is not connected right now (normal if it is closed, or was open before this installer ran).")
    return str(status.get("measuring", "")).startswith("ready")


# --------------------------------------------------------------------------------------------
# macOS: the double-click files
# --------------------------------------------------------------------------------------------

def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(1024 * 1024)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


# --------------------------------------------------------------------------------------------
# the Vocal Remover that ships inside this folder
# --------------------------------------------------------------------------------------------

VR_DIR = os.path.join(HERE, "VocalRemover")
VR_PROGRAM = os.path.join(VR_DIR, "VocalRemover" if PF.IS_MAC else "VocalRemover.exe")
# the model files and their published checksums (the Vocal Remover checks the same ones before loading)
VR_MODELS = {
    "MelRoformer-KimFT2-int8.onnx": "f81d9265cf57b66808db38d72ae136f3d74f3888f642e085478288f774b5a0d4",
    "BS-Roformer-1297-int8.onnx": "a31b1fed94ed5fcf3c8ef41f9cf55a31ba6da2f1028d91d296907ced99b23cdf",
    "UVR-MDX-NET-Inst_HQ_4.onnx": "3c4b5b9b05090fdf238f38ba5046813982d50e2a652e9cb3324ea79720c3c9c8",
    "Kim_Vocal_2.onnx": "ce74ef3b6a6024ce44211a07be9cf8bc6d87728cc852a68ab34eb8e58cde9c8b",
}
VR_NEEDED = ("MelRoformer-KimFT2-int8.onnx",)       # the default mode needs this one; the others add modes


def _piece_dirs():
    """Where downloaded model pieces may lie: this folder, the Vocal Remover folder (and its models folder), RapReaper's
    own models folder, and Downloads. (Not the folder above this one: whatever lies there is not RapReaper's.)"""
    dirs = [HERE, VR_DIR, os.path.join(VR_DIR, "models"), os.path.join(KIT, "models")]
    home = PF.home_dir()
    if home:
        dirs.append(os.path.join(home, "Downloads"))
    return [d for d in dirs if d and os.path.isdir(d)]


# how many pieces each model is delivered in (a name that claims more is not one of RapReaper's pieces)
VR_PIECES = {"MelRoformer-KimFT2-int8.onnx": 8, "Kim_Vocal_2.onnx": 3}
VR_SIZES = {"MelRoformer-KimFT2-int8.onnx": 236771629, "Kim_Vocal_2.onnx": 66759214, "BS-Roformer-1297-int8.onnx": 170491558}
VR_MAX_BYTES = 600 * 1024 * 1024        # (a model of unknown size is never joined past this)


def _regular_file(path):
    """A plain file, not a link, a pipe or a device (a piece that is a link to /dev/zero would fill the disk)."""
    import stat as _stat
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return _stat.S_ISREG(st.st_mode)


def _version_key(text):
    try:
        return tuple(int(x) for x in str(text).split("."))
    except ValueError:
        return None
VR_PIECES_MAX = 16

WOULD_JOIN = set()      # models whose pieces --check found complete (the real run joins them)

# The models a person can build from their authors' own public files (build/build_roformer_onnx.py, NumPy only, no
# PyTorch). "Get Vocal Remover Model" downloads the two files into VocalRemover/models; they may also lie in Downloads.
# Both are checked against their published checksums before anything reads them, and the built file must come out
# with the model's published checksum (the converter is deterministic), so it is the very file the program checks.
VR_BUILDABLE = {
    "MelRoformer-KimFT2-int8.onnx": {
        "checkpoint": ("kimmel_unwa_ft2.ckpt", "5ed7b9e4c2eebbec7a7e5e8113058f7b68ba5e6048db8eaccfbbeb884c7884c0", 913100690),
        "config": ("config_kimmel_unwa_ft.yaml", "0942cb7da8e86ac1be840cc7b9647831c11ffe00327a0b239df256031e6c8355", 1722),
        "args": ["801", "--int8"],
        "source": "https://huggingface.co/pcunwa/Kim-Mel-Band-Roformer-FT",
    },
}
BUILD_TIMEOUT = 3600


def _find_checked(name, want, size):
    """The first plain file called `name` in the places pieces may lie whose size and SHA-256 are the published ones."""
    for d in _piece_dirs():
        path = os.path.join(d, name)
        if not _regular_file(path):
            continue
        try:
            if os.path.getsize(path) == size and sha256_of(path) == want:
                return path
        except OSError:
            continue
    return None


def build_models_from_checkpoints(check_only, present):
    """Build the models listed in VR_BUILDABLE that are not present yet, when their source files are here. Returns the
    names built (each checked against its published checksum)."""
    built = []
    models_dir = os.path.join(VR_DIR, "models")
    script = os.path.join(HERE, "build", "build_roformer_onnx.py")
    for name, spec in VR_BUILDABLE.items():
        if name in present:
            continue
        ckpt = _find_checked(*spec["checkpoint"])
        cfg = _find_checked(*spec["config"])
        if not (ckpt and cfg):
            seen = [spec[k][0] for k, found in (("checkpoint", ckpt), ("config", cfg))
                    if not found and any(os.path.isfile(os.path.join(d, spec[k][0])) for d in _piece_dirs())]
            if seen:
                todo("%s is here but is not the published file (its size or checksum differs), so %s was not built from it. "
                     "Delete it and run 'Get Vocal Remover Model%s' again." % (seen[0], name, PF.LAUNCHER_EXT))
            continue
        if not os.path.isfile(script):
            todo("build%sbuild_roformer_onnx.py is missing, so %s cannot be built. Unzip the package again." % (os.sep, name))
            continue
        if check_only:
            info("%s: its source files are here (checksums verified); the installer would build it" % name)
            WOULD_JOIN.add(name)
            continue
        python = server_python()
        have, _why = _probe_packages(python)
        if not have:
            todo("%s cannot be built yet: NumPy is not installed for RapReaper. Run Install RapReaper%s first, then this again."
                 % (name, PF.LAUNCHER_EXT))
            continue
        os.makedirs(models_dir, exist_ok=True)
        target = os.path.join(models_dir, name)
        tmp = target + ".building"
        info("building %s from %s (about a minute; it needs about 3 GB of free memory) ..." % (name, os.path.basename(ckpt)))
        try:
            if os.path.lexists(tmp):
                os.remove(tmp)
            r = subprocess.run([python, script, cfg, ckpt, tmp] + spec["args"], capture_output=True, text=True,
                               timeout=BUILD_TIMEOUT, cwd=HERE)
            if r.returncode != 0:
                raise OSError(((r.stderr or r.stdout) or "the converter stopped").strip().splitlines()[-1][-300:])
            got = sha256_of(tmp)
            if got != VR_MODELS[name]:
                raise OSError("the built file's checksum is %s, not the published %s" % (got[:16] + "...", VR_MODELS[name][:16] + "..."))
            os.replace(tmp, target)
        except (OSError, subprocess.SubprocessError) as e:
            try:
                os.remove(tmp)
            except OSError:
                pass
            todo("%s could not be built: %s" % (name, e))
            continue
        ok("%s built from its authors' public files; checksum matches the published one" % name)
        built.append(name)
        for src in (ckpt, cfg):         # the downloaded sources inside this folder are no longer needed (Downloads is left alone)
            if os.path.dirname(os.path.abspath(src)) == os.path.abspath(models_dir):
                try:
                    os.remove(src)
                except OSError:
                    pass
    return built


def build_model_only(check_only=False):
    """--build-model: build the Vocal Remover's model from the files 'Get Vocal Remover Model' downloaded."""
    say("RapReaper %s: build the Vocal Remover's model from its authors' public files" % VERSION)
    present = join_model_pieces(check_only)
    present += build_models_from_checkpoints(check_only, present)
    if check_only:
        return 0 if (VR_NEEDED[0] in present or VR_NEEDED[0] in WOULD_JOIN) else finish()
    if VR_NEEDED[0] in present:
        ok("%s is in VocalRemover%smodels, checked" % (VR_NEEDED[0], os.sep))
    elif not problems:
        todo("%s is not built yet. Run 'Get Vocal Remover Model%s', which downloads its two source files." % (VR_NEEDED[0], PF.LAUNCHER_EXT))
    if problems:
        return finish()
    say()
    say("DONE. The Vocal Remover and RapReaper's separate_song can use the model now.")
    return 0


def _model_ok(path, want):
    """True when the model file at `path` is the published one (its SHA-256 is computed every time: a file's size and
    date say nothing about its content)."""
    try:
        return sha256_of(path) == want
    except OSError:
        return False


def _set_aside(path):
    """Move a damaged file out of the way (to <name>.damaged), so that it is neither used nor counted. Returns the new
    name, or None when it could not be moved."""
    dest = path + ".damaged"
    try:
        os.replace(path, dest)
        return dest
    except OSError:
        return None


def join_model_pieces(check_only):
    """Put the model files together from their downloaded pieces ('Name.onnx.part1of8' ...) into VocalRemover/models,
    checking each against its published checksum. Pieces inside this folder are removed afterwards; pieces in Downloads
    are left for the person to delete. Returns the names of the models now present (each checked by its checksum)."""
    models_dir = os.path.join(VR_DIR, "models")
    present = []
    downloads = os.path.join(PF.home_dir() or "", "Downloads")
    if not check_only:
        try:                                                     # leftovers of a join that was cut off
            for n in os.listdir(models_dir):
                if n.endswith(".joining"):
                    try:
                        os.remove(os.path.join(models_dir, n))
                    except OSError:
                        pass
        except OSError:
            pass
    for name, want in VR_MODELS.items():
        target = os.path.join(models_dir, name)
        if os.path.isfile(target):
            if _model_ok(target, want):
                present.append(name)
                continue
            if check_only:
                info("The model file VocalRemover%smodels%s%s is not the published one (its checksum differs): it is damaged "
                     "or from another version, and is not used. The installer (without Check) moves it aside to %s.damaged "
                     "and joins it again from the downloaded pieces." % (os.sep, os.sep, name, name))
                continue
            moved = _set_aside(target)
            if moved:
                info("The model file %s was not the published one (its checksum differs); it was moved aside to %s and is "
                     "not used." % (name, os.path.basename(moved)))
            else:
                todo("The model file VocalRemover%smodels%s%s is not the published one (its checksum differs) and could not be "
                     "moved aside. Delete it, put its pieces in Downloads and run the installer again." % (os.sep, os.sep, name))
                continue
        other = os.path.join(KIT, "models", name)
        if os.path.isfile(other) and _model_ok(other, want):
            present.append(name)      # RapReaper's own copy (earlier versions kept Kim Vocal 2 there)
            continue
        cap = VR_PIECES.get(name, VR_PIECES_MAX)
        exact = VR_PIECES.get(name)                     # a known model comes in exactly this many pieces
        sets = {}                                       # pieces by their "of N": one stray name cannot block the real set
        for d in _piece_dirs():
            try:
                names = os.listdir(d)
            except OSError:
                continue
            for n in names:
                low = n.lower()
                if not low.startswith(name.lower() + ".part"):
                    continue
                m = re.search(r"\.part([0-9]{1,3})of([0-9]{1,3})$", low)
                if not m:
                    continue
                idx, of = int(m.group(1)), int(m.group(2))
                if of > cap or idx < 1 or of < 1 or idx > of or (exact and of != exact):
                    continue
                if not _regular_file(os.path.join(d, n)):
                    continue                            # a link, pipe or device is never read
                sets.setdefault(of, {}).setdefault(idx, os.path.join(d, n))
        if not sets:
            continue
        total = max(sets, key=lambda k: (len(sets[k]) == k, len(sets[k])))
        pieces = sets[total]
        missing = [i for i in range(1, total + 1) if i not in pieces]
        if missing:
            todo("The model %s is incomplete: pieces %s of %d are missing. Download them again into Downloads (or this folder) and run "
                 "the installer again." % (name, ", ".join(str(i) for i in missing), total))
            continue
        size_all = sum(os.path.getsize(pieces[i]) for i in range(1, total + 1))
        if size_all != VR_SIZES.get(name, size_all) or size_all > VR_MAX_BYTES:
            todo("The pieces of %s add up to %d bytes, not the published %s: they are damaged or from another version. "
                 "Download them again." % (name, size_all, "{:,}".format(VR_SIZES[name]) if name in VR_SIZES else "size"))
            continue
        if check_only:
            info("%s: %d pieces found, would be joined into VocalRemover%smodels" % (name, total, os.sep))
            WOULD_JOIN.add(name)
            continue
        tmp = target + ".joining"
        h = hashlib.sha256()
        try:
            os.makedirs(models_dir, exist_ok=True)
            if os.path.lexists(tmp):
                os.remove(tmp)                          # (removes a link itself, never what it points to)
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0), 0o644)
            with os.fdopen(fd, "wb") as out:
                written = 0
                for i in range(1, total + 1):
                    with open(pieces[i], "rb") as f:
                        while written <= size_all:      # never more than the pieces' sizes said (a file that grows)
                            b = f.read(4 * 1024 * 1024)
                            if not b:
                                break
                            written += len(b)
                            h.update(b)
                            out.write(b)
                out.flush()
                os.fsync(out.fileno())
            if h.hexdigest() != want:
                os.remove(tmp)
                todo("The pieces of %s are damaged or from another version (the joined file's checksum is wrong). Download them again." % name)
                continue
            os.replace(tmp, target)
        except OSError as e:
            try:
                os.remove(tmp)
            except OSError:
                pass
            todo("Could not join %s into VocalRemover%smodels: %s. Check that the folder is not read-only and that the disk "
                 "has room, then run the installer again." % (name, os.sep, e))
            continue
        ok("%s joined from %d pieces into VocalRemover%smodels (checksum verified)" % (name, total, os.sep))
        present.append(name)
        for i in range(1, total + 1):
            if os.path.dirname(pieces[i]) != downloads:
                try:
                    os.remove(pieces[i])
                except OSError:
                    pass
    return present


def _note_missing_model(needed, present):
    needed = [n for n in needed if n not in WOULD_JOIN]
    if needed:
        info("OPTIONAL, NOT DONE YET: the Vocal Remover's model file %s is not here. Double-click 'Get Vocal Remover Model%s': "
             "it downloads the model's two source files from their authors and builds it (or put its pieces, %s.part1of.. "
             "and so on, into your Downloads folder%s and run the installer again; it joins them). Until then separate_song "
             "and learn_song_effects cannot separate a song%s. Everything else works."
             % (needed[0], PF.LAUNCHER_EXT, needed[0], "" if PF.IS_WINDOWS else " (or into this folder)",
                "" if "Kim_Vocal_2.onnx" not in present else " with the new network (the older Kim Vocal 2 model is still used)"))


class SettingsUnreadable(Exception):
    pass


def _settings_path(kit):
    return os.path.join(kit, "state", "settings.json")


def _settings_problem(kit):
    """None when kit/state/settings.json may be changed (it is missing, empty, or a JSON object RapReaper can read);
    otherwise why not. A settings file that cannot be read is never replaced: it holds the programs the person allowed
    and their choices, and writing a new one would lose them without a word."""
    path = _settings_path(kit)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as e:
        return "it could not be read (%s)" % e
    if not raw.strip():
        return None
    try:
        data = json.loads(raw.decode("utf-8-sig"))      # (Notepad may add a byte-order mark)
    except (UnicodeDecodeError, ValueError) as e:
        return "it is not valid JSON text (%s)" % e
    if not isinstance(data, dict):
        return "it does not hold a JSON object"
    return None


def _settings_refused(kit, why):
    todo("RapReaper's settings file (%s) %s. It was left exactly as it is, because writing a new one would lose the "
         "programs you allowed and your choices kept in it. Open it in Notepad and fix it (or delete it: RapReaper then "
         "forgets those permissions and choices), and run this again." % (_settings_path(kit), why))


def _read_settings(kit):
    """The settings as a dict; SettingsUnreadable when the file is there but cannot be read as a JSON object."""
    why = _settings_problem(kit)
    if why:
        raise SettingsUnreadable(why)
    try:
        with open(_settings_path(kit), "r", encoding="utf-8-sig") as f:      # (Notepad may add a byte-order mark)
            text = f.read()
        return json.loads(text) if text.strip() else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        raise SettingsUnreadable(str(e))


def _write_settings(kit, data):
    os.makedirs(os.path.join(kit, "state"), exist_ok=True)
    write_atomic(_settings_path(kit), json.dumps(data, indent=1) + "\n")


def _disallow_vocal_remover(path):
    """Take a Vocal Remover program out of settings.json when it is the one named there (it cannot run on this computer)."""
    kit = os.path.abspath(os.environ.get("RAPREAPER_HOME") or KIT)
    try:
        current = _read_settings(kit)
    except SettingsUnreadable:
        return
    if current.get("vocal_remover_exe") == path:
        current.pop("vocal_remover_exe", None)
        current.pop("vocal_remover_sha256", None)
        try:
            _write_settings(kit, current)
        except OSError:
            pass


VR_VERIFIED = {}     # set only when THIS run checked the Vocal Remover program against the manifest and allowed that very checksum


def setup_vocal_remover(check_only):
    """The Vocal Remover shipped in this folder: its files checked against the manifest, the program allowed for RapReaper
    (path + checksum in kit/state/settings.json), its model pieces joined, and on a Mac its files released from the
    download quarantine. Nothing here is a problem that stops the install: the Vocal Remover is an optional part.
    The program is allowed (and on a Mac released) only when the manifest lists it AND its runtime library, and both
    files on disk match those checksums."""
    step(7, "The Vocal Remover (instrumentals and vocals from finished songs)")
    VR_VERIFIED.clear()
    # the model files first: RapReaper's own ONNX Runtime can use them even without the program
    present = join_model_pieces(check_only)
    present += build_models_from_checkpoints(check_only, present)
    needed = [n for n in VR_NEEDED if n not in present]
    extra = [n for n in present if n not in VR_NEEDED]
    if present:
        ok("model files present (checksums verified): %s" % ", ".join(present))
    manifest = _manifest()
    rel = os.path.relpath(VR_PROGRAM, HERE)
    prog = os.path.basename(VR_PROGRAM)
    if not os.path.isdir(VR_DIR) or not os.path.isfile(VR_PROGRAM):
        info("the program file %s is not in this package: separation runs through RapReaper's own ONNX Runtime" % rel)
        _note_missing_model(needed, present)
        return
    held = " and was left in the download quarantine" if PF.IS_MAC else ""
    if not manifest:
        todo("This folder has no MANIFEST.sha256 (the list of every file RapReaper ships, with its checksum), so the Vocal "
             "Remover program cannot be checked. It was not allowed for RapReaper%s. Unzip the whole package again and run "
             "the installer from the unzipped folder." % held)
        _note_missing_model(needed, present)
        return
    lib = os.path.join(VR_DIR, "libonnxruntime.dylib" if PF.IS_MAC else "onnxruntime.dll")
    lib_rel = os.path.relpath(lib, HERE)
    for r, label in ((rel, "VocalRemover%s%s" % (os.sep, prog)), (lib_rel, "The Vocal Remover's runtime library %s" % os.path.basename(lib))):
        if not manifest.get(r):
            todo("%s is not listed in this folder's MANIFEST.sha256, so it cannot be checked; the Vocal Remover program was "
                 "not allowed for RapReaper%s. Unzip the whole package again." % (label, held))
            _note_missing_model(needed, present)
            return
    if not os.path.isfile(lib):
        todo("The Vocal Remover's runtime library (%s) is missing from the VocalRemover folder. Unzip the package again." % os.path.basename(lib))
        return
    try:
        digest = sha256_of(VR_PROGRAM)
        lib_digest = sha256_of(lib)
    except OSError as e:
        todo("The Vocal Remover's files could not be read (%s). Unzip the package again." % e)
        return
    if manifest[rel] != digest:
        todo("VocalRemover%s%s is not the file RapReaper shipped (its checksum differs from the manifest). It was not allowed "
             "for RapReaper%s. Unzip the package again." % (os.sep, prog, held))
        return
    if manifest[lib_rel] != lib_digest:
        todo("The Vocal Remover's runtime library %s is not the file RapReaper shipped. The Vocal Remover program was not "
             "allowed for RapReaper%s. Unzip the package again." % (os.path.basename(lib), held))
        return
    _note_missing_model(needed, present)
    if PF.IS_MAC:
        arch, chip = macho_arch(VR_PROGRAM), mac_chip()
        if arch == "arm64" and chip == "x86_64":
            info("This package's Vocal Remover program is for Macs with an Apple chip and cannot run on this Intel Mac, so it is "
                 "not allowed. RapReaper separates songs with its own ONNX Runtime instead (Python 3.12 or 3.13 on an Intel Mac). "
                 "For the Vocal Remover program, download RapReaper-%s-Mac-Intel.zip and update from it." % VERSION)
            if not check_only:
                _disallow_vocal_remover(VR_PROGRAM)
            return
        if arch == "x86_64" and chip == "arm64":
            info("This package's Vocal Remover is the Intel Mac version: it runs on this Mac through Rosetta, more slowly. "
                 "RapReaper-%s-Mac.zip has the version for Apple chips." % VERSION)
    if check_only:
        info("would allow %s for RapReaper (its checksum %s...)" % (prog, digest[:12]))
        return
    kit = os.path.abspath(os.environ.get("RAPREAPER_HOME") or KIT)
    try:
        current = _read_settings(kit)
        if current.get("vocal_remover_exe") != VR_PROGRAM or str(current.get("vocal_remover_sha256") or "").lower() != digest:
            current["vocal_remover_exe"], current["vocal_remover_sha256"] = VR_PROGRAM, digest
            _write_settings(kit, current)
            ok("the Vocal Remover program is allowed for RapReaper (its checksum %s... is kept in kit%sstate%ssettings.json)" % (digest[:12], os.sep, os.sep))
        else:
            ok("the Vocal Remover program is allowed for RapReaper")
        VR_VERIFIED.update(path=VR_PROGRAM, sha256=digest)
    except SettingsUnreadable as e:
        _settings_refused(kit, str(e))
    except OSError as e:
        todo("The Vocal Remover could not be allowed for RapReaper: %s. Run 'Allow Vocal Remover%s' with its path." % (e, PF.LAUNCHER_EXT))
    if PF.IS_MAC:
        released = []
        for name in (prog, os.path.basename(lib), "Vocal Remover.command"):
            path = os.path.join(VR_DIR, name)
            if not os.path.isfile(path):
                continue
            r = os.path.relpath(path, HERE)
            try:
                same = bool(manifest.get(r)) and manifest.get(r) == sha256_of(path)
            except OSError:
                same = False
            if not same:
                info("%s is not listed in the manifest with this checksum (not the file RapReaper shipped): left in quarantine" % name)
                continue
            try:
                os.chmod(path, 0o755)
                if sys.platform == "darwin":
                    subprocess.run(["xattr", "-d", "com.apple.quarantine", path], capture_output=True, timeout=30)
                released.append(name)
            except (OSError, subprocess.SubprocessError):
                pass
        if released:
            ok("released from the download quarantine (checksums verified): %s" % ", ".join(released))
    if extra:
        info("extra modes available with: %s" % ", ".join(extra))


def offer_gpu_test(check_only, interactive):
    """After an install: offer the Vocal Remover's graphics-processor test (a few minutes). Its result is kept by the Vocal
    Remover itself (settings.json next to it); RapReaper follows that setting when it starts the program. Offered only
    for the program this very run checked against the manifest and allowed, with the published main model in place,
    and the program is checked once more right before it is started."""
    if check_only or not interactive or not sys.stdin or not sys.stdin.isatty():
        return
    want = VR_VERIFIED.get("sha256")
    if not want or VR_VERIFIED.get("path") != VR_PROGRAM or not os.path.isfile(VR_PROGRAM):
        return
    model = os.path.join(VR_DIR, "models", VR_NEEDED[0])
    if not os.path.isfile(model) or not _model_ok(model, VR_MODELS[VR_NEEDED[0]]):
        return
    vr_settings = {}
    try:
        with open(os.path.join(VR_DIR, "settings.json"), encoding="utf-8") as f:
            vr_settings = json.load(f)
    except (OSError, ValueError):
        pass
    if not isinstance(vr_settings, dict):
        vr_settings = {}
    if vr_settings.get("gpu_tested"):
        info("the Vocal Remover's graphics test last ran %s: %s" % (vr_settings.get("gpu_tested"), vr_settings.get("gpu_note") or "no verdict"))
        return
    say()
    say("The Vocal Remover can try your graphics processor (%s). The test runs one 8-second window through the network on the"
        % ("DirectML" if PF.IS_WINDOWS else "CoreML"))
    say("processor and on the graphics processor and compares them; it takes a few minutes and switches the graphics processor")
    say("on only if it works and is faster. You can also run it later from the Vocal Remover's window.")
    try:
        answer = input("Run the graphics test now? [y/N] ").strip().lower()
    except EOFError:
        return
    if answer not in ("y", "yes"):
        return
    try:
        now = sha256_of(VR_PROGRAM)
    except OSError:
        now = None
    if now != want:
        info("the Vocal Remover program changed since it was checked a moment ago, so the test was not started. Unzip the "
             "package again and run the installer again.")
        return
    try:
        r = subprocess.run([VR_PROGRAM, "--gpu-test"], cwd=VR_DIR, capture_output=True, text=True, timeout=1800, stdin=subprocess.DEVNULL)
        for line in (r.stdout or "").splitlines()[-12:]:
            say("    " + line)
        if r.returncode != 0:
            info("the test did not finish: %s" % (r.stderr or r.stdout or "").strip()[-300:])
    except (OSError, subprocess.SubprocessError) as e:
        info("the test could not be started: %s" % e)


def set_gpu(value):
    """--gpu on|off: RapReaper's own separation (and the Vocal Remover when RapReaper starts it) asks for the graphics processor."""
    sys.path.insert(0, KIT)
    import rapreaper_separate as SEP
    kit = os.path.abspath(os.environ.get("RAPREAPER_HOME") or KIT)
    want = str(value).strip().lower() in ("on", "true", "yes", "1")
    say("RapReaper %s: graphics processor for vocal separation %s" % (VERSION, "ON" if want else "OFF"))
    try:
        s = _read_settings(kit)
        s["gpu"] = want
        _write_settings(kit, s)
    except SettingsUnreadable as e:
        _settings_refused(kit, str(e))
        return finish()
    except OSError as e:
        todo("The setting could not be saved in %s: %s." % (_settings_path(kit), e))
        return finish()
    ok("saved in kit\\state\\settings.json")
    if want:
        provs = SEP.gpu_providers()
        if provs:
            ok("ONNX Runtime here has the graphics provider %s" % ", ".join(provs))
        elif PF.IS_WINDOWS:
            info("RapReaper's own ONNX Runtime has no DirectML provider (the package onnxruntime-directml would add it); the Vocal "
                 "Remover program, which RapReaper prefers when it is allowed, has its own graphics support and will be asked for it")
        else:
            info("the ONNX Runtime package here has no CoreML provider; the Vocal Remover program will be asked for the graphics processor")
    return 0


def set_privacy(level, activity_log, expire_days=None):
    """--privacy LEVEL / --activity-log on|off: the person's own choices, made on this PC (a chat can raise the level and
    switch the record on, never the reverse)."""
    kit = os.path.abspath(os.environ.get("RAPREAPER_HOME") or KIT)
    changes = {}
    if level is not None:
        level = str(level).strip().lower()
        if level not in ("standard", "strict", "maximum"):
            todo("--privacy takes standard, strict or maximum.")
            return finish()
        changes["privacy"] = level
    if activity_log is not None:
        changes["activity_log"] = str(activity_log).strip().lower() in ("on", "true", "yes", "1")
    if expire_days is not None:
        if not (0 <= int(expire_days) <= 3650):
            todo("--expire-days takes 0 (never) to 3650.")
            return finish()
        changes["expire_days"] = int(expire_days)
    say("RapReaper %s: privacy settings %s" % (VERSION, ", ".join("%s = %s" % kv for kv in changes.items())))
    try:
        s = _read_settings(kit)
        s.update(changes)
        _write_settings(kit, s)
    except SettingsUnreadable as e:
        _settings_refused(kit, str(e))
        return finish()
    except OSError as e:
        todo("The setting could not be saved in %s: %s." % (_settings_path(kit), e))
        return finish()
    ok("saved in kit\\state\\settings.json; RapReaper uses it from its next answer on")
    return 0


def _manifest(folder=None):
    """{relative path: sha256} from MANIFEST.sha256 (the build writes it), or {} when there is none."""
    path = os.path.join(folder or HERE, "MANIFEST.sha256")
    out = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n").rstrip("\r")
                if not line or line.startswith("#"):
                    continue
                digest, _sp, rel = line.partition("  ")
                if len(digest) == 64 and rel:
                    out[rel.replace("/", os.sep)] = digest.lower()
    except (OSError, UnicodeDecodeError):
        pass
    return out


def release_launchers():
    """macOS keeps a 'downloaded from the internet' mark (quarantine) on every file that came out of the zip, and refuses
    to open a marked .command file by double-click until the person allows it in System Settings. The person has just
    done that for the installer (or started it from Terminal). The other double-click files in this folder are
    released here too, each only after its checksum has been found in the shipped manifest, so that a file that is
    not the one RapReaper shipped is never released. Without a manifest nothing is released. Returns the files released."""
    done = []
    manifest = _manifest()
    if not manifest:
        info("This folder has no MANIFEST.sha256 (the list of the files RapReaper ships, with their checksums), so the other "
             "double-click files could not be checked and were left as they are: macOS will ask about each the first time.")
        return done
    for name in sorted(os.listdir(HERE)):
        if not name.endswith(".command"):
            continue
        path = os.path.join(HERE, name)
        try:
            if manifest.get(name) != sha256_of(path):
                info("%s is not listed in the manifest with this checksum (not the file RapReaper shipped): left as it is." % name)
                continue
            os.chmod(path, 0o755)
            if sys.platform == "darwin":
                subprocess.run(["xattr", "-d", "com.apple.quarantine", path], capture_output=True, timeout=30)
            done.append(name)
        except (OSError, subprocess.SubprocessError):
            pass
    if done:
        ok("the double-click files (%s) can be opened by double-click from now on" % ", ".join(done))
    return done


# --------------------------------------------------------------------------------------------
# updating from a newer zip
# --------------------------------------------------------------------------------------------

KEEP_ON_UPDATE = ("kit/state", "kit/chains/user", "kit/references", "kit/exports", "kit/captures", "kit/models", "kit/logs",
                  "kit/imports",
                  "kit/venv", "tools", "updates", "VocalRemover/models", "VocalRemover/Output", "VocalRemover/settings.json",
                  "kit/reaper-daemon/bridge/bridge_config.json", "kit/reaper-daemon/bridge/heartbeat.json",
                  "kit/reaper-daemon/inbox", "kit/reaper-daemon/outbox", "kit/reaper-daemon/processing",
                  "kit/reaper-daemon/archive", "kit/reaper-daemon/failed", "kit/reaper-daemon/logs")
UPDATE_ZIP_RE = re.compile(r"^rapreaper-([0-9]+(?:\.[0-9]+){0,3})-(windows|mac)((?<=mac)-intel)?(-full)?\.zip$", re.I | re.A)
UPDATE_STATE = {}        # what an update in this run has done so far (for the words after an unexpected stop)
ZIP_DAMAGE = (zlib.error, zipfile.BadZipFile, EOFError, RuntimeError, NotImplementedError, OSError, ValueError)


def _norm_rel(rel):
    """A relative path in the one form two names that Windows or macOS take for the same file share: forward slashes,
    no '.' or doubled slashes, Unicode in one normal form, and case folded (both systems ignore case by default)."""
    r = unicodedata.normalize("NFC", str(rel).replace("\\", "/"))
    r = posixpath.normpath(r) if r else r
    return r.casefold()


_KEEP_NORM = tuple(_norm_rel(k) for k in KEEP_ON_UPDATE)


def _is_kept(rel):
    """True for a path inside the person's own files (KEEP_ON_UPDATE), however its letters are cased."""
    r = _norm_rel(rel)
    return any(r == k or r.startswith(k + "/") for k in _KEEP_NORM)


_WIN_DEVICES = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} | {"COM%d" % i for i in range(10)} | {"LPT%d" % i for i in range(10)}


def _unsafe_rel(rel):
    """Why a path listed in a manifest cannot be used inside this folder, or None when it can: it must be relative,
    without '..', drive letters or other ':' (Windows also reads that as a hidden 'stream' of a file), control
    characters, names that Windows shortens or trims to another name ('STATE~1', 'state.'), and it must stay inside."""
    if not isinstance(rel, str) or not rel:
        return "an empty name"
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in rel):
        return "a control character in the name"
    if ":" in rel:
        return "a ':' in the name"
    if rel.startswith(("/", "\\")):
        return "a path that starts at the top of the disk"
    if "\\" in rel:
        return "a backslash in the name (the manifest uses forward slashes)"
    parts = rel.replace("\\", "/").split("/")
    for part in parts:
        if part == "..":
            return "a '..' (a step out of the folder)"
        if part and part != "." and (part.endswith((".", " ")) or re.search(r"~\d", part)
                                     or part.split(".", 1)[0].strip().upper() in _WIN_DEVICES):
            return "a name Windows would read as another one"
    norm = posixpath.normpath(rel.replace("\\", "/"))
    if norm in (".", "") or norm.startswith("../") or norm == ".." or posixpath.isabs(norm):
        return "a path outside the folder"
    return None


def _inside_here(dest):
    """True when `dest`, with every link followed, is inside this folder (and is not the folder itself)."""
    try:
        here = os.path.realpath(HERE)
        real = os.path.realpath(dest)
        return real != here and os.path.commonpath([os.path.normcase(real), os.path.normcase(here)]) == os.path.normcase(here)
    except ValueError:
        return False


def _linked_parent(rel_os):
    """The first folder on the way from this folder to `rel_os` that is a link (writing through it would write
    elsewhere), or None."""
    p = HERE
    for comp in rel_os.split(os.sep)[:-1]:
        if not comp or comp == ".":
            continue
        p = os.path.join(p, comp)
        if os.path.islink(p):
            return p
        if not os.path.exists(p):
            break
    return None


def _vtuple(v):
    """'1.4.0' -> (1, 4, 0, 0); None for anything that is not a plain version number."""
    if not isinstance(v, str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+){0,3}", v.strip()):
        return None
    t = tuple(int(x) for x in v.strip().split("."))
    return t + (0,) * (4 - len(t))


def _this_system():
    return "mac" if PF.IS_MAC else "windows"


def mac_chip():
    """'arm64' on a Mac with an Apple chip, 'x86_64' on an Intel Mac, '' elsewhere. Asks the system itself, because a
    Python running under Rosetta reports x86_64 on an Apple-chip Mac. RAPREAPER_MAC_CHIP overrides it (tests)."""
    forced = os.environ.get("RAPREAPER_MAC_CHIP", "").strip().lower()
    if forced in ("arm64", "x86_64"):
        return forced
    if not PF.IS_MAC:
        return ""
    if sys.platform != "darwin":
        return "arm64"                  # a stand-in Mac (RAPREAPER_PLATFORM=mac in the tests): the usual Mac
    try:
        r = subprocess.run(["/usr/sbin/sysctl", "-n", "hw.optional.arm64"], capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip() == "1":
            return "arm64"
    except (OSError, subprocess.SubprocessError):
        pass
    return "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x86_64"


def mac_package_suffix():
    """The end of the zip name for this Mac: 'Mac' (Apple chip) or 'Mac-Intel'."""
    return "Mac-Intel" if mac_chip() == "x86_64" else "Mac"


def _wrong_mac_package(zip_name):
    """None when a Mac zip suits this Mac; else what it is for. Zips before 1.4.6 have no Intel version, so an Intel Mac
    still takes an older plain -Mac.zip (its Vocal Remover program is then not allowed: see setup_vocal_remover)."""
    m = UPDATE_ZIP_RE.match(zip_name)
    if not m or not PF.IS_MAC or m.group(2).lower() != "mac":
        return None
    intel_zip = bool(m.group(3))
    if intel_zip and mac_chip() == "arm64":
        return "for an Intel Mac"
    if not intel_zip and mac_chip() == "x86_64" and _vtuple(m.group(1)) >= (1, 4, 6):
        return "for a Mac with an Apple chip"
    return None


def _switches_mac_chip(zip_name):
    """True when a Mac zip of the installed version suits this Mac's chip while the Vocal Remover program in this folder
    is for the other chip: Update then takes it, so an Intel Mac that got the Apple-chip package (an update from 1.4.5,
    whose updater knew no Intel package) can move to the Intel one without --allow-same-or-older."""
    m = UPDATE_ZIP_RE.match(zip_name)
    if not m or not PF.IS_MAC or m.group(2).lower() != "mac" or _wrong_mac_package(zip_name):
        return False
    have = macho_arch(VR_PROGRAM)
    return have in ("arm64", "x86_64") and have != mac_chip()


MACHO_CPU = {0x01000007: "x86_64", 0x0100000C: "arm64"}


def macho_arch(path):
    """'arm64' or 'x86_64' for a Mac program file (Mach-O, 64-bit), 'universal', or '' when it is none of these."""
    try:
        with open(path, "rb") as f:
            head = f.read(8)
    except OSError:
        return ""
    if len(head) < 8:
        return ""
    if head[:4] == b"\xcf\xfa\xed\xfe":
        return MACHO_CPU.get(struct.unpack("<I", head[4:8])[0], "")
    if head[:4] in (b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"):
        return "universal"
    return ""


def _scan_update_zips(folder):
    """The RapReaper release zips for this system in `folder`: [(version tuple, full?, path)], plus the names of other
    zips that look like RapReaper's but are not used (the other system's, or not named like a release)."""
    found, unused = [], []
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return found, unused
    for name in names:
        low = name.lower()
        if not (low.startswith("rapreaper") and low.endswith(".zip")):
            continue
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            continue
        m = UPDATE_ZIP_RE.match(name)
        if not m:
            unused.append("%s (not named like a RapReaper release: RapReaper-<version>-%s.zip)" % (name, mac_package_suffix() if PF.IS_MAC else "Windows"))
        elif m.group(2).lower() != _this_system() or (m.group(3) and not PF.IS_MAC):
            unused.append("%s (for %s)" % (name, "Windows" if m.group(2).lower() == "windows" else "a Mac"))
        else:
            wrong = _wrong_mac_package(name)
            if wrong:
                unused.append("%s (%s)" % (name, wrong))
            else:
                found.append((_vtuple(m.group(1)), bool(m.group(4)), path))
    return found, unused


def find_update_zip():
    """The RapReaper release zip for this system with the highest version in Downloads, or None (and the others)."""
    downloads = os.path.join(PF.home_dir() or os.path.expanduser("~"), "Downloads")
    found, unused = _scan_update_zips(downloads)
    if not found:
        return None, [], unused
    found.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return found[0][2], [os.path.basename(p) for _v, _f, p in found[1:]], unused


ENTRY_MAX = 700 * 1024 * 1024       # no file of RapReaper is bigger (the full package's model is 237 MB)
ZIP_MAX_TOTAL = 2 * 1024 * 1024 * 1024
MANIFEST_MAX = 4 * 1024 * 1024


def _zip_sizes_ok(zf):
    """None when the sizes the zip declares are those of a RapReaper package; else why not (a 'zip bomb' that would
    unpack to gigabytes is refused before anything is read)."""
    total = 0
    for info in zf.infolist():
        if info.file_size > ENTRY_MAX:
            return "%s would unpack to %d MB" % (info.filename.split("/", 1)[-1], info.file_size // 1048576)
        if info.filename.endswith("MANIFEST.sha256") and info.file_size > MANIFEST_MAX:
            return "its list of checksums would unpack to %d MB" % (info.file_size // 1048576)
        total += info.file_size
    if total > ZIP_MAX_TOTAL:
        return "it would unpack to %d MB" % (total // 1048576)
    return None


def _read_entry(zf, name, limit=None):
    """An entry's bytes (never more than the sizes RapReaper's files have); KeyError when there is no such entry;
    _Damaged when it cannot be unpacked or would unpack to more than it says."""
    cap = ENTRY_MAX if limit is None else limit
    try:
        with zf.open(name) as f:
            data = f.read(cap + 1)
        if limit is None and len(data) > cap:
            raise _Damaged("%s: larger than any file of RapReaper" % name.split("/", 1)[-1])
        return data if limit is None else data[:limit]
    except KeyError:
        raise
    except ZIP_DAMAGE as e:
        raise _Damaged("%s: %s" % (name.split("/", 1)[-1], e))


class _Damaged(Exception):
    pass


def _entry_sha256(zf, name):
    h = hashlib.sha256()
    try:
        with zf.open(name) as f:
            for chunk in iter(lambda: f.read(4 * 1024 * 1024), b""):
                h.update(chunk)
    except KeyError:
        raise
    except ZIP_DAMAGE as e:
        raise _Damaged("%s: %s" % (name.split("/", 1)[-1], e))
    return h.hexdigest()


def _zip_version(zf, top):
    try:
        text = _read_entry(zf, top + "rapreaper_install.py").decode("utf-8", "replace")
    except (KeyError, _Damaged):
        return None
    m = re.search(r'^VERSION(?:\s*:\s*str)?\s*=\s*["\']([^"\'\n]+)["\']', text, re.M)
    return m.group(1).strip() if m else None


def _parse_manifest(man_text):
    manifest = {}
    for line in man_text.splitlines():
        line = line.rstrip("\r")
        if not line or line.startswith("#"):
            continue
        digest, _sp, rel = line.partition("  ")
        if len(digest) == 64 and rel and re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            manifest[rel] = digest.lower()
    return manifest


def _check_zip(zf, top):
    """A RapReaper zip checked against its own manifest. Returns (problems, damaged, manifest, manifest text):
    problems are files that differ, are missing, are not listed, or have names that cannot be used safely; damaged
    are entries that cannot be unpacked at all."""
    problems, damaged = [], []
    try:
        too_big = _zip_sizes_ok(zf)
        if too_big:
            raise _Damaged("the zip is not a RapReaper package: %s" % too_big)
        man_text = _read_entry(zf, top + "MANIFEST.sha256").decode("utf-8", "replace")
    except KeyError:
        return ["no MANIFEST.sha256"], [], {}, None
    except _Damaged as e:
        return [], [str(e)], {}, None
    manifest = _parse_manifest(man_text)
    seen = {}
    for rel in manifest:
        why = _unsafe_rel(rel)
        if why:
            problems.append("%r (%s)" % (rel, why))
            continue
        key = _norm_rel(rel)
        if key in seen:
            problems.append("%s and %s (the same file to Windows and macOS)" % (seen[key], rel))
        seen[key] = rel
    if problems:
        return problems, damaged, manifest, man_text
    for rel, digest in manifest.items():
        try:
            if _entry_sha256(zf, top + rel) != digest:
                problems.append(rel)
        except KeyError:
            problems.append(rel + " (missing)")
        except _Damaged as e:
            damaged.append(str(e))
    for n in zf.namelist():
        if n.endswith("/"):
            continue
        if not n.startswith(top):
            problems.append("a file outside the RapReaper folder: %r" % n)
        elif n[len(top):] not in manifest and n[len(top):] != "MANIFEST.sha256":
            problems.append("unlisted file " + n[len(top):])
    return problems, damaged, manifest, man_text


def _zip_top(zf):
    names = zf.namelist()
    tops = {n.split("/", 1)[0] for n in names if "/" in n}
    return names, tops


def _sums_lists(zip_path, version):
    """[(list file name, sha256 it gives for this zip)] from a SHA256SUMS.txt or 'RapReaper <ver> - SHA256SUMS.txt' next
    to the zip that names it."""
    folder, base = os.path.dirname(zip_path), os.path.basename(zip_path)
    out = []
    for cand in ("SHA256SUMS.txt", "RapReaper %s - SHA256SUMS.txt" % version):
        p = os.path.join(folder, cand)
        try:
            if not os.path.isfile(p) or os.path.getsize(p) > 1024 * 1024:
                continue
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
        except OSError:
            continue
        for line in lines:
            m = re.match(r"^\s*([0-9a-fA-F]{64})\s+\*?(.+?)\s*$", line)
            if m and os.path.basename(m.group(2).replace("\\", "/")).lower() == base.lower():
                out.append((cand, m.group(1).lower()))
                break
    return out


def _interactive():
    try:
        return bool(sys.stdin) and sys.stdin.isatty() and not os.environ.get("RAPREAPER_UNATTENDED")
    except (OSError, ValueError):
        return False


def update_from_zip(zip_path, check_only=False, allow_older=False, others=()):
    """Replace RapReaper's program files with those in `zip_path`, keeping everything that is the person's own
    (KEEP_ON_UPDATE). Before a single file is written: the zip's name, system and version are checked (a version that
    is not newer is refused unless allow_older), every file in it is checked against the zip's own manifest, every path
    in it is checked to stay inside this folder, a checksum list next to the zip (if there is one) is compared, the
    person is asked (when there is someone at the keyboard), and every file to be replaced is checked to be writable.
    The new files are then written next to their targets, the old ones copied to updates/backup-<old version>-<stamp>/,
    and only then put in place; if one cannot be put in place, those already replaced are put back. Returns the new
    version, or None when nothing was done."""
    UPDATE_STATE.clear()
    say("RapReaper %s: update from %s" % (VERSION, zip_path))
    for name in others:
        info("also in Downloads, not used%s: %s" % ("" if "(" in name else " (the zip with the highest version is used)", name))
    base = os.path.basename(zip_path)
    if not os.path.isfile(zip_path):
        todo("There is no file at %s." % zip_path)
        return None
    m = UPDATE_ZIP_RE.match(base)
    if not m:
        todo("%s is not named like a RapReaper release zip (RapReaper-<version>-%s.zip, or the same ending in -full.zip). "
             "Nothing was changed. Use the zip under the name it was delivered with (if your browser "
             "added something like ' (1)' to the name, take that out)." % (base, "Mac" if PF.IS_MAC else "Windows"))
        return None
    if m.group(2).lower() != _this_system() or (m.group(3) and not PF.IS_MAC):
        todo("That is the %s zip of RapReaper. Download the %s zip (its name ends in -%s.zip)."
             % (("Windows", "Mac", mac_package_suffix()) if PF.IS_MAC else ("Mac", "Windows", "Windows")))
        return None
    wrong = _wrong_mac_package(base)
    if wrong:
        todo("That zip is %s. Download RapReaper-<version>-%s.zip, the one for this Mac." % (wrong, mac_package_suffix()))
        return None
    name_version = m.group(1)
    try:
        zf = zipfile.ZipFile(zip_path)
    except (ZIP_DAMAGE + (zipfile.LargeZipFile,)) as e:
        todo("That is not a zip file RapReaper can read (%s). Nothing was changed. Download it again." % e)
        return None
    with zf:
        names, tops = _zip_top(zf)
        if len(tops) != 1:
            todo("The zip does not hold one RapReaper folder (it holds: %s)." % ", ".join(sorted(tops)[:5]))
            return None
        top = sorted(tops)[0] + "/"
        if top + "rapreaper_install.py" not in names or top + "kit/rapreaper_server.py" not in names:
            todo("The zip does not hold RapReaper's files (no rapreaper_install.py and kit/rapreaper_server.py).")
            return None
        # the system is told by the double-click files at the top of the folder (the Vocal Remover's own
        # "Vocal Remover.command" sits one level down and says nothing about the zip)
        top_files = [n[len(top):] for n in names if n.startswith(top) and "/" not in n[len(top):]]
        has_bat = any(n.endswith(".bat") for n in top_files)
        has_cmd = any(n.endswith(".command") for n in top_files)
        if PF.IS_MAC and has_bat and not has_cmd:
            todo("That is the Windows zip of RapReaper. Download the Mac zip (its name ends in -%s.zip)." % mac_package_suffix())
            return None
        if PF.IS_WINDOWS and has_cmd and not has_bat:
            todo("That is the Mac zip of RapReaper. Download the Windows zip (its name ends in -Windows.zip).")
            return None
        # every file in the zip against the zip's own manifest: a download that broke off, or a zip that was changed
        # on the way, does not get installed; nor does one with a path that would lead outside this folder
        problems, damaged, manifest, man_text = _check_zip(zf, top)
        if damaged:
            todo("The zip is damaged: %s cannot be unpacked (%s). Nothing was changed. Download it again and try once more."
                 % (damaged[0].split(":", 1)[0], damaged[0].split(":", 1)[-1].strip()))
            return None
        if man_text is None:
            todo("The zip has no MANIFEST.sha256, so its files cannot be checked. Only zips built for RapReaper 1.4.0 or newer "
                 "can be used for an update; for an older one, unzip it over this folder by hand and run Install.")
            return None
        if problems:
            todo("The zip is damaged or has been changed (%s). Nothing was changed. Download it again and try once more."
                 % ", ".join(problems[:6]))
            return None
        # its version (read from its own installer, which the manifest has just vouched for), before anything is written
        new_version = _zip_version(zf, top)
        if not new_version or not _vtuple(new_version):
            todo("The version of the RapReaper in %s could not be read (its rapreaper_install.py has no VERSION line "
                 "RapReaper understands). Nothing was changed. Get the zip again from where RapReaper was delivered to you." % base)
            return None
        if _vtuple(new_version) != _vtuple(name_version):
            todo("%s is named as RapReaper %s, but the files inside are RapReaper %s. Nothing was changed. Download the zip "
                 "again from where RapReaper was delivered to you." % (base, name_version, new_version))
            return None
        switch = _vtuple(new_version) == _vtuple(VERSION) and _switches_mac_chip(base)
        if switch:
            info("Same version, but the package for this Mac's chip (%s): its Vocal Remover replaces the one for the other "
                 "chip." % ("Intel" if mac_chip() == "x86_64" else "Apple chip"))
        if _vtuple(new_version) <= (_vtuple(VERSION) or (0,)) and not allow_older and not switch:
            todo("That zip holds RapReaper %s, and this folder already has RapReaper %s, so it is not an update. Nothing was "
                 "changed. To put that version on anyway (to repair this one, or to go back to an older one), run:  "
                 "%s rapreaper_install.py --update \"%s\" --allow-same-or-older"
                 % (new_version, VERSION, PY_CMD, zip_path))
            return None
        old_manifest = {rel.replace(os.sep, "/"): d for rel, d in _manifest().items()}
        bad_old = [rel for rel in old_manifest if _unsafe_rel(rel)]
        if bad_old:
            todo("This folder's own MANIFEST.sha256 lists a path that is not inside the folder (%s), so the update cannot tell "
                 "safely which old files to retire. Nothing was changed. Unzip the new RapReaper into a new folder instead, "
                 "and run its installer." % ", ".join(repr(r) for r in bad_old[:3]))
            return None
        try:
            zip_sha = sha256_of(zip_path)
            zip_date = time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(zip_path)))
        except OSError as e:
            todo("The zip could not be read (%s). Nothing was changed." % e)
            return None
        sums = _sums_lists(zip_path, new_version)
        for list_name, listed in sums:
            if listed != zip_sha:
                todo("%s does not match the checksum list next to it (%s gives %s..., the file is %s...). Nothing was changed. "
                     "Get the zip (and the list) again from where RapReaper was delivered to you." % (base, list_name, listed[:16], zip_sha[:16]))
                return None
        info("file:     %s" % base)
        info("version:  RapReaper %s (this folder has RapReaper %s)" % (new_version, VERSION))
        info("date:     %s" % zip_date)
        info("SHA-256:  %s" % zip_sha)
        if sums:
            ok("the zip's checksum matches the list next to it (%s)" % ", ".join(n for n, _d in sums))
        else:
            info("(no checksum list, SHA256SUMS.txt, lies next to the zip; compare the SHA-256 above with the list delivered with the zip if you like)")
        ok("the zip is whole: RapReaper %s, %d files, each matching the zip's own list of checksums" % (new_version, len(manifest)))
        info("That list shows that nothing in the zip broke off or was changed after it was made; it does not show who made it.")
        info("Use only zips delivered to you as RapReaper, and compare with the SHA256SUMS list that came with them.")
        if check_only:
            return new_version
        if _interactive():
            try:
                answer = input("Install this update? (y/n) ").strip().lower()
            except EOFError:
                answer = ""
            if answer not in ("y", "yes"):
                UPDATE_STATE["declined"] = True
                say("Nothing was changed.")
                return None
        return _apply_update(zf, top, manifest, man_text, old_manifest, new_version)


def _apply_update(zf, top, manifest, man_text, old_manifest, new_version):
    backup_dir = os.path.join(HERE, "updates", "backup-%s-%s" % (VERSION, STAMP))
    retired = [rel for rel in old_manifest if rel not in manifest and not _is_kept(rel) and rel != "MANIFEST.sha256"]
    # 1. what is to be written
    targets, kept, unchanged = [], 0, 0
    for rel, digest in sorted(manifest.items()):
        rel_os = os.path.normpath(rel.replace("/", os.sep))
        dest = os.path.join(HERE, rel_os)
        if _is_kept(rel):
            leaf = os.path.basename(rel_os)
            model_ok = (rel.replace("\\", "/") == "VocalRemover/models/" + leaf and leaf in VR_MODELS
                        and digest == VR_MODELS[leaf])
            # a placeholder only at the top of one of the person's folders (never "settings.json/.keep", which would
            # make a folder where the person's file belongs)
            keep_ok = leaf == ".keep" and any(_norm_rel(os.path.dirname(rel.replace("\\", "/"))) == _norm_rel(k) for k in KEEP_ON_UPDATE)
            if (keep_ok or model_ok) and not os.path.lexists(dest):
                targets.append({"rel": rel_os, "dest": dest, "entry": top + rel, "digest": digest, "existed": False})
            elif os.path.lexists(dest):
                kept += 1
            continue
        if os.path.isfile(dest) and not os.path.islink(dest):
            try:
                if sha256_of(dest) == digest:
                    unchanged += 1
                    continue
            except OSError:
                pass
        targets.append({"rel": rel_os, "dest": dest, "entry": top + rel, "digest": digest, "existed": os.path.lexists(dest)})
    man_bytes = man_text.encode("utf-8")
    try:
        with open(MANIFEST, "rb") as f:
            man_same = f.read() == man_bytes
    except OSError:
        man_same = False
    if not man_same:
        targets.append({"rel": "MANIFEST.sha256", "dest": MANIFEST, "data": man_bytes, "existed": os.path.lexists(MANIFEST)})
    # 2. every target checked before anything is written: inside this folder, not through a link, not a folder, and
    #    (for a file that is there) open for writing, so that a program holding it open is found now, not halfway
    blocked = []
    need = 0
    for t in targets:
        dest = t["dest"]
        if not _inside_here(dest):
            blocked.append("%s (leads outside the RapReaper folder)" % t["rel"])
            continue
        link = _linked_parent(t["rel"])
        if link:
            blocked.append("%s (its folder %s is a link to another place)" % (t["rel"], os.path.relpath(link, HERE)))
            continue
        if os.path.isdir(dest) and not os.path.islink(dest):
            blocked.append("%s (there is a folder of that name)" % t["rel"])
            continue
        if os.path.lexists(dest):
            try:
                with open(dest, "r+b"):
                    pass
            except OSError as e:
                blocked.append("%s (it cannot be changed: %s)" % (t["rel"], e.strerror or e))
                continue
            try:
                need += os.path.getsize(dest)
            except OSError:
                pass
        else:
            parent = os.path.dirname(dest)
            while not os.path.exists(parent) and parent != os.path.dirname(parent):
                parent = os.path.dirname(parent)
            if not os.path.isdir(parent) or not os.access(parent, os.W_OK | os.X_OK):
                blocked.append("%s (its folder cannot be made or written to)" % t["rel"])
                continue
        if "data" in t:
            need += len(t["data"])
        else:
            try:
                need += zf.getinfo(t["entry"]).file_size
            except KeyError:
                pass
    if blocked:
        todo("The update was not started, because %d file(s) in this folder cannot be replaced: %s. Nothing was changed. "
             "Close REAPER, Claude and the Vocal Remover (a program that is open can keep its files locked), clear "
             "'read-only' on the RapReaper folder if it is set, and run the update again."
             % (len(blocked), "; ".join(blocked[:5])))
        return None
    try:
        free = shutil.disk_usage(HERE).free
    except OSError:
        free = None
    if free is not None and free < need + 50 * 1024 * 1024:
        todo("The disk has %.0f MB free and the update needs about %.0f MB (the new files and the copies of the old ones). "
             "Nothing was changed. Make room and run the update again." % (free / 1e6, (need + 50 * 1024 * 1024) / 1e6))
        return None
    # 3. the new files written next to their targets (name.rapreaper-new), each checked against the manifest again
    made_dirs, staged = [], []

    def clean_staged():
        for t in staged:
            try:
                os.remove(t["tmp"])
            except OSError:
                pass
        for d in reversed(made_dirs):
            try:
                os.rmdir(d)
            except OSError:
                pass

    try:
        for t in targets:
            parent = os.path.dirname(t["dest"])
            todo_dirs = []
            p = parent
            while not os.path.exists(p):
                todo_dirs.append(p)
                p = os.path.dirname(p)
            for d in reversed(todo_dirs):
                os.mkdir(d)
                made_dirs.append(d)
            tmp = t["dest"] + ".rapreaper-new"
            if os.path.lexists(tmp):
                os.remove(tmp)
            t["tmp"] = tmp
            staged.append(t)
            h = hashlib.sha256()
            with open(tmp, "wb") as out:
                if "data" in t:
                    out.write(t["data"])
                else:
                    with zf.open(t["entry"]) as src:
                        for chunk in iter(lambda: src.read(4 * 1024 * 1024), b""):
                            h.update(chunk)
                            out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
            if "data" not in t and h.hexdigest() != t["digest"]:
                raise OSError("%s changed inside the zip while it was being read" % t["rel"])
            mode = None
            if t["existed"]:
                try:
                    mode = stat.S_IMODE(os.stat(t["dest"]).st_mode)
                except OSError:
                    pass
            elif t["rel"].endswith(".command"):
                mode = 0o755
            if mode is not None:
                os.chmod(tmp, mode)
    except (ZIP_DAMAGE + (KeyError,)) as e:
        clean_staged()
        todo("The new files could not be prepared (%s). Nothing was changed. Check that the disk has room and the RapReaper "
             "folder is not read-only, and run the update again." % e)
        return None
    # 4. a copy of every file that will be replaced or retired, in updates/backup-<old version>-<stamp>
    to_copy = [(t["rel"], t["dest"]) for t in targets if t["existed"]]
    to_copy += [(rel.replace("/", os.sep), os.path.join(HERE, rel.replace("/", os.sep))) for rel in retired
                if os.path.isfile(os.path.join(HERE, rel.replace("/", os.sep)))]
    try:
        for rel_os, src in to_copy:
            dst = os.path.join(backup_dir, rel_os)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
    except (OSError, shutil.Error) as e:
        clean_staged()
        shutil.rmtree(backup_dir, ignore_errors=True)
        todo("A copy of the old files could not be kept in %s (%s), so nothing was replaced: this folder is RapReaper %s "
             "as before. Check that the disk has room and the folder is not read-only, and run the update again."
             % (backup_dir, e, VERSION))
        return None
    if to_copy:
        UPDATE_STATE["backup_dir"] = backup_dir
    # 5. the new files put in place (the manifest last); if one cannot be, the ones already replaced are put back
    done = []
    fail_at = os.environ.get("RAPREAPER_TEST_FAIL_REPLACE")       # (the tests' stand-in for a file that is locked right now)
    try:
        for t in targets:
            if fail_at and t["rel"].replace(os.sep, "/") == fail_at:
                raise OSError("[test] the file is in use by another program")
            os.replace(t["tmp"], t["dest"])
            done.append(t)
    except BaseException as e:            # (an interruption, too: the files already replaced are put back first)
        failed_rel = t["rel"]
        not_back = []
        for d in reversed(done):
            try:
                if d["existed"]:
                    tmp = d["dest"] + ".rapreaper-restore"
                    shutil.copy2(os.path.join(backup_dir, d["rel"]), tmp)
                    os.replace(tmp, d["dest"])
                else:
                    os.remove(d["dest"])
            except (OSError, shutil.Error):
                not_back.append(d["rel"])
        clean_staged()
        if not not_back:
            todo("The update stopped while replacing %s (%s). It undid what it had done so far (%d file(s) put back from "
                 "the copies, %d new file(s) removed), so this folder is RapReaper %s as before. Close REAPER, Claude and "
                 "the Vocal Remover (a program that is open can keep its files locked) and run the update again."
                 % (failed_rel, e, sum(1 for d in done if d["existed"]), sum(1 for d in done if not d["existed"]), VERSION))
        else:
            todo("The update stopped while replacing %s (%s), and %d file(s) could not be put back: %s. The old versions of "
                 "the replaced files are in %s; copy them back into the same places in this folder, or close REAPER, Claude "
                 "and the Vocal Remover and run the update again (it puts everything in place)."
                 % (failed_rel, e, len(not_back), ", ".join(not_back[:6]), backup_dir))
        if not isinstance(e, OSError):
            raise
        return None
    # 6. program files of the old version that the new one no longer has: their copies are in the backup folder
    for rel in retired:
        src = os.path.join(HERE, rel.replace("/", os.sep))
        if os.path.isfile(src) and _inside_here(src):
            try:
                os.remove(src)
            except OSError:
                info("%s is no longer part of RapReaper but could not be removed; it does no harm." % rel)
    replaced = sum(1 for t in targets if t["existed"] and t["rel"] != "MANIFEST.sha256")
    added = sum(1 for t in targets if not t["existed"] and t["rel"] != "MANIFEST.sha256")
    ok("updated to RapReaper %s: %d files replaced, %d added, %d of your own left as they were%s"
       % (new_version, replaced, added, kept, ("; the old files are in %s" % backup_dir) if to_copy else ""))
    _remember(updated_from=VERSION, updated_on=time.strftime("%Y-%m-%d %H:%M:%S"))
    return new_version


# --------------------------------------------------------------------------------------------
# full packages: the zip with the Vocal Remover's model already inside, to carry to another computer
# --------------------------------------------------------------------------------------------

FULL_ZIP_RE = re.compile(r"^rapreaper-([0-9][0-9.]*)-(windows|mac|mac-intel)\.zip$", re.I)


def _zip_problems(zf, top):
    """What is wrong with a RapReaper zip ([] when every file matches its own manifest), and the manifest text."""
    problems, damaged, _manifest_, man_text = _check_zip(zf, top)
    return ["damaged entry " + d for d in damaged] + problems, man_text


def make_full_packages(zip_args):
    """Write '<zip>-full.zip' next to each RapReaper zip (Windows and Mac, from Downloads or next to this folder, or the
    zips given): the same files plus the Vocal Remover's model in VocalRemover/models, checked against its published
    checksum and listed in the package's manifest. Unzipping a full package gives a folder with everything in place."""
    say("RapReaper %s: make full packages (the zips with the Vocal Remover's model inside)" % VERSION)
    say()
    name = VR_NEEDED[0]
    want = VR_MODELS[name]
    model = os.path.join(VR_DIR, "models", name)
    if not os.path.isfile(model):
        join_model_pieces(False)          # from the pieces in Downloads, checked
    if not os.path.isfile(model):
        todo("The model %s is not in VocalRemover%smodels and its eight pieces were not found. Put the pieces "
             "(%s.part1of8 ... part8of8) into Downloads and run this again." % (name, os.sep, name))
        return finish()
    if sha256_of(model) != want:
        todo("The model file VocalRemover%smodels%s%s is not the published one (its checksum differs). Delete it, put the "
             "pieces into Downloads and run this again." % (os.sep, os.sep, name))
        return finish()
    ok("model %s checked against its published checksum" % name)
    zips = [os.path.abspath(os.path.expanduser(z)) for z in zip_args] if zip_args else []
    if not zips:
        best = {}
        folder = os.path.join(PF.home_dir() or "", "Downloads")       # (Downloads only, as Update)
        try:
            names = os.listdir(folder)
        except OSError:
            names = []
        for n in names:
            m = FULL_ZIP_RE.match(n)
            pth = os.path.join(folder, n)
            if not m or not _regular_file(pth):
                continue
            key, ver = m.group(2).lower(), _version_key(m.group(1))
            if ver is not None and (key not in best or ver > best[key][0]):
                best[key] = (ver, pth)                  # the highest version, not the newest file
        zips = [best[k][1] for k in ("windows", "mac", "mac-intel") if k in best]
    if not zips:
        todo("No RapReaper zip (RapReaper-<version>-Windows.zip or -Mac.zip) was found in Downloads or next to the RapReaper "
             "folder. Put them there and run this again.")
        return finish()
    arc_model = "VocalRemover/models/" + name
    for src in zips:
        base = os.path.basename(src)
        try:
            zf = zipfile.ZipFile(src)
        except (ZIP_DAMAGE + (zipfile.LargeZipFile,)) as e:
            todo("%s cannot be read as a zip (%s). Download it again." % (base, e))
            continue
        with zf:
            tops = {n.split("/", 1)[0] for n in zf.namelist() if "/" in n}
            if len(tops) != 1:
                todo("%s does not hold one RapReaper folder." % base)
                continue
            top = sorted(tops)[0] + "/"
            bad, man_text = _zip_problems(zf, top)
            if bad:
                todo("%s is damaged or has been changed (%s). Download it again." % (base, ", ".join(bad[:4])))
                continue
            out = os.path.splitext(src)[0] + "-full.zip"
            tmp = out + ".making"
            # a checksum list next to the zip must agree with it
            m_v = FULL_ZIP_RE.match(base)
            wrong = [lst for lst, digest in _sums_lists(src, m_v.group(1) if m_v else "") if digest != sha256_of(src)]
            if wrong:
                todo("%s does not match its checksum list (%s): it is not the zip that was delivered. Get it again." % (base, wrong[0]))
                continue
            # the package is written to a file of its own, never through a link planted under its name
            if os.path.islink(out) or (os.path.lexists(out) and not _regular_file(out)):
                todo("%s exists and is not a plain file (a link?); it was left alone. Delete it and run this again." % os.path.basename(out))
                continue
            try:
                if os.path.lexists(tmp):
                    if os.path.islink(tmp) or not _regular_file(tmp):
                        todo("%s exists and is not a plain file (a link?); it was left alone. Delete it and run this again."
                             % os.path.basename(tmp))
                        continue
                    os.remove(tmp)
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0), 0o644)
                os.close(fd)
            except OSError as e:
                todo("Could not start %s: %s" % (os.path.basename(out), e))
                continue
            lines = [l for l in man_text.splitlines() if not l.endswith("  " + arc_model)]
            lines.append("%s  %s" % (want, arc_model))
            try:
                with open(tmp, "r+b") as raw_out, zipfile.ZipFile(raw_out, "w", zipfile.ZIP_DEFLATED) as zo:
                    for zinfo in zf.infolist():
                        if zinfo.filename in (top + "MANIFEST.sha256", top + arc_model):
                            continue
                        zo.writestr(zinfo, _read_entry(zf, zinfo.filename))
                    mi = zipfile.ZipInfo(top + "MANIFEST.sha256", date_time=time.localtime()[:6])
                    mi.compress_type = zipfile.ZIP_DEFLATED
                    mi.external_attr = 0o644 << 16
                    zo.writestr(mi, "\n".join(sorted(lines, key=lambda l: l[66:])) + "\n")
                    zi = zipfile.ZipInfo(top + arc_model, date_time=time.localtime(os.path.getmtime(model))[:6])
                    zi.compress_type = zipfile.ZIP_STORED      # the network's 8-bit weights hardly compress
                    zi.external_attr = 0o644 << 16
                    with open(model, "rb") as f, zo.open(zi, "w") as w:
                        shutil.copyfileobj(f, w, 4 * 1024 * 1024)
                # read back: the new package is checked the way Update checks a zip
                with zipfile.ZipFile(tmp) as chk:
                    bad2, _ = _zip_problems(chk, top)
                if bad2:
                    raise OSError("the written package does not check out (%s)" % ", ".join(bad2[:3]))
                os.replace(tmp, out)
            except _Damaged as e:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                todo("%s is damaged: %s. Download it again." % (base, e))
                continue
            except (ZIP_DAMAGE + (KeyError,)) as e:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                todo("Could not write %s: %s" % (os.path.basename(out), e))
                continue
            ok("%s (%.0f MB): %s with the model inside, every file checked" % (os.path.basename(out), os.path.getsize(out) / 1e6, base))
    if problems:
        return finish()
    say()
    say("-" * 78)
    say("DONE. The full packages are next to the zips they were made from.")
    say()
    say("Copy a full package to any computer (USB stick, cloud drive, network folder), unzip it there and run")
    say("Install RapReaper%s: the Vocal Remover's model is already in place, nothing else to download." % PF.LAUNCHER_EXT)
    say("The -Windows-full.zip is for Windows, the -Mac-full.zip for a Mac with an Apple chip, the -Mac-Intel-full.zip for an")
    say("Intel Mac.")
    say()
    return 0


def run_update(zip_arg, no_pip, allow_older=False, check_only=False):
    """--update [ZIP]: the zip given, or the release zip for this system with the highest version in Downloads. With
    --check, the zip is only checked (nothing is written)."""
    others = []
    if zip_arg:
        zip_path = os.path.abspath(os.path.expanduser(zip_arg))
    else:
        zip_path, others, unused = find_update_zip()
        others = others + unused
        if not zip_path:
            say("RapReaper %s: update" % VERSION)
            for name in unused:
                info("in Downloads, not used: %s" % name)
            todo("No RapReaper zip was found in Downloads for this %s (RapReaper-<version>-%s.zip). Put the new zip in "
                 "Downloads and run this again, or give its path:  %s rapreaper_install.py --update <the zip>"
                 % ("Mac" if PF.IS_MAC else "PC", "Mac" if PF.IS_MAC else "Windows", PY_CMD))
            return finish()
    new_version = update_from_zip(zip_path, check_only=check_only, allow_older=allow_older, others=others)
    if not new_version:
        if UPDATE_STATE.get("declined"):
            return 0
        return finish()
    if check_only:
        say()
        say("Checked only (--check): nothing was changed. Run Update without --check to install it.")
        return 0
    say()
    say("Now running the installer of RapReaper %s ..." % new_version)
    say()
    cmd = [sys.executable, os.path.join(HERE, "rapreaper_install.py")] + (["--no-pip"] if no_pip else [])
    try:
        return subprocess.call(cmd)
    except OSError as e:
        todo("The new installer could not be started (%s). Double-click Install RapReaper.bat to finish the update." % e)
        return finish()


def allow_vocal_remover(path):
    """Record the person's permission for RapReaper to start this one program (path + SHA-256). RapReaper never runs a
    program it merely found on the PC; this is the only way one gets allowed, and it is done here, by the person,
    never by a tool call from the chat."""
    say("RapReaper %s: allow a Vocal Remover program" % VERSION)
    path = os.path.abspath(os.path.expanduser(str(path).strip().strip('"')))
    if not os.path.isfile(path):
        todo("There is no file at %s. Give the full path of %s (%s)." % (path, "VocalRemover" if PF.IS_MAC else "VocalRemover.exe",
             "in Finder: right-click it, hold Option, Copy as Pathname" if PF.IS_MAC else "in File Explorer: right-click it > Copy as path"))
        return 1
    base = os.path.basename(path).lower()
    if not (base.endswith(".exe") or base == "vocalremover"):
        todo("That is not the Vocal Remover program (VocalRemover.exe, or VocalRemover on a Mac): %s" % path)
        return 1
    kit = os.path.abspath(os.environ.get("RAPREAPER_HOME") or KIT)       # (the same folder the server itself uses)
    sys.path.insert(0, KIT)
    try:
        import rapreaper_separate as SEP
    except ImportError as e:
        todo("RapReaper's separation module could not be loaded (%s). Unzip RapReaper again." % e)
        return 1
    why = _settings_problem(kit)
    if why:
        _settings_refused(kit, why)
        return finish()
    try:
        digest = SEP.allow_exe(kit, path)
    except (OSError, ValueError) as e:
        todo("Could not record it: %s" % e)
        return 1
    ok("allowed: %s" % path)
    ok("its checksum (SHA-256) is %s..., kept in kit\\state\\settings.json; if the program file ever changes, RapReaper "
       "stops using it until you run this again" % digest[:16])
    say()
    say("RapReaper may now start this program when you ask it to learn a song's effects. Nothing else runs it.")
    return 0


def allow_program(kind, path):
    """Record (or take back) the person's permission for RapReaper to start one program: ffmpeg or whisper.cpp
    (path + SHA-256, like the Vocal Remover). Done here by the person, never by a tool call from the chat."""
    sys.path.insert(0, KIT)
    try:
        import rapreaper_programs as PR
    except ImportError as e:
        todo("RapReaper's programs module could not be loaded (%s). Unzip RapReaper again." % e)
        return 1
    kind = str(kind).strip().lower()
    if kind not in PR.KNOWN:
        todo("Unknown program kind '%s'. Known: %s." % (kind, ", ".join(PR.KNOWN)))
        return 1
    kit = os.path.abspath(os.environ.get("RAPREAPER_HOME") or KIT)
    why = _settings_problem(kit)
    if why:
        say("RapReaper %s: %s the permission for %s" % (VERSION, "take back" if path is None else "record", kind))
        _settings_refused(kit, why)
        return finish()
    if path is None:
        say("RapReaper %s: take back the permission for %s" % (VERSION, kind))
        if PR.disallow(kit, kind):
            ok("%s is no longer allowed; RapReaper will not start it" % kind)
        else:
            info("%s was not allowed in the first place" % kind)
        return 0
    say("RapReaper %s: allow a program (%s)" % (VERSION, kind))
    path = os.path.abspath(os.path.expanduser(str(path).strip().strip('"')))
    if not os.path.isfile(path):
        todo("There is no file at %s. Give the full path of the program (%s)."
             % (path, "in File Explorer: right-click it > Copy as path" if PF.IS_WINDOWS else "in Finder: right-click it, hold Option, Copy as Pathname"))
        return 1
    try:
        digest = PR.allow(kit, kind, path)
    except (OSError, ValueError) as e:
        todo("Could not record it: %s" % e)
        return 1
    ok("allowed: %s" % path)
    ok("its checksum (SHA-256) is %s..., kept in kit\\state\\settings.json; if the program file ever changes, RapReaper stops "
       "using it until you run this again" % digest[:16])
    say()
    if kind == "ffmpeg":
        say("export_mix can now write MP3 and M4A copies with it. Nothing else runs it, and it only ever reads and writes")
        say("files inside RapReaper's exports folder.")
    else:
        say("vocal_clarity (with words true) can now write down the words of a vocal with it, on this PC. Put a whisper.cpp")
        say("model file (ggml-base.en.bin or ggml-small.en.bin) in kit\\models. The words stay in kit\\state\\transcripts;")
        say("Claude is told which words are unclear and when, not the words themselves, unless you ask.")
    return 0


# --------------------------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description="Install, check or remove RapReaper.")
    ap.add_argument("--check", action="store_true", help="change nothing; just report")
    ap.add_argument("--uninstall", action="store_true", help="remove RapReaper from REAPER's startup and from Claude")
    ap.add_argument("--no-pip", action="store_true", help="do not install Python packages")
    ap.add_argument("--reaper-resource-dir", default=None, help="REAPER's settings folder (portable installs)")
    ap.add_argument("--allow-vocal-remover", default=None, metavar="PATH",
                    help="allow RapReaper to run this Vocal Remover program (its path and checksum are recorded)")
    ap.add_argument("--allow-program", nargs=2, default=None, metavar=("KIND", "PATH"),
                    help="allow RapReaper to run this program: KIND is ffmpeg (MP3/M4A exports) or transcriber (whisper.cpp)")
    ap.add_argument("--disallow-program", default=None, metavar="KIND", help="take a program's permission back")
    ap.add_argument("--update", nargs="?", const="", default=None, metavar="ZIP",
                    help="replace the program files with those of a newer RapReaper zip, keeping your own files")
    ap.add_argument("--allow-same-or-older", action="store_true",
                    help="with --update: also accept a zip whose version is not newer than this one")
    ap.add_argument("--make-full-packages", nargs="*", default=None, metavar="ZIP",
                    help="write <zip>-full.zip next to the RapReaper zips: the same files plus the Vocal Remover's model")
    ap.add_argument("--build-model", action="store_true",
                    help="build the Vocal Remover's model from the files 'Get Vocal Remover Model' downloaded")
    ap.add_argument("--gpu", default=None, metavar="on|off", help="use the graphics processor for vocal separation")
    ap.add_argument("--privacy", default=None, metavar="LEVEL", help="the privacy level: standard, strict or maximum")
    ap.add_argument("--activity-log", default=None, metavar="on|off", help="the record of everything sent to Claude (kit/logs)")
    ap.add_argument("--expire-days", default=None, type=int, metavar="N", help="remove measurements and logs older than N days (0 = never)")
    ap.add_argument("--no-gpu-test", action="store_true", help="do not offer the Vocal Remover's graphics test at the end")
    args = ap.parse_args(argv)

    if args.allow_vocal_remover is not None:
        return allow_vocal_remover(args.allow_vocal_remover)
    if args.privacy is not None or args.activity_log is not None or args.expire_days is not None:
        return set_privacy(args.privacy, args.activity_log, args.expire_days)
    if args.gpu is not None:
        return set_gpu(args.gpu)
    if args.build_model:
        return build_model_only(args.check)
    if args.make_full_packages is not None:
        return make_full_packages(args.make_full_packages)
    if args.update is not None:
        return run_update(args.update, args.no_pip, args.allow_same_or_older, args.check)
    if args.allow_program is not None:
        return allow_program(args.allow_program[0], args.allow_program[1])
    if args.disallow_program is not None:
        return allow_program(args.disallow_program, None)

    say("RapReaper installer %s (%s)" % (VERSION, PF.NAME))
    say("Folder: %s" % HERE)
    if args.uninstall:
        say()
        remove_reaper(args.reaper_resource_dir)
        reaper_done = not problems
        remove_claude()
        say()
        if problems:
            say("NOT FINISHED YET. %d thing(s) could not be removed:" % len(problems))
            for i, p in enumerate(problems, 1):
                say()
                say("  %d. %s" % (i, p))
            say()
            say("Fix that and run the uninstaller again. Do not delete this folder before it has finished:")
            say("REAPER would show a script error at every start.")
            return 1
        if reaper_done:
            forget_reaper_dir()
        say("RapReaper is disconnected. Restart REAPER and Claude. You can now delete this folder if you want;")
        say("your saved chains are in  kit\\chains\\user  and your mix log is in  kit\\state  (copy them first to keep them).")
        say("kit\\exports holds the songs you exported and kit\\captures the short measurement recordings (and any")
        say("separated vocals of songs you had RapReaper learn): delete the folder if you want them gone.")
        say()
        say("What stays on the computer, on purpose:")
        say("  - your REAPER projects, with the plug-ins RapReaper added (they are REAPER's own, so the projects keep working)")
        say("  - RapReaper's one small effect, Effects\\RapReaper\\ducks.jsfx in REAPER's settings folder: projects that have")
        say("    ducks drawn on it keep playing them (delete the file if you like; those projects then show it as missing)")
        say("  - the copies this installer kept, named  *.rapreaper-backup-*  , next to REAPER's __startup.lua,")
        say("    reaper.ini and Claude's settings file (delete them if you like)")
        was = _install_notes().get("offline_preference_was")
        if was:
            say("  - REAPER's preference 'Set media items offline when application is not active' stays OFF (the installer")
            say("    switched it off). To have it on again: Options > Preferences in REAPER, search 'offline', tick it.")
        if PF.IS_WINDOWS:
            say("  - Python, NumPy and SciPy (and lameenc / onnxruntime if they were added); remove them with:")
            say("        \"%s\" -m pip uninstall numpy scipy lameenc onnxruntime" % sys.executable)
        else:
            say("  - Python itself. RapReaper's packages live in kit/venv inside this folder and go with it.")
        return 0

    previous = _install_notes().get("version")
    if not check_python():
        return finish()
    if not check_location():
        return finish()
    check_packages(not args.no_pip, args.check)
    check_lameenc(not args.no_pip, args.check)
    check_onnx(not args.no_pip, args.check)
    setup_reaper(args.reaper_resource_dir, args.check)
    setup_claude(args.check)
    if not any("NumPy" in p for p in problems):
        self_check()
    else:
        step(6, "Starting the RapReaper tool server")
        info("skipped until NumPy and SciPy are installed")
    setup_vocal_remover(args.check)
    if not args.check:
        if PF.IS_MAC:
            release_launchers()
        offer_gpu_test(args.check, not args.no_gpu_test and not os.environ.get("RAPREAPER_UNATTENDED"))
        if not problems:
            _remember(version=VERSION, python=server_python(), installed_on=time.strftime("%Y-%m-%d %H:%M:%S"))
            if previous and previous != VERSION:
                say()
                say("Updated from RapReaper %s to %s. Your saved chains, mix log, settings, snapshots, references and exports were kept."
                    % (previous, VERSION))
    return finish()


def finish():
    say()
    say("-" * 78)
    if problems:
        say("NOT FINISHED YET. %d thing(s) to do, then run this installer again:" % len(problems))
        for i, p in enumerate(problems, 1):
            say()
            say("  %d. %s" % (i, p))
        say()
        return 1
    say("ALL SET.")
    say()
    say("Next:")
    say("  1. Quit Claude completely (right-click its icon near the clock > Quit) and open it again.")
    say("  2. Start REAPER and open (or create) a project. Wait until its window is fully up.")
    say("  3. In Claude, type:   Run the RapReaper self-test")
    say("     Claude will ask permission to use the RapReaper tools the first time; choose 'Always allow'.")
    say()
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except KeyboardInterrupt:
        code = 130
    except Exception as e:                    # never leave the person with a bare traceback
        import traceback
        traceback.print_exc()
        say()
        say("The installer stopped because of an unexpected problem: %s" % e)
        say("Each file it writes is written in one piece, so no single file is half-written; but the steps after the")
        say("problem were not done. Run it again (that is safe).")
        if UPDATE_STATE.get("backup_dir"):
            say("It stopped during an update: copies of the files it replaced are in %s" % UPDATE_STATE["backup_dir"])
        code = 1
    sys.exit(code)
