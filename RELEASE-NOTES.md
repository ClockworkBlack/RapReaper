# RapReaper 1.4.7 (nightly) release notes

Built 9 November 2026 on the Nightly branch. 1.4.6 is the current release, on the main branch.

## Downloads: pick the one for your computer

| Computer | Download | Size |
|---|---|---|
| 🪟 **Windows** (64-bit) | [**RapReaper-1.4.7-Windows.zip**](https://github.com/ClockworkBlack/RapReaper/raw/Nightly/Windows/RapReaper-1.4.7-Windows.zip) and the [Windows guide](Windows/RapReaper%20Guide%20%28Windows%29.pdf) | 29 MB |
| 🍎 **Mac with an Apple chip** | [**RapReaper-1.4.7-Mac.zip**](https://github.com/ClockworkBlack/RapReaper/raw/Nightly/Mac/RapReaper-1.4.7-Mac.zip) and the [Mac guide](Mac/RapReaper%20Guide%20%28Mac%29.pdf) | 19 MB |
| 🍎 **Intel Mac** | [**RapReaper-1.4.7-Mac-Intel.zip**](https://github.com/ClockworkBlack/RapReaper/raw/Nightly/Mac-Intel/RapReaper-1.4.7-Mac-Intel.zip) and the [Mac guide](Mac-Intel/RapReaper%20Guide%20%28Mac%29.pdf) | 18 MB |
| All | [**SHA256SUMS.txt**](SHA256SUMS.txt): checksums to check your download | under 1 KB |

The zips aren't interchangeable. The Windows one has `.bat` double-click files and the Mac ones have `.command` files, and the updater refuses a zip for another system or another kind of Mac.

## What's new in 1.4.7 (nightly)

Fixes for every problem met with RapReaper so far that was still open and can be fixed in RapReaper itself:

- **"Get Vocal Remover Model" works on a normal computer.** In 1.4.6 it downloaded and checked the model's source files, then stopped at the build step: the builder needed the Python package PyYAML, which RapReaper's installer never installs (the test computer happened to have it). It now reads the model's settings file by itself, and the model still comes out byte for byte the published file.
- **The "media offline" preference stays off.** REAPER's "Set media items offline when application is not active" came back on every time REAPER started (seen on 7 and 10 October), so the first measurement after each restart was silent. `fix_setup` and the self-test switched it off in the running REAPER only; they now also save it in REAPER's settings file (`reaper.ini`, keeping a copy of the old file next to it). `status` says when it would come back at the next start.
- **A half-finished update says so.** After Update RapReaper, Claude kept running the previous version until Claude itself was restarted, and new options were refused without saying why (4, 7 and 23 October). Every answer now says which version is installed and which is running, and to quit Claude completely and open it again.
- **Real-time rendering is always caught.** A REAPER set to render in real time could slip past the speed check when the computer was busy, so a long measurement then ran in real time. Both test renders lasting their full length now counts as real time too.
- **"Get Vocal Remover Model.bat" has Windows line endings.** In 1.4.6 it was the only .bat file with Unix line endings, which Windows' command prompt can misread.
- **The full test suite runs anywhere.** 1.4.6 shipped without its full end-to-end run because the build machine had no Lua 5.4. The tests now run the simulated REAPER on the Lua inside the `lupa` Python package when there is no Lua installed (`tests/lua54_standin.py`), and this build passed the whole suite.
- **Small things:** the Mac guide named the Windows model getter (`.bat`); the zips now include `build/fetch_runtime.py`, which `BUILDING.md` refers to; the guides are rebuilt for 1.4.7.

Nothing changes in how you use RapReaper. The helper inside REAPER is 1.4.7 (only its version line changed), and the Vocal Remover is unchanged (2.0.3).

**Still open:** the optional BS-Roformer model (the "Two networks" and BS-Roformer modes) still can't be rebuilt byte for byte from its public checkpoint, so it's only used when you already have the file. The Intel Mac Vocal Remover has been compiled and checked but not yet run on a real Intel Mac.

## What was new in 1.4.6

- **The Vocal Remover runs on Intel Macs.** The new Mac-Intel zip has the Vocal Remover built for Intel processors, with ONNX Runtime 1.23.2, the last version Microsoft built for Intel Macs (macOS 13 or newer). The Vocal Remover is now version 2.0.3; on Windows and Apple-chip Macs nothing changes.
- **Get the model yourself.** **Get Vocal Remover Model** (`.bat` / `.command`) downloads the model's two source files from their author on Hugging Face, checks them, and builds the model in about a minute, byte for byte the published file. Model pieces still work.
- **MIT licence.** RapReaper's own files are now under the [MIT licence](LICENSE). Third-party parts keep their own licences (`NOTICE.txt`).
- **Build it yourself.** The full source is in [`source/`](source), with `build/fetch_runtime.py` to get Microsoft's runtime libraries (each checked by SHA-256) and a GitHub Actions workflow that builds all three zips. See [`source/BUILDING.md`](source/BUILDING.md).
- **The installer knows which Mac it's on.** It won't allow an Apple-chip Vocal Remover on an Intel Mac (RapReaper's own ONNX Runtime separates instead), and it tells you which zip to get. Update and Make Full Packages know the new zip name.

**Intel Mac on 1.4.5:** the 1.4.5 updater doesn't know the Intel zip. Put both Mac zips in Downloads and double-click Update RapReaper.command twice: the first run updates to 1.4.7, the second swaps in the Intel Vocal Remover.

## What was new in 1.4.5

- **The Vocal Remover is rebuilt with Go 1.27.3**, the current release of the language it's written in. It was built with Go 1.24.7, which no longer gets fixes and has had security fixes since. Its code is otherwise unchanged; it's now version 2.0.2. Go's vulnerability checker (`govulncheck`) now finds no known issues in it on either system.
- **Both guides are rebuilt for 1.4.5.** The Windows PDF still said 1.4.1 and the Mac PDF 1.4.2. The Mac guide now writes the Ducks effect's folder the Mac way (`Effects/RapReaper/ducks.jsfx`).
- **Requirements, now stated correctly:** the Mac Vocal Remover needs **macOS 14 or newer** on Apple silicon. Its ONNX Runtime library is built for macOS 14; the Vocal Remover's README used to say macOS 13.
- **README corrections:** the Windows README now counts all 57 tools (it said 56), and both file lists include `kit/effects`.

Nothing else works differently. Your chains, settings, projects and model files stay as they are when you update.

Earlier changes:

- **1.4.4:** dips drawn with `duck_section` now really dip. Before, they came out as a small boost. RapReaper now uses its own small "RapReaper Ducks" effect for them.
- **1.4.3:** a heavy, slow-rendering project is now measured and exported in shorter stretches instead of being refused.

The details for every version are in `README.txt` and `kit/CHANGES-1.4.md` inside the zip.

## Before you install (all free)

1. **REAPER**: https://www.reaper.fm/download.php (start it once, then close it)
2. **SWS extension**, through ReaPack: https://reapack.com
3. **Python 3.12 or newer** (64-bit): https://www.python.org/downloads/
4. **Claude desktop app**: https://claude.com/download (sign in once)

## First install on Windows

1. Unzip the file and put the `RapReaper` folder at `C:\Users\<your Windows name>\RapReaper`, not in Downloads or OneDrive.
2. Double-click **Install RapReaper.bat**. If it lists anything under "TO DO", do that and run it again until it says **ALL SET**.
3. Quit Claude completely (right-click its icon near the clock, then Quit) and open it again. Start REAPER and open a project.
4. In Claude, type: `Run the RapReaper self-test`

## First install on a Mac

1. Download the zip for your Mac (Apple menu > About This Mac: "Chip: Apple…" takes the Mac zip, an Intel processor the Mac-Intel zip). Double-click it, then move the `RapReaper` folder into your home folder (in Finder, Go > Home).
2. Double-click **Install RapReaper.command**. The first time, macOS says it "cannot be opened": go to System Settings > Privacy & Security, click **Open Anyway**, then double-click it again.
3. Quit Claude completely and open it again. Start REAPER and open a project.
4. In Claude, type: `Run the RapReaper self-test`

On both systems, double-click **Get Vocal Remover Model** afterwards to get the Vocal Remover's model (below).

The full steps are in `README.txt` and the guide PDF inside each zip. Setting up a microphone or audio interface in REAPER is covered in `AUDIO INTERFACE SETUP.txt`.

## Updating from 1.4.0 or newer

1. Put the new zip for your computer, and `SHA256SUMS.txt`, in your Downloads folder. Don't unzip the zip.
2. Double-click **Update RapReaper.bat** (Windows) or **Update RapReaper.command** (Mac) in your RapReaper folder. It checks the zip, replaces the program files, keeps your chains, settings, mix log, references, exports and model files, and runs the new installer.
3. Close REAPER and start it again once.

## The Vocal Remover's model: one double-click

The Vocal Remover takes a finished song apart into the voice and the music. It needs a 237 MB model file, which isn't part of this release: its author published it without a licence that lets anyone else pass it on. Everything else in RapReaper works without it.

After installing, double-click **Get Vocal Remover Model** (`.bat` or `.command`). It downloads the model's two source files from [unwa's Hugging Face page](https://huggingface.co/pcunwa/Kim-Mel-Band-Roformer-FT) (about 900 MB), checks them, builds the model in about a minute and checks the result against the published checksum. Model pieces (`MelRoformer-KimFT2-int8.onnx.part1of8` to `part8of8`) in Downloads still work too. The sources of every model are listed in `VocalRemover/licenses/Models - credits.txt`.

## Check your download

- **Windows** (PowerShell, in the folder with the file): `Get-FileHash .\RapReaper-1.4.7-Windows.zip -Algorithm SHA256`
- **Mac** (Terminal, in the folder with the file): `shasum -a 256 RapReaper-1.4.7-Mac.zip` (or `RapReaper-1.4.7-Mac-Intel.zip`)

The result must match the file's line in `SHA256SUMS.txt`:

```
fcc7c56ba46b2eb1d4cc1cf0eb2ec07ee15deaf3b369cf07b5b8c853cf996bb8  RapReaper-1.4.7-Mac-Intel.zip
c319f96398e0ec5ecd87610c309d7be6658603e7e5868e10c0b8312656fbc374  RapReaper-1.4.7-Mac.zip
b522c6d63c1001ecaaa85af3f53f93e11c3f47c0b4823f703ec077ca43d1bc82  RapReaper-1.4.7-Windows.zip
```

## How this build was tested

What was run for 1.4.7 (nightly):

- **The whole test suite passed:** all 150 measuring-code checks and all 1,248 end-to-end checks, in all 46 scenarios. This is the first full end-to-end run since 1.4.5. It ran the simulated REAPER on the Lua inside the `lupa` Python package, because the build machine has no Lua installed.
- **Each fix has its own new checks** in that suite: the model's settings read without PyYAML (and settings it can't read refused), the offline preference saved in `reaper.ini` (Windows and Mac line ends, a file without the line, no file at all), a tool server left running after an update, real-time rendering on a busy computer, and the `.bat` line ends. The real-time and `.bat` checks fail on 1.4.6's files and pass on these.
- **Model build without PyYAML:** "Get Vocal Remover Model" was run from the new Windows zip on a stand-in Windows profile, with a Python that has NumPy and SciPy but no PyYAML. The model came out with the published checksum, byte for byte.
- **Updating from 1.4.6** was simulated with the shipped 1.4.6 zips on stand-in Windows, Mac and Intel Mac profiles. Each one became 1.4.7, with the old files backed up and the person's own chains, references and exports kept. Every installed file matched the 1.4.7 checksum list.
- **The unchanged parts are the same files:** the Vocal Remover programs and Microsoft's libraries in the three zips are byte for byte the ones in 1.4.6.
- **Three problems in the tests themselves** came to light in the first full run and are fixed; the run above is the second, on the final files. On a busy machine, the simulated REAPER could show a test the project from just before a render. The Intel Mac scenario picked up settings left behind by earlier scenarios in the same run. And a check on the imports folder didn't expect the `.keep` placeholder file the shipped folders have had since 1.4.6.
- **No personal data:** every file of the three zips (programs and PDFs included) and the zips' own metadata were searched again. There are no names, e-mail addresses, folder paths, account names or keys of anyone's, and no e-mail address that 1.4.6 didn't already carry (those are third-party licence authors and the tests' invented people).
- **Not tested here:** RapReaper on a real Windows PC or Mac with REAPER and Claude (as with every release; the self-test checks the main points on your own REAPER), and on a real Intel Mac.

What was run for 1.4.6:

- **The source rebuilds 1.4.5 exactly.** Before any change, the source in `source/` was built and gave the shipped 1.4.5 Vocal Remover programs byte for byte, and zips whose every file matched the 1.4.5 checksum lists.
- **Intel Mac runtime:** the Vocal Remover separated a test song through ONNX Runtime 1.23.2 (the Intel Mac version's API, on Linux) with about 30 dB of separation for the voice. The Intel Mac program itself was compiled and checked, not run: no Mac was available.
- **Model build:** built from unwa's public files, the model came out with the published checksum, byte for byte. A changed source file is refused by name.
- **Runtime libraries:** every Microsoft file `build/fetch_runtime.py` downloads is identical to the one shipped in 1.4.5.
- **Tests:** all 150 measuring-code checks pass; the installer, Mac installer and update, protocol and new Intel Mac scenarios pass (112 of 112 checks, before the point where the simulated REAPER needs Lua). The rest of the end-to-end suite needs Lua 5.4, which wasn't available in the build environment, so it wasn't run for 1.4.6. The tool server itself didn't change apart from its version line.
- **Updating an Intel Mac from 1.4.5** was simulated: the first update gives 1.4.6 and refuses the Apple-chip Vocal Remover with the Intel zip named, the second swaps in the Intel one and allows it.

For 1.4.5:

- **RapReaper's own tests:** 150 measuring-code checks and 1,206 end-to-end checks passed. The end-to-end tests drive the real tool server, bridge script and helper against a simulated REAPER, and include the Windows and Mac installers, updating from a zip, privacy and hardening.
- **Updating from 1.4.4:** a simulated update from the shipped 1.4.4 zips worked on stand-in Windows and Mac profiles. The person's own chains, references and settings were kept, the old files were backed up, and every installed file matched the 1.4.5 checksum list.
- **The Vocal Remover:** its Go tests pass. A Linux build of the same source separated a test song, with about 30 dB of separation for both voice and music, and the two results added back to the song exactly. Its window server still refuses look-alike host names, other sites' pages and requests without its one-time key.

## Security and privacy

What was checked for this release:

- **No personal data.** Every file of both zips was searched, programs and PDFs included, plus the zips' own metadata. There are no names, e-mail addresses, folder paths, account or computer names, keys or passwords of anyone's. The test data uses invented names, and the folders RapReaper fills as you use it ship empty.
- **Programs match their source.** Both Vocal Remover programs rebuild byte for byte from `VocalRemover/source` with Go 1.27.3. The third-party Go code bundled with that source matches the official module copies.
- **Microsoft files are genuine.** ONNX Runtime, DirectML and the Visual C++ runtime files are byte-identical to Microsoft's official releases.
- **No network access.** RapReaper itself makes no internet connections, and ONNX Runtime's usage reporting is switched off. The Vocal Remover's window is served only on this computer (127.0.0.1), behind a per-session key plus host and origin checks.
- **Clean scans.** `govulncheck` finds no known vulnerabilities in the Vocal Remover, and the Python code analyser (bandit) finds nothing of high severity.

Known limitations:

- The programs aren't code-signed by a company, so Windows SmartScreen and macOS show a warning the first time you open them.
- "Get ffmpeg and Transcriber" downloads ffmpeg and whisper.cpp builds that have no fixed checksum. RapReaper only ever runs the exact file you allow afterwards with the "Allow" files.

More detail is in `SECURITY.txt` inside each zip.

## Licences

RapReaper's own files are under the [MIT licence](LICENSE). Third-party parts keep their own terms, listed in `NOTICE.txt` and `VocalRemover/licenses/` inside each zip. These include Reaper Daemon by David Russell (MIT licence), ONNX Runtime, DirectML, the Go libraries, and the credits for the separation models.
