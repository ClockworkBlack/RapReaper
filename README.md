# RapReaper

**Mix rap vocals in REAPER by talking to Claude.**

RapReaper connects the Claude desktop app to REAPER on your own computer. You say what you hear or what you want ("clean up my lead vocal", "a bit brighter", "make it loud enough for Spotify", "what key is this beat in"). Claude then sets REAPER's own plug-ins in real units, measures the audio before and after, and tells you what each change did. RapReaper and your audio stay on your computer.

## Download

The latest version is **1.4.6**, released 9 November 2026. Pick the one for your computer:

| Computer | Program | Guide |
|---|---|---|
| 🪟 **Windows** (64-bit) | [RapReaper-1.4.6-Windows.zip](https://github.com/ClockworkBlack/RapReaper/raw/main/Windows/RapReaper-1.4.6-Windows.zip) (29 MB) | [RapReaper Guide (Windows).pdf](Windows/RapReaper%20Guide%20%28Windows%29.pdf) |
| 🍎 **Mac with an Apple chip** (M1 and later) | [RapReaper-1.4.6-Mac.zip](https://github.com/ClockworkBlack/RapReaper/raw/main/Mac/RapReaper-1.4.6-Mac.zip) (19 MB) | [RapReaper Guide (Mac).pdf](Mac/RapReaper%20Guide%20%28Mac%29.pdf) |
| 🍎 **Intel Mac** | [RapReaper-1.4.6-Mac-Intel.zip](https://github.com/ClockworkBlack/RapReaper/raw/main/Mac-Intel/RapReaper-1.4.6-Mac-Intel.zip) (18 MB) | [RapReaper Guide (Mac).pdf](Mac-Intel/RapReaper%20Guide%20%28Mac%29.pdf) |

Not sure which Mac you have? Apple menu > About This Mac: "Chip: Apple M…" means an Apple chip, "Processor: … Intel …" means an Intel Mac.

- **Checksums** for all three zips: [SHA256SUMS.txt](SHA256SUMS.txt). See [Check your download](#check-your-download).
- **What's new**, the security review and the known limitations are in [RELEASE-NOTES.md](RELEASE-NOTES.md).
- The guide is also inside each zip. It covers set-up, your first session, what to say, every tool in plain words, safety and privacy, and troubleshooting.

## What it does

- **Cleans up a vocal in one pass:** low cut, clean-up, two compressors, tone EQ, de-essing and the finish, each fitted by measuring your recording, not by presets.
- **Follows what you hear:** "brighter", "less muddy", "the S sounds hurt", "punchier", "it rings", "too boomy". Each request moves the right control by a measured amount and re-fits whatever comes after it.
- **Sits the vocal in the beat:** balances the vocal against the instrumental and dips the beat's EQ where it hides the words. It also brings several vocal tracks to one level.
- **Masters for streaming:** brings a mix to the loudness the streaming services play at, and checks how it sounds on a phone speaker and in mono.
- **Edits clips for you:** cuts out empty air and breaths, turns down a single harsh S or a loud word (ducking), and can always put the clips back from a restore point.
- **Adds voice effects:** auto-tune set to the beat's key, doubles, a telephone sound, and reverb and delay timed to the beat, all with REAPER's own plug-ins.
- **Separates songs (Vocal Remover):** makes an instrumental and a vocal from a finished song, on your computer, with an open neural network. The two files add up to the song exactly.
- **Learns a song's sound:** measures the vocal effects of a song you like and puts matching settings on your track.
- **Reads a beat:** tempo, key, note lengths for delays, and whether the beat is tuned off A=440. It can also slide a new instrumental into place against the old one, to the sample.
- **Handles projects and other programs:** opens, saves and closes REAPER projects when you say so, and remasters songs exported from Pro Tools, FL Studio, Ableton Live, Logic and others.

That's 57 tools in all. Every change is one named step in REAPER's undo history, and each answer says how to take it back.

## What you need

All of these are free to install:

| What | Where |
|---|---|
| REAPER | [reaper.fm/download.php](https://www.reaper.fm/download.php). Free for 60 days, then $60 for a personal licence. |
| The SWS extension, through ReaPack | [reapack.com](https://reapack.com). SWS lets REAPER measure audio without pop-up windows. |
| Python 3.12 or newer (64-bit) | [python.org/downloads](https://www.python.org/downloads/). RapReaper's installer adds the maths packages it needs. |
| The Claude desktop app | [claude.com/download](https://claude.com/download). The app is free, but tool use uses up the free allowance quickly, so a paid plan is realistic for regular mixing. |

Any audio interface or USB microphone works. `AUDIO INTERFACE SETUP.txt` in the zip sets one up in REAPER, step by step, for any brand.

**Vocal Remover:** on Windows it needs Windows 10 or 11 (64-bit). On a Mac with an Apple chip it needs macOS 14 or newer; on an Intel Mac, macOS 13 or newer and the Mac-Intel zip. It also needs its model, which you get once with **Get Vocal Remover Model** (see [The Vocal Remover's model](#the-vocal-removers-model)).

## Install

**Windows**

1. Unzip the file and put the `RapReaper` folder at `C:\Users\<your Windows name>\RapReaper`, not in Downloads or OneDrive.
2. Double-click **Install RapReaper.bat**. If it lists anything under "TO DO", do that and run it again until it says **ALL SET**.

**Mac**

1. Double-click the zip, then move the `RapReaper` folder into your home folder (in Finder, Go > Home).
2. Double-click **Install RapReaper.command**. The first time, macOS says it "cannot be opened": go to System Settings > Privacy & Security, click **Open Anyway**, then double-click it again.

**Then, on both:** quit Claude completely and open it again, start REAPER, open a project, and type in Claude:

```
Run the RapReaper self-test
```

The installer needs no administrator rights. Outside its own folder it does four things:

- adds two short marked blocks to REAPER's startup script;
- adds one "rapreaper" entry to Claude's settings file;
- copies RapReaper's small "Ducks" effect into REAPER's Effects folder;
- if REAPER is closed and one of its preferences would make every measurement come back silent, switches that preference off.

It keeps a dated copy of each file before changing it, and **Uninstall RapReaper** takes the startup and settings changes out again.

## Updating

From 1.4.0 or newer:

1. Put the new zip for your computer, and `SHA256SUMS.txt`, in your Downloads folder. Don't unzip the zip.
2. Double-click **Update RapReaper** (`.bat` on Windows, `.command` on a Mac) in your RapReaper folder. It checks the zip, replaces the program files, keeps your chains, settings, mix log, references, exports and model files, and runs the new installer.
3. Close REAPER and start it again once.

**Intel Mac on 1.4.5 or older:** the old updater doesn't know the Intel zip yet. Put both Mac zips in Downloads and double-click Update RapReaper.command twice: the first run updates to 1.4.6, the second swaps in the Intel Vocal Remover (the installer says so between the two).

## Privacy and safety

- **Your audio never leaves your computer.** RapReaper measures it locally and gives Claude only the numbers. What goes to Anthropic is what any Claude chat sends: the words you type and the text the tools return.
- **No folder paths, user names or computer names reach Claude.** RapReaper replaces them with labels such as `<RapReaper folder>` and `<you>`. The names of your songs and projects do go to Claude, so it can call them by name. A privacy setting lets you turn other file names, and even track names, into labels.
- **You can see exactly what was sent.** Everything sent to Claude is recorded, as sent, in `kit/logs/sent_to_claude.log` on your computer.
- **No network code.** RapReaper's tools open no network port and have no network code. The Vocal Remover's window is served only to your own computer, behind a one-time key. The only downloads are the two optional **Get …** files you start yourself, each from a named site, with every file it can check verified by its published checksum.
- **It never deletes your files or tracks**, and never overwrites a project unless you ask it to save. Opening or closing a project never drops unsaved changes without your word.
- **It never starts a program it merely found.** It starts only its own programs, including the Vocal Remover (checked by checksum), plus the optional ffmpeg and whisper.cpp, and only the exact files you allowed yourself.

`SECURITY.txt` in the zip has the full review.

## The Vocal Remover's model

The Vocal Remover needs a 237 MB neural-network model, which isn't included here: its author published it without a licence that lets anyone else pass it on, so you get it from the author yourself. Everything else in RapReaper works without it.

1. Install RapReaper first (it installs the NumPy the next step needs).
2. Double-click **Get Vocal Remover Model** (`.bat` on Windows, `.command` on a Mac) in your RapReaper folder. It downloads the model's two source files from [unwa's Hugging Face page](https://huggingface.co/pcunwa/Kim-Mel-Band-Roformer-FT) (about 900 MB), checks each against its published checksum, and builds the model in about a minute. The result is checked too: it's byte for byte the file the Vocal Remover expects.

If you have the model's pieces from an earlier delivery (`MelRoformer-KimFT2-int8.onnx.part1of8` to `part8of8`), putting them in Downloads before you run the installer still works. `VocalRemover/licenses/Models - credits.txt` in the zip names every model's authors and sources.

## Check your download

- **Windows** (PowerShell, in the folder with the file): `Get-FileHash .\RapReaper-1.4.6-Windows.zip -Algorithm SHA256`
- **Mac** (Terminal, in the folder with the file): `shasum -a 256 RapReaper-1.4.6-Mac.zip` (or `RapReaper-1.4.6-Mac-Intel.zip`)

The result must match the file's line in [SHA256SUMS.txt](SHA256SUMS.txt). The updater does this check by itself when that file is next to the zip.

## Source code and building it yourself

The complete source is in [`source/`](source): the Python tool server and installer, the helper script that runs inside REAPER, the simulated REAPER and the tests (`sim/`, `tests/`), the release build (`build/`) and the Vocal Remover's Go source with all its dependencies (`VocalRemover/source`). Each zip carries the same source.

To build the three zips yourself you need Go 1.27.3 and Python 3.12 or newer, nothing else (no Xcode, no Visual Studio, no PyTorch), on Windows, macOS or Linux:

```
cd source
python3 build/fetch_runtime.py              # Microsoft's runtime libraries, each checked by SHA-256
python3 build/build_release.py --no-pdf     # the three zips and SHA256SUMS.txt into dist/
```

The Vocal Remover programs rebuild byte for byte with Go 1.27.3. [`source/BUILDING.md`](source/BUILDING.md) has the details, and `source/DEVELOPMENT.md` explains how to test. Pushing a tag such as `v1.4.6` makes GitHub build the three zips and attach them to the release ([`.github/workflows/build.yml`](.github/workflows/build.yml)).

## Licences and credits

RapReaper's own files are under the [MIT licence](LICENSE): you may use, change and share them, also commercially, as long as the licence notice stays with them. There is no warranty. Third-party parts keep their own terms, listed in `NOTICE.txt` and `VocalRemover/licenses/` inside each zip. Among them are the REAPER bridge from [Reaper Daemon](https://github.com/wretcher207/reaper-daemon) by David Russell (MIT licence) and Microsoft's ONNX Runtime and DirectML. The separation models are credited to their authors (Kimberley Jensen, unwa, viperx and the Ultimate Vocal Remover project). They are not part of RapReaper and not covered by its licence; each person downloads them from their authors.
