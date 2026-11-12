# RapReaper 1.5.0 (nightly) release notes

Built 12 November 2026 on the Nightly branch. 1.4.6 is the current release, on the main branch.

## Downloads: pick the one for your computer

| Computer | Download | Size |
|---|---|---|
| 🪟 **Windows** (64-bit) | [**RapReaper-1.5.0-Windows.zip**](https://github.com/ClockworkBlack/RapReaper/raw/Nightly/Windows/RapReaper-1.5.0-Windows.zip) and the [Windows guide](Windows/RapReaper%20Guide%20%28Windows%29.pdf) | 29 MB |
| 🍎 **Mac with an Apple chip** | [**RapReaper-1.5.0-Mac.zip**](https://github.com/ClockworkBlack/RapReaper/raw/Nightly/Mac/RapReaper-1.5.0-Mac.zip) and the [Mac guide](Mac/RapReaper%20Guide%20%28Mac%29.pdf) | 19 MB |
| 🍎 **Intel Mac** | [**RapReaper-1.5.0-Mac-Intel.zip**](https://github.com/ClockworkBlack/RapReaper/raw/Nightly/Mac-Intel/RapReaper-1.5.0-Mac-Intel.zip) and the [Mac guide](Mac-Intel/RapReaper%20Guide%20%28Mac%29.pdf) | 18 MB |
| All | [**SHA256SUMS.txt**](SHA256SUMS.txt): checksums to check your download | under 1 KB |

The zips aren't interchangeable. The Windows one has `.bat` double-click files and the Mac ones have `.command` files, and the updater refuses a zip for another system or another kind of Mac.

## What's new in 1.5.0 (nightly)

RapReaper works with any AI app, and comes with an AI of its own.

- **Any AI app that speaks MCP.** Until now the installer connected only Claude Desktop, and without Claude it never finished. It now adds RapReaper to every AI app it finds:
  - Claude Desktop
  - ChatGPT's desktop app and Codex (`~/.codex/config.toml`)
  - Cursor
  - VS Code with GitHub Copilot, and VS Code Insiders
  - Windsurf
  - LM Studio
  - Gemini CLI

  Each settings file is copied before any change and read back after it. A VS Code file with comments in it is left untouched, and the installer says so. Any other MCP app gets ready-made settings in `kit/state/ai_app_settings.txt`. `--ai-apps claude,chatgpt` (or `none`) picks which apps to connect, and Uninstall takes RapReaper out of all of them.
- **RapReaper's Local AI.** **Get Local AI** downloads two things once, each checked against a fixed checksum:
  - [llama.cpp](https://github.com/ggml-org/llama.cpp)'s llama-server, build b12250 (MIT licence)
  - an open model, [Qwen 3.5](https://huggingface.co/Qwen/Qwen3.5-4B) (Apache 2.0): the 4B (2.7 GB) or the 9B (5.7 GB), chosen by the computer's memory

  **Start Local AI** opens a chat window in your browser with RapReaper connected. Nothing leaves your computer, and there's no account or subscription. The window asks before every tool runs. Start Local AI checks every llama.cpp file and refuses any file that doesn't belong. It checks that RapReaper's tools answer before it starts llama.cpp, and starts llama.cpp once more if its start-up got none of them. It falls back to the processor if the graphics card fails, and finds a free port by itself.
- **No more Claude-only wording.** Answers name the app you're using, so "quit Cursor completely and open it again" replaces "quit Claude". The privacy notes say who reads the chat:
  - Anthropic for Claude
  - OpenAI for ChatGPT and Codex
  - Google for Gemini
  - nobody for a model on your computer

  The record of what was sent is now `kit/logs/sent_to_ai.log`. Your old `sent_to_claude.log` files are still counted and cleaned up.
- **Tool definitions every app accepts.** Each option has one type, and free-form options are marked as such. RapReaper still checks every option itself, so nothing it refused before is accepted now. The 28 core tools come first, because a few apps only use the first 40 or so. `RAPREAPER_TOOLS=core` gives a small model just those 28 and a short working method (`kit/PLAYBOOK-CORE.md`).

Nothing changes in how you mix. The helper inside REAPER is 1.5.0 (only its version line changed), and the Vocal Remover is unchanged (2.0.3). Everything from 1.4.7 (nightly) is included; it's listed below.

**Tested with the real model:** the real llama.cpp server and the Qwen 3.5 4B model drove RapReaper on a simulated REAPER: they passed the self-test (27 of 27), put a whole vocal chain on a vocal, and balanced it against the beat. The Windows and Mac builds of llama.cpp haven't run on a real PC or Mac yet, so run the self-test in your first Local AI session. Still open: the optional BS-Roformer model still can't be rebuilt byte for byte from its public checkpoint, and the Intel Mac Vocal Remover hasn't run on a real Intel Mac.

## What was new in 1.4.7 (nightly)

- **"Get Vocal Remover Model" works on a normal computer.** It no longer needs the Python package PyYAML, which the installer never installed. The model still comes out byte for byte the published file.
- **The "media offline" preference stays off.** `fix_setup` and the self-test now also save it in REAPER's settings file (`reaper.ini`, keeping a copy of the old file next to it), so it no longer comes back on when REAPER restarts.
- **A half-finished update says so.** Every answer says which version is installed and which is running, and how to restart the app.
- **Real-time rendering is always caught**, even on a busy computer.
- **"Get Vocal Remover Model.bat" has Windows line endings.**
- **The full test suite runs anywhere**, on the Lua inside the `lupa` Python package when no Lua is installed.

The details for every version are in `README.txt`, `kit/CHANGES-1.5.md` and `kit/CHANGES-1.4.md` inside the zip.

## Before you install (all free)

1. **REAPER**: https://www.reaper.fm/download.php (start it once, then close it)
2. **SWS extension**, through ReaPack: https://reapack.com
3. **Python 3.12 or newer** (64-bit): https://www.python.org/downloads/
4. **An AI app**, if you want one: Claude Desktop, ChatGPT's desktop app, LM Studio, Cursor, VS Code, Windsurf, Gemini CLI or any other MCP app (start it once). Or skip this step and use RapReaper's free Local AI.

## First install on Windows

1. Unzip the file and put the `RapReaper` folder at `C:\Users\<your Windows name>\RapReaper`, not in Downloads or OneDrive.
2. Double-click **Install RapReaper.bat**. If it lists anything under "TO DO", do that and run it again until it says **ALL SET**.
3. Quit your AI app completely (Claude: right-click its icon near the clock, then Quit) and open it again. No AI app? Double-click **Get Local AI.bat** once, then **Start Local AI.bat**.
4. Start REAPER and open a project. In your AI app, type: `Run the RapReaper self-test`

## First install on a Mac

1. Download the zip for your Mac (Apple menu > About This Mac: "Chip: Apple…" takes the Mac zip, an Intel processor the Mac-Intel zip). Double-click it, then move the `RapReaper` folder into your home folder (in Finder, Go > Home).
2. Double-click **Install RapReaper.command**. The first time, macOS says it "cannot be opened": go to System Settings > Privacy & Security, click **Open Anyway**, then double-click it again.
3. Quit your AI app completely (Cmd+Q) and open it again. No AI app? Double-click **Get Local AI.command** once, then **Start Local AI.command**.
4. Start REAPER and open a project. In your AI app, type: `Run the RapReaper self-test`

On both systems, double-click **Get Vocal Remover Model** afterwards to get the Vocal Remover's model.

## Updating from 1.4.0 or newer

1. Put the new zip for your computer, and `SHA256SUMS.txt`, in your Downloads folder. Don't unzip the zip.
2. Double-click **Update RapReaper.bat** (Windows) or **Update RapReaper.command** (Mac) in your RapReaper folder. It checks the zip, replaces the program files, keeps your own files, and runs the new installer, which connects every AI app it finds.
3. Close REAPER and start it again once, and quit and reopen your AI app.

## Check your download

- **Windows** (PowerShell, in the folder with the file): `Get-FileHash .\RapReaper-1.5.0-Windows.zip -Algorithm SHA256`
- **Mac** (Terminal, in the folder with the file): `shasum -a 256 RapReaper-1.5.0-Mac.zip` (or `RapReaper-1.5.0-Mac-Intel.zip`)

The result must match the file's line in `SHA256SUMS.txt`:

```
2e4ad3549c98ecdf04298a759ac6736016cd5bafe467b149f400923da7b993db  RapReaper-1.5.0-Mac-Intel.zip
39b6fbcb28aa6b03d3409ed1839a8da445fb431057d71b4e34e46c9309bcea3b  RapReaper-1.5.0-Mac.zip
16cfe1de8b9de89e975e54f9ff19e9a920b5522f56eeebe94152e4c78bb040a3  RapReaper-1.5.0-Windows.zip
```

## How this build was tested

What was run for 1.5.0 (nightly):

- **The whole test suite passed:** all 150 measuring-code checks and all 1,280 end-to-end checks, in all 48 scenarios. Two scenarios are new: `ai_apps` and `local_ai` (46 checks). Every scenario that runs the installer was run again on the final files, after the last changes to the Local AI's launcher.
- **Each AI app's handshake.** RapReaper's tool server was started the way each app starts it: Claude, ChatGPT and Codex, Cursor, VS Code, LM Studio, Gemini CLI, llama.cpp, an app that doesn't give its name, and the 2026-07-28 protocol, where the app's name comes with each request. Each answer named the right app and carried the right privacy note. The tool definitions were in the portable form, and everything refused before was still refused.
- **The installer, on stand-in Windows and Mac profiles** with every supported app present:
  - one entry added to each settings file (JSON, VS Code's `servers`, Codex's TOML), each file backed up first
  - a file with comments, a damaged file and an unusual TOML layout left untouched, with a note
  - the `--ai-apps` choices honoured
  - Uninstall taking every entry out again

  No test touched the AI apps of the computer it ran on.
- **The Local AI's launcher, with a stand-in llama-server:**
  - llama-server gets the right arguments: the model, RapReaper as its MCP server, this computer only, a 32k context, thinking off
  - it moves to the next port when one is taken, and retries on the processor alone
  - when llama.cpp's start-up gets none of RapReaper's tools, it starts llama.cpp once more, and stops for good after a second time; a tool server that can't list its tools stops it before llama.cpp starts
  - a planted library, a changed file and a wrong model are each refused, while the `.DS_Store`, `Thumbs.db` and `desktop.ini` files Finder and Explorer leave are let through
  - Get Local AI's Windows and Mac scripts, the installer and the shipped file list pin the same files and checksums
- **The Local AI's downloads.** The llama.cpp b12250 archives for Windows (Vulkan), Apple-chip Macs and Intel Macs, and the Qwen 3.5 4B model, were downloaded from their authors here. Their SHA-256 checksums match the ones in the scripts, and every file in the archives is in `LocalAI/runtime-b12250.json`. The 9B model's checksum is the one Hugging Face publishes for the pinned revision; that 5.7 GB file wasn't downloaded here.
- **Updating from 1.4.7** was simulated with the shipped 1.4.7 zips on stand-in Windows, Mac and Intel Mac profiles, using 1.4.7's own updater. Each one became 1.5.0:
  - the old files were backed up
  - the person's chains, references and mix log were kept
  - every installed file matched the 1.5.0 checksum list
  - the Local AI's files were in place, and the new installer connected Claude
- **The unchanged parts are the same files:** the Vocal Remover programs and Microsoft's libraries in the three zips are byte for byte the ones in 1.4.7.
- **GitHub rebuilt it from source:** GitHub's own build of this commit (with Go 1.27.3 rebuilding the Vocal Remover programs) made three zips whose every file is byte for byte the one in the published zips. Only the compression of the zip files differs.
- **No personal data:** every file of the three zips (programs and PDFs included) and the zips' own metadata were searched. There are no names, e-mail addresses, folder paths, account names or keys of anyone's, and no e-mail address that 1.4.7 didn't already carry (those are third-party licence authors and the tests' invented people).
- **The real Local AI, end to end.** The real llama-server (b12250, the Linux build of the same release) with the real Qwen 3.5 4B file, RapReaper as its MCP server exactly as Start Local AI sets it up, and the simulated REAPER, driven the way llama.cpp's chat window drives it:
  - its start-up found all 28 RapReaper tools
  - asked to run the self-test, the model called status, the playbook and the self-test, and reported 27 of 27 passed
  - asked for a vocal chain, it planned, then ran every stage; after a yes, it balanced the vocal against the beat. Its summaries matched the tools' own numbers
  - on the build machine's 2 processor cores, each answer took one to seven minutes; a PC with more cores or a graphics card is much quicker
  - the first answer got quicker: the Local AI now gets short descriptions of the longest tools, so the model reads about 15% less before its first answer (180 seconds instead of 249 on the build machine)
  - the model sometimes carried on to the next step without being asked (balancing after a vocal chain); its instructions now say to stop and offer the next step
  - one problem found and fixed: by default llama.cpp keeps up to 8 GB of earlier conversations in memory, and it ran out of memory there. Start Local AI now caps that at 1 GB, and the same conversation then ran to the end
- **Not run here:**
  - llama.cpp's Windows and Mac builds (the same release, built for those systems)
  - RapReaper with the real AI apps and REAPER on a real PC or Mac (as with every release; the self-test checks the main points on your own REAPER)
  - a real Intel Mac

What was run for 1.4.7 (nightly):

- **The whole test suite passed:** all 150 measuring-code checks and all 1,248 end-to-end checks, in all 46 scenarios. This is the first full end-to-end run since 1.4.5. It ran the simulated REAPER on the Lua inside the `lupa` Python package, because the build machine has no Lua installed.
- **Each fix has its own new checks** in that suite: the model's settings read without PyYAML (and settings it can't read refused), the offline preference saved in `reaper.ini` (Windows and Mac line ends, a file without the line, no file at all), a tool server left running after an update, real-time rendering on a busy computer, and the `.bat` line ends. The real-time and `.bat` checks fail on 1.4.6's files and pass on these.
- **Model build without PyYAML:** "Get Vocal Remover Model" was run from the new Windows zip on a stand-in Windows profile, with a Python that has NumPy and SciPy but no PyYAML. The model came out with the published checksum, byte for byte.
- **Updating from 1.4.6** was simulated with the shipped 1.4.6 zips on stand-in Windows, Mac and Intel Mac profiles. Each one became 1.4.7, with the old files backed up and the person's own chains, references and exports kept. Every installed file matched the 1.4.7 checksum list.
- **The unchanged parts are the same files:** the Vocal Remover programs and Microsoft's libraries in the three zips are byte for byte the ones in 1.4.6.
- **Three problems in the tests themselves** came to light in the first full run and are fixed; the run above is the second, on the final files. On a busy machine, the simulated REAPER could show a test the project from just before a render. The Intel Mac scenario picked up settings left behind by earlier scenarios in the same run. And a check on the imports folder didn't expect the `.keep` placeholder file the shipped folders have had since 1.4.6.
- **No personal data:** every file of the three zips (programs and PDFs included) and the zips' own metadata were searched again. There are no names, e-mail addresses, folder paths, account names or keys of anyone's, and no e-mail address that 1.4.6 didn't already carry (those are third-party licence authors and the tests' invented people).
- **Not tested here:** RapReaper on a real Windows PC or Mac with REAPER and Claude (as with every release; the self-test checks the main points on your own REAPER), and on a real Intel Mac.

## Security and privacy

What was checked for this release:

- **No personal data.** Every file of the three zips was searched, programs and PDFs included, plus the zips' own metadata. There are no names, e-mail addresses, folder paths, account names or keys of anyone's.
- **The AI apps' settings are handled with care.** The installer copies each settings file before changing it, and writes it in one step. A TOML file is read back and must match what was there before plus RapReaper's entry, or nothing is written. A file with comments is never rewritten. A test run never touches the AI apps of the computer it runs on.
- **The Local AI starts only what was checked.** Get Local AI downloads from one pinned llama.cpp release and one pinned model revision, each with a fixed SHA-256. Start Local AI checks every file against the list shipped in the zip, and refuses anything extra, because llama.cpp loads the libraries next to it. (The `.DS_Store`, `Thumbs.db` and `desktop.ini` files that Finder and Explorer leave in an opened folder are let through.) llama-server answers only this computer and refuses other web pages, and its chat window asks before every tool call.
- **Still no network code in RapReaper.** The Local AI's launcher starts a program and a browser. The only downloads are the optional **Get …** files you start yourself.
- **The unchanged parts are the same files.** The Vocal Remover programs and Microsoft's libraries in the three zips are byte for byte the ones in 1.4.7 and 1.4.6.

Known limitations:

- The programs aren't code-signed by a company, so Windows SmartScreen and macOS show a warning the first time you open them.
- A small local model is easier to mislead than a large online one. RapReaper's safeguards don't depend on the model:
  - a plan before any cut
  - the person's yes before anything is removed
  - one undo step per change
  - restore points for cuts
- "Get ffmpeg and Transcriber" downloads ffmpeg and whisper.cpp builds that have no fixed checksum. RapReaper only ever runs the exact file you allow afterwards with the "Allow" files.

## Licences

RapReaper's own files are under the [MIT licence](LICENSE). Third-party parts keep their own licences: see `NOTICE.txt`, `VocalRemover/licenses/` and `LocalAI/licenses/` in each zip. llama.cpp (MIT) and Qwen 3.5 (Apache 2.0) aren't in the zips. Get Local AI downloads them from their authors.
