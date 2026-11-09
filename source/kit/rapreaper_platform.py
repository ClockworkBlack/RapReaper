"""Where things are on this computer, and how to say them: Windows or macOS.

Everything in RapReaper that depends on the operating system goes through here, so that the server, the
installer and the separation code never have to ask "which system is this?" themselves:

  * which system this is (Windows, macOS, or something else), and the person's home folder
  * where REAPER keeps its settings, where Claude Desktop keeps its settings and its logs
  * the names of the launcher files ("Install RapReaper.bat" on Windows, "Install RapReaper.command" on a Mac)
  * how to say things: the keys REAPER uses on a Mac (Cmd instead of Ctrl, Option instead of Alt), Finder
    instead of File Explorer, "~" instead of "%USERPROFILE%" for the home folder, and so on. The server
    passes every answer through `wording()` at the door, so the texts in the code can stay written once,
    in Windows words, and still read right on a Mac.

RAPREAPER_PLATFORM=windows|mac|linux overrides the detection. That is for the test-suite, which runs on Linux
and has to see what a Mac or a Windows user would see; it changes wording and folder lookups, nothing else.
"""
from __future__ import annotations

import os
import platform
import re
import sys


def kind():
    """'windows', 'mac' or 'linux'."""
    forced = (os.environ.get("RAPREAPER_PLATFORM") or "").strip().lower()
    if forced in ("windows", "win", "nt"):
        return "windows"
    if forced in ("mac", "macos", "darwin", "osx"):
        return "mac"
    if forced in ("linux", "unix"):
        return "linux"
    if os.name == "nt":
        return "windows"
    if sys.platform == "darwin":
        return "mac"
    return "linux"


IS_WINDOWS = kind() == "windows"
IS_MAC = kind() == "mac"
NAME = {"windows": "Windows", "mac": "macOS", "linux": "Linux"}[kind()]
MACHINE = "PC" if IS_WINDOWS else ("Mac" if IS_MAC else "computer")
LAUNCHER_EXT = ".bat" if IS_WINDOWS else ".command"
HOME_WORD = "%USERPROFILE%" if IS_WINDOWS else "~"        # how answers refer to the person's home folder


def home_dir():
    """The person's own folder (C:\\Users\\<name> or /Users/<name>)."""
    if IS_WINDOWS:
        return os.environ.get("USERPROFILE") or os.path.expanduser("~")
    return os.environ.get("HOME") or os.path.expanduser("~")


def user_name():
    return (os.environ.get("USERNAME") if IS_WINDOWS else (os.environ.get("USER") or os.environ.get("LOGNAME"))) or ""


def computer_names():
    """Names this computer goes by, for the privacy filter (a Mac's 'Alexs-MacBook-Pro.local' with and without '.local')."""
    names = set()
    if IS_WINDOWS:
        names.add(os.environ.get("COMPUTERNAME") or "")
    try:
        node = platform.node() or ""
    except Exception:                                      # pragma: no cover
        node = ""
    if node:
        names.add(node)
        if node.lower().endswith(".local"):
            names.add(node[:-6])
    return sorted(n for n in names if n)


def reaper_resource_dir():
    """REAPER's settings folder (a portable REAPER is given to the installer by hand, and remembered there)."""
    env = os.environ.get("REAPER_RESOURCE_PATH")
    if env:
        return env
    if IS_MAC:
        return os.path.join(home_dir(), "Library", "Application Support", "REAPER")
    if IS_WINDOWS:
        appdata = os.environ.get("APPDATA") or os.path.join(home_dir(), "AppData", "Roaming")
        return os.path.join(appdata, "REAPER")
    xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.join(home_dir(), ".config")
    return os.path.join(xdg, "REAPER")


CLAUDE_PACKAGE = "Claude_pzs8sxrjxfjjc"          # Claude Desktop's Windows package name (MSIX install)


