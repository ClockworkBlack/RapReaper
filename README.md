# RapReaper

Mix rap vocals in REAPER by talking to Claude.

RapReaper connects the Claude desktop app to REAPER on your own computer. You say what you want ("clean up my lead vocal", "a bit brighter", "make it loud enough for Spotify", "what key is this beat in"), and Claude sets REAPER's own plug-ins in real units, measures the audio before and after, and tells you what changed. RapReaper and your audio stay on your computer.

## Download

The latest version is **1.4.4**. Pick the one for your computer:

| Computer | Download |
|---|---|
| 🪟 **Windows** (64-bit) | [RapReaper-1.4.4-Windows.zip](https://github.com/ClockworkBlack/RapReaper/releases/download/v1.4.4/RapReaper-1.4.4-Windows.zip) (28 MB) |
| 🍎 **Mac** | [RapReaper-1.4.4-Mac.zip](https://github.com/ClockworkBlack/RapReaper/releases/download/v1.4.4/RapReaper-1.4.4-Mac.zip) (18 MB). The Vocal Remover needs Apple silicon (M1 or later). |
| Checksums | [SHA256SUMS.txt](https://github.com/ClockworkBlack/RapReaper/releases/download/v1.4.4/SHA256SUMS.txt) |

Install steps, what's new and the security notes are on the [release page](https://github.com/ClockworkBlack/RapReaper/releases/tag/v1.4.4). Older and newer versions are under [Releases](https://github.com/ClockworkBlack/RapReaper/releases).

## What you need (all free)

- [REAPER](https://www.reaper.fm/download.php)
- The SWS extension, through [ReaPack](https://reapack.com)
- [Python](https://www.python.org/downloads/) 3.12 or newer
- The [Claude desktop app](https://claude.com/download)

## Quick start

**Windows:** unzip, put the `RapReaper` folder at `C:\Users\<your Windows name>\RapReaper`, and double-click **Install RapReaper.bat**.

**Mac:** unzip, move the `RapReaper` folder into your home folder, and double-click **Install RapReaper.command**. The first time, allow it under System Settings > Privacy & Security > Open Anyway.

Then quit Claude completely, open it again, start REAPER, and type in Claude: `Run the RapReaper self-test`

The full guide is in the PDF inside each zip, and `AUDIO INTERFACE SETUP.txt` covers setting up any microphone or audio interface.

## Licences

You may use, change and share RapReaper's own files freely. Third-party parts keep their own terms, listed in `NOTICE.txt` and `VocalRemover/licenses/` inside each zip.
