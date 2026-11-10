# RapReaper 1.4.0 — the problems met so far, and what was done about each (6 October 2026)

This file is the spec the 1.4.0 work was built from, in the same form as CHANGES-1.3.md: each line is something that
could not be done, went wrong, or cost more effort than it should have while using, updating or developing RapReaper,
followed by the change. "Us" is the person and Claude working together on it.

## A Mac version, as easy as the Windows one

| What happened | Change in 1.4.0 |
|---|---|
| RapReaper only ran on Windows: a .bat installer, Windows folders, Windows words (Ctrl, File Explorer, %USERPROFILE%). | Two packages from one source: `RapReaper-1.4.0-Windows.zip` and `RapReaper-1.4.0-Mac.zip`. The Mac one has `Install / Check / Update / Uninstall RapReaper.command` to double-click (they open Terminal, find a suitable Python and run the same installer), and its own README, AUDIO INTERFACE SETUP and guide. |
| Where things live differs on a Mac: REAPER's settings in `~/Library/Application Support/REAPER`, Claude's in `~/Library/Application Support/Claude`, the logs in `~/Library/Logs/Claude`. | One module, `kit/rapreaper_platform.py`, knows both systems; the installer, the server and the separation code ask it. Nothing else in the code says "Windows". |
| Apple's own `/usr/bin/python3` is a stub that offers to install developer tools (a dialog), and is too old (3.9). Homebrew's Python refuses `pip install` outside an environment. | The .command files never start `/usr/bin/python3`; they look for python.org's Python and Homebrew's, and say where to get one. The installer makes RapReaper's own small Python environment (`kit/venv`) on a Mac and installs NumPy, SciPy, lameenc and ONNX Runtime there; the Mac's Python is left as it is, and Claude is pointed at `kit/venv/bin/python3`. |
| macOS protects Downloads, Desktop and Documents: Terminal, REAPER and Claude would each ask for permission, and a refused one fails silently. iCloud Drive and cloud folders move files. | The Mac installer refuses those places with the reason and says to use `~/RapReaper` (the home folder itself needs no permission). The Mac separation code looks for the model only in `kit/models`, never in Downloads (a program Claude started reading Downloads would make macOS ask, in Claude's name). |
| macOS marks downloaded files as "from the internet" and refuses to open a `.command` by double-click until the person allows it in System Settings. | The guide gives the two routes (Privacy & Security > Open Anyway; or `bash` + drag into Terminal). Once the installer has run, it releases the other `.command` files in the folder, each only after its checksum matched the shipped manifest, so one allowance covers them all. |
| Answers were written in Windows words. | At the door, on a Mac, every answer is reworded: Cmd for Ctrl, Option for Alt, Finder for File Explorer, `~` for `%USERPROFILE%`, `.command` for `.bat`, `kit/models` for `kit\models`, "this Mac" for "this PC". The privacy filter also knows a Mac's user name (`/Users/<name>`) and host name (`Alexs-MacBook-Pro.local`). |
| The optional programs (ffmpeg, whisper.cpp) came with a Windows PowerShell fetcher. | `Get ffmpeg and Transcriber.command` installs both with Homebrew and fetches the speech model (checksum verified), then offers to run the two Allow steps; `Allow ffmpeg.command` and `Allow Transcriber.command` take a path. The Vocal Remover is a Windows program; the Mac uses the Kim Vocal 2 model. |

## Updating without losing anything

| What happened | Change in 1.4.0 |
|---|---|
| Updates were delivered as loose files to drop into the folder, or as a zip to unzip over the old folder, by hand, with the risk of taking the person's chains, settings and references with it. | `Update RapReaper.bat` / `.command`: put the new RapReaper zip in Downloads (or next to the RapReaper folder) and double-click. The installer checks the zip against its own `MANIFEST.sha256` (a damaged or changed download is refused), checks it is the zip for this system, replaces the program files, keeps `kit/state`, `kit/chains/user`, `kit/references`, `kit/exports`, `kit/captures`, `kit/models`, `kit/logs`, `tools` and `VocalRemover` as they are, puts the replaced files in `updates/backup-<old version>-<stamp>/`, and then runs the new version's installer. `kit/state/install.json` records the version, and the installer says "Updated from 1.3.2 to 1.4.0" when it sees one. |
| The Vocal Remover's network (237 MB) cannot travel inside a zip that has to stay under the download limit, so a package could not simply be unzipped on another computer with everything in place. | `Make Full Packages.bat` / `.command` (`--make-full-packages`): from the zips and the eight model pieces in Downloads it writes `RapReaper-<version>-Windows-full.zip` and `-Mac-full.zip` with the model in `VocalRemover/models`, checked against its published checksum, listed in the package's manifest, and the written zip read back and checked. Unzip one on any computer and Install: nothing else to fetch. Update from a full zip puts the model in place where it is missing (files of the person's own are still never overwritten). |
| After an update REAPER kept running the previous helper script (it only loads at start), and the new tools answered "unknown command". | `status` says "REAPER is still running RapReaper's previous helper: close REAPER and start it again when convenient" (everything else keeps working), and a tool that needs the new helper says so plainly instead of "unknown command". |
| Updating from 1.3.2 itself: that version has no Update file. | Unzip the 1.4.0 zip over the old folder once (replace files; nothing of yours is in the zip), run Install. From 1.4.0 on, Update does it. |

## Reliability

| What happened | Change in 1.4.0 |
|---|---|
| A test run died when the disk filled up, with the suite's own scratch files in a temporary folder that was also full. On a person's PC the same would show up as REAPER failing in the middle of a render with a half-written file and an error window. | `status` warns when the disk that holds RapReaper has under 2 GB free, and says what is safe to delete; a measurement is refused under 0.3 GB and an export under 1 GB, before REAPER is asked to render. The test suite writes under a folder given by `RAPREAPER_E2E_DIR` (DEVELOPMENT.md says how). |
| Testing and building were done with scripts kept outside the folder; a later session had to find them again. | `build/build_release.py` builds both zips, the manifest and `SHA256SUMS.txt`; `build/stamp_helper_hash.py` stamps or unstamps the helper's checksum; `DEVELOPMENT.md` says how to test, build and update. The source tree carries `HELPER_SHA256 = UNSTAMPED` so the helper can be edited and tested; the build stamps it. |

## Projects (asked for on 6 October 2026)

| What happened | Change in 1.4.0 |
|---|---|
| RapReaper could save a project but not open or close one; "open my song" meant doing it by hand. | `list_projects` (the open tabs, which one is active, which has unsaved changes, and REAPER's own recent-projects list, numbered), `open_project` (by name, recent number, or the path of an .rpp in the person's own folder; or a new empty project; in the current tab or a new tab) and `close_project`. Both show the plan first and need `confirm`; both are flagged to the Claude app as changes, so it asks. |
| Opening or closing a project with unsaved changes makes REAPER show a "save changes?" dialog, which would freeze the bridge, or lose work. | Unsaved changes are never dropped unless the person chose that in the chat (`discard_changes`); `save_first` does REAPER's own Save (refused when the project has no file yet); `new_tab` keeps the open project open. REAPER is driven with its `noprompt:` form only after that choice, so no dialog appears. When the only tab is closed, REAPER keeps one project open, so an empty project takes its place; RapReaper says so. |
| Which file to open: Claude must not open just any file on the computer. | Only `.rpp` files that REAPER lists as recent, or that lie in the person's own user folder. A project REAPER has never opened on this computer is opened once by hand; then it is in the recent list. |

## Fitting an instrumental into place (asked for on 6 October 2026)

| What happened | Change in 1.4.0 |
|---|---|
| A new instrumental (a better master, a longer or trimmed version of the same beat) had to be dragged into place by eye and ear, against a vocal recorded over the old beat. | `fit_instrumental`: the track to move and a reference track that holds the same music (the old beat, or a vocal recorded over it, in which the beat is faintly present) are rendered as recorded and lined up: first by their attacks (a frame-free onset curve at one value per millisecond, cross-correlated over every possible shift, normalized by the overlap), then by the waveforms themselves around that shift, to the sample. The track's clips are moved as one undoable step, and the result is checked by lining them up again. |
| The two files start and end at different times (a longer intro, a trimmed start, a different master). | Handled: the shift can be anything up to the length of the files; the overlap is reported. A longer intro that would have to start before the project's start is trimmed off the clip (the clip starts at 0 and reads its recording from further in; the recording file is untouched; undoable), and the plan says so before anything moves. |
| Beats repeat: a bar earlier or later fits almost as well. | The other shifts that fit are listed with how well the rhythm, the waveform and the arrangement match at each; the chosen one keeps the most music inside the reference; `alternative n` moves by the n-th instead. A match with confidence under 0.4 is not applied unless `force` is given. A speed difference between the two recordings (one was stretched) is measured and reported, not corrected. |
| A plain move was also missing ("put the beat at 8 seconds"). | `to_seconds` (where the first clip should start) and `by_seconds`. |

## Remastering a song made in another program (asked for on 6 October 2026)

| What happened | Change in 1.4.0 |
|---|---|
| Songs made in Pro Tools, FL Studio and other programs could not be mixed or mastered with RapReaper; REAPER opens none of those project files (a .ptx, .flp or .als holds patterns, plug-in settings and references to samples, not sound). | `remaster`: the person exports the tracks or stems (or the finished mix) as audio files and copies them into `kit/imports/<song name>`; the tool lists what is there (music files with length, channels and a guessed role: vocal, beat, mix), recognizes project files of other programs (Pro Tools, FL Studio, Ableton Live, Logic, Cubase, Studio One, GarageBand, Audacity, OMF/AAF, MIDI) and gives the exact export steps for each, and with `apply` puts every music file on a track named after it, lined up from the same start or by the timeline positions Pro Tools writes into its exports (the broadcast-WAV time reference), as one undoable step; the usual pass (vocal_chain, balance, master_loudness, export_mix) then applies, or for one finished mix the mastering recipe. The helper reads files only from the imports and references folders. `reference_card` topic `daw_export` holds the same steps. |

## Names Claude may use (asked for on 6 October 2026)

| What happened | Change in 1.4.0 |
|---|---|
| At the default privacy level every file name was a label ("file 3.wav"), so Claude could not say what is in the references folder or call a song by its name. | Music files (songs, beats, takes, stems, mixes, exports, the music in the references and remaster folders) and saved projects are given by name at every level; never a folder or a path, never any other kind of file (those stay labels at strict), and the playbook tells Claude not to repeat a person's name beyond what the task needs. |

## Music and engineering knowledge for Claude

| What happened | Change in 1.4.0 |
|---|---|
| Auto-tune needs the song's key and a timed delay needs the tempo; Claude had to ask, and the person often did not know. REAPER's project tempo is usually the default 120, not the beat's. | `song_tempo_and_key` measures a track (the beat by default) or a file in the references folder: BPM with both the straight and the half/double-time reading (a 140 trap beat is also felt at 70; both are real), the key with its confidence, the relative key and the Camelot code, note values in milliseconds (eighth, dotted eighth, quarter, triplets, a reverb pre-delay), the auto-tune settings, and whether the beat is tuned off A=440 (ReaTune's reference pitch then has to follow). It says when REAPER's project tempo does not match the beat and how to set it. `learn_song_effects` reads the song's tempo by itself when none is given. |
| Numbers Claude quoted (how loud for Spotify, where mud lives, compressor settings) came from memory and varied from chat to chat. | `reference_card`: one set of engineering facts kept on the computer, the same numbers the guide uses: loudness targets of the services, where things live in a rap vocal, compression and de-essing starting points, reverb and delay for rap, auto-tune and keys, the order of work, doubles and ad-libs, the 808 problem, recording, a glossary, and the person's phrases with the tool for each. The playbook tells Claude to use it instead of memory, as starting points. |

## The Vocal Remover 2.0 and the analyzer (asked for on 6 October 2026)

| What happened | Change in 1.4.0 / Vocal Remover 2.0 |
|---|---|
| The Vocal Remover's best mode (BS-Roformer) took about an hour for a song on the laptop (131 s for one 8-second window measured in its graphics test), and its graphics-processor test failed on both model files it tried (the int8 one: "the parameter is incorrect"; the float16 one: "not enough memory"). | A new default network, the Mel-Band Roformer "Kim FT2" (Kimberley Jensen's model fine-tuned by unwa, built from the public checkpoint by `source/tools/build_roformer_onnx.py` without PyTorch): measured within 0.3 dB of the BS-Roformer on three MUSDB18-HQ test songs with true stems, in well under half the time (2.2x faster on the 2-core test system: 81 s against 181 s for 60 seconds of song). The BS-Roformer stays as a mode, and "Two networks" averages both. |
| The graphics processor could not be used at all. | A real graphics path with a built-in test: Windows through DirectML (ONNX Runtime 1.24.4 DirectML build and DirectML 1.15.4 shipped in `VocalRemover/gpu`), Macs through CoreML (in the standard ONNX Runtime library). The graphics model is a float16 copy that the program makes itself from the int8 file (`internal/onnxconv`, deterministic, checksum known in advance): no quantisation operators, which DirectML could not run, and half the memory of float32. The test runs one window on the processor and on the graphics processor, first as one compiled graph and then one operation at a time (far less memory), compares the results and times them; it switches the graphics processor on only when it works and is faster, writes `Output/GPU test.txt` and remembers the verdict in `settings.json`. A failure in the middle of a song falls back to the processor and says so. RapReaper's installer offers the test at the end of an install; `--gpu on` asks RapReaper's own ONNX Runtime for the graphics provider too. |
| Only MP3 and WAV opened; FLAC and Ogg files had to be converted elsewhere. | FLAC (mewkiz/flac) and Ogg Vorbis (jfreymuth/oggvorbis) decode in the program, pure Go, vendored and checksummed with the rest. M4A/AAC still does not (no trustworthy pure-Go decoder); RapReaper's export_mix or Audacity convert it. |
| The Vocal Remover was a Windows program, and a separate download with eleven pieces to place by hand; RapReaper could start it only after "Allow Vocal Remover.bat" with a typed path. | It ships inside the RapReaper folder (`VocalRemover/`) on both systems, with a Mac build (Apple Silicon, `Vocal Remover.command`) from the same source. The installer checks the program against the package manifest, allows exactly that copy for RapReaper (path + SHA-256), joins the model pieces from Downloads into `VocalRemover/models` (checksum verified) and, on a Mac, releases the files from the download quarantine after the checks. The update path replaces the program and keeps `VocalRemover/models`, `Output` and `settings.json`. The Mel-Roformer file is 8 pieces; the BS-Roformer file is already on a PC that had 1.3. |
| Claude could learn a song's effects but could not make an instrumental for the person to record over; the separated files were hidden in `captures`. | `separate_song`: the song in references (or imports/exports) → a plan first, then a background job with the Vocal Remover's networks (the program when it is allowed, else RapReaper's own ONNX Runtime with the same model) → `<song> (Instrumental).wav` and `<song> (Vocals).wav` in `kit/imports` (32-bit float, so the two add up to the song exactly) → `import_to_reaper` puts them on tracks. `status` shows `vocal_separation`: what this computer separates with, and whether the graphics processor is asked for. `learn_song_effects` uses the same networks and prefers the Vocal Remover program. |
| Modes whose model file was missing stopped the program at start. | The window greys them out and names the missing file; the default mode needs only the Mel-Roformer file. |
| The window had no idea where a job ran or why the graphics processor gave up. | The result says "on the processor" / "on the graphics processor (DirectML)" and carries the fallback reason; the command line prints the same. |

| "Sit my vocal like in that song" had no number to aim at: `learn_song_effects` measured the vocal's effects but not where the voice sits against the music. | With the music on its own beside the separated vocal (both ways of separating write it), `learn_song_effects` measures the record's balance: the integrated loudness of the voice and of the music over the analysed stretch, the difference in dB with plain words (vocal-forward, level, beat-heavy) and the exact `balance_vocal_and_beat` call (`vocal_above_beat_lu`) to put the same balance on the person's own tracks. |

The analyzer's other measurements (`rapreaper_audio`, `rapreaper_effects`, `rapreaper_music`) are as described above; the upgrade on the analysis side is the separation they build on: a cleaner vocal from the Mel-Roformer makes `learn_song_effects`' readings of auto-tune, reverb, delay and compression more reliable, the music on its own gives the balance, and the graphics processor makes it all faster.

## Safety and privacy (unchanged promises, kept for the new parts)

- No network code anywhere in RapReaper; the Mac installer uses pip the way the Windows one does (ready-built wheels, pinned ranges), and `Get ffmpeg and Transcriber.command` is a separate script the person starts, like its Windows counterpart.
- `open_project` reads one project file; `close_project` and `open_project` never drop unsaved changes without the person's word; `save_first` is REAPER's own Save to the project's own file. `fit_instrumental` moves clips (and trims a start when it must) as one undoable step after a plan the person has seen; nothing is cut, stretched or deleted, and no recording file is written.
- `song_tempo_and_key` and `reference_card` measure and look up only; nothing leaves the computer but the numbers.
- The update path installs only a zip whose every file matches the zip's own manifest, and only the zip for this system; the person's own files are never replaced; the replaced program files are kept.
- On a Mac the quarantine mark is removed only from RapReaper's own `.command` files, only after the installer has run (the person allowed it once) and only when each file's checksum matches the shipped manifest.
- Every new answer passes the same privacy filter, the same wording door and the same audio-data guard as before, and the tests run each new scenario under them.
- The Vocal Remover has no network code; its window listens on 127.0.0.1 with a one-time key. It checks every library (ONNX Runtime, DirectML, the C++ runtime) and every model file against known checksums before loading them, writes only results, `settings.json` and the graphics model file, and RapReaper starts only the copy the installer allowed. The graphics model is made on the computer from the int8 file and verified against a checksum baked into the program, so nothing is downloaded and nothing unverified is loaded.

## Known limits that stayed (and why)

| What happened | Why it stays |
|---|---|
| A perfectly looping beat (sample-identical bars) fits at several shifts equally. | No measurement can tell identical bars apart; RapReaper picks the shift that keeps the most music inside the reference, lists the others, and says so. |
| The key of a beat built on drums and one bass note is a guess. | Said as such (confidence, "the producer's key beats this number"); drums alone give no key at all. |
| `open_project` may still meet a REAPER window about missing media or a newer file version. | Those are REAPER's own, after the project is open; the answer says to answer them and call `status`. |
| On a Mac, ReaPack and the SWS extension are downloaded programs too, and macOS may refuse to load them once. | The guide gives the one-time "Allow Anyway" step from ReaPack's own instructions. |
| Whether the graphics processor works on a given computer cannot be known in advance. | DirectML and CoreML could not be run where the Vocal Remover was built; the built-in test answers the question on the person's own computer in a few minutes and the program falls back to the processor by itself. |
| M4A/AAC songs do not open in the Vocal Remover. | No pure-Go AAC decoder exists that could be vendored and checked; RapReaper (export_mix) or Audacity converts them to WAV first. |
| The Vocal Remover on a Mac runs on Apple Silicon only. | ONNX Runtime 1.30 is published for Apple Silicon Macs only; an Intel Mac separates through RapReaper's own ONNX Runtime package. |

## 1.4.1: an independent audit (7 October 2026)

Five reviewers who had not built RapReaper examined the 1.4.0 packages and tested every suspicion. What they found and the change each got is in SECURITY.txt ("An independent audit, and what 1.4.1 changed"). The ones that mattered most: ONNX Runtime's usage reporting on the Mac (now off before the library loads, checked by a system-call trace), Update installing any `rapreaper*.zip` without asking (now named, newer, shown and confirmed, with path checks and roll-back), folder paths outside the user folder reaching Claude (a general path rule), separate_song taking or overwriting other files (private job folders, sidecars), and a cancelled command running later (expiry, claiming). The helper is 1.4.1, the Vocal Remover 2.0.1.

## 1.4.2: every problem met on 7 October 2026, and what was done about each (8 October 2026)

The day was spent auditing the 1.4.1 packages: thirteen rounds of independent review, each one trying to break the
fixes of the one before, until a round found nothing caused by earlier fixes. Then one request: potential fixes for
mixes that have to work on a phone. This section is the record; SECURITY.txt has the security and privacy items in
detail (audit rounds 7 to 13).

### Mixes that must carry on a phone (asked for on 8 October 2026)

- **Problem:** `device_check` said how a mix comes across on a phone speaker and in mono, but left the fixing to
  guesswork. **Resolution:** the new `phone_fixes` tool measures the master (and the vocal alone against the rest) as a
  phone plays it and as mono, and makes a plan of concrete moves from the numbers: `beat_lift` (+2 dB at 200 Hz on the
  beat's EQ: the bass harmonics a phone can play, instead of more sub), `beat_dip` (2-4 dB at 2.5 kHz, wide: room for
  the words), `effects_down` (RapReaper's effect tracks 2 dB quieter when wide parts cancel in mono), `centre` (vocal and
  beat panned to the middle) and `ceiling` (the master limiter at -1.5 dBTP). The plan comes first; `apply: true`
  makes the moves, measures again and shows every number before and after. What it cannot do by itself (a harsh top, no
  limiter yet) comes back as advice naming the tool. Existing EQ bands (a balance dip) are kept; every move is one undo
  entry; the mix log records it.
- **Problem:** two of the ways a mix fails on a phone were not measured at all. **Resolution:** `device_check` (and so
  `export_mix`'s `on_devices`) gained `bass_harmonics_150to300_over_sub_db` (an 808 that is all sub vanishes on a phone;
  aim for -12 or better) and `phone_top_5to9k_over_words_1to4k_db` (a small speaker makes S sounds and hi-hats harsh;
  aim for under -3), each with a finding and the fix in words.
- **Problem:** Claude had no reference for why these moves work. **Resolution:** the engineering cards gained a
  `phones` topic (what a phone plays, the five ways a mix fails there, the fix for each, the numbers to aim for); the
  playbook maps "it sounds bad on my phone" to the tool; the guide has a chapter "Mixes that carry on a phone".

### Privacy: names that still reached Claude (audit rounds 7 to 13)

Each item was found by a reviewer, reproduced against the simulated REAPER, fixed, and pinned by a test (scenario
`audit2`, now 93 checks).

- One-word track names at the maximum level in texts RapReaper builds itself: send texts ("Kofi -> Verb Bus"), undo
  texts, lists of problems, the mix log's send entries, measuring notes, restore-point reasons, chain notes ("Built by
  RapReaper on 'Kofi'"), the self-test's track, progress messages, error texts, the solo and mute lists, and the names
  of capture files that an error text could quote. **Resolution:** every one of those goes through the same rules
  (`track_out` for answers, `_uname` for REAPER's undo history, `_names_in_sentence` for sentences and every error
  text); capture files are named by a fingerprint; chain notes no longer name the track, and older notes have the name
  taken out when shown.
- Names seen in other capitals ("kofi" then "Kofi") and names in decomposed Unicode ("José" stored two ways) were
  matched in one spelling only. **Resolution:** every spelling seen is replaced; names are compared in one Unicode form.
- A track name inside a song file's name ("Kofi Vocal.wav") was replaced, breaking the file name. **Resolution:** song
  file names stay whole wherever they stand.
- A project row's name could get a track's label; a quoted option word ('tone', 'reverb') that happened to be a track
  name was replaced; the home folder with a space in its name ("Alex Sample") leaked its second word when another path
  followed, and a sibling folder with several words leaked. **Resolution:** project rows are named at the source; option
  words in quotes are never replaced; the path rule reads on to the next separator or colon.
- A track RapReaper makes from another one (a split piece named "<name>b", an effect track "<name> Reverb") carried the
  account name along at the standard and strict levels. **Resolution:** such a track is called "Vocal split" /
  "Vocal Reverb" when the source name is private, and every derived name stays within REAPER's 60 characters (the
  doubler, reverb and delay used to fail on tracks with long names).

