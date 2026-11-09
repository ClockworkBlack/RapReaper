# RapReaper 1.3.0 — everything that stopped us in the October 2026 mixing session, and what was done about it

This file is the spec the 1.3.0 work was built from. Each line is something that could not be done,
or went wrong, while mixing a real song with RapReaper through Claude, followed by the change.
"Done by hand" means the person had to do it in REAPER themselves.

## Clips (editing the recordings' timeline)

| What happened | Change in 1.3.0 |
|---|---|
| An effect was wanted "from 6 seconds on" (Vocal2) and "from 30 seconds on" (an ad-lib); there was no way to start a send partway through a track. The person had to split the clip and drag it to a new track by hand (Vocal2b, ab). | `split_to_track`: split a track's clips at a time (or between two times) and move the piece to a new track that gets the same chain; one undoable step. |
| `cut_empty_air` had no way back once the undo history had moved on (`restore` was not an option). v11 came out choppy and the cut could not be taken back from here. | Every cut writes the clips' state before it to `kit\state\edits\`; `restore_clips` puts a track's clips back from any saved state (or the one before the last cut), as one undoable step, by re-creating the pieces from the same recording. `cut_empty_air`/`cut_section` answers name the restore point. |
| Cuts left slivers of 0.02-0.07 s (a cut-off word start), which click. | `cut_empty_air` never leaves a piece shorter than 120 ms (it keeps the air instead); `tidy_clips` lists and removes slivers under a chosen length and heals the splits that only sit next to each other; both undoable. |
| "Heal splits" and "extend the clip edge to uncover more audio" had to be done by hand. | `tidy_clips` heals; `restore_clips` with `extend_ms` uncovers audio around each piece. |
| Clip edges had 10 ms fades only when RapReaper made them; dragged clips had none. | `tidy_clips` with `fades_ms` puts fades on every clip edge. |

## Export and checking on devices

| What happened | Change in 1.3.0 |
|---|---|
| Only a whole-song export existed; a 33-second preview with a fade had to be cut with ffmpeg elsewhere. | `export_mix` takes `start_seconds`, `end_seconds`, `fade_out_seconds`, `fade_in_seconds`. |
| Only WAV; the person's listener could not play it and nearly asked for a transcript. | `formats`: wav, mp3 (needs the small `lameenc` package the installer adds, or an ffmpeg the person allowed), m4a (needs an allowed ffmpeg). Lossy files get `lossy_headroom_db` (default 1 dB) so the decoded peaks stay under the ceiling; every file is measured after writing. |
| How the mix sounds on an iPhone speaker was checked by hand with ffmpeg filters. | `device_check` (and every export answer): the mix as a phone speaker plays it (mono, 350 Hz - 12 kHz), as mono, and how much of the vocal survives, with plain-words findings. |
| An export was made by accident while probing the tool. | `dry_run: true` says what would be exported and changes nothing. |

## Levels and balance

| What happened | Change in 1.3.0 |
|---|---|
| Matching v11, v12, v13, V5 to the other vocals took a measure-set-measure loop per track. | `level_match`: measure a list of tracks (or all vocals) and set each to one target loudness in one call; it uses the fader up to +12 dB and the chain's output trim above that. |
| The fader stops at +12 dB (v11 needed more). | `set_track` accepts up to +24 dB: the fader takes the first 12, the output trim the rest; the answer says so. |
| `measure` refused `role: adlib`. | Roles: vocal, adlib (a vocal measured with ad-lib expectations), beat, mix. |
| The same EQ change had to be sent to 13 tracks one call at a time. | `set_plugin` takes `tracks` (a list, or "all vocals") as well as `track`. |
| "Make all vocals very dynamic" could only bypass the compressors, which changed little because the recordings were already even. | `vocal_dynamics`: more or less dynamic by a stated number of dB, with a real upward/downward stage (expander for more, the slow compressor for less) fitted by measurement, level-matched afterwards. |
| A send-fed effect track ("v4b Pitch Down") could not be measured ("no audio clips of its own"). | `measure` on a track without clips measures what it receives, over the feeding tracks' range. |

## Clarity (the listener "wished she could hear the vocals better")

| What happened | Change in 1.3.0 |
|---|---|
| There was no measurement for "can the words be heard"; it took several blind passes of EQ and faders. | `vocal_clarity`: for a vocal against the rest of the mix, over time: speech-band vocal-to-beat ratio, the ranges where the beat covers the voice ("words likely lost" with times), consonant energy, modulation clarity (how much the syllables stand out), a brassiness index (energy and ringing in 1-4 kHz), and what to change first. Measures; changes nothing. |
| "Brassy" (honky, ringing upper mids) had to be guessed at. | `fix_resonances`: narrow cuts at the ringing peaks the measurement flags, on the clean-up EQ, only as deep as the peak sticks out, undoable; `vocal_clarity` reports brassiness before and after. |
| Would a model trained on audio help the analyser? | Looked into (see "Training on audio" in README): no training on the person's audio, ever; an optional, local-only transcription step (whisper.cpp, if the person installs it and allows it with `Allow Transcriber.bat`) can mark the words a listener would miss. Without it, `vocal_clarity` works from signal measurements alone. |

