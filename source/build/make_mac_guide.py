#!/usr/bin/env python3
"""Write docs/guide_mac.md from docs/guide_windows.md: the chapters that differ on a Mac are replaced whole
(set-up, updating, troubleshooting rows, keeping it working, sources), and the words that differ everywhere
(file names, keys, folders, the system's name) are swapped. The two guides have the same chapters."""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "docs", "guide_windows.md")
DST = os.path.join(ROOT, "docs", "guide_mac.md")


def chapter_span(text, heading):
    """(start, end) of the chapter that begins with this '## ' heading (end = start of the next '## ')."""
    i = text.index("\n" + heading + "\n") + 1
    m = re.search(r"\n## ", text[i + len(heading):])
    j = i + len(heading) + (m.start() + 1 if m else len(text) - i - len(heading))
    return i, j


def replace_chapter(text, heading, body):
    i, j = chapter_span(text, heading)
    return text[:i] + heading + "\n\n" + body.strip("\n") + "\n\n" + text[j:]


WHAT_YOU_NEED = '''
Five things, all free to install. Only REAPER (after its trial) and regular use of Claude cost money.

| What | Where to get it | Cost | Why |
| --- | --- | --- | --- |
| REAPER (the current version) | [reaper.fm/download.php](https://www.reaper.fm/download.php), the macOS download for your Mac (Apple Silicon or Intel) | Free for 60 days with every feature, then $60 for a personal licence | The recording program Claude controls. |
| ReaPack, then the SWS extension through it | [reapack.com](https://reapack.com/) for ReaPack, REAPER's package installer; SWS then comes from inside REAPER | Free | SWS lets REAPER measure audio without pop-up windows. |
| Python (3.12 or newer) | [python.org/downloads/macos](https://www.python.org/downloads/macos/), the "macOS 64-bit universal2 installer"; Homebrew's Python works too | Free | Runs RapReaper. Its maths packages go into RapReaper's own small environment inside the folder. |
| Claude Desktop | [claude.com/download](https://claude.com/download) | The app is free. A paid plan is realistic for regular mixing: tool use eats the free allowance fast | Where you type. |
| The RapReaper folder | The Mac zip that came with this guide, plus the model pieces (the files whose names contain `.onnx.part`) | Free | The connector: 57 tools, the measuring code, the bridge script for REAPER, RapReaper's own helper for clip edits, the Vocal Remover program with its neural networks (the pieces are its model file), and the engineering notes Claude works from. |

Your audio interface or USB microphone works with REAPER as it is; on a Mac almost all of them need no driver. "AUDIO INTERFACE SETUP.txt" in the RapReaper folder sets it up step by step for any brand.
'''