### The master bus and tracks with odd names

- A real track called "MASTER" or "Master" was shown as the bus and could be muted or set in its place; the bus itself
  could appear as a track's label in the mix log and in song setups; a track named like a label ("track #2") was written
  as that label in texts, meaning another track. **Resolution:** the bus is always "MASTER" and nothing else is; a real
  track so named gets its own label at every level; the mix log writes the bus as "<master bus>" (older entries are read
  the old way); song setups answer "MASTER" for their master row; odd-named tracks are written as their labels in every
  text; status lists `track_names_to_change`.
- Tools that picked a track by themselves (`song_tempo_and_key`, `fit_instrumental`) looked it up again by name and
  could land on the bus. **Resolution:** they keep the track they picked, by REAPER's id.
- A real track called "Vocals" or "All" was swallowed by the "all vocals" keyword (or, at maximum, the keyword by the
  track). **Resolution:** where names are shown the track wins; where Claude sees labels only the keyword keeps its
  meaning.

### Things that stopped working, and work again

- `restore_clips` restored a later point when two edits happened in the same second (an ordering change had made the
  exact id lose to a longer one). **Resolution:** an exact id always wins; the start of an id that fits several points
  is refused with the list.
- `level_match` matched its rows by name and lost them at the maximum level. **Resolution:** rows carry REAPER's id.
- `add_send_effect` refused the label of an existing effect track; a one-file `remaster` made "Imported track #2" instead
  of using the labelled track. **Resolution:** both take the label Claude was given.
