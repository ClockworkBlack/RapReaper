# RapReaper: what changed in 1.1.1, 1.2.0, 1.3.0, 1.3.1 and 1.3.2

(Moved here from README.txt in 1.4.0, word for word. The 1.4.0 changes are in CHANGES-1.4.md; the problems that led to
the 1.3 work are in CHANGES-1.3.md.)

```
What changed in 1.3.2 (October 2026: a one-click way to get ffmpeg and the transcriber)
----------------------------------------------------------------------------------------
  - New: "Get ffmpeg and Transcriber.bat". Double-click it once (with internet) and it downloads
    ffmpeg (MP3/M4A copies of exports), whisper.cpp (writes down a vocal's words on this PC) and the
    speech model ggml-base.en.bin into this folder: tools\ffmpeg, tools\whisper and kit\models. The
    model is checked against its published SHA-256 and deleted if it does not match; the programs'
    SHA-256 values are printed. It then tells you the two paths to paste into "Allow ffmpeg.bat" and
    "Allow Transcriber.bat". It allows nothing itself: RapReaper still uses a program only after you
    allowed that exact file.
  - This is a separate script (get_tools_b5130.ps1, plain text, one screen of PowerShell) that you start
    yourself. RapReaper's own programs still contain no network code, and nothing in RapReaper starts the
    script. whisper.cpp is fetched from its release b5130 (the newest release that carries Windows
    programs); if a link ever stops working, the script prints the error and changes nothing else.
  - Both programs stay optional: without them RapReaper exports WAV (and MP3 with the lameenc package)
    and measures clarity from the signal alone.

What changed in 1.3.1 (October 2026: setup for any audio interface)
--------------------------------------------------------------------
  - New: "AUDIO INTERFACE SETUP.txt", setting up any interface or microphone for recording
    in REAPER (driver, ASIO or WASAPI, sample rate and buffer, a mono record input on the
    right socket, gain to about -12 dB peaks, phantom power, direct monitoring, and a
    troubleshooting list). Nothing in RapReaper depends on the brand; 1.3.0 only read as if
    it did.
  - The prerequisites above no longer ask for a Focusrite driver; Claude's playbook and the
    recording advice RapReaper gives ("set the record input to Mono and pick the input the
    microphone is plugged into") are written for any interface, with the Scarlett Solo kept
    as one example.
  - README no longer points to a "RapReaper Guide" PDF that was never in the folder.
  - No change to what any tool does to a project; the helper inside REAPER only carries the
    new version number.

What changed in 1.3.0 (October 2026: everything that stopped us while mixing a real song)
-----------------------------------------------------------------------------------------
  Every line here was something that could not be done, or went wrong, in one long mixing
  session; kit\CHANGES-1.3.md lists them all with the change each got. In short:

  Clips
  - restore_clips: before every cut RapReaper now writes a restore point (each clip's place,
    length, fades and recording) to kit\state\edits; any track's clips can be put back as they
    were before any earlier cut, whatever has happened since, as one undoable step. The cut
    tools name their restore point in the answer.
  - A cut never leaves a sliver shorter than 120 ms (they click); tidy_clips removes slivers,
    heals splits that changed nothing and puts fades on every clip edge.
  - split_to_track: an effect "from 6 seconds on" no longer needs you to split and drag the
    clip by hand: the piece moves to a new track under the source, with the same chain.
  Export
  - export_mix takes start_seconds / end_seconds (a 33-second preview), fade_in / fade_out,
    and formats ['wav', 'mp3', 'm4a'] (MP3 with the small lameenc package the installer adds,
    or an ffmpeg you allowed; M4A with ffmpeg); lossy copies get 1 dB of headroom so decoded
    peaks stay under the ceiling. dry_run says what would be written and writes nothing.
  - Every export answer, and the new device_check tool, say how the mix comes across on a
    phone speaker (mono, nothing under 350 Hz) and summed to mono, and -- with vocal_track --
    how far the words stand above the rest there.
  Levels
  - level_match: measure several vocal tracks and set them all to one loudness in one go.
  - set_track goes to +24 dB: the fader takes +12, the chain's output trim the rest.
  - set_plugin takes tracks (a list, or 'all vocals'); measure takes role 'adlib' and can
    measure an effect track that only receives sends.
  - vocal_dynamics: more or less dynamic by a stated number of dB, by measurement, loudness kept.
  Clarity (the listener who "wished she could hear the vocals better")
  - vocal_clarity: measures a vocal against the rest of the mix: how far the words stand above
    it in the 1-4 kHz band, where the beat covers the voice (with times), how much of the
    syllable rhythm survives, a brassiness index, a 0-10 clarity score, and what to change first.
  - fix_resonances: narrow cuts at the ringing peaks that make a vocal brassy or honky.
  - Optional, local only: a transcriber (whisper.cpp) you allow yourself can mark the words a
    listener would miss. See "Training on audio" below for what was and was not done here.
  Project and names
  - save_project: REAPER's own Save (Ctrl+S) of the open project to its own file, after you
    asked; never Save As, never a new path.
  - Song setups now keep every track's sends, the plug-ins on RapReaper's effect tracks, the
    master limiter and the clip layout (recall_clips true puts the clips back too).
  - rename_track; remove_effect_track (only a track RapReaper made, with no clips, confirm).
  - 'grit', 'reverb', 'auto pan', 'Effect EQ' and the other words people use for plug-ins
    work in every tool; option names people reach for (song_setup name, bypass off, set_track
    muted, measure from/to_seconds, export_mix seconds ...) are accepted, and a stray option
    nothing needs is ignored with a note instead of stopping the call (a misspelling is still
    an error, with the right name suggested). balance_vocal_and_beat finds the vocal and the
    beat itself when you do not name them.
  Two new "Allow" files: "Allow ffmpeg.bat" and "Allow Transcriber.bat" (see "Programs").

Training on audio: would a model trained on audio help the analyser?
---------------------------------------------------------------------
  Looked into for 1.3.0, and this is where it landed:
  - Training a model on YOUR recordings was ruled out. It would mean keeping and processing
    your audio for a purpose other than the mix in front of you, and RapReaper's promise is
    that audio is only ever measured, on this PC, for the task you asked for.
  - A model trained from scratch on other people's rap vocals was ruled out too: there is no
    honest data set of "how clear were the words to a listener" to learn from, so such a
    model would be guessing in a way that cannot be checked, and it would be large.
  - What does help, and is in 1.3.0: measurements that follow what listeners report. The
    vocal_clarity numbers (vocal over the rest of the mix in the word band, covered stretches,
    syllable-rhythm survival, brassiness) are the things a listener means by "I can't hear
    the words" and "it sounds brassy", and they can be checked against the mix.
  - What helps further, as an option: an existing speech recogniser run on this PC. whisper.cpp
    (open source, offline) can write down the words of the vocal with a confidence for each;
    a word it is unsure of is one a listener strains for. RapReaper uses it only if you install
    it, put a model file in kit\models and allow the program with "Allow Transcriber.bat". It
    never downloads anything, and the words stay on this PC: Claude is told which words are
    unclear and when, not what they are, unless you ask (show_words).

What changed in 1.2.0 (October 2026: more stays on this PC, less goes to Claude)
--------------------------------------------------------------------------------
  - A privacy LEVEL, set with the new 'privacy' tool ("show me RapReaper's privacy settings",
    "set privacy to maximum") and kept in kit\state\settings.json:
      standard  answers never carry a folder path on this PC or your Windows user name, and
                audio never leaves the PC (this was the only level before 1.2.0).
      strict    (the default) as standard, and the names of audio files (your beats, songs,
                exports, reference songs) and of the project are replaced by labels such as
                "file 3.wav" and "project 1". Claude only ever sees the labels; the list of
                which label is which is kit\state\private_names.txt on this PC, and the tools
                take the labels back ("compare the mix to file 3.wav" just works). Track
                names are still given as they are, because you type them in the chat anyway.
      maximum   as strict, and track names are labels too ("track 2"), which every tool
                accepts. Claude then talks about "track 2"; you read what that is in
                private_names.txt. Name the track yourself in the chat if you prefer.
  - An activity log on this PC: kit\logs\sent_to_claude.log records every answer exactly
    as it was sent to Claude, with the time, the tool and the arguments Claude gave. Open it
    any time to see what left this PC; nothing in it goes anywhere. It is turned over at
    8 MB and removed by expiry. The privacy tool can switch it off.
  - Expiry: measurement files, vocal-session notes and the activity log older than 30 days
    are removed when RapReaper starts (privacy set expire_days N; 0 = never). Your exports,
    reference songs, chains, song setups and the mix log are never removed by RapReaper
    itself; the privacy tool's 'clear' can remove them only after you agreed (confirm).
  - A cap on every answer (150,000 characters) and a test that no answer ever carries audio
    data: only numbers measured from the audio leave the PC, never samples.
  - Shorter answers by default: project_overview leaves out what says nothing (a pan in the
    middle, a track that is not muted); track_details can list one plug-in's controls
    (plugin ...); the undo note is one sentence. Less to read, less that leaves the PC.
  - Song setups (the new 'song_setup' tool): "save this song's setup as Rough Draft 3" keeps
    every track's fader, pan and mute, the RapReaper plug-ins with all their settings and
    ReaTune's key, scale and speed in one file in kit\state\songs; "recall Rough Draft 3"
    puts it all back on the tracks with the same names, undoably. Nothing in a setup
    leaves the PC.
  - The self-test no longer quotes a clip's file name in its result (it says what it saw,
    not what it was called), and the server log keeps names out of Claude's reach too.
  - Clean-up: 'privacy' with action clear removes captures, vocal_sessions, activity_log,
    private_names or all three temporary kinds at once (temporary).

What changed in 1.1.1 (October 2026, after a first run on a real REAPER 7.81)
-----------------------------------------------------------------------------
  - Auto-tune sets ReaTune's key, scale and speed itself: RapReaper now knows REAPER 7's
    ReaTune settings block (104 bytes) by heart. ReaTune keeps key and scale as the set of
    allowed notes, so any scale can be written: major, minor, chromatic, dorian, phrygian,
    lydian, mixolydian, locrian, arabian, egyptian, harmonic minor, major/minor pentatonic,
    blues. The self-test checks that the layout fits this REAPER; plugin_state can still
    learn a different one.
  - The voice effects use the control names REAPER 7 really reports: ReaPitch's "1: Shift
    (semitones)" / "1: Formant adjust (semitones)", the stock JS Saturation's one slider
    "Amount (%)", and the stock JS Chorus (guitar/chorus; delay/delay_chorus is the
    fallback, set as a chorus). The self-test checks the effects with the same settings
    voice_effect makes.
  - The self-test switches the "media offline when inactive" preference off itself
    instead of stopping, and the installer does it in reaper.ini when REAPER is closed.
  - RapReaper's helper inside REAPER (1.1.1) no longer answers "could not be read" when a
    virus scanner holds a fresh command file for a moment; it waits for it.
```