SETUP = '''
Do these once, in this order; allow about half an hour. The installer in step 7 checks Python, the folder, REAPER, SWS and Claude, and tells you what is missing.

1. **Your interface.** Follow "AUDIO INTERFACE SETUP.txt" (REAPER pointed at it, a Mono input for the mic, gain to about -12 dB peaks). If it already records in another program on this Mac, most of that is done.
2. **REAPER.** Download the macOS version for your Mac (Apple Silicon or Intel), drag it into Applications and start it once. Until you buy a licence it shows an evaluation notice at every start: wait a few seconds, then click the button to carry on. Quit REAPER.
3. **SWS extension, through ReaPack.** SWS's own website is not needed: ReaPack, REAPER's free package installer, fetches SWS from its official releases.
   - Download ReaPack from its official site, [reapack.com](https://reapack.com/) (by Christian Fillion): the file for your Mac, `reaper_reapack-arm64.dylib` (Apple Silicon: M1, M2, M3, M4) or `reaper_reapack-x86_64.dylib` (Intel).
   - Start REAPER, choose Options > Show REAPER resource path in finder, open the **UserPlugins** folder there and put the file in it. Quit REAPER and start it again. If macOS says the file cannot be opened, allow it once: System Settings > Privacy & Security, scroll down, "Allow Anyway", then start REAPER again (this is ReaPack's own documented step). The Extensions menu now has a ReaPack entry.
   - Extensions > ReaPack > Browse packages. Type `SWS` in the Filter box, right-click **SWS/S&M extension** and choose Install, then click Apply. Restart REAPER when ReaPack asks.
   - Check: the installer in step 7 reports "SWS extension found", and RapReaper's status says SWS is installed. Quit REAPER.
4. **Python.** On python.org/downloads/macos take the **macOS 64-bit universal2 installer** of the current Python 3 and click through it with the standard choices. The `python3` that macOS itself offers is only a stub for Apple's developer tools and is not used; RapReaper's `.command` files look for python.org's or Homebrew's Python (`brew install python` works too).
5. **Claude Desktop.** Install it and sign in.
6. **RapReaper.** Double-click the RapReaper zip in Downloads; it unpacks into a folder. Open it until you see **Install RapReaper.command**: the folder that holds it is the RapReaper folder. Move that folder into your home folder (in the Finder: Go > Home, or Cmd+Shift+H), so that it becomes `/Users/<your name>/RapReaper`. Leave the model pieces (`MelRoformer-KimFT2-int8.onnx.part1of8` and so on) in Downloads: the installer joins them.
   - Not in Downloads, Desktop or Documents: macOS asks each program (Terminal, REAPER, Claude) for permission to use those folders, and a refused one fails silently. Not in iCloud Drive, Dropbox or Google Drive either: they move files.
7. **Run the installer.** In the RapReaper folder, double-click **Install RapReaper.command**. The first time, macOS says the file "cannot be opened" because it came from the internet: open System Settings > Privacy & Security, scroll down, click **Open Anyway**, and double-click it again. (Or: open Terminal, type `bash` and a space, drag the file into the window, press Return.) A Terminal window shows what it does.
   - It ends with **ALL SET**, or with **NOT FINISHED YET** and a short list of things to do. Do the list and run it again. It is safe to run as often as you like. After this first run, every RapReaper `.command` file opens by double-click: the installer releases them (and the Vocal Remover's files) from the download quarantine after checking each against the package's manifest.
   - Terminal asks once for permission to read your Downloads folder (for the model pieces): allow it.
   - With REAPER closed, it also switches off the one REAPER preference RapReaper cannot work with ("Set media items offline when application is not active"), keeping a copy of the settings file; with REAPER open, the self-test does that instead.
   - Its last step sets up the Vocal Remover: it checks the program against the package's manifest, allows it for RapReaper, joins the model pieces into `VocalRemover/models` (each checked against its published checksum) and then offers the graphics-processor test (a few minutes; see "Instrumentals from a song"). You can say no and run the test later from the Vocal Remover's window.
8. **Restart Claude completely.** Claude menu > Quit Claude (or Cmd+Q), then open Claude again. Closing the window is not enough.
9. **Start REAPER** and open or create a project. Save it once (Cmd+S). Give REAPER about a minute before the next step: the bridge starts after the evaluation notice, and after a quick quit-and-reopen of REAPER it waits a full minute. A small window with the words BRIDGE\\_ALREADY\\_RUNNING during that minute is harmless: close it.
10. **Run the self-test.** In a new Claude chat, type: `Run the RapReaper self-test`
    - The first time Claude uses each RapReaper tool it asks permission. Choose **Always allow** to stop the question for that tool, or Allow once to be asked every time.
    - Claude runs the test in several short steps and then lists the checks. You may hear a faint beep. The test adds one muted track called "RapReaper self-test"; delete it afterwards if you like (right-click the track > Remove tracks).
11. **Read the result.** Every line should say PASS. A line that says FAIL names what was measured and comes with its fix: do the fix and ask Claude to run the test again.

The installer needs no administrator password. It makes RapReaper's own small Python environment inside the folder (`kit/venv`) and puts NumPy and SciPy there, plus two optional packages when they can be fetched (lameenc for MP3 copies of exports, ONNX Runtime so RapReaper can run the separation networks itself when the Vocal Remover program is not allowed); the Mac's own Python is left as it is. Outside the folder it changes two files: REAPER's startup script (`~/Library/Application Support/REAPER/Scripts/__startup.lua`: two short marked blocks, one starts the bridge, the other notes each REAPER start and starts RapReaper's own small helper) and Claude's settings file (`~/Library/Application Support/Claude/claude_desktop_config.json`: one "rapreaper" entry). It keeps a dated copy of both files before changing them.

To see RapReaper inside Claude, click the + in the chat box and open Connectors: "rapreaper" should be listed.
'''

