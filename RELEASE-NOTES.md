# RapReaper 1.4.5 release notes

Released 9 November 2026.

## Downloads: pick the one for your computer

| Computer | Download | Size |
|---|---|---|
| 🪟 **Windows** (64-bit) | [**RapReaper-1.4.5-Windows.zip**](https://github.com/ClockworkBlack/RapReaper/raw/main/Windows/RapReaper-1.4.5-Windows.zip) and the [Windows guide](Windows/RapReaper%20Guide%20%28Windows%29.pdf) | 29 MB |
| 🍎 **Mac** | [**RapReaper-1.4.5-Mac.zip**](https://github.com/ClockworkBlack/RapReaper/raw/main/Mac/RapReaper-1.4.5-Mac.zip) and the [Mac guide](Mac/RapReaper%20Guide%20%28Mac%29.pdf) | 19 MB |
| Both | [**SHA256SUMS.txt**](SHA256SUMS.txt): checksums to check your download | under 1 KB |

The two zips aren't interchangeable. The Windows one has `.bat` double-click files and the Mac one has `.command` files, and the updater refuses the zip for the other system.

## What's new in 1.4.5

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

1. Double-click the zip, then move the `RapReaper` folder into your home folder (in Finder, Go > Home).
2. Double-click **Install RapReaper.command**. The first time, macOS says it "cannot be opened": go to System Settings > Privacy & Security, click **Open Anyway**, then double-click it again.
3. Quit Claude completely and open it again. Start REAPER and open a project.
4. In Claude, type: `Run the RapReaper self-test`

The full steps are in `README.txt` and the guide PDF inside each zip. Setting up a microphone or audio interface in REAPER is covered in `AUDIO INTERFACE SETUP.txt`.

## Updating from 1.4.0 or newer

1. Put the new zip, and `SHA256SUMS.txt`, in your Downloads folder. Don't unzip the zip.
2. Double-click **Update RapReaper.bat** (Windows) or **Update RapReaper.command** (Mac) in your RapReaper folder. It checks the zip, replaces the program files, keeps your chains, settings, mix log, references, exports and model files, and runs the new installer.
3. Close REAPER and start it again once.

## Not included: the Vocal Remover's model

The Vocal Remover takes a finished song apart into the voice and the music. It needs a 240 MB model file, which isn't part of this release. Everything else in RapReaper works without it. If you have the model's pieces (`MelRoformer-KimFT2-int8.onnx.part1of8` to `part8of8`), put them in Downloads before you run the installer. The model's sources, and the tool that rebuilds it from the public checkpoint, are listed in `VocalRemover/licenses/Models - credits.txt`.

## Check your download

- **Windows** (PowerShell, in the folder with the file): `Get-FileHash .\RapReaper-1.4.5-Windows.zip -Algorithm SHA256`
- **Mac** (Terminal, in the folder with the file): `shasum -a 256 RapReaper-1.4.5-Mac.zip`

The result must match the file's line in `SHA256SUMS.txt`:

```
96625b28523d826b15c7043b9e74afadac611aa440f591f3b281b66a83961541  RapReaper-1.4.5-Mac.zip
5fd47d744cb74884fb733338e58c3ef276fcb4e28b71d44b1a6d159e72e489fc  RapReaper-1.4.5-Windows.zip
```

## How this release was tested

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

You may use, change and share RapReaper's own files freely. Third-party parts keep their own terms, listed in `NOTICE.txt` and `VocalRemover/licenses/` inside each zip. These include Reaper Daemon by David Russell (MIT licence), ONNX Runtime, DirectML, the Go libraries, and the credits for the separation models.
