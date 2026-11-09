"""Programs on this PC that RapReaper may start, and only those.

RapReaper never runs a program it merely found on the PC. The person allows one program at a time, by
running an "Allow ... .bat" file in the RapReaper folder (which calls the installer), and that records the
program's full path and its SHA-256 checksum in kit\\state\\settings.json. A tool then gets the program only
if the file on disk still has that checksum. Every program is started with fixed arguments, on files inside
RapReaper's own folders, with no shell and no network.

Programs RapReaper knows how to use (none is required):
  ffmpeg        ffmpeg.exe, to write MP3 and M4A (AAC) copies of an export when the small 'lameenc' Python
                package is not installed (MP3) or for M4A (always ffmpeg). https://ffmpeg.org
  transcriber   whisper.cpp's command-line program (whisper-cli.exe or main.exe), to write down the words of a
                vocal with a time and a confidence for each, on this PC, so vocal_clarity can say which words a
                listener would miss. Needs a model file (ggml-*.bin) in kit\\models. https://github.com/ggerganov/whisper.cpp
  vocal_remover is handled by rapreaper_separate.py (the first program this mechanism was built for).
"""
import hashlib
import json
import os
import subprocess

KNOWN = {
    "ffmpeg": {"names": ("ffmpeg.exe", "ffmpeg"), "allow_bat": "Allow ffmpeg.bat",
               "for": "MP3 and M4A copies of an export"},
    "transcriber": {"names": ("whisper-cli.exe", "whisper-cli", "main.exe", "main", "whisper.exe", "whisper"),
                    "allow_bat": "Allow Transcriber.bat", "for": "writing down the words of a vocal (vocal_clarity)"},
}

REJECTED = {}       # key -> why the program named in settings was not used (for the answer's benefit)


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _settings_path(home):
    return os.path.join(home, "state", "settings.json")


def _settings(home):
    try:
        with open(_settings_path(home), encoding="utf-8-sig") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_settings(home, s):
    os.makedirs(os.path.join(home, "state"), exist_ok=True)
    tmp = _settings_path(home) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=1, sort_keys=True)
    os.replace(tmp, _settings_path(home))


def find(home, key):
    """The allowed program for 'key', or None (REJECTED[key] then says why, when one was named but not usable)."""
    REJECTED[key] = None
    if key not in KNOWN:
        return None
    rec = (_settings(home).get("allowed_programs") or {}).get(key) or {}
    path, want = rec.get("path"), str(rec.get("sha256") or "").lower()
    if not path or not os.path.isfile(path):
        return None
    if len(want) != 64:
        REJECTED[key] = "named in settings.json but not allowed with its checksum (run '%s')" % KNOWN[key]["allow_bat"]
        return None
    try:
        if sha256_of(path).lower() != want:
            REJECTED[key] = ("the program file has changed since it was allowed (run '%s' again if that was you)"
                             % KNOWN[key]["allow_bat"])
            return None
    except OSError:
        return None
    return path


def allow(home, key, path):
    """Record the person's permission for RapReaper to run this one program (called by the installer, never by a tool)."""
    if key not in KNOWN:
        raise ValueError("unknown program kind %r (known: %s)" % (key, ", ".join(KNOWN)))
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise ValueError("there is no file at %s" % path)
    base = os.path.basename(path).lower()
    if os.name == "nt" and not base.endswith(".exe"):
        raise ValueError("that is not a program file (.exe): %s" % path)
    if base.split(".")[0] not in {n.split(".")[0] for n in KNOWN[key]["names"]}:
        raise ValueError("for '%s' RapReaper expects a program called %s, not %s" % (key, " or ".join(KNOWN[key]["names"]), base))
    digest = sha256_of(path)
    s = _settings(home)
    progs = s.get("allowed_programs")
    if not isinstance(progs, dict):
        progs = {}
    progs[key] = {"path": path, "sha256": digest}
    s["allowed_programs"] = progs
    _write_settings(home, s)
    return digest


def disallow(home, key):
    s = _settings(home)
    progs = s.get("allowed_programs")
    if isinstance(progs, dict) and key in progs:
        progs.pop(key)
        s["allowed_programs"] = progs
        _write_settings(home, s)
        return True
    return False


def run(exe, args, timeout=600.0, cwd=None):
    """Run an allowed program with fixed arguments: no shell, no window, output captured, a time limit."""
    kwargs = {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE, "timeout": timeout, "cwd": cwd}
    if os.name == "nt":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        kwargs["startupinfo"] = si
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run([exe] + [str(a) for a in args], **kwargs)


def status(home):
    """What is allowed and usable, for the status tool and the installer's check."""
    out = {}
    for key, info in KNOWN.items():
        exe = find(home, key)
        out[key] = {"allowed": bool(exe), "for": info["for"]}
        if REJECTED.get(key):
            out[key]["problem"] = REJECTED[key]
        if not exe:
            out[key]["how"] = "run '%s' in the RapReaper folder after installing the program" % info["allow_bat"]
    return out