UPDATING = '''
When a new RapReaper zip comes, download it into Downloads (with any new model pieces) and double-click **Update RapReaper.command**. It checks the zip against the checksums it carries (a damaged download is refused), replaces the program files (the Vocal Remover's program too), keeps your chains, settings, mix log, references, exports, imports, song setups, model files, the Vocal Remover's results and settings and the optional programs exactly as they are, puts the files it replaced in the `updates` folder, and then runs the new version's installer. Afterwards quit REAPER and start it again: the helper inside REAPER only loads when REAPER starts, and until you do, RapReaper's status reminds you (everything else keeps working). Terminal asks once for permission to read Downloads: allow it.

**One complete package to carry to another computer.** The Vocal Remover's network (237 MB) is too large to come inside the zip, so it arrives as eight pieces. Once the zip(s) and the eight pieces are in Downloads, double-click **Make Full Packages.command** in the RapReaper folder: it writes `RapReaper-1.4.5-Windows-full.zip` (and `RapReaper-1.4.5-Mac-full.zip`, when the Mac zip is in Downloads too) next to them, with the model already in `VocalRemover/models`, checked against its published checksum and listed in the package's own checksum list. Copy a full package to any computer by USB stick or cloud drive, unzip it and run Install RapReaper: nothing else to download. A full package also works with Update RapReaper (it puts the model in place if that computer lacks it), and a damaged one is refused like any damaged zip.
'''

KEEPING = '''
RapReaper itself uses nothing online (Claude does), so it keeps working until something around it changes. When that happens, run the installer and the self-test again.

| When | Do this |
| --- | --- |
| A new RapReaper zip | Put it in Downloads, double-click Update RapReaper.command, then restart REAPER. |
| REAPER updates | Ask Claude to run the self-test, with a recording in the project. Control names and the way REAPER renders can change between versions, and the test catches both. |
| Claude Desktop updates | If the RapReaper tools vanish, run Install RapReaper.command again. If Claude asks permission again, choose Always allow. |
| macOS updates | Usually nothing. If a `.command` file is refused again, allow it once more in System Settings > Privacy & Security. |
| You install a new Python | Run Install RapReaper.command again; it rebuilds RapReaper's own environment with it. |
| You move the RapReaper folder | Run Install RapReaper.command again from the new place (keep it in your home folder). |
| REAPER's 60-day trial ends | Buy a licence: $60 if you use REAPER personally or earn under $20,000 a year with it. |
| Before each session | File > Save new version of project. |
| Every month or so | Copy your REAPER project folders, `kit/chains/user` and `kit/state/songs` from the RapReaper folder to a USB drive or a cloud folder. |
| You want RapReaper gone | Copy `kit/chains/user` and `kit/state/songs` first if you want to keep them. Run Uninstall RapReaper.command, then delete the folder. Python itself stays; RapReaper's packages live inside the folder and go with it. |

Housekeeping is automatic. `kit/captures` holds the measurement files: RapReaper keeps the newest ones, about 1.5 GB at most, plus those your vocal sessions of the last 45 days still need for their before/after tables, and removes measurement files, vocal-session notes and the activity log older than 30 days. You may empty `kit/captures` at any time; the next vocal step then asks for a fresh plan. Exports and imports are yours to move or delete.

For comfort, create a Project called "Mixing" in Claude and start your sessions there, so the chats stay together.
'''

