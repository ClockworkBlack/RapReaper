# RapReaper 1.4.4 release notes

Mix rap vocals in REAPER by talking to Claude. RapReaper connects the Claude desktop app to REAPER on your own computer. You say what you want ("clean up my lead vocal", "a bit brighter", "make it loud enough for Spotify"), and Claude sets REAPER's own plug-ins, measures the audio before and after, and tells you what changed. Your audio stays on your computer.

## Downloads: pick the one for your computer

| Computer | Download | Size |
|---|---|---|
| 🪟 **Windows** (64-bit) | [**RapReaper-1.4.4-Windows.zip**](https://github.com/ClockworkBlack/RapReaper/raw/main/Windows/RapReaper-1.4.4-Windows.zip) | 28 MB |
| 🍎 **Mac** | [**RapReaper-1.4.4-Mac.zip**](https://github.com/ClockworkBlack/RapReaper/raw/main/Mac/RapReaper-1.4.4-Mac.zip) (the Vocal Remover needs Apple silicon, M1 or later) | 18 MB |
| Both | [**SHA256SUMS.txt**](SHA256SUMS.txt): checksums to check your download | under 1 KB |

The two zips aren't interchangeable. The Windows one has `.bat` double-click files and the Mac one has `.command` files, and the updater refuses the zip for the other system.

## Before you install (all free)

1. **REAPER**: https://www.reaper.fm/download.php (start it once, then close it)
2. **SWS extension**, through ReaPack: https://reapack.com
3. **Python 3.12 or newer**: https://www.python.org/downloads/
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

## What's new

- **1.4.4:** dips drawn with `duck_section` now really dip. Before, they came out as a small boost. RapReaper now uses its own small "RapReaper Ducks" effect for them.
- **1.4.3:** a heavy, slow-rendering project is now measured and exported in shorter stretches instead of being refused.

The details are in `README.txt` and `kit/CHANGES-1.4.md` inside the zip.

## Not included: the Vocal Remover's model

The Vocal Remover takes a finished song apart into the voice and the music. It needs a 240 MB model file, which isn't part of this release. Everything else in RapReaper works without it. If you have the model's pieces (`MelRoformer-KimFT2-int8.onnx.part1of8` to `part8of8`), put them in Downloads before you run the installer. The model's sources, and the tool that rebuilds it from the public checkpoint, are listed in `VocalRemover/licenses/Models - credits.txt`.

## Check your download

- **Windows** (PowerShell, in the folder with the file): `Get-FileHash .\RapReaper-1.4.4-Windows.zip -Algorithm SHA256`
- **Mac** (Terminal, in the folder with the file): `shasum -a 256 RapReaper-1.4.4-Mac.zip`

The result must match the file's line in `SHA256SUMS.txt`:

```
9221ee31512b44639ba649f2533c831a0d1a1c1293f1d5ed0d29af38822c1949  RapReaper-1.4.4-Windows.zip
92633b6089858eb3483bc346ed3bce24c3eb643c86d84b69aad70f4d367d6bd3  RapReaper-1.4.4-Mac.zip
```

## Security and privacy

What was checked for this release:

- Neither zip contains personal details, credentials or anyone's data. The folders RapReaper fills as you use it ship empty.
- The Vocal Remover programs (`VocalRemover.exe` and the Mac `VocalRemover`) rebuild byte for byte from the source in `VocalRemover/source`. The third-party Go code bundled with that source matches the official module copies.
- ONNX Runtime, DirectML and the Visual C++ runtime files are byte-identical to Microsoft's official releases.
- RapReaper itself makes no internet connections, and ONNX Runtime's usage reporting is switched off. The Vocal Remover's window is served only on this computer (127.0.0.1), behind a per-session key plus host and origin checks, so websites can't reach it.

Known limitations:

- The programs aren't code-signed by a company, so Windows SmartScreen and macOS show a warning the first time you open them.
- The Vocal Remover was built with Go 1.24.7. Go's web-server code has had security fixes since that version. The program's server only accepts connections from the same computer.
- "Get ffmpeg and Transcriber" downloads ffmpeg and whisper.cpp builds that have no fixed checksum. RapReaper only ever runs the exact file you allow afterwards with the "Allow" files.
- The guide PDFs were last rebuilt for 1.4.1 (Windows) and 1.4.2 (Mac). Later changes are listed in `README.txt` and `kit/CHANGES-1.4.md`.

More detail is in `SECURITY.txt` inside each zip.

## Licences

You may use, change and share RapReaper's own files freely. Third-party parts (Reaper Daemon by David Russell under the MIT licence, ONNX Runtime, DirectML, the Go libraries, the model credits) keep their own terms, listed in `NOTICE.txt` and `VocalRemover/licenses/` inside each zip.
