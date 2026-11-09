# RapReaper 1.4.6 release notes

Released 9 November 2026.

## Downloads: pick the one for your computer

| Computer | Download | Size |
|---|---|---|
| 🪟 **Windows** (64-bit) | [**RapReaper-1.4.6-Windows.zip**](https://github.com/ClockworkBlack/RapReaper/raw/main/Windows/RapReaper-1.4.6-Windows.zip) and the [Windows guide](Windows/RapReaper%20Guide%20%28Windows%29.pdf) | 29 MB |
| 🍎 **Mac with an Apple chip** | [**RapReaper-1.4.6-Mac.zip**](https://github.com/ClockworkBlack/RapReaper/raw/main/Mac/RapReaper-1.4.6-Mac.zip) and the [Mac guide](Mac/RapReaper%20Guide%20%28Mac%29.pdf) | 19 MB |
| 🍎 **Intel Mac** | [**RapReaper-1.4.6-Mac-Intel.zip**](https://github.com/ClockworkBlack/RapReaper/raw/main/Mac-Intel/RapReaper-1.4.6-Mac-Intel.zip) and the [Mac guide](Mac-Intel/RapReaper%20Guide%20%28Mac%29.pdf) | 18 MB |
| All | [**SHA256SUMS.txt**](SHA256SUMS.txt): checksums to check your download | under 1 KB |

The zips aren't interchangeable. The Windows one has `.bat` double-click files and the Mac ones have `.command` files, and the updater refuses a zip for another system or another kind of Mac.

## What's new in 1.4.6

- **The Vocal Remover runs on Intel Macs.** The new Mac-Intel zip has the Vocal Remover built for Intel processors, with ONNX Runtime 1.23.2, the last version Microsoft built for Intel Macs (macOS 13 or newer). The Vocal Remover is now version 2.0.3; on Windows and Apple-chip Macs nothing changes.
- **Get the model yourself.** **Get Vocal Remover Model** (`.bat` / `.command`) downloads the model's two source files from their author on Hugging Face, checks them, and builds the model in about a minute, byte for byte the published file. Model pieces still work.
- **MIT licence.** RapReaper's own files are now under the [MIT licence](LICENSE). Third-party parts keep their own licences (`NOTICE.txt`).
- **Build it yourself.** The full source is in [`source/`](source), with `build/fetch_runtime.py` to get Microsoft's runtime libraries (each checked by SHA-256) and a GitHub Actions workflow that builds all three zips. See [`source/BUILDING.md`](source/BUILDING.md).
- **The installer knows which Mac it's on.** It won't allow an Apple-chip Vocal Remover on an Intel Mac (RapReaper's own ONNX Runtime separates instead), and it tells you which zip to get. Update and Make Full Packages know the new zip name.

**Intel Mac on 1.4.5:** the 1.4.5 updater doesn't know the Intel zip. Put both Mac zips in Downloads and double-click Update RapReaper.command twice: the first run updates to 1.4.6, the second swaps in the Intel Vocal Remover.

The guide PDFs are still the 1.4.5 ones. Everything new is in this page, the README and `README.txt` inside the zip; the guides will be rebuilt with the next full test run.

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

- **Windows** (PowerShell, in the folder with the file): `Get-FileHash .\RapReaper-1.4.6-Windows.zip -Algorithm SHA256`
- **Mac** (Terminal, in the folder with the file): `shasum -a 256 RapReaper-1.4.6-Mac.zip` (or `RapReaper-1.4.6-Mac-Intel.zip`)

The result must match the file's line in `SHA256SUMS.txt`:

```
282cfbc8b4f31cdfae272b70196a4c3da8e3c039d403fb718b418cce34cc5378  RapReaper-1.4.6-Mac-Intel.zip
16a2e69f2c26495c6223b8439a86f6dcabe2d4a2a7ed9b254b6c11a7988cabde  RapReaper-1.4.6-Mac.zip
8c91112b5cb0269a5193876db077eaa67a9fde8380f1fa566772878ef7b95b82  RapReaper-1.4.6-Windows.zip
```

## How this release was tested

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