# rows of the troubleshooting table that differ on a Mac: (Windows row start, Mac row)
TROUBLE_ROWS = {
    '| The installer says the tool server "did not answer properly" or took long |':
        '| The installer says the tool server "did not answer properly" or took long | The first start after an install, while the maths library loads | Run Install RapReaper.command once more; the second start is quick. If it happens again, the installer has saved the exact answer in `kit/state/installer_selfcheck.json`: send that file to whoever helps you (it holds no paths or names). |',
    '| Claude has no RapReaper tools |':
        '| Claude has no RapReaper tools | Claude was not fully quit, or an update reset its settings | Quit Claude (Claude menu > Quit Claude, Cmd+Q) and reopen it. Still missing: run Install RapReaper.command again. |',
    '| The installer says Python is missing or unsuitable |':
        '| The installer says Python is missing or unsuitable, or a `.command` file says "no Python 3.12" | Only Apple\'s stub `python3` is present, or Python is older than 3.12 | Install Python from python.org (set-up step 4) or with Homebrew, then double-click the installer again. |',
    '| The voice is in one ear only |':
        '| "cannot be opened because it is from an unidentified developer", or a `.command` file does nothing | macOS\'s download quarantine | System Settings > Privacy & Security, scroll down, Open Anyway; then double-click again. After the installer has run once, it releases every RapReaper file it shipped. |\n'
        '| The installer refuses the folder | The RapReaper folder is in Downloads, Desktop, Documents or a cloud folder | Move it to your home folder (`/Users/<your name>/RapReaper`) and run the installer again. |\n'
        '| REAPER or Claude cannot see a file | macOS has not given that program permission for a folder | Keep everything inside the RapReaper folder in your home folder; System Settings > Privacy & Security > Files and Folders shows what each program may read. |\n'
        '| The voice is in one ear only | The mic was recorded on a stereo input | Click the clip, press F2 and set Channel mode to Mono (left) or Mono (right), whichever side has the voice. Record new takes on a Mono input. |',
}

LOGS = '''For deeper digging, Claude keeps a log for RapReaper called `mcp-server-rapreaper.log`. In Claude, Help > Troubleshooting > Show Logs opens its folder: `~/Library/Logs/Claude`. RapReaper's own record of what it sent is `kit/logs/sent_to_claude.log`.'''

GPU_MAC = '''**The graphics processor.** On a Mac this is CoreML, which runs the network on the graphics processor and the Neural Engine of Apple Silicon. The Vocal Remover tests it before using it: "Test the graphics processor" in its window (the installer offers the same test at the end) runs one 8-second window through the network on the processor and through CoreML, compares the two results and times them. It switches CoreML on only when it works and is faster, and remembers that in `VocalRemover/settings.json`; RapReaper follows the same setting when it starts the program for Claude. If CoreML fails in the middle of a song, the song is finished on the processor and the result says so. The graphics-processor model is a float16 copy that the program makes from the processor model once (460 MB in `VocalRemover/models`, nothing downloaded), and its checksum is checked like every other model file. `python3 rapreaper_install.py --gpu on` additionally makes RapReaper's own ONNX Runtime ask for CoreML when it separates without the program. The Vocal Remover runs on Apple Silicon Macs (M1 and later) with macOS 14 or newer; on an Intel Mac RapReaper separates with its own ONNX Runtime on the processor.'''

SOURCES_PY = '''- Python: [macOS downloads](https://www.python.org/downloads/macos/), [using Python on a Mac](https://docs.python.org/3/using/mac.html), [NumPy on PyPI](https://pypi.org/project/numpy/), [SciPy on PyPI](https://pypi.org/project/scipy/)'''

