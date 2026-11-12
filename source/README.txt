RapReaper 1.5.0 - mix rap vocals in REAPER by talking to an AI (Windows)
=======================================================================

What this folder is
-------------------
RapReaper connects an AI app to REAPER on this computer: any app that speaks MCP (Claude,
ChatGPT's desktop app, Cursor, VS Code, LM Studio, Gemini CLI ...), or RapReaper's own Local
AI, which runs on this computer. You type what you want ("clean up my lead vocal", "a bit
brighter", "make it loud enough for Spotify", "line the new beat up with the old one", "what
key is this beat in"); the AI sets REAPER's own plug-ins in real units, measures the audio
before and after, and tells you what changed. RapReaper and your audio stay on this
computer. (An online AI app is an online service: the text of the chat goes to the company
that runs its model, Anthropic for Claude, OpenAI for ChatGPT; your audio does not. With the
Local AI, nothing leaves this computer.)

Any audio interface or microphone works: Focusrite, PreSonus, Universal Audio, MOTU,
Audient, Behringer, Steinberg, a USB microphone, or none at all. RapReaper works on what
REAPER has recorded, never on the interface itself. Setting up your microphone and
interface in REAPER, step by step for any brand, is in "AUDIO INTERFACE SETUP.txt" in
this folder. The full guide is "RapReaper Guide (Windows).pdf" in this folder; how the AI
works with you is in kit\PLAYBOOK.md. There is a Mac version of RapReaper too (its own
zip, with the same tools).

Before you install RapReaper (all free to install)
-----------------------------------------------
  1. REAPER            https://www.reaper.fm/download.php      (start it once, then close it)
  2. SWS extension     through ReaPack, REAPER's package installer:
                       a. From ReaPack's official site https://reapack.com download
                          reaper_reapack-x64.dll (the file for 64-bit Windows REAPER)
                       b. In REAPER: Options > Show REAPER resource path in explorer;
                          put the file in the UserPlugins folder there; restart REAPER.
                       c. Extensions > ReaPack > Browse packages, type SWS, right-click
                          "SWS/S&M extension" > Install, Apply, restart REAPER.
  3. Python            https://www.python.org/downloads/windows/
                       Take the "Python install manager" or the "Windows installer (64-bit)".
                       Not the 32-bit installer, not an "embeddable package".
  4. An AI app        any app that speaks MCP, started once: Claude Desktop
                       (https://claude.com/download), ChatGPT's desktop app, LM Studio,
                       Cursor, VS Code, Windsurf, Gemini CLI ... Or none: RapReaper's own Local
                       AI (Get Local AI.bat, after the install) runs on this PC, for free.
  5. Your audio interface's (or USB microphone's) Windows driver, from the support or
     downloads page of whoever made it, and REAPER pointed at it. Not needed if it already
     records in REAPER or another recording program on this PC; many USB microphones need
     no driver. Step by step for any brand: "AUDIO INTERFACE SETUP.txt".

Install
-------
  1. Put this whole folder inside your own user folder, so that it ends up at
         C:\Users\<your Windows name>\RapReaper
     (quick way there: in File Explorer click the address bar, type %USERPROFILE% and
     press Enter). Not in Downloads, OneDrive or AppData. If your Windows name has
     accented or non-English letters, use  C:\RapReaper  instead.
  2. Double-click   Install RapReaper.bat   and read what it prints. If it lists things
     "TO DO", do them and run it again. It is safe to run as often as you like.
  3. Quit your AI app completely and open it again (Claude: right-click its icon near the
     clock > Quit). No AI app? Double-click Get Local AI.bat once, then Start Local AI.bat.
  4. Start REAPER, open a project.
  5. In your AI app type:   Run the RapReaper self-test
     Run it again once your first recording is in a project: it then also checks, on your
     REAPER, that one track is measured on its own and that measurements follow the fader.

The installer needs no administrator rights. It adds two Python packages (NumPy and
SciPy), plus two optional ones when they can be fetched (lameenc for MP3 copies of exports,
ONNX Runtime for taking the voice out of a song), and, outside this folder, changes two
files: REAPER's startup script (two short marked blocks: one starts the bridge, one notes
the time REAPER was started and starts RapReaper's own small helper for clip edits) and
the MCP settings of each AI app it finds (one "rapreaper" entry each: Claude, ChatGPT's
desktop app and Codex, Cursor, VS Code, Windsurf, LM Studio, Gemini CLI). It keeps a dated
copy of each file before changing it, and Uninstall RapReaper.bat takes the changes out again. One more thing it
may change, with REAPER closed: if REAPER's preference "Set media items offline when
application is not active" is on, the installer switches it off in reaper.ini (copy kept),
because with it on REAPER unloads the audio whenever your AI app's window is in front and every
measurement comes back silent. With REAPER open, the self-test switches it off instead.

Updating
--------
  From 1.4.0 on: download the new RapReaper zip into Downloads and double-click
  Update RapReaper.bat. It checks the zip against the checksums it carries, replaces the
  program files, keeps your chains, settings, mix log, references, exports, snapshots, model
  files and the optional programs, puts the replaced files in updates\, and runs the new
  version's installer. Then close REAPER and start it again (the helper inside REAPER only
  loads at start; status reminds you until you do).
  From 1.3.2 or older (no Update file yet): unzip the new zip over the old folder once,
  replacing files (nothing of yours is in the zip), then double-click Install RapReaper.bat.

What changed in 1.5.0, nightly (12 November 2026)
-------------------------------------------------
  A nightly build: RapReaper works with any AI app that speaks MCP, and comes with an AI of
  its own that runs on this PC.
  - Any AI app. Before, the installer connected only Claude Desktop, and stopped with a
    "TO DO" without it. It now adds RapReaper to every AI app it finds: Claude Desktop,
    ChatGPT's desktop app and Codex (.codex\config.toml), Cursor, VS Code (GitHub Copilot),
    Windsurf, LM Studio and Gemini CLI, keeping a copy of each settings file first. Any other
    app that speaks MCP gets the three settings to add by hand in kit\state\ai_app_settings.txt.
    "py rapreaper_install.py --ai-apps claude,chatgpt" (or none) chooses; Uninstall takes it out of all.
  - RapReaper's Local AI. Get Local AI.bat downloads, once, llama.cpp's llama-server (MIT) and
    an open AI model, Qwen 3.5 (Apache 2.0; 4B, 2.7 GB, or 9B, 5.7 GB, by the PC's memory),
    each checked against its published checksum. Start Local AI.bat then opens a chat window in
    your browser with RapReaper connected: nothing you say leaves this PC, no account, no
    subscription. It asks before every tool runs. It is slower and less careful than the big
    online models, and gets the 28 core tools (LocalAI\README.txt says what to expect).
  - The tools no longer assume Claude: answers name the app you are using ("Quit Cursor
    completely and open it again"), the privacy notes say who reads the chat (Anthropic for
    Claude, OpenAI for ChatGPT, Google for Gemini, nobody for a model on this PC), and the log
    of what was sent is kit\logs\sent_to_ai.log (an older sent_to_claude.log is still counted
    and cleared).
  - The tool list suits every app: each option has one type, free-form options are marked as
    such, and the 28 core tools come first (a few apps use only the first 40 tools). Setting
    RAPREAPER_TOOLS=core in an app's settings gives a small model those 28 and a short playbook.
  Details in kit\CHANGES-1.5.md. The helper inside REAPER is 1.5.0 (one version line); the
  Vocal Remover is unchanged (2.0.3). RapReaper itself still has no network code: Get Local AI
  is a separate script you start, like the other Get files.

What changed in 1.4.7, nightly (9 November 2026)
------------------------------------------------
  A nightly build: the fixes for every problem met with RapReaper so far that were still
  open. Use it to try them before they come out as a release.
  - "Get Vocal Remover Model.bat" stopped at its last step on most computers: the model
    builder needed the Python package PyYAML, which the installer never installs. It now
    reads the model's settings file by itself, and the model comes out byte for byte the
    published file as before.
  - REAPER's preference "Set media items offline when application is not active" came back
    on every time REAPER started (7 and 10 October). fix_setup and the self-test switched it
    off in the running REAPER only; they now save it in REAPER's settings file too
    (reaper.ini, a copy of the old file kept next to it). status says when it would come
    back at the next start.
  - After Update RapReaper, Claude kept running the previous version until Claude itself was
    restarted, and new options were refused without saying why (4, 7 and 23 October). Every
    answer now says so ("Quit Claude completely and open it again") until you do.
  - A REAPER set to render in real time could slip past the speed check when the computer
    was busy, and a long measurement then ran in real time. It is caught now.
  - "Get Vocal Remover Model.bat" had line ends Windows' cmd can misread; it has Windows'
    own now, like every other .bat file.
  Details in kit\CHANGES-1.4.md ("1.4.7"). The helper inside REAPER is 1.4.7 (one version
  line); the Vocal Remover is unchanged (2.0.3).

What changed in 1.4.6 (9 November 2026)
---------------------------------------
  Ready for GitHub. RapReaper's own files are now under the MIT licence (LICENSE.txt). The
  Vocal Remover's model can be built by anyone: "Get Vocal Remover Model.bat" downloads its two
  source files from their authors (checked by checksum) and the installer builds the model from
  them in about a minute, byte for byte the published file. Model pieces still work as before.
  A third package, RapReaper-1.4.6-Mac-Intel.zip, brings the Vocal Remover to Intel Macs.
  Nothing works differently on Windows; the Vocal Remover is 2.0.3 (same program, which also
  knows the Intel Mac runtime). Details in kit\CHANGES-1.4.md ("1.4.6").
  The helper inside REAPER is 1.4.6 (one version line).

What changed in 1.4.5 (9 November 2026)
---------------------------------------
  The Vocal Remover is rebuilt with Go 1.27.3, the current release of the language it is written
  in. It was built with Go 1.24.7, which no longer gets fixes and has had security fixes since,
  in the parts of Go that serve the Vocal Remover's window. Its own code is unchanged; it is now
  version 2.0.2. Nothing else works differently: your chains, settings, projects and model files
  stay as they are. Both guides (PDF) are rebuilt for this version (the Windows one said 1.4.1, the
  Mac one 1.4.2); the Mac guide now writes the Ducks effect's folder the Mac way and says that the
  Vocal Remover needs macOS 14 or newer. Details in kit\CHANGES-1.4.md ("1.4.5").
  The helper inside REAPER is 1.4.5 (one version line).

What changed in 1.4.4 (11 October 2026)
---------------------------------------
  Ducks that did not duck. A dip drawn with duck_section (a breath, a loud word, the beat under
  a line) came out as a small PLUS of up to 0.8 dB instead: REAPER applies an envelope in the
  control's own units, and the stock Volume/Pan Smoother that served as the "Ducks" plug-in has
  a dB slider, so the 0..1 values RapReaper wrote played as dB. The Ducks plug-in is now
  RapReaper's own small effect, "JS: RapReaper Ducks" (kit\effects\RapReaper\ducks.jsfx), whose
  one control runs 0..1; the installer copies it into REAPER's Effects folder, so after this
  update run Install (Update does that for you) and restart REAPER once. A track that still has
  the old plug-in gets it replaced, and its dips drawn again at their real depth, the first
  time duck_section is used on it; the answer says so. Details in kit\CHANGES-1.4.md ("1.4.4").
  The helper inside REAPER is 1.4.4 (one version line).

What changed in 1.4.3 (10 October 2026)
---------------------------------------
  A heavy project (dozens of pitch plug-ins: ReaTune on every vocal, ReaPitch on every
  double) that rendered at twice real time was turned down with "REAPER renders too slowly
  for measuring", even for a ten-second preview, because any render slower than a quarter
  of real time was refused outright. Now only a real-time ("Online render") speed is refused
  with that fix; a project that is merely slow is measured and exported in shorter stretches,
  and a piece that is too long for one call says how long a piece fits and how to export the
  whole song in REAPER. The helper inside REAPER is 1.4.3 (one version line; restart REAPER
  once after updating).

What changed in 1.4.2 (8 October 2026)
---------------------------------------
  Every problem met on 7 October, and its fix, is in kit\CHANGES-1.4.md ("1.4.2"); the security
  and privacy ones in detail in SECURITY.txt (audit rounds 7 to 13). In short:
  - Mixes for phones: the new phone_fixes tool measures the mix as a phone speaker plays it and
    in mono, shows a plan (bass harmonics the phone can play, room for the words, wide effects a
    little quieter, vocal and beat centred, limiter headroom), makes the moves when you say yes
    and measures again. device_check gained two numbers (bass harmonics, harsh top), the
    engineering cards a "phones" topic, the guide a chapter. 57 tools.
  - Privacy, maximum level: track names no longer slip through in undo texts, send texts, the
    solo list, the mix log, error texts, progress messages, restore points, chain notes or
    capture file names; a real track called "MASTER" is never confused with the master bus; a
    track RapReaper makes from another (a split piece, an effect track) never carries your
    account name along.
  - Working again: restore_clips with two edits in one second, level_match at the maximum
    level, the doubler/reverb/delay on tracks with long names, labels accepted for an existing
    effect track and for a remaster, auto-picked tracks never the master bus.
  - Update keeps placeholder files only at the top of your own folders; the build refuses to
    ship a package with the builder's own folder names in it.
  The helper inside REAPER is 1.4.2: close REAPER and start it again after updating.

What changed in 1.4.1 (7 October 2026)
--------------------------------------
  An independent audit of the 1.4.0 packages and the fixes it led to; SECURITY.txt has the list.
  In short: no folder path, account name, computer name or e-mail address can reach Claude any more
  (only file names), the Vocal Remover makes no internet connection on a Mac either, Update asks
  before installing and refuses older versions or crafted zips, separate_song never overwrites or
  takes another song's files, a cancelled command never runs later, and lowering the privacy level
  is your own step (rapreaper_install.py --privacy). The helper inside REAPER is now 1.4.1: restart
  REAPER once after updating.

What changed in 1.4.0 (October 2026)
------------------------------------
  Everything that stopped us or cost effort so far, and what was done, is in kit\CHANGES-1.4.md.
  In short:
  - A Mac version (RapReaper-1.4.1-Mac.zip), with the same tools, double-click .command files,
    its own README, interface setup and guide. One source tree, two packages.
  - Updating in place: Update RapReaper.bat (above). Status says when REAPER still runs the
    previous helper after an update.
  - Projects: list_projects, open_project (by name, by recent number, by path in your own
    folder, or a new empty project; in the current tab or a new one) and close_project. Both
    show a plan first and need your yes; unsaved changes are never dropped unless you chose
    that (save first, drop them, or keep the project open in its tab).
  - fit_instrumental: slide a track into place by its music. A new instrumental, a longer or
    trimmed version of the same beat, or a vocal recorded over the old beat: RapReaper lines
    the two up by their attacks and then by the waveforms themselves, to the sample, moves the
    clips as one undoable step and checks the result. Repeating music lists the other shifts
    that fit; a longer intro that would start before 0 is trimmed off the clip (undoable, the
    recording untouched). Or a plain move: "put the beat at 8 seconds".
  - song_tempo_and_key: the beat's BPM (with the half/double-time reading), its key with the
    confidence and the relative key, note values in milliseconds for delays and pre-delay, the
    auto-tune settings, and whether the beat is tuned off A=440. learn_song_effects reads the
    song's tempo by itself now.
  - reference_card: the engineering numbers Claude works from, kept on this PC (loudness
    targets of the services, where things live in a rap vocal, compression and de-essing,
    reverb and delay for rap, auto-tune and keys, the order of work, doubles and ad-libs, the
    808 problem, recording, a glossary, and your phrases with the tool for each).
  - remaster: mix and master a song made in another program here. Export its tracks (or
    the finished mix) as audio files from Pro Tools, FL Studio, Ableton Live, Logic, Cubase,
    Studio One, GarageBand or Audacity, copy them into kit\imports\<song name>, and ask Claude to
    remaster that folder: every file goes on its own track, lined up from the same start (or by
    the positions Pro Tools wrote into the files), and the usual pass follows. REAPER cannot
    open another program's project file; when one is found, RapReaper gives the exact export
    steps for that program instead.
  - Names: the names of music files and of saved projects are given to Claude as they are, at
    every privacy level, so Claude can list what is in the references or remaster folder and
    call songs by name; never a folder or path, and never a file that is not music.
  - The Vocal Remover 2.0 comes inside this folder (VocalRemover\) and Claude can use it:
    "make an instrumental from <song>" (separate_song) separates a song in kit\references into
    the music and the voice, as WAV files in kit\imports that add up to the song exactly, and
    puts them on tracks when you ask. New default network (the Mel-Band Roformer "Kim FT2",
    as accurate as 1.3's BS-Roformer within 0.3 dB in well under half the time), the
    graphics processor through DirectML with a built-in test that switches it on only when
    it works and is faster, FLAC and Ogg files, a Mac build, and "Two networks" for the most
    accurate result. The installer joins the model pieces from Downloads into
    VocalRemover\models (or, since 1.4.6, builds the model from the files "Get Vocal Remover
    Model.bat" downloads), allows the program for RapReaper and offers the graphics test.
    Its own README.txt in VocalRemover\ explains every mode.
  - A full disk is noticed before REAPER renders into it (status warns under 2 GB free).
  - For whoever works on RapReaper next: build\build_release.py builds both zips; DEVELOPMENT.md
    says how to test, build and update.
  Earlier versions (1.1.1, 1.2.0, 1.3.0, 1.3.1, 1.3.2): kit\CHANGES-older.md and kit\CHANGES-1.3.md.

What is in here
---------------
  RapReaper Guide (Windows).pdf  the full guide: set-up, first session, what to say, every tool in
                          plain words, safety and privacy, troubleshooting
  AUDIO INTERFACE SETUP.txt  setting up your microphone and interface in REAPER (any brand)
  Install RapReaper.bat      sets everything up (runs rapreaper_install.py)
  Check RapReaper.bat        shows what is and is not set up; changes nothing outside this folder
  Update RapReaper.bat       updates from a newer RapReaper zip in Downloads, keeping your files
  Make Full Packages.bat     makes RapReaper-<version>-Windows-full.zip (and -Mac-full.zip) from the zips
                          and the model pieces in Downloads: one zip with the Vocal Remover's model
                          inside, to unzip and install on another computer
  Uninstall RapReaper.bat    disconnects RapReaper from REAPER and from the AI apps
  Get Local AI.bat        downloads RapReaper's Local AI once: llama.cpp and an open AI model (Qwen 3.5),
                          each checked by checksum (get_local_ai.ps1 is the script it runs)
  Start Local AI.bat      starts the Local AI and opens its chat window in your browser
  LocalAI\                the Local AI: README.txt, licenses\, runtime-b12250.json (llama.cpp's files and
                          their checksums); runtime\ and models\ fill when you run Get Local AI
  Get ffmpeg and Transcriber.bat   downloads ffmpeg, whisper.cpp and a speech model into this folder
                          (optional, one time; get_tools_b5130.ps1 is the script it runs)
  Allow Vocal Remover.bat lets RapReaper start another copy of the Vocal Remover (the installer
                          allows the one in this folder by itself)
  VocalRemover\           the Vocal Remover 2.0: VocalRemover\VocalRemover.exe starts its window;
                          models\ holds the network files (joined from the downloaded pieces by the
                          installer), Output\ its results, settings.json the graphics-processor
                          choice; README.txt explains the modes; source\ is its complete source code
  Allow ffmpeg.bat        lets RapReaper start your ffmpeg (optional; only for MP3/M4A copies of exports)
  Allow Transcriber.bat   lets RapReaper start your whisper.cpp (optional; only to write down a vocal's
                          words on this PC for vocal_clarity)
  SECURITY.txt            what was reviewed for security and privacy, what was found and fixed
  MANIFEST.sha256         the checksum of every shipped file (the updater checks a new zip with its own)
  DEVELOPMENT.md          for whoever changes RapReaper: how to test, build and update
  kit\rapreaper_server.py    the tool server your AI app starts (57 tools; 28 with RAPREAPER_TOOLS=core)
  kit\rapreaper_platform.py  what differs between Windows and macOS (folders, words), in one place
  kit\rapreaper_music.py     tempo, key and alignment of recordings; the reference cards
  kit\rapreaper_programs.py  the one door through which an allowed program (ffmpeg, whisper.cpp) is started
  kit\rapreaper_effects.py   reads a song's vocal effects (auto-tune, key, reverb, delay, width ...)
  kit\rapreaper_separate.py  takes the voice out of a song: the Vocal Remover program, or the same
                            networks through ONNX Runtime (model files in VocalRemover\models or kit\models)
  kit\rapreaper_audio.py     the measuring code (loudness, noise, hum, tone, sibilance, gaps, clarity,
                          breaths ...)
  kit\rapreaper_helper.lua   RapReaper's own small helper inside REAPER: splits clips, cuts,
                          mutes or turns pieces down, heals splits, removes slivers, restores a
                          saved clip layout, moves clips (into place, or to another track), reads
                          and sets sends, saves, opens and closes projects on request, and
                          reads/writes a plug-in's settings block (for ReaTune's key and scale)
  kit\PLAYBOOK.md         the working method the AI follows (PLAYBOOK-CORE.md: the short form for the
                          core tools and small models)
  kit\CHANGES-1.5.md      what 1.5.0 changed (any AI app, the Local AI)
  kit\CHANGES-1.4.md      every problem met so far and the change it got, 1.4.0 to 1.4.7
  kit\CHANGES-1.3.md, kit\CHANGES-older.md   the same for the earlier versions
  kit\chains\             starter chains; your saved chains go in kit\chains\user
  kit\effects\            RapReaper's own small "Ducks" effect (RapReaper\ducks.jsfx); the installer
                          copies it into REAPER's Effects folder
  kit\references\         put finished songs you like here (WAV) to compare against
  kit\imports\            put a song exported from another program here (its tracks or its mix), in a folder
                          named after the song, then ask the AI to remaster it
  kit\exports\            where "export the mix" puts your bounced songs
  kit\captures\           measurement recordings RapReaper makes and tidies up by itself (it keeps
                          the newest ones, at most about 1.5 GB; you may empty it any time
                          REAPER is closed)
  kit\state\              the mix log, RapReaper's notes about your tracks, settings.json (the
                          privacy level, expiry, the activity log on/off, the programs you allowed),
                          install.json (the version installed), private_names.txt (which label stands
                          for which name), songs\ (saved song setups), edits\ (restore points for
                          clips) and transcripts\ (words written down by the transcriber, if you use one)
  kit\logs\               sent_to_ai.log: every answer exactly as it was sent to the AI app
  kit\reaper-daemon\      the bridge script that runs inside REAPER (third-party, MIT
                          licence, unmodified; see NOTICE.txt)
  updates\                the files an update replaced (made by Update RapReaper.bat)
  build\                  how the kit is packaged (not needed to use it)
  sim\  and  tests\       how this kit was tested before it was shipped: a simulated REAPER
                          and the test programs (not needed to use RapReaper)

Safety in one paragraph
-----------------------
RapReaper opens no network port and contains no network code (the one optional download
helpers, "Get ffmpeg and Transcriber.bat" and "Get Local AI.bat", are separate scripts you
start yourself); the AI talks to it through the app, and it talks to REAPER through small text files inside this
folder. It can add and set plug-ins, move faders, add tracks and sends, and render
measurement files and your exported songs into this folder. It can split a clip and cut
out, mute or turn down a piece of it when you ask for that (empty air, a breath, a harsh
S), put the clips back from a restore point, move clips into place or to another track,
draw volume dips (ducks) on a plug-in of its own, rename a track, and remove a plug-in or
an effect track it added itself (never a track with recordings on it). It cannot delete
your files or your tracks, never overwrites a project: save_project does REAPER's own Save
of the open project to its own file, only when you asked, and never Save As; open_project
and close_project do what REAPER's File menu does and never drop unsaved changes without
your word. It starts no other program, with the exceptions you allow by name yourself (see
"Programs" below). Every change to your project is a named entry in REAPER's undo history
(Ctrl+Alt+Z shows the list), and each answer says how to take that step back. When it has
to switch a plug-in off or pull a fader down to measure, that change, the measurement and
the change back are sent to REAPER as one command, so nothing is left changed even if
the AI app or this program is closed in the middle. Keep the folder inside your own user
folder: a folder directly under C:\ can be changed by every Windows account on the PC.

What leaves this PC (to the company that runs your AI app's model: Anthropic for Claude,
OpenAI for ChatGPT and Codex, Google for Gemini; nothing with the Local AI or another model on
this PC): the words you type and the text the tools answer (plug-in names,
settings, measured numbers, track names, and the names of files and of the project only
as the privacy level allows). Never audio. The answers never contain the folder paths on
this PC or your Windows user name (they say <RapReaper folder> and %USERPROFILE% instead);
the same goes for RapReaper's log. The names of your music files (songs, beats, takes,
stems, mixes, exports, the songs in the references and remaster folders) and of your saved
projects are given as they are, so the AI can list what is in a folder and call songs by
name; never a folder, a path or any other kind of file: at the default level (strict) a
file that is not music is a label whose meaning stays on this PC (kit\state\private_names.txt),
and at maximum, track names are labels too. kit\logs\sent_to_ai.log shows you exactly
what left this PC, answer by answer. Name tracks plainly and leave personal details out of the chat: what you type goes
to your AI app's company regardless of the level. For troubleshooting only, RAPREAPER_PRIVACY=off shows
the real paths again; 'status' always says which level is in force.

Programs. What RapReaper will not run or load: it never starts a program it merely found on
this PC. Besides its own separation worker it can start only programs you allowed yourself,
one at a time, with an "Allow ... .bat" file: a Vocal Remover (learning a song's effects),
ffmpeg (MP3 and M4A copies of exports) and whisper.cpp (writing down a vocal's words for
vocal_clarity). For each, RapReaper keeps the file's path and checksum in kit\state\settings.json
and stops using it if the file changes; each is started with fixed arguments, without a
shell or a window, only on files inside RapReaper's own folders, and never goes online.
The Kim Vocal 2 model file is checked against its published checksum before it is loaded.
The bridge script and RapReaper's own helper script are checked against their checksums at
every start; a changed file is reported and not used. A RapReaper zip is installed by the
updater only when every file in it matches the zip's own manifest. Names from your project
(tracks, files, notes) are data to the AI, never instructions; anything that removes or
silences part of a recording, or opens or closes a project, happens only after you said yes
to that step.