- A song setup with two tracks of one name was recalled by guessing. **Resolution:** refused with the names to change.
- The learned-layout cap refused a real 32-byte ReaTune layout. **Resolution:** small blocks may name up to 24 bytes.

### Build, tests and packaging

- A test still expected the 1.4.0 label form ("file 1.ptx") and the old placement of `.keep` files; the package scan
  tripped on its own text (the build folder's name written in the script, a mathematician's surname inside the compiled Go
  program). **Resolution:** tests updated; the scan takes the build folder from where it runs and skips compiled
  programs. The full suite: 1,199 checks on the simulated REAPER plus 150 audio checks, all passing on the shipped tree.
- A twelfth-round finding that the round-11 fix had broken `restore_clips` showed why each round re-ran the whole suite
  on a frozen copy of the exact tree that ships.

### Delivery

- Asked on 7 October: can the zips carry the model, or be made transportable? **Resolution (1.4.1, kept):** the model
  (240 MB) comes as eight pieces beside the zips; Make Full Packages.bat / .command joins them into
  RapReaper-<version>-Windows-full.zip and -Mac-full.zip with the model inside and checked, to carry to any computer.
- The 1.4.2 zips update a 1.4.1 or 1.4.0 installation in place with Update RapReaper (the SHA256SUMS list next to the
  zip is checked when present). The helper inside REAPER is 1.4.2: close REAPER and start it again after updating.