def claude_config_dirs():
    """Folders where Claude Desktop may keep claude_desktop_config.json on this computer. The Windows app comes in
    two forms; the packaged (MSIX) one keeps a private copy of AppData, so both places are served when they exist.
    RAPREAPER_CLAUDE_CONFIG_DIRS (folders separated by the path separator) names them by hand."""
    import glob
    override = os.environ.get("RAPREAPER_CLAUDE_CONFIG_DIRS")
    if override:
        return [d for d in override.split(os.pathsep) if d]
    dirs = []
    if IS_WINDOWS:
        local = os.environ.get("LOCALAPPDATA")
        if local:
            for pkg in sorted(glob.glob(os.path.join(local, "Packages", "Claude_*"))) or [os.path.join(local, "Packages", CLAUDE_PACKAGE)]:
                if os.path.isdir(pkg):
                    dirs.append(os.path.join(pkg, "LocalCache", "Roaming", "Claude"))
            # The app has been published under more than one package name. Any other package with "Claude" in its
            # name counts only when it already holds Claude's settings folder, so nothing is written into an unrelated app.
            for pkg in sorted(glob.glob(os.path.join(local, "Packages", "*Claude*"))):
                d = os.path.join(pkg, "LocalCache", "Roaming", "Claude")
                if os.path.isdir(d) and d not in dirs:
                    dirs.append(d)
        appdata = os.environ.get("APPDATA")
        if appdata and (os.path.isdir(os.path.join(appdata, "Claude")) or not dirs):
            dirs.append(os.path.join(appdata, "Claude"))
    elif IS_MAC:
        dirs.append(os.path.join(home_dir(), "Library", "Application Support", "Claude"))
    else:
        dirs.append(os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.join(home_dir(), ".config"), "Claude"))
    return dirs


def claude_logs_hint():
    """Where Claude Desktop keeps RapReaper's log, in words for the person."""
    if IS_MAC:
        return "~/Library/Logs/Claude (in Finder: Go > Go to Folder, paste that)"
    if IS_WINDOWS:
        return "%APPDATA%\\Claude\\logs (or under %LOCALAPPDATA%\\Packages\\Claude_... for the packaged app)"
    return "~/.config/Claude/logs"


def launcher(stem):
    """'Install RapReaper' -> the file the person double-clicks on this system."""
    return stem + LAUNCHER_EXT


def quit_claude_how():
    if IS_MAC:
        return "quit Claude completely (Claude menu > Quit Claude, or Cmd+Q) and open it again"
    return "quit Claude completely (right-click its icon near the clock > Quit) and open it again"


def key(text):
    """A key combination as REAPER names it on this system: 'Ctrl+Alt+Z' -> 'Cmd+Option+Z' on a Mac."""
    if not IS_MAC:
        return text
    return re.sub(r"\bCtrl\+", "Cmd+", re.sub(r"\bAlt\+", "Option+", text))