## Project, snapshots, names

| What happened | Change in 1.3.0 |
|---|---|
| "Save project" could not be done; Ctrl+S by hand. | `save_project` (confirm required): REAPER's own save of the open project to its own file. Never "Save As", never a new path, never when the project has no file yet. |
| The song-setup snapshot left out sends, effect-bus plug-ins (pitch, delay, reverb settings), the master limiter, and clip edits. | Snapshots (format 2) keep sends with their levels, every plug-in setting on RapReaper's effect tracks, the master limiter, and a copy of the clip layout; `recall` puts them back; old snapshots still load. |
| "Rename 'Adlib Space'" and "remove the muted 'Vocal2 Pitch Down'" could only be offered. | `rename_track` (any track); `remove_effect_track` (only a track RapReaper itself made, only when it is muted or silent, confirm required). |
| Friendly names failed in some tools: `add_plugin grit`, `add_send_effect reverb`, `Auto Pan`, `Effect EQ`. | One name resolver for every tool: slot keys (hpf, grit ...), slot labels ("Effect EQ", "Tone EQ"), effect words (reverb, delay, pitch, saturation, tremolo, auto pan, chorus, limiter, compressor, eq, gate/expander, de-esser) and REAPER's own names. |
| Option names that did not exist cost a call each: song_setup `name`, bypass `off`, set_track `muted`, measure `from_seconds`/`to_seconds`, export_mix `seconds`, get_chain `track`, save_chain `name`, voice_effect without `effect`, project_overview `detail`. | Aliases accepted everywhere (the answer says which option was meant); a stray option that nothing needs is ignored with a note instead of an error. |
| `balance_vocal_and_beat` needs the two track names. | Without them it takes the tracks RapReaper marked vocal and beat (the beat is the track with the longest clip that is not a vocal). |

## Safety and privacy (unchanged promises, kept for the new parts)

- No network code anywhere; the optional helpers (ffmpeg, whisper.cpp, lameenc) run only on this PC, only on files inside the kit's own folders, only with fixed arguments, and only after the person allowed the exact program file (path + SHA-256, like Vocal Remover).
- A transcription, when the person has allowed one, is written to `kit\state\transcripts\` on this PC; Claude is told which words are unclear and at what time, never the words themselves unless the person asks for them (`vocal_clarity` with `show_words: true`).
- Nothing new leaves the PC: answers carry numbers and times; the privacy filter covers every new answer.
- `save_project` writes one file: the project's own. `remove_effect_track` and `restore_clips` are undoable and refuse anything RapReaper did not make or record.
- The clip journal in `kit\state\edits\` holds positions and the recordings' paths (on this PC only); it is removed by the same expiry as the other state files.

## Known limits that stayed (and why)

| What happened | Why it stays |
|---|---|
| The "sliding pan" wanted a sweep slower than once a second; the stock JS Tremolo's slowest rate is 1 Hz. | A slower sweep is a pan envelope on the track, and RapReaper writes automation only on the volume of its own 'Ducks' plug-in (a deliberate limit: envelopes on the person's own controls are theirs). Ask for it by hand in REAPER: the track's pan envelope. |
| The master limiter is fitted on the first 2 minutes of a longer song. | One render has to fit inside the time Claude waits for a tool; the export answer measures the whole file afterwards and says if the end differs. |
| M4A/AAC needs ffmpeg; nothing in Python's standard library or in a small ready-built package writes AAC. | ffmpeg is used only after the person allowed that exact file; the WAV and MP3 (lameenc) never need it. |
| The transcriber (whisper.cpp) is optional and has to be installed by the person. | RapReaper never downloads programs or models: that would be network code, and a model file is large. |

## 1.3.1 — setup for any audio interface

| What happened | Change in 1.3.1 |
|---|---|
| The prerequisites asked for "your Focusrite driver", and the playbook and the recording advice spoke of the Scarlett Solo's inputs only; someone with another interface (or a USB microphone) had no setup instructions. | `AUDIO INTERFACE SETUP.txt`: driver, REAPER's Audio > Device page (ASIO or WASAPI, inputs, sample rate, buffer), a mono record input on the right socket, phantom power, gain to about -12 dB peaks, monitoring, troubleshooting, for any brand. README, PLAYBOOK and the one-sided-take advice are brand-neutral, with the Scarlett kept as one example. |
| README pointed to a "RapReaper Guide" PDF that was never in the folder. | It points to `AUDIO INTERFACE SETUP.txt` and `kit\PLAYBOOK.md`. |

## 1.3.2 — a one-click way to get the optional programs

| What happened | Change in 1.3.2 |
|---|---|
| ffmpeg, whisper.cpp and a model are optional but had to be found, downloaded, unpacked and placed by hand before the "Allow" files could be used. whisper.cpp's newest release (v1.9.4) carries no Windows programs, so a "latest" link failed. | `Get ffmpeg and Transcriber.bat` + `get_tools_b5130.ps1`: downloads, unpacks and places them (tools\ffmpeg, tools\whisper, kit\models), checks the model's published SHA-256, prints the programs' SHA-256 and the two paths for the Allow files. whisper.cpp is pinned to release b5130 (Windows zip present). Allows nothing itself. |