## 1.4.3: a slow project is not a wrong setting (10 October 2026)

| Problem met | Change |
|---|---|
| `export_mix` for a 33-second preview (and then a 10-second one) of a 65-track project with ReaTune on 29 vocal tracks and ReaPitch on 26 doubler tracks was refused: "REAPER renders too slowly for measuring (about 0.5 seconds of waiting per second of audio)", with the Full-speed Offline fix, which was already set. The one-off speed probe (`_check_render_speed`) refused any render slower than 0.25 s per second of audio, a limit sized for a seven-minute measurement, whatever the length actually asked for. Twice real time is plenty for ten seconds. | `SLOW_RENDER_LIMIT` is 0.9: at or above that REAPER is rendering at its "Online render" (real-time) speed, which is still refused with the fix (the `realtime` scenario and the self-test check are unchanged). Below it the probe records the speed, so `mix_limit()` shortens every later measurement by itself, as on any slow PC. The probe now also takes the length of the render that follows it (`_render` clamps first, then checks): a piece longer than `mix_limit()` is refused with `SLOW_RENDER_PART`, which gives the longest piece that fits, says the render setting is not the cause, and names REAPER's own File > Render and Track > Render/freeze tracks for the whole song. Automatic tools that clamp to `mix_limit()` before the first probe of a session can meet that refusal once; its text says to ask again, and the next call fits. |
| No test covered a project that renders slowly but faster than real time. | `tests/e2e.py` scenario `slowproject` (a 100-second song rendered at 0.5 s per second, with the real 25-second budget): the whole song asked for first is refused with the longest piece that fits and without the Full-speed Offline fix; 33-, 10- and 70-second exports go through; a vocal measurement clamps itself. The simulator takes `render_rate` (seconds of waiting per second of audio) next to `realtime`. The export tool's own refusal of a long piece said "too short for an export in one go"; it says "too long for one export". |
| Nothing in the playbook or the guides told Claude the difference between the two messages. | PLAYBOOK.md and both guides' "when something is off" tables have the new line. The Windows guide PDF in this package is the 1.4.1 one (rebuilt with the next full release). |