# What answers say on a Mac instead of the Windows words the code is written in. Applied to every answer at the
# door (server) and to the installer's own lines. Longest phrases first, so that "Install RapReaper.bat" is
# handled before ".bat" would be.
_MAC_WORDS = [
    ("right-click its icon near the clock > Quit", "Claude menu > Quit Claude (Cmd+Q)"),
    ("right-click the Claude icon near the clock", "open the Claude menu"),
    ("Install RapReaper.bat", "Install RapReaper.command"),
    ("Check RapReaper.bat", "Check RapReaper.command"),
    ("Uninstall RapReaper.bat", "Uninstall RapReaper.command"),
    ("Update RapReaper.bat", "Update RapReaper.command"),
    ("Make Full Packages.bat", "Make Full Packages.command"),
    ("Get ffmpeg and Transcriber.bat", "Get ffmpeg and Transcriber.command"),
    ("Allow ffmpeg.bat", "Allow ffmpeg.command"),
    ("Allow Transcriber.bat", "Allow Transcriber.command"),
    ("Allow Vocal Remover.bat", "Allow Vocal Remover.command"),
    ("VocalRemover.exe", "VocalRemover"),
    ("your Windows user name", "your user name"),
    ("the Windows user name", "the user name"),
    ("Windows user name", "user name"),
    ("File Explorer", "Finder"),
    ("Windows Explorer", "Finder"),
    ("in Notepad", "in TextEdit"),
    ("Notepad", "TextEdit"),
    ("%USERPROFILE%", "~"),
    ("%APPDATA%\\Claude\\logs", "~/Library/Logs/Claude"),
    ("%APPDATA%\\REAPER", "~/Library/Application Support/REAPER"),
    ("Ctrl+Alt+Shift+", "Cmd+Option+Shift+"),
    ("Ctrl+Alt+", "Cmd+Option+"),
    ("Ctrl+Shift+", "Cmd+Shift+"),
    ("Ctrl+", "Cmd+"),
    ("Alt+", "Option+"),
    ("this PC's", "this Mac's"),
    ("this PC", "this Mac"),
    ("the PC", "the Mac"),
    ("your PC", "your Mac"),
    ("a slow PC", "a slow Mac"),
    ("an ordinary PC", "an ordinary Mac"),
    ("on Windows", "on this Mac"),
    ("kit\\models", "kit/models"),
    ("kit\\references", "kit/references"),
    ("kit\\exports", "kit/exports"),
    ("kit\\captures", "kit/captures"),
    ("kit\\state", "kit/state"),
    ("kit\\chains\\user", "kit/chains/user"),
    ("kit\\chains", "kit/chains"),
    ("kit\\rapreaper_helper.lua", "kit/rapreaper_helper.lua"),
    ("kit\\reaper-daemon", "kit/reaper-daemon"),
]


_WIN_PATH = re.compile(r"\b(kit|VocalRemover|updates|tests|docs|references|imports|exports|captures|models)(\\[\w .()+-]+)+")


def wording(text):
    """`text` in this system's words (unchanged on Windows)."""
    if not IS_MAC or not isinstance(text, str):
        return text
    if "\\" in text:    # relative paths written the Windows way in the code (kit\\logs\\sent_to_claude.log) read with slashes here
        text = _WIN_PATH.sub(lambda m: m.group(0).replace("\\", "/"), text)
    for a, b in _MAC_WORDS:
        if a in text:
            text = text.replace(a, b)
    return text


def reword(value):
    """`wording` applied through lists and dicts (what a tool answers)."""
    if not IS_MAC:
        return value
    if isinstance(value, str):
        return wording(value)
    if isinstance(value, dict):
        return {k: reword(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [reword(v) for v in value]
    return value


def reapack_file():
    """The ReaPack file for this system, as named on reapack.com."""
    if IS_MAC:
        arch = (platform.machine() or "").lower()
        if arch in ("arm64", "aarch64"):
            return "reaper_reapack-arm64.dylib (for a Mac with an Apple chip; reaper_reapack-x86_64.dylib on an Intel Mac)"
        if arch in ("x86_64", "amd64"):
            return "reaper_reapack-x86_64.dylib (for an Intel Mac; reaper_reapack-arm64.dylib on a Mac with an Apple chip)"
        return "reaper_reapack-arm64.dylib on a Mac with an Apple chip, reaper_reapack-x86_64.dylib on an Intel Mac"
    if IS_WINDOWS:
        return "reaper_reapack-x64.dll on a usual 64-bit Windows PC"
    return "reaper_reapack-x86_64.so on a usual 64-bit Linux PC"


def free_disk_gb(path):
    """Free space on the disk that holds `path`, in GB; None when it cannot be read."""
    import shutil
    p = path
    while p and not os.path.exists(p):
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    try:
        return shutil.disk_usage(p).free / float(1024 ** 3)
    except OSError:
        return None
