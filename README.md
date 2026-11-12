# RapReaper

**Mix rap vocals in REAPER by talking to an AI: Claude, ChatGPT, Cursor, VS Code, LM Studio, any app that speaks MCP, or RapReaper's own Local AI on your computer.**

> **This is the Nightly branch.** It holds RapReaper 1.5.0 (nightly): it works with any AI app that speaks MCP, not only Claude, and it comes with its own free Local AI that runs on your computer. It's built and tested, but not yet a release. The current release, 1.4.6, is on the [main branch](https://github.com/ClockworkBlack/RapReaper/tree/main). Every push to this branch also builds the three zips on GitHub (Actions > build > the newest run > Artifacts).

RapReaper connects an AI app to REAPER on your own computer. You say what you hear or what you want ("clean up my lead vocal", "a bit brighter", "make it loud enough for Spotify", "what key is this beat in"). The AI then sets REAPER's own plug-ins in real units, measures the audio before and after, and tells you what each change did. RapReaper and your audio stay on your computer.

## Download

This branch holds **1.5.0 (nightly)**, built 12 November 2026. Pick the one for your computer:

| Computer | Program | Guide |
|---|---|---|
| 🪟 **Windows** (64-bit) | [RapReaper-1.5.0-Windows.zip](https://github.com/ClockworkBlack/RapReaper/raw/Nightly/Windows/RapReaper-1.5.0-Windows.zip) (29 MB) | [RapReaper Guide (Windows).pdf](Windows/RapReaper%20Guide%20%28Windows%29.pdf) |
| 🍎 **Mac with an Apple chip** (M1 and later) | [RapReaper-1.5.0-Mac.zip](https://github.com/ClockworkBlack/RapReaper/raw/Nightly/Mac/RapReaper-1.5.0-Mac.zip) (19 MB) | [RapReaper Guide (Mac).pdf](Mac/RapReaper%20Guide%20%28Mac%29.pdf) |
| 🍎 **Intel Mac** | [RapReaper-1.5.0-Mac-Intel.zip](https://github.com/ClockworkBlack/RapReaper/raw/Nightly/Mac-Intel/RapReaper-1.5.0-Mac-Intel.zip) (18 MB) | [RapReaper Guide (Mac).pdf](Mac-Intel/RapReaper%20Guide%20%28Mac%29.pdf) |

Not sure which Mac you have? Apple menu > About This Mac: "Chip: Apple …" means an Apple chip, "Processor: … Intel …" means an Intel Mac.

- **Checksums** for all three zips: [SHA256SUMS.txt](SHA256SUMS.txt). See [Check your download](#check-your-download).
- **What's new in this nightly**, how it was tested, and the security notes are in [RELEASE-NOTES.md](RELEASE-NOTES.md).
- The guide is also inside each zip. It covers set-up, your first session, what to say, every tool in plain words, other AI apps and the Local AI, safety and privacy, and troubleshooting.

## Which AI?

RapReaper is an MCP server, the standard way AI apps use tools, so it isn't tied to one AI. The installer adds it to every one of these it finds on your computer:

| AI app | Who reads the chat | Notes |
|---|---|---|
| **Claude Desktop** | Anthropic | Settings > Developer shows RapReaper. |
| **ChatGPT's desktop app**, **Codex** | OpenAI | They share `~/.codex/config.toml`. ChatGPT in a web browser can't reach a program on your computer: use the desktop app. |
| **Cursor**, **Windsurf**, **VS Code** (GitHub Copilot) | Whoever runs the model you picked there | VS Code's `mcp.json` with comments in it is left alone; the installer says so. |
| **LM Studio** | Nobody: its models run on your computer | Use a model made for tool calling. |
| **Gemini CLI** | Google | |
| **Any other app that speaks MCP** | That app's model | Add RapReaper by hand: the installer writes the settings to `kit/state/ai_app_settings.txt`. |
| **RapReaper's Local AI** | Nobody: everything stays on your computer | Free, no account. See below. |

Answers name the app you're using ("quit Cursor completely and open it again"), and the privacy notes say who reads the chat.

### RapReaper's Local AI

An open AI model that runs on your own computer and drives RapReaper. Nothing you say to it leaves the computer, and there is no account or subscription.

1. Install RapReaper (below).
2. Double-click **Get Local AI** (`.bat` on Windows, `.command` on a Mac) once. It downloads [llama.cpp](https://github.com/ggml-org/llama.cpp)'s llama-server (build b12250, MIT licence) and an open model, [Qwen 3.5](https://huggingface.co/Qwen/Qwen3.5-4B) (Apache 2.0): the **4B** (2.7 GB) for computers with about 8 GB of memory, or the **9B** (5.7 GB) for 16 GB or more. Each file is checked against its published checksum.
3. Double-click **Start Local AI** whenever you want to use it. Your browser opens its chat window, at an address only your computer can reach. Start REAPER and type: `Run the RapReaper self-test`

The chat window asks before every RapReaper tool runs. Expect a local model to be slower and less careful than Claude or ChatGPT. Without a good graphics card, and on an Intel Mac, the first answer of a chat can take a few minutes, because the model first reads RapReaper's tool descriptions; later answers come faster. On an Apple-chip Mac it runs on the chip's graphics part. It gets RapReaper's 28 core tools and a short working method, so the tool descriptions fit in a small model's memory. `LocalAI/README.txt` in the zip has the details.

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

That's 57 tools in all (28 of them make up the core set). Every change is one named step in REAPER's undo history, and each answer says how to take it back.

## What you need

All of these are free to install:

| What | Where |
|---|---|
| REAPER | [reaper.fm/download.php](https://www.reaper.fm/download.php). Free for 60 days, then $60 for a personal licence. |
| The SWS extension, through ReaPack | [reapack.com](https://reapack.com). SWS lets REAPER measure audio without pop-up windows. |
| Python 3.12 or newer (64-bit) | [python.org/downloads](https://www.python.org/downloads/). RapReaper's installer adds the maths packages it needs. |
| An AI app, or none | Any app above. Online apps are free to install, but tool use eats a free allowance quickly, so a paid plan is realistic for regular mixing. Or RapReaper's own Local AI: free. |

Any audio interface or USB microphone works. `AUDIO INTERFACE SETUP.txt` in the zip sets one up in REAPER, step by step, for any brand.

**Vocal Remover:** on Windows it needs Windows 10 or 11 (64-bit). On a Mac with an Apple chip it needs macOS 14 or newer; on an Intel Mac, macOS 13 or newer and the Mac-Intel zip. It also needs its model, which you get once with **Get Vocal Remover Model** (see [The Vocal Remover's model](#the-vocal-removers-model)).

## Install

**Windows**

1. Unzip the file and put the `RapReaper` folder at `C:\Users\<your Windows name>\RapReaper`, not in Downloads or OneDrive.
2. Double-click **Install RapReaper.bat**. If it lists anything under "TO DO", do that and run it again until it says **ALL SET**.

**Mac**

1. Double-click the zip, then move the `RapReaper` folder into your home folder (in Finder, Go > Home).
2. Double-click **Install RapReaper.command**. The first time, macOS says it "cannot be opened": go to System Settings > Privacy & Security, click **Open Anyway**, then double-click it again.

**Then, on both:** quit your AI app completely and open it again (or, with no AI app, double-click **Get Local AI** once and then **Start Local AI**). Start REAPER, open a project, and type:

```
Run the RapReaper self-test
```

The installer needs no administrator rights. Outside its own folder it does four things:

- adds two short marked blocks to REAPER's startup script;
- adds one "rapreaper" entry to the MCP settings of each AI app it finds;
- copies RapReaper's small "Ducks" effect into REAPER's Effects folder;
- if REAPER is closed and one of its preferences would make every measurement come back silent, switches that preference off.

It keeps a dated copy of each file before changing it, and **Uninstall RapReaper** takes the startup and settings changes out again, from every AI app. To connect only some apps: `rapreaper_install.py --ai-apps claude,chatgpt` (or `none`).

## Updating

From 1.4.0 or newer:

1. Put the new zip for your computer, and `SHA256SUMS.txt`, in your Downloads folder. Don't unzip the zip.
2. Double-click **Update RapReaper** (`.bat` on Windows, `.command` on a Mac) in your RapReaper folder. It checks the zip, replaces the program files, keeps your chains, settings, mix log, references, exports, model files and the Local AI's downloads, and runs the new installer. The new installer connects RapReaper to every AI app it finds.
3. Close REAPER and start it again once, and quit and reopen your AI app.

**Intel Mac on 1.4.5 or older:** the old updater doesn't know the Intel zip yet. Put both Mac zips in Downloads and double-click Update RapReaper.command twice: the first run updates, the second swaps in the Intel Vocal Remover (the installer says so between the two).

## Privacy and safety

- **Your audio never leaves your computer.** RapReaper measures it locally and gives the AI only the numbers. What leaves is what any chat in your AI app sends to the company that runs its model (Anthropic for Claude, OpenAI for ChatGPT, Google for Gemini): the words you type and the text the tools return. With the Local AI, or another model on your computer, nothing leaves at all.
- **No folder paths, user names or computer names reach the AI.** RapReaper replaces them with labels such as `<RapReaper folder>` and `<you>`. The names of your songs and projects do go to the AI, so it can call them by name. A privacy setting lets you turn other file names, and even track names, into labels.
- **You can see exactly what was sent.** Everything sent to the AI app is recorded, as sent, in `kit/logs/sent_to_ai.log` on your computer.
- **No network code.** RapReaper's tools open no network port and have no network code. The Vocal Remover's window and the Local AI's chat window are served only to your own computer. The only downloads are the optional **Get …** files you start yourself, each from a named site, with every file checked against a published checksum where one exists.
- **It never deletes your files or tracks**, and never overwrites a project unless you ask it to save. Opening or closing a project never drops unsaved changes without your word.
- **It never starts a program it merely found.** It starts only its own programs, including the Vocal Remover (checked by checksum), plus the optional ffmpeg and whisper.cpp, and only the exact files you allowed yourself. Start Local AI starts llama.cpp only when every one of its files is the one Get Local AI checked.

`SECURITY.txt` in the zip has the full review.

## The Vocal Remover's model

The Vocal Remover needs a 237 MB neural-network model, which isn't included here: its author published it without a licence that lets anyone else pass it on, so you get it from the author yourself. Everything else in RapReaper works without it.

1. Install RapReaper first (it installs the NumPy the next step needs).
2. Double-click **Get Vocal Remover Model** (`.bat` on Windows, `.command` on a Mac) in your RapReaper folder. It downloads the model's two source files from [unwa's Hugging Face page](https://huggingface.co/pcunwa/Kim-Mel-Band-Roformer-FT) (about 900 MB), checks each against its published checksum, and builds the model in about a minute. The result is checked too: it's byte for byte the file the Vocal Remover expects.

If you have the model's pieces from an earlier delivery (`MelRoformer-KimFT2-int8.onnx.part1of8` to `part8of8`), putting them in Downloads before you run the installer still works. `VocalRemover/licenses/Models - credits.txt` in the zip names every model's authors and sources.

## Check your download

- **Windows** (PowerShell, in the folder with the file): `Get-FileHash .\RapReaper-1.5.0-Windows.zip -Algorithm SHA256`
- **Mac** (Terminal, in the folder with the file): `shasum -a 256 RapReaper-1.5.0-Mac.zip` (or `RapReaper-1.5.0-Mac-Intel.zip`)

The result must match the file's line in [SHA256SUMS.txt](SHA256SUMS.txt). The updater does this check by itself when that file is next to the zip.

## Source code and building it yourself

The complete source is in [`source/`](source): the Python tool server and installer, the helper script that runs inside REAPER, the Local AI's download scripts and file list, the simulated REAPER and the tests (`sim/`, `tests/`), the release build (`build/`) and the Vocal Remover's Go source with all its dependencies (`VocalRemover/source`). Each zip carries the same source.

To build the three zips yourself you need Go 1.27.3 and Python 3.12 or newer, nothing else (no Xcode, no Visual Studio, no PyTorch), on Windows, macOS or Linux:

```
cd source
python3 build/fetch_runtime.py              # Microsoft's runtime libraries, each checked by SHA-256
python3 build/build_release.py --no-pdf     # the three zips and SHA256SUMS.txt into dist/
```

The Vocal Remover programs rebuild byte for byte with Go 1.27.3. [`source/BUILDING.md`](source/BUILDING.md) has the details, and `source/DEVELOPMENT.md` explains how to test. Every push to this branch makes GitHub build the three zips (Actions > build > Artifacts), and pushing a tag such as `v1.5.0` builds them and attaches them to the release ([`.github/workflows/build.yml`](.github/workflows/build.yml)).

## Licences and credits

RapReaper's own files are under the [MIT licence](LICENSE): you may use, change and share them, also commercially, as long as the licence notice stays with them. There is no warranty. Third-party parts keep their own terms, listed in `NOTICE.txt`, `VocalRemover/licenses/` and `LocalAI/licenses/` inside each zip. Among them are the REAPER bridge from [Reaper Daemon](https://github.com/wretcher207/reaper-daemon) by David Russell (MIT licence) and Microsoft's ONNX Runtime and DirectML. The Local AI uses [llama.cpp](https://github.com/ggml-org/llama.cpp) by the ggml authors (MIT) and Qwen 3.5 by the Qwen team (Apache 2.0), both downloaded from their authors by Get Local AI. The separation models are credited to their authors (Kimberley Jensen, unwa, viperx and the Ultimate Vocal Remover project). None of the models is part of RapReaper or covered by its licence; each person downloads them from their authors.
