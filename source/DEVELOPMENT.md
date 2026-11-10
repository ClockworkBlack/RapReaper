# Working on RapReaper (for whoever changes it next, person or Claude)

This is the source tree of RapReaper. One tree gives three packages: `RapReaper-<version>-Windows.zip`,
`RapReaper-<version>-Mac.zip` (Apple chip) and `RapReaper-<version>-Mac-Intel.zip`. RapReaper's own files are under
the MIT licence (`LICENSE.txt`); `BUILDING.md` is the short version of this file for someone building from GitHub. Everything below runs on Linux or macOS with Python 3.12+ and Lua 5.4; nothing here
needs Windows, REAPER or Claude Desktop (a simulated REAPER stands in for them).

## What is where

| Path | What |
|---|---|
| `kit/rapreaper_server.py` | the tool server Claude Desktop starts (MCP over stdio); every tool is a `t_...` function registered in `TOOLS` |
| `kit/rapreaper_helper.lua` | RapReaper's own script inside REAPER (clip edits, project open/close/save, moving and importing clips, plug-in settings blocks); talks to the server through `kit/state/helper/inbox` and `outbox` |
| `kit/reaper-daemon/` | the vendored bridge (Reaper Daemon, MIT, **unmodified**; its checksums are in the installer's `BRIDGE_SHA256` and in NOTICE.txt) |
| `kit/rapreaper_audio.py` | measuring (loudness, noise, tone, sibilance, gaps, breaths, clarity ...) |
| `kit/rapreaper_music.py` | tempo, key, note values, lining two recordings up (`align`), the reference cards, the DAW export notes, `wav_info` |
| `kit/rapreaper_effects.py`, `kit/rapreaper_separate.py` | learning a song's vocal effects; vocal separation (the Vocal Remover program when it is allowed, else the same Roformer/MDX models through ONNX Runtime) |
| `VocalRemover/` | the Vocal Remover 2.0: `source/` (Go, no cgo; `vendor/` holds purego, flac, oggvorbis), `licenses/`, `README.txt`, `Vocal Remover.command` (Mac launcher, checksums filled by the build). The binaries and the runtime libraries are not in the tree: `build/vr_runtime/` holds the ONNX Runtime / DirectML files the build packs, `build/out/` the compiled programs |
| `kit/rapreaper_platform.py` | everything that differs between Windows and macOS: folders, Claude's settings place, the words answers use (`wording`), the home-folder placeholder. `RAPREAPER_PLATFORM=mac|windows|linux` overrides the detection (tests) |
| `kit/rapreaper_programs.py` | the one door through which an allowed program (ffmpeg, whisper.cpp) is started |
| `kit/PLAYBOOK.md` | the working method Claude reads at the start of a conversation (`playbook` tool) |
| `rapreaper_install.py` | the installer (both systems): Python check, folder check, packages (a venv on a Mac), REAPER's start-up blocks and reaper.ini, Claude's settings, a self-check; `--update` (from a zip), `--allow-program`, `--allow-vocal-remover` |
| `*.bat` / `*.command` | the double-click files (Windows / Mac). The `.command` files find a Python 3.12+ (never Apple's stub) and run the installer |
| `mac/` | the Mac versions of README.txt and AUDIO INTERFACE SETUP.txt (the root ones are Windows) |
| `docs/` | the guides' sources (`guide_windows.md`, `guide_mac.md`) and the built PDFs |
| `sim/` | the simulated REAPER (`fake_reaper.lua`: tracks, items, plug-in models, renders, project tabs) and the plug-in sound models |
| `tests/` | `test_audio.py` (the measuring and music code, ~3 minutes) and `e2e.py` (the whole thing end to end, ~1.5-2 hours) |
| `build/` | `build_release.py` (both zips, manifest, SHA256SUMS, the Vocal Remover built with Go), `stamp_helper_hash.py`, `build_guides.py` (markdown to PDF), `make_mac_guide.py` (the Mac guide from the Windows one), `build_roformer_onnx.py` + `onnxwriter.py` + `torch_ckpt.py` (a Roformer model file from a public checkpoint, no PyTorch), `eval_separation.py` + `roformer_numpy.py` (the quality measurements), `eval.json` (their results), `test_counts.json` (the numbers the guides quote) |
| `kit/CHANGES-1.4.md`, `kit/CHANGES-1.3.md`, `kit/CHANGES-older.md` | every problem met and the change it got |

## The Vocal Remover

- Build: `cd VocalRemover/source && CGO_ENABLED=0 go build -mod=vendor -trimpath -buildvcs=false -ldflags "-s -w" .`
  (GOOS=windows GOARCH=amd64 for the exe, GOOS=darwin GOARCH=arm64 for Apple-chip Macs, GOOS=darwin GOARCH=amd64 for
  Intel Macs, which run ONNX Runtime 1.23.2, the last Intel Mac build; its checksum is `macIntelRuntimeSHA256` in engine.go). `go test -mod=vendor ./internal/dsp/
  ./internal/onnxconv/`. The release build does all of this. Releases are built with Go 1.27.3 (since 2.0.2; 2.0.1 was
  Go 1.24.7; `go.mod` says `go 1.27`, so Go's current default settings apply): only the same Go version gives byte-identical programs, so a release check rebuilds with that version.
  Run `govulncheck ./...` (golang.org/x/vuln) for both GOOS values before a release.
- Test it here: put the Linux binary, `libonnxruntime.so` (ONNX Runtime 1.30.0 Linux x64) and the model files in one folder
  and run `./VocalRemover --in song.wav --quality mel`; `--no-browser` starts the window server for a headless-browser test
  (Playwright drives it in the release checks). The graphics processor (DirectML, CoreML) cannot run here: the program's own
  `--gpu-test` answers that on the person's computer.
- Models: `rapreaper_install.py --build-model` (and the installer's step 7) builds the Mel model from the two files
  `Get Vocal Remover Model.bat/.command` downloads (`VR_BUILDABLE` in the installer: names, sizes, checksums), with
  `build/build_roformer_onnx.py <config.yaml> <ckpt> <out.onnx> 801 --int8`, which builds the processor file from a public
  checkpoint (Kim FT2: config_kimmel_unwa_ft.yaml + kimmel_unwa_ft2.ckpt from Hugging Face; the BS-Roformer from the UVR
  releases). The checksums live in `internal/separate/checksums.go`, `kit/rapreaper_separate.py`, the installer's `VR_MODELS`
  and NOTICE.txt and must agree. The graphics file is made by the program (`--make-gpu-model`); its checksum is the
  int8 file's deterministic conversion, so a new int8 file means running the converter once and recording both checksums.
- Measuring: `build/eval_separation.py build/eval.json model.onnx [...] --tracks="..."` against MUSDB18-HQ stems extracted into
  `<parquet dir>/wav/<track>/`; `a.onnx+b.onnx` measures the two-network average. The guides and the Vocal Remover's README
  read the numbers from `build/eval.json` at build time.
- Delivery: the model file is split into pieces of at most 29 MiB (`Name.onnx.partNofM`); the installer and the program join them.

## Rules that keep it safe (do not break these)

- No network code in RapReaper's Python or Lua. pip in the installer is the one exception; the "Get ffmpeg and Transcriber"
  scripts are separate files the person starts. Tests look for `socket`, `urllib`, `requests` and the like and fail on them.
- The bridge files are never edited. A change to `kit/rapreaper_helper.lua` means: bump its `local VERSION`, bump
  `HELPER_WANTED_VERSION` in the server when the change adds commands, and the build stamps its checksum.
- Every tool answer goes out through one door (`send()` in `serve()`): the Mac wording (`PF.reword`), then the privacy filter
  (`_private`), then the activity log. New tools need nothing special, but file names in answers go through
  `name_out(name, "file")` (music files stay by name; other files become labels), track names through `name_out(_label(name), "track")`.
- Anything that removes or silences audio, opens or closes a project, or moves clips: a plan first (no `apply`/`confirm`),
  then the change, as one undo block, and the tool is listed in `DESTRUCTIVE_TOOLS` when the Claude app should ask first.
- The helper reads and writes only inside the kit folder (plus REAPER's own project save and the files `import_files` is
  given, which must lie in `kit/imports` or `kit/references`).
- RapReaper starts the Vocal Remover only when `kit/state/settings.json` names it with its SHA-256 (the installer records the
  shipped copy after checking it against `MANIFEST.sha256`); every model file is checked against its published checksum
  before ONNX Runtime loads it, in Python and in Go alike. The Vocal Remover has no network code (its window is 127.0.0.1
  with a one-time key) and writes only results, `settings.json` and the graphics model file.

## Versions

Five places carry the version and must agree (the build checks): `VERSION` in `kit/rapreaper_server.py` and
`rapreaper_install.py`, `local VERSION` in `kit/rapreaper_helper.lua`, the first line of `README.txt` and of `mac/README.txt`
("RapReaper X.Y.Z - ..."), and the first line of `NOTICE.txt`.

## The helper's checksum

In the source tree `HELPER_SHA256 = UNSTAMPED` in the server and the installer (no check, so the helper can be edited and
tested). The release build stamps the real checksum; `python3 build/stamp_helper_hash.py --unstamp` puts it back for
development. A stamped tree refuses an edited helper ("not the one shipped") until re-stamped.

## Testing

```
python3 tests/test_audio.py                              # about 3 minutes
RAPREAPER_E2E_DIR=/path/with/room python3 tests/e2e.py   # everything, 1.5-2 hours, writes a few GB of renders
RAPREAPER_E2E_DIR=/path/with/room python3 tests/e2e.py installer projects_fit remaster   # some scenarios
```

- Needs `lua5.4` (or `lua` 5.4), NumPy, SciPy, `lameenc` (for the MP3 export tests), optionally `onnxruntime`. Without a
  Lua 5.4 of its own, `pip install lupa` is enough: `tests/harness.py` then runs the simulated REAPER through
  `tests/lua54_standin.py` (the Lua 5.4 inside lupa, plus the read-only `io.popen` the simulator needs).
- Always give `RAPREAPER_E2E_DIR` a folder on a disk with room (never the default temp folder if that is small: a run that
  fills the disk dies half-way and takes its own log with it).
- Run the suite on a **copy** of the tree, never on the tree being edited: the tests start the server from the files as
  they are at that moment, and an edit half-way through mixes versions.
- Scenario names are the keys of `SCENARIOS` at the end of `tests/e2e.py`. A new tool gets a scenario (or a few checks in an
  existing one) and the tool count in `scenario_protocol` and `scenario_mcp_sdk` goes up.
- Test switches the server honours (environment): `RAPREAPER_PLATFORM`, `RAPREAPER_FREE_GB` (pretend free disk space),
  `RAPREAPER_PRIVACY` (`standard|strict|maximum|off`), `RAPREAPER_BUDGET_SECONDS`, `RAPREAPER_KEEP_CAPTURES`,
  `RAPREAPER_PLUGIN_FORMATS`, `REAPER_RESOURCE_PATH`, `RAPREAPER_CLAUDE_CONFIG_DIRS`.

## Building a release

```
python3 build/build_release.py       # checks, guides (PDF), the Vocal Remover (Go), both zips, SHA256SUMS.txt
python3 build/build_release.py --check
python3 build/build_release.py --no-go --no-pdf   # reuse build/out binaries and the PDFs in docs/
```

The release build: checks the version numbers agree (server, installer, helper, both READMEs, NOTICE; the Vocal Remover's
main.go and README); parses every Python file and loads the Lua files; writes the Mac guide and both PDFs; runs the Go tests
and builds the Vocal Remover for both systems; stamps the helper's checksum into the server and the installer for the zips
and takes it out again afterwards (`--keep-stamped` to leave it); writes a `MANIFEST.sha256` into each zip (the updater checks
a new zip against it); keeps the person's folders out of the zips (state, captures, exports, logs, references, imports,
chains/user, models, VocalRemover/models and Output hold only a `.keep`); gives the Mac zip the `mac/` README and interface
text, the `.command` files with the executable bit set, the Mac Vocal Remover (arm64 binary, dylib, `Vocal Remover.command`)
and no `.bat` or `.ps1`; the Windows zip the `.bat` files, the exe with its DLLs and the `gpu/` runtime. Each zip must stay
under 29 MiB (the delivery limit); the model files travel as pieces beside the zips.

## Updating the person's installed copy

- From 1.4.0 on: they put the new zip in Downloads and double-click `Update RapReaper.bat` / `.command`. The old installer
  checks the zip's manifest, replaces the program files, keeps their folders, backs up what it replaced into `updates/`,
  and runs the new installer. REAPER keeps running the old helper until it is restarted; `status` says so.
- From 1.3.2 or older: unzip over the old folder (replace files), run Install.
- Never ship loose files to drop into the folder: that is what the update path replaces.

## The person's computer, from here

When a chat is linked to the person's computer, the installed RapReaper is at `C:\Users\<name>\RapReaper` (Windows) or
`~/RapReaper` (Mac). Take the installed files as the truth of what they have (their version can be newer than any copy
elsewhere); never edit files there directly, ship a release and let Update do it. Do not read `kit/state`, `kit/logs`,
`kit/exports` or `kit/captures` from there without asking: that is their work and their data.