Versions: server, installer and helper 1.4.3 (the helper's only change is its version line; `HELPER_WANTED_VERSION` is unchanged, so REAPER may keep running the older helper until it is restarted and nothing refuses that). The Vocal Remover is unchanged at 2.0.1.

## 1.4.4: ducks that did not duck (11 October 2026)

| Problem met | Change |
|---|---|
| `duck_section` on the beat of a 65-track project (five stretches, 4 dB) wrote its envelope, read it back exactly, and then measured 0.8 dB *louder* inside and outside the ranges alike; a 40 dB probe dip measured +0.3 dB inside and +0.75 dB outside. Those numbers are the envelope's own values: REAPER applies an FX parameter envelope in the control's own units (0..1 for a VST control, the slider's range for a JS slider), and the 'Ducks' plug-in was the stock JS Volume/Pan Smoother, whose Volume slider runs -60..+12 dB. RapReaper wrote every point as a *position* on that range (0 dB = 0.833, -4 dB = 0.778, -40 dB = 0.278), which REAPER played as +0.83 dB, +0.78 dB and +0.28 dB. The bridge only ever writes envelope values between 0 and 1, so no value a dB slider needs could be written on it. Every duck drawn with 1.4.3 and older behaved this way. | The Ducks plug-in is now RapReaper's own small JS effect, `kit/effects/RapReaper/ducks.jsfx` ("JS: RapReaper Ducks"), whose one control runs 0..1 (1 = full level, 0 = -40 dB, straight in dB between) with a few milliseconds of smoothing: what the bridge writes is what REAPER plays. The installer copies it into REAPER's Effects folder (`Effects\RapReaper\ducks.jsfx`; `Check RapReaper` reports it; REAPER reads it when it starts; the self-test is unchanged, so that it still fits in one call) and leaves it there on uninstall, so projects keep their ducks. `duck_section` maps dB to the control with `_duck_pos` / `_duck_db`, sets the control to full level before the first dip so that REAPER's seed point (time 0, the control's value when the envelope is made) is the base the dips return to, and, when REAPER has no such effect, says to run Install or Update and restart REAPER. |
| Every track that had ducks drawn before carries the old plug-in and a useless envelope. | The first `duck_section` on such a track replaces it: the old dips are read as they were *meant* (positions back to dB), the old plug-in is removed with its envelope (its own, named undo entry), RapReaper's effect is added, and the old dips are drawn again on it at their depth together with the new one, all reported under `replaced_old_ducks_plugin`. A new range that would cut an old dip in two is refused before anything is touched, as always. `find_breaths` on such a track marks nothing as already ducked and says the plug-in will be replaced. |
| The simulated REAPER applied FX envelopes as 0..1 positions on the slider's range, which is why the `ducks` scenario passed for three releases. | `sim/fake_reaper.lua` seeds a new envelope with the control's value in its own units and `sim/sim_render.py` applies envelope values in those units (dB on the Volume/Pan Smoother, 0..1 on the Ducks), as REAPER does; a JS effect can only be added when its file is in the simulated Effects folder, as in REAPER; a test project can open with plug-ins and envelopes already on a track. With these, the 1.4.3 server fails the `ducks` scenario's measured checks the way the real PC did. The scenario itself now covers RapReaper's effect, the seed point at full level, a 1.4.3 project with two old dips (replaced, redrawn, measured on two exports, nothing more to replace afterwards) and a REAPER without the effect file; the `installer` scenario checks that the effect lands in REAPER's Effects folder byte for byte. |

Versions: server, installer and helper 1.4.4 (the helper's only change is its version line; `HELPER_WANTED_VERSION` is unchanged). The Vocal Remover is unchanged at 2.0.1. The Windows guide PDF in this package is still the 1.4.1 one (rebuilt with the next full release).

## 1.4.5: the Vocal Remover rebuilt with a current Go; guides rebuilt (9 November 2026)

| Problem met | Change |
|---|---|
| A security review before the public release ran Go's vulnerability checker (`govulncheck`) on the Vocal Remover. Built with Go 1.24.7, it reached 31 known issues of Go's standard library (net/http, net/url, net/textproto, crypto/x509, crypto/tls, encoding/asn1, os). Most sit in code the program does not really use (TLS, certificates); the rest are in the HTTP server behind its window, which listens on 127.0.0.1 only, behind a one-time key and host and origin checks, so only a program already running on the same computer could reach them. Go 1.24 no longer gets fixes. | The Vocal Remover is built with Go 1.27.3 (the current release), its code otherwise unchanged, as 2.0.2. `go.mod` now says `go 1.27`, so the program also runs with Go 1.27's default settings (among them a limit on HTTP Range headers and stricter URL parsing) instead of keeping Go 1.24's for compatibility. `govulncheck` finds none of the 31 any more. `VocalRemover/source/BUILD.txt` and DEVELOPMENT.md name the Go version, because only the same version rebuilds the programs byte for byte. |
| The guide PDFs were older than their text: the Windows one said 1.4.1, the Mac one 1.4.2. `make_mac_guide.py` left one Windows path (`Effects\RapReaper\ducks.jsfx`) in the Mac guide, the guides did not say that the Mac Vocal Remover needs macOS 14 or newer (ONNX Runtime 1.30.0's Mac library is built for macOS 14), and the Vocal Remover's README said macOS 13. The Windows README still counted 56 tools (there are 57) and its file list, like the Mac one, left out `kit/effects`. | Both PDFs are rebuilt for 1.4.5. `make_mac_guide.py` turns that path into the Mac one; the Mac guide, the Mac README and the Vocal Remover's README name macOS 14. The guides' full-package example names are those of 1.4.5. Both READMEs count 57 tools and list `kit/effects`. |

Versions: server, installer and helper 1.4.5 (the helper's only change is its version line; `HELPER_WANTED_VERSION` is unchanged). The Vocal Remover is 2.0.2: the same code as 2.0.1, built with Go 1.27.3.

## 1.4.7, nightly: problems met so far that were still open (9 November 2026)

A nightly build on the GitHub branch "Nightly": every problem met with RapReaper (in use on 4 to 23 October and in
the 1.4.6 work) that was still open and can be fixed in RapReaper itself.

| Problem met | Change |
|---|---|
| "Get Vocal Remover Model" (1.4.6) downloaded and checked the model's two source files and then stopped at the build: `build/build_roformer_onnx.py` imported PyYAML to read the model's `.yaml` settings, and RapReaper's Python (kit/venv on a Mac, the person's Python on Windows) has no PyYAML; the installer never installs it. It passed in testing only because the build machine had PyYAML. | The builder reads the settings file with a small reader of its own (block mappings, block lists, also at the key's indentation, plain values, comments, the `!!python/tuple` tag), which refuses anything else instead of guessing. On both published config files it gives exactly what PyYAML gives, and with PyYAML blocked the Kim FT2 model came out byte for byte the published file (f81d9265...). Scenario `nightly` builds a config with PyYAML unavailable. |
| REAPER's preference "Set media items offline when application is not active" came back on after every restart of REAPER (7 and 10 October 2026), so the first measurement after each restart came back silent until the self-test or `fix_setup` switched it off again. They change it through the bridge (SWS's `SNM_SetIntConfigVar`), in REAPER's memory only; REAPER read reaper.ini again at its next start. | `fix_setup` (and the self-test through it) also writes `offlineinact=0` into reaper.ini: only that line in `[REAPER]` (added after the header when REAPER had it on and the file does not name it), line endings and every other byte kept, a copy of the old file next to it first, the new file put in place in one step. When REAPER has it off but reaper.ini still has it on, `fix_setup` saves it and `status` says (`after_reaper_restart`) that it would come back. The answer says when the file could not be found or written, with the one-time manual untick that REAPER saves itself. Scenario `offline` (22 checks). |
| After Update RapReaper, Claude kept running the previous tool server until Claude itself was restarted (4, 7 and 23 October: "the tool server I talk to is still the old one", new export options refused, status showing the old version). Nothing in the answers said why. | The server reads the version line of its own file at most every 15 seconds. When it differs from the running version, `status` is not ready and says which version is installed and which runs, with how to quit Claude completely on this system; every other answer carries the same sentence under `restart_claude`, every error and an unknown tool's refusal end with it. Scenario `nightly`. |
| `Get Vocal Remover Model.bat` (1.4.6) had Unix line ends (LF only), unlike every other .bat file. Windows' cmd misreads such a batch file in places, labels and `goto` among them, which this one uses. The installer scenario's line-end check would have said so, but in 1.4.6 that scenario could not run to its end (no Lua 5.4 on the build machine). | Windows line ends (CRLF), like the others; `get_model_kimft2.ps1` too, like `get_tools_b5130.ps1`. The check passes. |
| A REAPER set to render in real time ("Online render") could slip past the speed check when the computer was busy: the check takes the difference of a 2-second and a 6-second test render to cancel REAPER's fixed costs, and a 1.5-second stall during the shorter one brought it to about 0.6 s per second, under the 0.9 limit, so a long measurement then ran in real time. Found when the full test run for this build, under heavy load, failed scenario `realtime`; reproduced on 1.4.6. | A render in real time never takes less than its own length, so both timed renders lasting at least 95 % of their length now count as real time too (the message gives the larger of the two figures). A heavy project rendering at half real time is measured as before (scenario `slowproject`). Scenario `realtime` has the stalled case (it fails on 1.4.6). |
| BUILDING.md in the 1.4.6 zips says to run `build/fetch_runtime.py`, which was in the GitHub source but not in the zips. | The release build packs it with the other build tools. |
| 1.4.6 shipped without its full end-to-end run: the build machine had no Lua 5.4 for the simulated REAPER. | `tests/lua54_standin.py` runs the simulated REAPER on the Lua 5.4 of the pip package "lupa" (with a read-only `io.popen`, which that build lacks); `tests/harness.py` uses it when no `lua5.4`/`lua` is on the PATH. The whole suite was run on this build with it. On a busy machine one safety check could read the simulated project from just before a render (the simulator wrote its "rendering" mark first, then the project); `sim/fake_reaper.lua` now writes the project first. The same first full run found two checks that had not caught up with 1.4.6: the Intel Mac scenario picked up the platform module and `RAPREAPER_HOME` left behind by earlier scenarios in the same run (it now loads its own and clears them), and a check on the imports folder did not expect the `.keep` placeholder the shipped folders have had since 1.4.6. |

Versions: server, installer and helper 1.4.7 (the helper's only change is its version line; `HELPER_WANTED_VERSION` is unchanged). The Vocal Remover is unchanged at 2.0.3. Still open: the BS-Roformer file (optional "Two networks" and BS modes) is not rebuilt byte for byte by today's builder, so it is still delivered only as a file or pieces.

## 1.4.6: posting on GitHub, building it yourself, and Intel Macs (9 November 2026)

| Problem met | Change |
|---|---|
| The source was only inside the release zips, and RapReaper's own files had no licence beyond "use, change and share them freely", which is not one a public project can rely on. | The GitHub repository holds the source tree as well as the zips. RapReaper's own files are under the MIT licence (`LICENSE.txt`); NOTICE.txt says so and lists every third-party part with its licence, unchanged. `.gitignore` keeps the binaries, runtime libraries, model files and the person's folders out of the repository; `BUILDING.md` and a GitHub Actions workflow build all three zips from the source. |
| The Vocal Remover's model arrived as eight pieces next to the zips, which a public repository cannot carry (237 MB; its author published no licence that allows anyone else to redistribute it). Someone who got RapReaper from GitHub had no way to get the model. | `Get Vocal Remover Model.bat` / `.command` downloads the model's two source files from their author's page on Hugging Face (`config_kimmel_unwa_ft.yaml`, `kimmel_unwa_ft2.ckpt`), each checked against its published SHA-256, and runs `rapreaper_install.py --build-model`, which builds the model with `build/build_roformer_onnx.py` (NumPy only) and checks the result against the model's published checksum. The converter is deterministic: built on Linux it gives the published file byte for byte, in under a minute. The installer's step 7 does the same whenever the two files lie in `VocalRemover/models` or Downloads; the 913 MB checkpoint is removed from `VocalRemover/models` afterwards. Pieces still work. |
| The Vocal Remover was for Apple-chip Macs only: Microsoft builds ONNX Runtime 1.30.0 for arm64 Macs only, and the launcher stopped on an Intel Mac. | A third package, `RapReaper-<version>-Mac-Intel.zip`, with the Vocal Remover built for darwin/amd64 and ONNX Runtime 1.23.2 (the last Intel Mac build; it has CoreML and serves the C API level 12 the program asks for). The program knows that library's checksum on Intel Macs. The launcher is told which chip its package is for and says so on the other Mac (an Intel copy on an Apple-chip Mac runs through Rosetta, with a note). The installer reads the program's Mach-O header: an Apple-chip program on an Intel Mac is not allowed (RapReaper's own ONNX Runtime separates instead) and the answer names the Intel zip. Update and Make Full Packages know the new zip name and pick the one for this Mac. |
| On an Intel Mac, `pip install onnxruntime` only works with Python 3.10 to 3.13 (1.23.2 is the last version with Intel Mac wheels). | The installer says so when ONNX Runtime could not be installed on an Intel Mac. |

Versions: server, installer and helper 1.4.6 (the helper's only change is its version line). The Vocal Remover is 2.0.3: 2.0.2 plus the Intel Mac runtime's checksum; built with Go 1.27.3. The BS-Roformer model (the "Two networks" and BS modes) cannot yet be rebuilt byte for byte from its public checkpoint with today's converter, so it still comes only as a file or pieces.
