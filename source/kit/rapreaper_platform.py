"""Where things are on this computer, and how to say them: Windows or macOS.

Everything in RapReaper that depends on the operating system goes through here, so that the server, the
installer and the separation code never have to ask "which system is this?" themselves:

  * which system this is (Windows, macOS, or something else), and the person's home folder
  * where REAPER keeps its settings, where Claude Desktop keeps its settings and its logs
  * which AI app is at the other end (1.5.0: any app that speaks MCP), and where each AI app the installer
    can connect by itself keeps its MCP settings (`ai_apps()`)
  * the names of the launcher files ("Install RapReaper.bat" on Windows, "Install RapReaper.command" on a Mac)
  * how to say things: the keys REAPER uses on a Mac (Cmd instead of Ctrl, Option instead of Alt), Finder
    instead of File Explorer, "~" instead of "%USERPROFILE%" for the home folder, and so on. The server
    passes every answer through `wording()` at the door, so the texts in the code can stay written once,
    in Windows words, and still read right on a Mac; "your AI app" in a text becomes the app's own name
    once the app has said who it is.

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


# ---------------------------------------------------------------------------------------------------------------------
# The AI app at the other end (1.5.0)
# ---------------------------------------------------------------------------------------------------------------------
# RapReaper is an MCP server (the Model Context Protocol, over stdin/stdout): any AI app that speaks MCP can drive it --
# Claude, ChatGPT's desktop app and Codex, Cursor, VS Code with Copilot, Windsurf, LM Studio, Gemini CLI, RapReaper's
# own Local AI (llama.cpp with an open model on this computer) and others. The texts in the code say "your AI app";
# once the app has said who it is (clientInfo in the MCP handshake, or in each request's _meta since the 2026-07-28
# revision), answers say its name instead ("Quit Cursor completely and open it again"), and the privacy notes say who
# reads the chat where that is certain for the app. An app RapReaper does not know by name stays "your AI app".

AI_APP_WORDS = "your AI app"
LOCAL_AI_NAME = "RapReaper's Local AI"

# (words to find in the app's name or title, the name answers use, who runs its AI model, kind)
#   who: the company a chat in that app goes to, when the app fixes it; None when the person picks the model in the
#        app; "local" when the model normally runs on this computer.
#   kind: "app" (a window: quit it and open it again), "editor" (a code editor), "cli" (a session in a terminal)
# The first entry whose words appear (as whole words) wins, so the more specific ones come first.
_KNOWN_APPS = (
    (("rapreaper local ai", "rapreaper local"), LOCAL_AI_NAME, "local", "app"),
    (("llama cpp", "llama server", "llamacpp"), LOCAL_AI_NAME, "local", "app"),
    (("claude code",), "Claude Code", "Anthropic", "cli"),
    (("claude",), "Claude", "Anthropic", "app"),
    (("chatgpt",), "ChatGPT", "OpenAI", "app"),
    (("codex",), "ChatGPT or Codex", "OpenAI", "app"),
    (("cursor",), "Cursor", None, "editor"),
    (("windsurf", "codeium", "devin"), "Windsurf", None, "editor"),
    (("copilot cli",), "GitHub Copilot CLI", None, "cli"),
    (("visual studio code", "vscode", "vs code"), "VS Code", None, "editor"),
    (("copilot",), "GitHub Copilot", None, "editor"),
    (("lm studio", "lmstudio"), "LM Studio", "local", "app"),
    (("gemini cli",), "Gemini CLI", "Google", "cli"),
    (("gemini",), "Gemini", "Google", "app"),
    (("zed",), "Zed", None, "editor"),
    (("cline",), "Cline", None, "editor"),
    (("roo code", "roo cline"), "Roo Code", None, "editor"),
    (("kilo code", "kilocode"), "Kilo Code", None, "editor"),
    (("goose",), "Goose", None, "app"),
    (("kiro",), "Kiro", None, "editor"),
    (("opencode",), "opencode", None, "cli"),
    (("jan",), "Jan", "local", "app"),
    (("ollama",), "Ollama", "local", "app"),
    (("msty",), "Msty", None, "app"),
    (("anythingllm", "anything llm"), "AnythingLLM", None, "app"),
    (("cherry studio", "cherrystudio"), "Cherry Studio", None, "app"),
    (("perplexity",), "Perplexity", "Perplexity", "app"),
    (("le chat", "mistral"), "Le Chat", "Mistral AI", "app"),
)
# names that say nothing about the app (an SDK's default, a test client): those stay "your AI app"
_GENERIC_CLIENT_WORDS = {"client", "mcp", "mcp client", "test", "tests", "harness", "inspector", "mcp inspector", "python",
                         "node", "sdk", "app", "assistant", "ai", "agent", "example", "example client", "unknown"}
_AI = {"name": None, "who": None, "kind": None}


def set_ai_app(info):
    """Remember the AI app at the other end, from MCP's clientInfo ({name, title?, version}); returns the name answers
    use, or None for an app RapReaper does not know by name (answers then keep saying 'your AI app').
    RAPREAPER_AI_APP=local (written by Start Local AI) names RapReaper's own Local AI, whatever the client says."""
    if (os.environ.get("RAPREAPER_AI_APP") or "").strip().lower() == "local":
        _AI.update(name=LOCAL_AI_NAME, who="local", kind="app")
        return _AI["name"]
    if not isinstance(info, dict):
        return _AI["name"]
    raw = " ".join(str(info.get(k)) for k in ("title", "name") if isinstance(info.get(k), str))[:200]
    norm = " ".join(re.sub(r"[^a-z0-9]+", " ", raw.lower()).split())
    for words, name, who, kind in _KNOWN_APPS:
        if any(re.search(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(w), norm) for w in words):
            _AI.update(name=name, who=who, kind=kind)
            return name
    title = info.get("title")
    if isinstance(title, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9 .+'-]{1,29}", title.strip()) \
            and title.strip().lower() not in _GENERIC_CLIENT_WORDS:
        _AI.update(name=title.strip(), who=None, kind="app")
        return _AI["name"]
    _AI.update(name=None, who=None, kind=None)
    return None


def ai_app_name():
    """The AI app's name for a sentence ('Cursor'), or 'your AI app'."""
    return _AI["name"] or AI_APP_WORDS


def ai_app_known():
    return bool(_AI["name"])


def ai_app_is_local():
    """True when the app normally runs its AI model on this computer (RapReaper's Local AI, LM Studio, ...)."""
    return _AI["who"] == "local"


def chat_goes_to():
    """Where a chat with the AI app goes, as the start of a sentence ('everything in this chat goes to Anthropic')."""
    name, who = _AI["name"], _AI["who"]
    if who == "local":
        return ("%s runs its AI model on this computer, so nothing in this chat leaves it (unless the person switched "
                "it to an online model)" % name)
    if who:
        return "everything in this chat goes to %s, the company that runs the AI model in %s" % (who, name)
    return ("everything in this chat goes to whoever runs the AI model chosen in %s (nobody, when that model runs on this "
            "computer)" % ai_app_name())


def answers_read_by():
    """Who reads what the tools answer, for the privacy tool ('the AI model of Claude, at Anthropic')."""
    name, who = _AI["name"], _AI["who"]
    if who == "local":
        return "the AI model %s runs on this computer (nothing leaves it unless the person switched to an online model)" % name
    if who:
        return "the AI model in %s, at %s" % (name, who)
    return ("the AI model chosen in %s, at whoever runs it (it stays on this computer only when that model runs here)"
            % ai_app_name())


def restart_app_how():
    """How to make the AI app start RapReaper's tool server afresh (after an update, or a change to its settings)."""
    name, kind = _AI["name"], _AI["kind"]
    if name == "Claude":
        return quit_claude_how()
    if name == LOCAL_AI_NAME:
        return "close the Local AI's window (the one Start Local AI opened) and double-click Start Local AI again"
    app = name or AI_APP_WORDS
    if kind == "cli":
        return "end the %s session and start a new one" % app
    if kind == "editor":
        return "quit %s completely and open it again (or restart RapReaper in its MCP settings)" % app
    if IS_MAC:
        return "quit %s completely (%s menu > Quit, or Cmd+Q) and open it again" % (app, name if name else "its")
    return ("quit %s completely (close its window, and if it has an icon near the clock, right-click that > Quit) and "
            "open it again" % app)


def _with_app_name(text):
    if _AI["name"] and "AI app" in text:
        text = text.replace("Your AI app", _AI["name"]).replace("your AI app", _AI["name"])
    return text


# ---- where each AI app keeps its MCP settings (the installer connects RapReaper to every one it finds) -------------

def ai_home():
    """The folder the AI apps keep their settings under: the person's own folder. RAPREAPER_AI_HOME names it by hand
    (the tests: never the real one). On Windows it is %USERPROFILE%; a profile given only by %APPDATA% (the tests'
    stand-in Windows profile) is the folder two levels above that."""
    forced = os.environ.get("RAPREAPER_AI_HOME")
    if forced:
        return forced
    if IS_WINDOWS:
        if os.environ.get("USERPROFILE"):
            return os.environ["USERPROFILE"]
        if os.environ.get("APPDATA"):
            return os.path.dirname(os.path.dirname(os.environ["APPDATA"].rstrip("\\/")))
        return os.path.expanduser("~")
    return os.environ.get("HOME") or os.path.expanduser("~")


def _app_support(home):
    """Where desktop apps keep their settings: %APPDATA% (Windows), ~/Library/Application Support (Mac), ~/.config."""
    if IS_WINDOWS:
        if os.environ.get("RAPREAPER_AI_HOME") or not os.environ.get("APPDATA"):
            return os.path.join(home, "AppData", "Roaming")
        return os.environ["APPDATA"]
    if IS_MAC:
        return os.path.join(home, "Library", "Application Support")
    return os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")


def chatgpt_installed(home=None):
    """Is ChatGPT's desktop app installed? (It reads its MCP settings from the same file as Codex.)"""
    import glob
    home = home or ai_home()
    if IS_MAC:
        places = [os.path.join(home, "Applications", "ChatGPT.app"),
                  os.path.join(home, "Library", "Application Support", "com.openai.chat")]
        if not os.environ.get("RAPREAPER_AI_HOME"):
            places.append("/Applications/ChatGPT.app")
    elif IS_WINDOWS:
        local = os.environ.get("LOCALAPPDATA") if not os.environ.get("RAPREAPER_AI_HOME") else None
        local = local or os.path.join(home, "AppData", "Local")
        places = glob.glob(os.path.join(local, "Packages", "OpenAI.ChatGPT*")) + [os.path.join(local, "Programs", "ChatGPT")]
    else:
        places = []
    return any(os.path.exists(p) for p in places)


def ai_apps():
    """Every AI app the installer can connect RapReaper to by itself, and where it keeps its MCP settings here:
    [{id, name, files, present, form, where}]. form: 'mcpServers' (the JSON file most apps use), 'servers' (VS Code's
    mcp.json, with "type": "stdio"), 'codex' (the TOML file ChatGPT's desktop app and Codex share). Claude Desktop is
    looked for as before (claude_config_dirs); RapReaper's own Local AI needs no settings file (Start Local AI writes
    its own) and is not listed here."""
    home = ai_home()
    support = _app_support(home)
    apps = []
    dirs = claude_config_dirs()
    packaged = os.path.normcase(os.path.join("LocalCache", "Roaming", "Claude"))
    present = [d for d in dirs if os.path.isdir(d) or os.environ.get("RAPREAPER_CLAUDE_CONFIG_DIRS")
               or os.path.normcase(d).endswith(packaged)]
    apps.append({"id": "claude", "name": "Claude", "present": bool(present), "form": "mcpServers",
                 "files": [os.path.join(d, "claude_desktop_config.json") for d in (present or dirs)],
                 "where": "Claude: Settings > Developer > Edit Config"})
    codex = os.environ.get("CODEX_HOME") if not os.environ.get("RAPREAPER_AI_HOME") else None
    codex = codex or os.path.join(home, ".codex")
    apps.append({"id": "chatgpt", "name": "ChatGPT's desktop app and Codex", "form": "codex",
                 "present": os.path.isdir(codex) or chatgpt_installed(home), "files": [os.path.join(codex, "config.toml")],
                 "where": "~/.codex/config.toml (shared by ChatGPT's desktop app, the Codex app, CLI and IDE extension)"})
    apps.append({"id": "cursor", "name": "Cursor", "form": "mcpServers", "present": os.path.isdir(os.path.join(home, ".cursor")),
                 "files": [os.path.join(home, ".cursor", "mcp.json")], "where": "Cursor: Settings > Tools & MCP"})
    for folder, ident, label in (("Code", "vscode", "VS Code"), ("Code - Insiders", "vscode-insiders", "VS Code Insiders")):
        user = os.path.join(support, folder, "User")
        apps.append({"id": ident, "name": label, "form": "servers", "present": os.path.isdir(user),
                     "files": [os.path.join(user, "mcp.json")], "where": "%s: command 'MCP: Open User Configuration'" % label})
    ws = os.path.join(home, ".codeium", "windsurf")
    apps.append({"id": "windsurf", "name": "Windsurf", "form": "mcpServers", "present": os.path.isdir(ws),
                 "files": [os.path.join(ws, "mcp_config.json")], "where": "Windsurf: Cascade > MCPs > Configure"})
    lm = os.path.join(home, ".lmstudio")
    apps.append({"id": "lmstudio", "name": "LM Studio", "form": "mcpServers", "present": os.path.isdir(lm),
                 "files": [os.path.join(lm, "mcp.json")], "where": "LM Studio: Program > Install > Edit mcp.json"})
    gm = os.path.join(home, ".gemini")
    apps.append({"id": "gemini", "name": "Gemini CLI", "form": "mcpServers", "present": os.path.isdir(gm),
                 "files": [os.path.join(gm, "settings.json")], "where": "~/.gemini/settings.json"})
    return apps


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
    """`text` in this system's words (Windows words are the code's own), with "your AI app" as the app's own name
    once it has said who it is."""
    if not isinstance(text, str):
        return text
    text = _with_app_name(text)
    if not IS_MAC:
        return text
    if "\\" in text:    # relative paths written the Windows way in the code (kit\\logs\\sent_to_ai.log) read with slashes here
        text = _WIN_PATH.sub(lambda m: m.group(0).replace("\\", "/"), text)
    for a, b in _MAC_WORDS:
        if a in text:
            text = text.replace(a, b)
    return text


def reword(value):
    """`wording` applied through lists and dicts (what a tool answers)."""
    if not IS_MAC and not _AI["name"]:
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