WORDS = [
    ("# RapReaper Guide (Windows): mix rap vocals in REAPER with Claude", "# RapReaper Guide (Mac): mix rap vocals in REAPER with Claude"),
    ("This is the Windows guide. The Mac version of RapReaper has its own guide with the same chapters.",
     "This is the Mac guide. The Windows version of RapReaper has its own guide with the same chapters."),
    ("Three programs on your PC, one service outside.", "Three programs on your Mac, one service outside."),
    ("Install RapReaper.bat", "Install RapReaper.command"), ("Update RapReaper.bat", "Update RapReaper.command"), ("Make Full Packages.bat", "Make Full Packages.command"),
    ("Check RapReaper.bat", "Check RapReaper.command"), ("Uninstall RapReaper.bat", "Uninstall RapReaper.command"),
    ("Allow Vocal Remover.bat", "Allow Vocal Remover.command"), ("Allow ffmpeg.bat", "Allow ffmpeg.command"),
    ("Allow Transcriber.bat", "Allow Transcriber.command"), ("Get ffmpeg and Transcriber.bat", "Get ffmpeg and Transcriber.command"),
    ("`VocalRemover\\\\VocalRemover.exe`", "`VocalRemover/Vocal Remover.command`"),
    ("double-click `VocalRemover/Vocal Remover.command`, drop a song", "double-click `VocalRemover/Vocal Remover.command` (the first time: Open Anyway in System Settings > Privacy & Security, unless the installer has run already), drop a song"),
    ("Ctrl+Alt+Shift+S", "Cmd+Option+Shift+S"), ("Ctrl+Alt+Z", "Cmd+Option+Z"), ("Ctrl+", "Cmd+"), ("Alt+", "Option+"),
    ("File Explorer", "the Finder"), ("in Explorer", "in the Finder"),
    ("%APPDATA%\\\\REAPER\\\\Scripts\\\\__startup.lua", "~/Library/Application Support/REAPER/Scripts/__startup.lua"),
    ("`%USERPROFILE%`", "`~`"), ("%USERPROFILE%", "~"),
    ("your Windows user name", "your macOS user name"), ("Windows user name", "macOS user name"),
    ("the name of your PC or of a OneDrive organisation", "the name of your Mac or of an iCloud account"),
    ("Inside your own user folder, other Windows accounts on the PC cannot.", "Inside your own home folder, other macOS accounts on the Mac cannot."),
    ("Another program already running under your Windows account", "Another program already running under your macOS account"),
    ("Windows file rules", "file rules"),
    ("every kind of Windows path an independent privacy review could find", "every kind of Windows and Mac path an independent privacy review could find"),
    ("and the Python packages. A dated copy", "and RapReaper's own Python environment inside its folder. A dated copy"),
    ("Windows' virus scanner", "the system"),
    ("py rapreaper_install.py", "python3 rapreaper_install.py"),
    ("`kit\\\\", "`kit/"), ("kit\\\\", "kit/"), ("`VocalRemover\\\\", "`VocalRemover/"), ("VocalRemover\\\\", "VocalRemover/"),
    ("Output\\\\GPU test.txt", "Output/GPU test.txt"),
    ("Effects\\\\RapReaper\\\\ducks.jsfx", "Effects/RapReaper/ducks.jsfx"),
    ("on your PC", "on your Mac"), ("your PC", "your Mac"), ("the PC", "the Mac"), ("this PC", "this Mac"), ("a PC", "a Mac"),
    ("2-core PC", "2-core test computer"), ("test PC", "test computer"), ("the PC's", "the Mac's"),
    ("On a PC this is DirectML", "On a Mac this is CoreML"),
    ("stand-in Windows and Mac profiles", "stand-in Mac and Windows profiles"),
    ("(it may be behind the ^ arrow)", ""),
    ("a slow PC", "a slow computer"), ("PC names, OneDrive names", "computer names, OneDrive and iCloud names"),
    ("Right-click the Claude icon near the clock  and choose Quit.", "Claude menu > Quit Claude."),
]


def main():
    text = open(SRC, encoding="utf-8").read()
    text = replace_chapter(text, "## What you need", WHAT_YOU_NEED)
    text = replace_chapter(text, "## Set-up, step by step", SETUP)
    text = replace_chapter(text, "## Updating RapReaper", UPDATING)
    text = replace_chapter(text, "## Keeping it working", KEEPING)
    # the graphics paragraph of the Vocal Remover chapter
    m = re.search(r"\*\*The graphics processor\.\*\* On a PC this is DirectML.*?\n", text)
    if not m:
        sys.exit("graphics paragraph not found")
    text = text[:m.start()] + GPU_MAC + "\n" + text[m.end():]
    # troubleshooting rows and the logs line
    for start, row in TROUBLE_ROWS.items():
        i = text.index(start)
        j = text.index("\n", i)
        text = text[:i] + row + text[j:]
    i = text.index("For deeper digging, Claude keeps a log")
    j = text.index("\n", i)
    text = text[:i] + LOGS + text[j:]
    i = text.index("- Python: [Windows downloads]")
    j = text.index("\n", i)
    text = text[:i] + SOURCES_PY + text[j:]
    for a, b in WORDS:
        text = text.replace(a, b)
    # a Mac-only line about the Intel Mac in the Vocal Remover chapter's speed paragraph is already in GPU_MAC
    leftovers = [w for w in ("Windows", ".bat", "%APPDATA%", "Explorer", "kit\\\\") if w in text]
    with open(DST, "w", encoding="utf-8") as f:
        f.write(text)
    print("wrote", DST, "(%d lines)" % text.count("\n"), "words still mentioning the other system:", leftovers)
    for w in ("Windows", ".bat"):
        for line in text.splitlines():
            if w in line:
                print("   ", w, "->", line[:120])


if __name__ == "__main__":
    main()
