# RapReaper Guide (Mac): mix rap vocals in REAPER with Claude

RapReaper 1.4.7 (nightly) · 9 November 2026

RapReaper lets you mix rap vocals in REAPER by typing to Claude: Claude sets REAPER's own plug-ins in real units, measures the audio before and after, and tells you what each change did. RapReaper and your audio stay on your Mac. Claude itself is an online service, so you need the internet, and the text of the chat (never audio) goes to Anthropic.

It also cuts empty air, breaths and single harsh sounds out of the clips, or turns one spot down without touching the clip; adds voice effects including auto-tune, all with REAPER's own plug-ins; makes an instrumental (and the voice alone) out of a finished song with the Vocal Remover, which now comes inside the RapReaper folder and can use your graphics processor; learns the vocal effects of a finished song you like and puts them on your track; measures how clearly the words come across and how the mix sounds on a phone; saves, opens and closes projects on your word; slides an instrumental into place by its music; reads a beat's tempo and key; brings a song made in Pro Tools, FL Studio or another program in to mix and master it here; and keeps the engineering numbers it works from on your Mac, so the advice is the same from one chat to the next. The whole process was tested against a simulated REAPER; a short self-test repeats the decisive checks on your computer.

This is the Mac guide. The Windows version of RapReaper has its own guide with the same chapters.

## How it fits together

Three programs on your Mac, one service outside. You type in Claude Desktop, and Claude picks a RapReaper tool. The tool server (a small Python program Claude starts) writes a small command file; the bridge script inside REAPER carries it out and answers. To measure, REAPER renders a few seconds of a track to a file on your Mac and RapReaper analyses it. Only the resulting numbers go back to Claude.

## What you need

Five things, all free to install. Only REAPER (after its trial) and regular use of Claude cost money.

| What | Where to get it | Cost | Why |
| --- | --- | --- | --- |
| REAPER (the current version) | [reaper.fm/download.php](https://www.reaper.fm/download.php), the macOS download for your Mac (Apple Silicon or Intel) | Free for 60 days with every feature, then $60 for a personal licence | The recording program Claude controls. |
| ReaPack, then the SWS extension through it | [reapack.com](https://reapack.com/) for ReaPack, REAPER's package installer; SWS then comes from inside REAPER | Free | SWS lets REAPER measure audio without pop-up windows. |
| Python (3.12 or newer) | [python.org/downloads/macos](https://www.python.org/downloads/macos/), the "macOS 64-bit universal2 installer"; Homebrew's Python works too | Free | Runs RapReaper. Its maths packages go into RapReaper's own small environment inside the folder. |
| Claude Desktop | [claude.com/download](https://claude.com/download) | The app is free. A paid plan is realistic for regular mixing: tool use eats the free allowance fast | Where you type. |
| The RapReaper folder | The Mac zip that came with this guide, plus the model pieces (the files whose names contain `.onnx.part`) | Free | The connector: 57 tools, the measuring code, the bridge script for REAPER, RapReaper's own helper for clip edits, the Vocal Remover program with its neural networks (the pieces are its model file), and the engineering notes Claude works from. |

Your audio interface or USB microphone works with REAPER as it is; on a Mac almost all of them need no driver. "AUDIO INTERFACE SETUP.txt" in the RapReaper folder sets it up step by step for any brand.

## Set-up, step by step

Do these once, in this order; allow about half an hour. The installer in step 7 checks Python, the folder, REAPER, SWS and Claude, and tells you what is missing.

1. **Your interface.** Follow "AUDIO INTERFACE SETUP.txt" (REAPER pointed at it, a Mono input for the mic, gain to about -12 dB peaks). If it already records in another program on this Mac, most of that is done.
2. **REAPER.** Download the macOS version for your Mac (Apple Silicon or Intel), drag it into Applications and start it once. Until you buy a licence it shows an evaluation notice at every start: wait a few seconds, then click the button to carry on. Quit REAPER.
3. **SWS extension, through ReaPack.** SWS's own website is not needed: ReaPack, REAPER's free package installer, fetches SWS from its official releases.
   - Download ReaPack from its official site, [reapack.com](https://reapack.com/) (by Christian Fillion): the file for your Mac, `reaper_reapack-arm64.dylib` (Apple Silicon: M1, M2, M3, M4) or `reaper_reapack-x86_64.dylib` (Intel).
   - Start REAPER, choose Options > Show REAPER resource path in finder, open the **UserPlugins** folder there and put the file in it. Quit REAPER and start it again. If macOS says the file cannot be opened, allow it once: System Settings > Privacy & Security, scroll down, "Allow Anyway", then start REAPER again (this is ReaPack's own documented step). The Extensions menu now has a ReaPack entry.
   - Extensions > ReaPack > Browse packages. Type `SWS` in the Filter box, right-click **SWS/S&M extension** and choose Install, then click Apply. Restart REAPER when ReaPack asks.
   - Check: the installer in step 7 reports "SWS extension found", and RapReaper's status says SWS is installed. Quit REAPER.
4. **Python.** On python.org/downloads/macos take the **macOS 64-bit universal2 installer** of the current Python 3 and click through it with the standard choices. The `python3` that macOS itself offers is only a stub for Apple's developer tools and is not used; RapReaper's `.command` files look for python.org's or Homebrew's Python (`brew install python` works too).
5. **Claude Desktop.** Install it and sign in.
6. **RapReaper.** Double-click the RapReaper zip in Downloads; it unpacks into a folder. Open it until you see **Install RapReaper.command**: the folder that holds it is the RapReaper folder. Move that folder into your home folder (in the Finder: Go > Home, or Cmd+Shift+H), so that it becomes `/Users/<your name>/RapReaper`. Leave the model pieces (`MelRoformer-KimFT2-int8.onnx.part1of8` and so on) in Downloads: the installer joins them.
   - Not in Downloads, Desktop or Documents: macOS asks each program (Terminal, REAPER, Claude) for permission to use those folders, and a refused one fails silently. Not in iCloud Drive, Dropbox or Google Drive either: they move files.
7. **Run the installer.** In the RapReaper folder, double-click **Install RapReaper.command**. The first time, macOS says the file "cannot be opened" because it came from the internet: open System Settings > Privacy & Security, scroll down, click **Open Anyway**, and double-click it again. (Or: open Terminal, type `bash` and a space, drag the file into the window, press Return.) A Terminal window shows what it does.
   - It ends with **ALL SET**, or with **NOT FINISHED YET** and a short list of things to do. Do the list and run it again. It is safe to run as often as you like. After this first run, every RapReaper `.command` file opens by double-click: the installer releases them (and the Vocal Remover's files) from the download quarantine after checking each against the package's manifest.
   - Terminal asks once for permission to read your Downloads folder (for the model pieces): allow it.
   - With REAPER closed, it also switches off the one REAPER preference RapReaper cannot work with ("Set media items offline when application is not active"), keeping a copy of the settings file; with REAPER open, the self-test does that instead.
   - Its last step sets up the Vocal Remover: it checks the program against the package's manifest, allows it for RapReaper, joins the model pieces into `VocalRemover/models` (each checked against its published checksum) and then offers the graphics-processor test (a few minutes; see "Instrumentals from a song"). You can say no and run the test later from the Vocal Remover's window.
8. **Restart Claude completely.** Claude menu > Quit Claude (or Cmd+Q), then open Claude again. Closing the window is not enough.
9. **Start REAPER** and open or create a project. Save it once (Cmd+S). Give REAPER about a minute before the next step: the bridge starts after the evaluation notice, and after a quick quit-and-reopen of REAPER it waits a full minute. A small window with the words BRIDGE\_ALREADY\_RUNNING during that minute is harmless: close it.
10. **Run the self-test.** In a new Claude chat, type: `Run the RapReaper self-test`
    - The first time Claude uses each RapReaper tool it asks permission. Choose **Always allow** to stop the question for that tool, or Allow once to be asked every time.
    - Claude runs the test in several short steps and then lists the checks. You may hear a faint beep. The test adds one muted track called "RapReaper self-test"; delete it afterwards if you like (right-click the track > Remove tracks).
11. **Read the result.** Every line should say PASS. A line that says FAIL names what was measured and comes with its fix: do the fix and ask Claude to run the test again.

The installer needs no administrator password. It makes RapReaper's own small Python environment inside the folder (`kit/venv`) and puts NumPy and SciPy there, plus two optional packages when they can be fetched (lameenc for MP3 copies of exports, ONNX Runtime so RapReaper can run the separation networks itself when the Vocal Remover program is not allowed); the Mac's own Python is left as it is. Outside the folder it changes two files: REAPER's startup script (`~/Library/Application Support/REAPER/Scripts/__startup.lua`: two short marked blocks, one starts the bridge, the other notes each REAPER start and starts RapReaper's own small helper) and Claude's settings file (`~/Library/Application Support/Claude/claude_desktop_config.json`: one "rapreaper" entry). It keeps a dated copy of both files before changing them.

To see RapReaper inside Claude, click the + in the chat box and open Connectors: "rapreaper" should be listed.

## Updating RapReaper

When a new RapReaper zip comes, download it into Downloads (with any new model pieces) and double-click **Update RapReaper.command**. It checks the zip against the checksums it carries (a damaged download is refused), replaces the program files (the Vocal Remover's program too), keeps your chains, settings, mix log, references, exports, imports, song setups, model files, the Vocal Remover's results and settings and the optional programs exactly as they are, puts the files it replaced in the `updates` folder, and then runs the new version's installer. Afterwards quit REAPER and start it again: the helper inside REAPER only loads when REAPER starts, and until you do, RapReaper's status reminds you (everything else keeps working). Terminal asks once for permission to read Downloads: allow it.

**One complete package to carry to another computer.** The Vocal Remover's network (237 MB) is too large to come inside the zip, so it arrives as eight pieces. Once the zip(s) and the eight pieces are in Downloads, double-click **Make Full Packages.command** in the RapReaper folder: it writes `RapReaper-1.4.5-Windows-full.zip` (and `RapReaper-1.4.5-Mac-full.zip`, when the Mac zip is in Downloads too) next to them, with the model already in `VocalRemover/models`, checked against its published checksum and listed in the package's own checksum list. Copy a full package to any computer by USB stick or cloud drive, unzip it and run Install RapReaper: nothing else to download. A full package also works with Update RapReaper (it puts the model in place if that computer lacks it), and a damaged one is refused like any damaged zip.

## Your first session

A first mix is three things: a vocal and a beat on two named tracks, a saved version, and one sentence to Claude. Start every new song as a new project (File > New project) and save it once with Cmd+S. If the Save window offers to create a folder for the project, accept: each song then lives in its own folder.

1. **Beat.** Drag the beat's audio file from the Finder into REAPER's empty track area. Double-click the track's name and call it `Beat`.
2. **Vocal track.** Press Cmd+T for a new track and name it `Lead Vox`. Click the round red button on the track (record-arm). Then right-click that button and pick a Mono input: the one your microphone is plugged into ("AUDIO INTERFACE SETUP.txt" says which).
3. **Level.** Rap your loudest line and turn the interface's gain until the track meter peaks around -12 dB. Use the interface's direct monitoring so you hear yourself without delay. If you then hear your voice twice, switch off the small speaker button on the track (record monitoring).
4. **Record.** Cmd+R starts recording, Space stops. If REAPER asks what to do with the recorded file, choose to save it.
5. **Save a version.** File > Save new version of project (Cmd+Option+Shift+S). This is your way back if you dislike a mix.
6. **Check once.** The first time there is a recording in a project, type `Run the RapReaper self-test` again. Two more lines appear. They show that RapReaper measures your vocal on its own and at its true level, and that the measurement follows the fader. This is how RapReaper learns how your REAPER renders a single track.
7. **Ask.** In Claude, type something like:

> Clean up my lead vocal and make it sound professional. The vocal track is Lead Vox and the beat is Beat.

Claude then works in this order and reports after each step:

1. **Plan.** Measures the raw vocal and tells you what it found. Nothing is changed yet.
2. **Clean-up.** Rumble, hum, mud and the noise between lines.
3. **Compression.** Evens out loud and quiet words.
4. **Tone.** Clarity and air, toward a bright modern rap sound unless you ask otherwise.
5. **De-essing.** Tames sharp S and T sounds.
6. **Finish.** Matches the loudness to before, shows a before/after table and saves the chain.

Then listen. Ask Claude to "switch the chain off and on while it plays" and judge with your ears. When you like it, press Cmd+S, or say "save the project": RapReaper saves only when you ask. Typical next requests: "sit the vocal in the beat", "add a short reverb", "can the words be heard?", "make it loud enough for Spotify", "export the song".

While Claude works, REAPER's render window flashes open and shut and playback stops: that is RapReaper measuring. Claude waits only about a minute for any one tool, so RapReaper keeps each step short and does long jobs, such as the self-test and the final loudness, in several steps. Claude carries on by itself until the job says it is finished.

This is what a before/after table looks like. It comes from the deliberately bad test recording used to check RapReaper in simulation; your numbers will differ.

| Measurement | Before | After |
| --- | --- | --- |
| Loudness | -17.7 LUFS | -17.7 LUFS (matched on purpose) |
| Noise below voice | 22 dB | 35 dB |
| Rumble below voice | 17 dB | 32 dB |
| Mains hum | -52 dBFS | not found |
| Loud-to-quiet range | 14.6 dB | 6.1 dB |
| S sounds vs voice | -1.7 dB | -0.6 dB |
| Mud, compared with natural speech | +7.4 dB | +0.6 dB |
| Presence, compared with natural speech | -8.1 dB | -5.2 dB |

One pass does not fix everything, and RapReaper says so. Under this table it listed what was still there: room noise 35 dB below the voice, a few P and B pops, and a presence range still 5 dB under natural speech. Each came with what to ask for next, such as "quieter gaps" or "clearer".

## What to say

Talk to Claude the way you would talk to an engineer: say what you hear or what you want. These are the requests RapReaper is built around.

| You say | What Claude does |
| --- | --- |
| "Clean up my lead vocal", "make it sound professional" | The full pass: plan, clean-up, compression, tone, de-essing, finish. |
| "Brighter", "clearer", "more air", "less muddy", "warmer", "too harsh" | Moves the tone EQ 1-2 dB that way, then re-fits the de-esser and re-matches the loudness. |
| "More compression", "more even", "it sounds squashed" | Re-fits both compressors heavier or lighter, then the steps after them. |
| "Make it more dynamic", "less dynamic, by 3 dB" | A real upward or downward stage fitted by measurement, loudness kept (vocal\_dynamics). |
| "Punchier" | Claude asks which you mean: more even and in-your-face (heavier compression), or more snap at the start of each word (a slower attack on the first compressor). |
| "The S sounds hurt", "it sounds lispy" | More or less de-essing, in dB. |
| "I hear hiss between lines" | Turns the gaps down further with the expander. |
| "Too boomy", "too thin" | Moves the low cut up or down. |
| "It sounds brassy", "it rings" | Finds the ringing peaks and cuts them narrowly (fix\_resonances). |
| "Can the words be heard?", "my listener can't make out the lyrics" | Measures the vocal against the rest of the mix: how far the words stand above it, where the beat covers the voice, a clarity score, and what to change first (vocal\_clarity). With the optional transcriber, which words a listener would miss. |
| "How does it sound on a phone?" | Measures the mix as a phone speaker plays it and in mono (device\_check). |
| "It sounds bad on my phone", "the bass disappears on the speaker", "make it work on phones" | Measures the phone and mono versions and shows a plan of fixes (phone\_fixes), then makes them when you say yes: the wide effect tracks a little quieter, vocal and beat in the middle, bass harmonics the phone can play, room for the words, a limiter ceiling with headroom. Measures again and shows before and after. See "Mixes that carry on a phone". |
| "Sit the vocal in the beat", "vocal louder" | Measures both, shows you the plan, then moves the faders and dips the beat's EQ where it hides the words. |
| "Set all the vocals to the same level" | Measures every vocal track and brings them to one loudness in one go (level\_match). |
| "Add a short reverb", "add a delay" | Adds an effect track with REAPER's own reverb or delay and sends the vocal to it. |
| "A delay timed to the beat", "dotted eighth delay" | Reads the beat's tempo first (song\_tempo\_and\_key), then sets the delay in milliseconds. |
| "What key is this beat in?", "what's the tempo?", "auto-tune it to the beat" | Measures the beat: BPM (with the half/double-time reading), key with its confidence and relative key, note values in milliseconds, the auto-tune settings, and whether the beat is tuned off A=440. See "Tempo, key and the engineering cards". |
| "Cut the dead air", "remove the breaths", "turn that S down" | Finds the pauses and breaths by measurement, shows you the list, then cuts them out of the clips (or mutes or turns a piece down) as one undoable edit, with a restore point. See "Cutting empty air". |
| "Put the clips back the way they were" | Puts a track's clips back from a restore point (restore\_clips); "heal the splits", "add fades" (tidy\_clips); "move that part to its own track" (split\_to\_track). |
| "Auto-tune the hook in F sharp minor", "deep voice", "double it", "telephone voice", "add grit" | Puts the effect on with REAPER's own plug-ins, set in real units, and says what to listen for. See "Voice effects and auto-tune". |
| "The breaths are too loud but keep them", "turn that word down a bit", "that one S is harsh" | Turns the spots you pick down by a number of dB with short fades, without cutting the clip, as one undoable step. See "Turning one spot down". |
| "Make an instrumental from this song", "take the vocals out of Night Drive and put the beat in REAPER" | Separates a song in the references folder into the music and the voice with the Vocal Remover's networks (minutes; the graphics processor when it is on), saves both as WAV files in `kit\imports` and, if you ask, puts them on tracks (separate\_song). See "Instrumentals from a song". |
| "What effects are on the vocal in this song?", "make mine sound like that" | Takes the voice out of a song you put in the references folder, measures what was done to it (auto-tune, key, reverb, delay, compression, doubling, telephone) and puts the matching settings on your track. See "Learning the effects of a song". |
| "Line the new beat up with the old one", "the instrumental is in the wrong place", "put the beat at 8 seconds" | Lines a track up with another track that holds the same music, to the sample, after showing you the plan; or moves it to a time. See "Fitting an instrumental into place". |
| "Open my song Night Drive", "open a new project", "close this project", "which projects are there?", "save the project" | REAPER's own open, close and save, after showing you what would happen, and never over unsaved changes without your word. See "Projects". |
| "Remaster this song I made in FL Studio", "mix my Pro Tools session here" | Brings the exported tracks (or the finished mix) in from the imports folder onto tracks, lined up, and then the usual pass. See "Remastering a song from another program". |
| "How loud should it be for Spotify?", "where does mud live?", "what does attack do?" | Answers from the engineering cards kept on your Mac, so the numbers are the same every time (reference\_card). |
| "Do the same on my ad-libs", "use last week's chain" | Runs a fresh pass on that track, or applies a saved chain if you ask for the same settings. |
| "Save this song's setup", "put the Rough Draft setup back" | Saves every track's fader, pan, mute, plug-ins with settings, sends, effect tracks, the master limiter and the clip layout under a name, and puts it all back on request (song\_setup). |
| "Make it loud enough for Spotify", "master it" | Fits a limiter on the master to a loudness target, in a few steps, and checks the true peak of the whole song. It stops before the limiter flattens the song, and says so. |
| "Compare it with this song" | Compares loudness and tone with a WAV you put in the RapReaper folder under `kit\references`. |
| "Turn it off so I can compare" | Switches the whole chain off and on while the song plays. |
| "What does the de-esser do here?" | Switches that one tool off and on while the song plays, and tells you what to listen for. |
| "I liked it better before", "take the last step back" | Sets the earlier values back, or tells you which entry to click in REAPER's Undo History. |
| "Export the song", "export the first 33 seconds as an MP3 with a fade" | Bounces a WAV (and MP3 or M4A copies) into `kit\exports`, reports its loudness and true peak and how it comes across on a phone. If the file is clipped or peaks too high, Claude says so, and what to do first. |
| "What did we do last time?" | Reads the mix log. |
| "What does RapReaper send to Claude?", "set privacy to maximum" | Shows and sets the privacy level and what is stored on your Mac (privacy). |
| "Set the first compressor's ratio to 6" | Sets exactly that control and reads it back. |
| "Is everything working?", "run the self-test" | Checks the connection, the plug-ins and the measuring on your REAPER. Every line says PASS, or FAIL with its fix. |

Three habits make this work. Name your tracks. Ask for one change at a time when fine-tuning. Describe what you hear ("the S's are sharp") and let Claude choose what to turn.

## What each change does, and what you will hear

A vocal chain is a row of small tools that each fix one thing. The order matters, because each tool hears what the one before it left.

### The chain RapReaper builds, in order

| # | Tool (the REAPER plug-in) | What it does | What you hear | Too much sounds like |
| --- | --- | --- | --- | --- |
| 1 | High-pass filter (JS: RBJ Highpass/Lowpass Filters) | Removes everything below the voice's lowest note | Less rumble and thump, nothing missing from the voice | A thin, small voice |
| 2 | Hum notches (ReaEQ) | Very narrow cuts at the mains frequency (50 or 60 Hz) and its multiples. Added only when the hum is strong enough to hear | A steady buzz disappears | A hollow low end, if the cuts are too wide |
| 3 | Clean-up EQ (ReaEQ) | A broad cut around 300 Hz, sometimes one narrow cut for a ringing note | The voice stops sounding like it is in a box | Thin and papery |
| 4 | Expander (JS: Downward Expander) | Turns the quiet gaps between lines down further: by itself at most 12 dB | Room noise drops away between lines | Chopped word endings and breaths |
| 5 | Compressor 1, fast (ReaComp) | Catches the loudest words and turns them down | No word jumps out | Flat and lifeless, "squashed" |
| 6 | Compressor 2, slow (ReaComp) | Evens out whole phrases | Every line sits at the same level; quiet words come forward | Pumping; breaths and noise get loud |
| 7 | Tone EQ (ReaEQ) | A lift around 3.5 kHz, a shelf above 9 kHz, a small dip at 300 Hz | Words are easier to understand; the voice gets shine | Harsh, hissy, tiring |
| 8 | De-esser (JS: De-esser) | Turns the hissy top of the voice down, only while an S or T sounds: by itself at most 5 dB | S sounds stop stabbing | A lisp; dull consonants |
| 9 | Output trim (JS: Volume/Pan Smoother) | Sets the level back to where it started | A fair comparison: "after" is not simply louder | - |

RapReaper adds only the tools your recording needs, so your chain may have fewer rows; with strong rumble it uses two high-pass filters in a row. To see the tools, click the FX button on the Lead Vox track: each row in that window is one tool from the table. To hear what one of them does, ask Claude while the song plays: "switch only the de-esser off", then "back on".

Why this order: cleaning comes first so the compressors do not react to rumble. The de-esser comes after the compressors and the bright EQ, because both make S sounds stick out more. The trim comes last because louder always sounds better, and that would fool your ears.

On the whole song there are three more tools: a small EQ dip on the beat where it hides the words, an effect track for reverb or delay, and a limiter (ReaLimit) on the master.

### The controls, in plain words

**EQ (equalizer)**: volume knobs for ranges of pitch.

- **Frequency (Hz)**: which range. Low numbers are bass, high numbers are treble.
- **Gain (dB)**: how much up or down. 1 dB is subtle, 3 dB is clear, 6 dB is a lot.
- **Bandwidth**: how wide the range is. Wide changes the tone gently; narrow removes one specific sound.
- **Shapes**: a high-pass removes everything below a point, a shelf raises or lowers everything beyond a point, a bell touches one area.

**Compressor**: an automatic hand on the volume fader.

- **Threshold (dB)**: the level where it starts working. Lower means it works on more of the performance.
- **Ratio**: how firmly it pushes back. At 4:1, a word 8 dB over the threshold comes out 2 dB over.
- **Attack (ms)**: how fast it grabs. Fast (1-5 ms) controls the hit of each word; slower (20 ms) lets the start of a word punch through.
- **Release (ms)**: how fast it lets go. Too fast sounds jittery; too slow and the next word is still turned down.
- **Knee**: how gradually it starts.
- **Output gain**: turns the result back up. In ReaComp this is the slider called Wet.
- **Gain reduction (dB)**: how much it is turning down at this moment. 2-6 dB on the loud words is normal for a rap vocal.

**De-esser**: Frequency is where the hissy range starts; Threshold is how loud an S must be before it reacts. As RapReaper sets it, it turns down only the hissy top of the voice, and only while an S or T sounds.

**Expander**: below its Threshold, sound is turned down further. It is a gentler relative of the noise gate.

**Limiter**: Ceiling is the level the song can never pass. The lower the Threshold, the harder the song is pushed against that ceiling: louder, and flatter. Taking 2-6 dB off the loudest peaks is normal; RapReaper stops at 6 dB by itself.

**Reverb and delay**: they live on their own track, and the "send" is how much of the vocal goes there. Rap vocals want little: a short, dark reverb, or a delay timed to the beat (a quarter note lasts 60000 / BPM milliseconds; RapReaper measures the BPM for you).

### Where things live in the sound

| Range | What lives there | What RapReaper usually does |
| --- | --- | --- |
| below 80-100 Hz | rumble, thumps, the proximity bump of a close mic | high-pass filter |
| 100-250 Hz | the body of the voice; where it fights the 808 | leave it, or a small cut if the vocal and the bass blur |
| 250-500 Hz | mud and boxiness | the 2-4 dB cut around 300 Hz |
| 500-1000 Hz | honk, nasality | a small cut only when it nags |
| 1-2 kHz | edge, bite | a small lift for a soft voice |
| 2-5 kHz | presence: the consonants, the words you understand | the clarity lift, 1-3 dB |
| 5-9 kHz | S, T and SH sounds | the de-esser, only while they sound |
| above 10 kHz | air and shine | a 1-4 dB shelf |

Too much lift in presence turns clear into harsh; too much air turns shine into hiss. The same table, with more detail, is one of the engineering cards Claude reads.

## Reading the numbers

Claude reports measurements, not opinions. These are the ones you will see and what counts as normal.

| Number | What it means | What is normal |
| --- | --- | --- |
| dBFS | Level counted down from the digital ceiling, 0 dBFS. Anything that would go above 0 is cut off (clipped). | Recording: loudest lines peak around -12 dBFS. |
| LUFS | How loud something sounds on average. | A finished song: about -14 LUFS matches Spotify's playback level. Many rap releases are louder, roughly -10 to -7. |
| LU | A difference in loudness. 1 LU equals 1 dB. | Lead vocal from about 1 LU under the beat (laid back) to 3 LU above it (upfront). |
| dBTP (true peak) | The highest instant of the waveform, including between the samples. | Finished song: under -1 dBTP, and under -2 dBTP when it is louder than -14 LUFS. |
| Noise below voice | How far the room noise sits under the voice. Bigger is better. | Above 45 dB is clean. Under 35 dB you will hear it. |
| Loud-to-quiet range | The gap between loud and quiet phrases. | A raw take: often 8-14 dB. After compression: 4-7 dB. |
| Gain reduction | How much a compressor turns the loud words down. | 2-6 dB in the first compressor, 1.5-4 dB in the second. |
| S sounds vs voice | How strong the S and T sounds are next to the voice. | About 0 dB or lower is comfortable. +2 to +6 is a little forward. Above about +6 they hurt. |
| Tone vs natural speech | Mud, presence and air compared with an average speaking voice. 0 means the same. | For a bright modern vocal RapReaper aims at mud about -2, presence about +2, air about +4, and moves at most 4 dB per pass. |
| Clarity score (0-10) | How far the words stand above the rest of the mix in the speech band, how much of the syllable rhythm survives, and how much the beat covers the voice, in one number. | 7 and up: the words come through. Under 5: listeners will strain. The answer says which stretches are covered and what to change first. |
| Limiting | How much the limiter on the master takes off the loudest peaks. | 2-6 dB. RapReaper stops at 6 dB by itself, because more flattens a song. |
| Vocal + beat peak | The highest peak of the two together. | Under -3 dBFS, so the limiter on the master has room. |
| BPM | Beats per minute of the beat. | Whatever the producer set; a trap beat written as 140 is also felt at 70, and RapReaper gives both. |

The loudness and true-peak targets are the services' published figures (Spotify -14 LUFS / -1 dBTP; Apple Music -16; YouTube -14). The other ranges are common engineering practice and RapReaper's own working thresholds: starting points, not laws. Your ears decide. Numbers from the vocal steps describe the recording with the track's fader at 0 dB, so they stay the same when the fader moves; "measure" and the balance step describe the track as it sits in the mix.

## Cutting empty air, breaths and single sounds

Plug-ins can turn the gaps down; they cannot take them away. RapReaper can: it edits the clips themselves, the way an engineer does with the mouse, through a small helper of its own that runs inside REAPER. Every edit is a single entry in REAPER's Undo History, so Cmd+Z takes the whole edit back, and since 1.3.0 every cut also writes a restore point first. The recording file on disk is never changed.

| You say | What Claude does |
| --- | --- |
| "Cut the dead air", "clean the gaps", "remove the silence between lines" | **cut\_empty\_air**. First without changing anything: it measures the raw recording, finds every pause longer than half a second, and tells you how many it found and how many seconds of empty air they hold. Then, when you say yes, it cuts them out of the clips with 10 ms fades. Each cut keeps 0.2 s of tail after a word and 0.08 s of run-up before the next, so word endings and first consonants stay, and never leaves a sliver shorter than 120 ms (they click). A breath taken in a pause goes with the pause. |
| "Remove the breaths", "I can hear him breathing" | **find\_breaths** lists every breath it can find, with its time as REAPER's ruler shows it, how far under the words it sits, and whether it can be cut cleanly or runs straight into a word. Breaths that sit in pauses are easiest to take with cut\_empty\_air, all at once. A breath that runs into a word is turned down instead (next section), because cutting it would clip the word. |
| "That one S is too sharp", "turn that word down", "cut that word out" | **cut\_section** between two times: *duck* splits the piece off and lowers its clip volume (12-20 dB is a normal amount for a harsh S or a breath); *delete* cuts it out; *mute* keeps it in the clip but silent, so you can un-mute it later in REAPER. |
| "Put the clips back the way they were before the cut", "undo yesterday's cuts" | **restore\_clips** puts a track's clips back from any restore point (each cut names its own), re-creating the pieces from the same recordings, as one undoable step; with `extend_ms` it uncovers audio around each piece. |
| "Heal the splits", "add fades to every clip", "remove the slivers" | **tidy\_clips**: heals splits that changed nothing, puts fades on every clip edge, removes pieces shorter than a length you choose; undoable. |
| "Put the second verse on its own track" | **split\_to\_track** splits at the times you give and moves the piece to a new track under the source, with the same chain. |

**What counts as a word.** RapReaper measures the level of the loud parts and treats anything more than 24 dB quieter as "not a word": room tone, breaths, lip noise, the chair. Quiet words in a rap take are usually 8-15 dB under the loud ones, so they are safe. If a soft phrase does get cut, say so: Claude restores it and runs again with a larger "words" margin or a longer run-up, and the result says which to change.

**Where the breath limit is.** A breath that is as loud as the quiet words cannot be told from a word by measurement alone. find\_breaths still catches most of them by their sound (a breath is hiss, a word has pitch), but a very loud breath right before a line is the one case where the mouse in REAPER is quicker: click the clip, drag over the breath, press S at both ends and Delete.

**After a cut.** The vocal chain's settings still fit, because a cut only changes the gaps. Before re-fitting any stage, say "plan the vocal again": the clips changed, and the plan's before/after section is tied to them.

## Voice effects and auto-tune

The same stock plug-ins that mix the voice can also change it. One tool, **voice\_effect**, puts each of these on with sensible settings, in real units, and says what to listen for. They sit after the clean-up chain and before the output trim, so the chain keeps working as fitted.

| Effect | What it is | What to say |
| --- | --- | --- |
| Auto-tune (ReaTune) | Pitch correction: every note is pulled to the nearest note of the song's key. The speed decides the sound: *natural* (120 ms) is invisible repair, *tight* (40 ms) the modern pop vocal, *hard* (2 ms) the robotic effect itself. | "Auto-tune the hook, F sharp minor, tight" or "T-Pain effect, key C minor". RapReaper needs the key: ask for it ("what key is this beat in?") and it measures the beat; a wrong key pulls notes the wrong way. |
| Pitch shift (ReaPitch) | Moves the voice by semitones, with a separate *formant* control that moves the size of the "mouth". Pitch down with the formant left alone keeps the voice recognisable; both down makes a giant. | "Pitch it down three", "deep voice" (-3 semitones, formant -2), "chipmunk" (+5, +3). |
| Doubler | A second copy, 9 cents out of tune and 18 ms late, off to one side, on its own effect track. Wider and thicker, without an audible echo. | "Double the hook", "make it wider". "mix" sets how loud the copy is; -8 dB under the voice is normal. |
| Telephone / megaphone | Only the middle of the voice, about 400 Hz to 3 kHz, pushed forward at 1.5 kHz. Megaphone adds grit on top. | "Telephone voice on the ad-libs", "megaphone". Use it on a copy of a line or on an ad-lib track, not the whole lead. |
| Grit | Saturation: warmth at 1, audible fuzz at 3. | "Add some grit", "dirtier". |
| Chorus | A soft shimmer and width from moving, detuned copies. | "A little chorus". |
| Reverb, delay | Space behind the voice on its own effect track. The delay is timed to the song: a quarter note at the project's tempo, or at the beat's measured tempo. | "Short dark reverb", "a quarter-note delay", "a dotted-eighth delay". More or less of it later: move the effect track's fader. |
| Remove | Takes the effect plug-ins off the track again. Effect tracks stay; mute them, or ask to remove an effect track RapReaper made. | "Take the effect off". |

Every effect has an *amount* of 1 (subtle), 2 (normal) or 3 (strong), and you can still ask for any single control by name, exactly as with the mixing chain.

**Auto-tune and your REAPER.** ReaTune keeps its key, scale and speed in an internal settings block. RapReaper knows REAPER 7's layout of that block by heart and writes key, scale and speed straight into it (any scale: major, minor, chromatic, dorian, harmonic minor, pentatonic, blues ...), then reads it back. The self-test checks that the layout fits your REAPER; if a REAPER update ever changes it, RapReaper falls back to telling you exactly what to click in ReaTune's window, and can learn the new layout with you (plugin\_state).

**The self-test covers these too.** It adds each effect plug-in to its own test track, sets the controls RapReaper uses, and reports a control with a different name on your REAPER as "not set" together with the real names, so Claude can set it by its real name right away.

## Turning one spot down: breaths, words, one S

Sometimes the chain is right but one place is not: a loud breath before a line, one word that jumps out, one S that stabs. The section above cuts or lowers such a piece in the clip itself. RapReaper can also turn just that spot down without touching the clip at all, by the number of dB you choose, with fades of a few milliseconds so you do not hear a jump. This is called a duck. Nothing else in the song changes; the clip stays in one piece.

| You say | What Claude does |
| --- | --- |
| "The breaths are too loud, but keep them" | **find\_breaths** lists every breath with its time, how far under your voice it is, whether it can be cut cleanly, and whether it is already ducked. **duck\_section** then turns down the ones you pick, all in one step. 6 to 10 dB down keeps them natural; breaths taken out completely sound edited. |
| "Turn the word at 1:12 down a bit" | **duck\_section** turns that stretch down: 3 to 6 dB is a gentle change. Give the time from REAPER's timeline, or put the play cursor there and say so. |
| "That one S at 0:48 is harsh" | **duck\_section** finds the S nearest that time and dips just that S, with 3 ms fades. For sharp S sounds all through the song the de-esser is the right tool instead. |
| "Undo that duck" | One Cmd+Z in REAPER takes a duck back. Each answer says exactly how many presses it takes. |

**How a duck works.** RapReaper puts one more plug-in at the very end of the vocal chain, after the Output trim: a small effect of its own, "RapReaper Ducks" (the installer copies it into REAPER's Effects folder). It sits at full level, so on its own it changes nothing. Each duck is a dip drawn in that plug-in's one control as an envelope, and in REAPER you see it as a line under the track. RapReaper draws the dip, reads the whole envelope back to check it, and then measures the spot with the ducks on and off. When it did what you asked, the answer says so in numbers.

**Two things to know.** The ducks belong to this recording: they are tied to times in the song. Re-fitting the chain leaves them alone and measures without them; a saved chain leaves them out. If you re-record or move the vocal, take the old ducks back first. And a new dip may cover an old one, not cut it: a range that covers an earlier dip completely replaces it, which is how you change its depth; a range that starts or ends inside one is refused, and Claude says where the old dip is.

## Learning the effects of a song

Put a finished song you like in `kit\references` (WAV; MP3 too when the Vocal Remover program is on your Mac) and ask what was done to its vocal. Two tools do it: **learn\_song\_effects** takes the voice out of the song and measures it; **apply\_song\_effects** puts the matching settings on a track of yours. Since 1.4.0 the song's tempo is read from the song itself, so a delay comes back named as a note value without you looking the BPM up.

| Step | What happens | How long |
| --- | --- | --- |
| 1. The voice on its own | The Vocal Remover's network (the Mel-Band Roformer, see "Instrumentals from a song") separates the voice from the music: RapReaper starts the Vocal Remover program that came in its folder (the installer allowed exactly that copy, by path and checksum; RapReaper never starts a program it merely found), or runs the same network itself through ONNX Runtime with the model file in `VocalRemover\models`. If you already have the song's vocal as a file, name that instead and nothing needs separating. | A few minutes for a whole song, less with the graphics processor. Claude calls the tool again every minute or so until it is done; nothing is lost in between. |
| 2. The measurements | Up to 90 seconds of the voice, from its first sung part, are measured: auto-tune and how hard, the key, reverb, delay, compression, width, telephone, tone and S sounds. With the music on its own beside it, also the record's balance: how many dB the voice sits above the music, in plain words, with the balance\_vocal\_and\_beat call that puts the same balance on your tracks. | Seconds. |
| 3. On your track | Reverb and delay as effect tracks, set to the measured size, time and level; the double, the telephone sound and auto-tune through the voice\_effect tool, with the measured key, scale and speed; compression and tone come back as the vocal chain stages to run, because those are fitted by measurement to your own recording. | Seconds. Every part is a named entry in REAPER's undo history. |

**How sure it is.** These are measurements of a mixed, mastered record with the music taken out by a neural network, not a reading of the original session. Every finding carries a confidence from 0 to 1 and the numbers it was read from, and Claude reads them out that way. A delay shorter than about a quarter of a second cannot be told from the voice's own ending; gentle saturation cannot be told from a bright EQ, so it is reported as not measurable rather than guessed.

**What this needs on your Mac.** The Vocal Remover with its model file (the installer joins the pieces into `VocalRemover\models`), or ONNX Runtime (the installer adds it) with the same model file. Every model file is checked against its published checksum before it is loaded. The older Kim Vocal 2 model (`Kim_Vocal_2.onnx` in `kit\models`) still works when nothing newer is there.

## Instrumentals from a song: the Vocal Remover

The Vocal Remover takes a finished song apart into the music and the voice. Since 1.4.0 it lives inside the RapReaper folder (`VocalRemover`), the installer sets it up, and Claude can use it: say "make an instrumental from Night Drive" (the song in `kit\references`) and **separate\_song** first tells you what it would make, then on your word runs the separation in the background (minutes for a whole song; Claude follows the job until it is finished) and saves `Night Drive (Instrumental).wav` and `Night Drive (Vocals).wav` into `kit\imports`. "Put it in REAPER" lays the instrumental (and the vocal, if you want it) on new tracks, lined up at 0, and you record over it. The two files add up to the song exactly, sample for sample.

You can also use the Vocal Remover on its own: double-click `VocalRemover\VocalRemover.exe`, drop a song on its window (MP3, WAV, FLAC or Ogg Vorbis), pick a part or the whole song, a mode and a file type, and it writes the two files into `VocalRemover\Output`. Its own `README.txt` explains every mode.

**The networks.** The default is the Mel-Band Roformer "Kim FT2" (Kimberley Jensen's model, fine-tuned by unwa): on three test songs with known separate tracks it came within 0.3 dB of version 1.3's BS-Roformer and ran 2.2 times faster. The BS-Roformer is still there (its model file was already on your Mac if you had 1.3), and "Two networks" averages both for the most accurate result at about three times the time. Measured on the test songs (60 seconds each around the loudest singing; accuracy of the instrumental and of the vocal in dB, higher is better):

| Mode | Rap track (Little Chicago's Finest) | Electronic (BKS) | Rock (Angels In Amplifiers) | Time on a 2-core test computer, 60 s of song |
| --- | --- | --- | --- | --- |
__GUIDE_TABLE__

What remains after a separation is mostly traces of S and T sounds and the reverb or delay tails of the old voice; heavily processed ad-libs, layered doubles and vocal chops are the hardest case. Expect an instrumental you can record over on most songs, not a guaranteed total removal: listen before you build on it. Your new vocal covers most of what is left; where the old voice is still audible in a gap, "turn the beat down there" (duck\_section) hides it.

**The graphics processor.** On a Mac this is CoreML, which runs the network on the graphics processor and the Neural Engine of Apple Silicon. The Vocal Remover tests it before using it: "Test the graphics processor" in its window (the installer offers the same test at the end) runs one 8-second window through the network on the processor and through CoreML, compares the two results and times them. It switches CoreML on only when it works and is faster, and remembers that in `VocalRemover/settings.json`; RapReaper follows the same setting when it starts the program for Claude. If CoreML fails in the middle of a song, the song is finished on the processor and the result says so. The graphics-processor model is a float16 copy that the program makes from the processor model once (460 MB in `VocalRemover/models`, nothing downloaded), and its checksum is checked like every other model file. `python3 rapreaper_install.py --gpu on` additionally makes RapReaper's own ONNX Runtime ask for CoreML when it separates without the program. The Vocal Remover runs on Apple Silicon Macs (M1 and later) with macOS 14 or newer; on an Intel Mac RapReaper separates with its own ONNX Runtime on the processor.

**Speed to expect on the processor.** On the two-core test computer (a server processor with the 8-bit vector instructions) a 3-minute song takes about 4 minutes in the default mode, 9 with the BS-Roformer and 13 with both networks. A laptop processor without those instructions is much slower: on a Ryzen 7 3700U one 8-second window of the BS-Roformer took 131-189 s against about 20 s on the test computer, so count on roughly half an hour for a 3-minute song in the default mode and over an hour with the BS-Roformer; `start_seconds` and `seconds` cut the work down to the part you need, and the graphics processor is the real cure.

**On your Mac, nothing leaves it.** The Vocal Remover has no network code at all; its window is a page it serves to your browser on the address 127.0.0.1 with a one-time key. It writes your results, one `settings.json` next to itself and the graphics model file, nothing else. The ONNX Runtime and DirectML libraries it ships are Microsoft's unchanged signed files; the program checks them and every model against known checksums before loading them, and RapReaper starts only the copy the installer allowed, by path and checksum.

## Mixes that carry on a phone

Half of all listening happens on one small speaker: nothing under about 350 Hz, little over 12 kHz, mono, with the phone's own limiter clipping whatever peaks too hard. A mix that is right on studio speakers can fall apart there in five ways, and RapReaper measures each and fixes what it can:

| What happens on the phone | Why | What phone\_fixes does |
|---|---|---|
| The bass vanishes; the mix sounds thin and rattly | An 808 or kick that is all sub (40-120 Hz) has nothing the phone can play; the bass that survives is its harmonics at 150-300 Hz | Lifts the beat's EQ +2 dB at 200 Hz (wide). Never more sub: that only wastes headroom everywhere else. The number: `bass_harmonics_150to300_over_sub_db`, aim for -12 or better |
| The words get lost | What stands out on a phone is 1-4 kHz, where consonants live; keys, pads and hi-hats there hide the voice even when big speakers show room | Dips the beat's EQ at 2.5 kHz (2-4 dB, wide), measured with the vocal alone against the rest. The number: `phone_vocal_over_rest_1to4k_db`, aim for 0 or more |
| The vocal gets smaller, the reverb washes out | Stereo widening, doublers, chorus and stereo delays rely on two speakers; summed to one they partly cancel | Turns RapReaper's effect tracks down 2 dB (ask again for more) and pans the vocal and the beat to the middle. The number: `mono_loss_db`, aim for under 2.5 |
| Harsh, spitty top | A small speaker exaggerates 5-9 kHz: S sounds and hi-hats | Advice (nothing automatic): de-ess the lead a little harder (`vocal_chain` stage `deess`), dip the beat 1-2 dB at 6-8 kHz when the hi-hats are the cause. The number: `phone_top_5to9k_over_words_1to4k_db`, aim for under -3 |
| Clipping | With the bass taken away the mid-range peaks reach full scale, and the phone's limiter clips them | Sets the master limiter's ceiling to -1.5 dB (or says to finish with master\_loudness when there is no limiter yet). The number: `phone_true_peak_dbtp`, aim for under -0.5 |

Ask first without applying: Claude reads the plan out, then makes the moves when you say yes. Every move is one entry in REAPER's undo history, the answer shows every number before and after, and the engineering card `phones` (reference\_card) holds the reasons. Nothing replaces listening on the phone itself: export a short preview (`export_mix` with a start and end) and play it there.

## Tempo, key and the engineering cards

Two 1.4.0 tools give Claude facts instead of guesses.

**song\_tempo\_and\_key** measures the beat (or any track, or a song in the references folder) and gives:

- the tempo in BPM, with the other reading that is just as real: a trap beat written as 140 is felt at 70 (the kick and snare move at one speed, the hi-hats at twice that). A delay in milliseconds is the same either way; only its note name changes. The producer's number decides what to call it;
- the key, with how sure the reading is and its relative key (A minor and C major share every note: for auto-tune they are the same setting), the Camelot code DJs use, and the notes of the scale;
- note values in milliseconds at that tempo: eighth, dotted eighth and quarter for a delay, a sixteenth or thirty-second for a reverb's pre-delay, an eighth for a compressor's release that breathes with the beat;
- the auto-tune settings to use, and a warning when the beat is tuned off standard A=440 (then ReaTune's reference pitch has to follow, or the voice fights the beat);
- a note when REAPER's project tempo (usually still the default 120) does not match the beat, and how to set it.

Drums alone give no key, and a beat built on drums and one bass note gives a guess: the answer says so.

**reference\_card** holds the numbers RapReaper and this guide work from, on your Mac: the loudness the streaming services normalize to and their true-peak ceilings; where things live in a rap vocal; compressor and de-esser starting points; reverb and delay for rap (short dark plate, pre-delay, timed echoes, throws); auto-tune and keys; the order of work; doubles and ad-libs; the 808 problem; recording; a glossary; and your phrases with the tool for each. When you ask "how loud should it be for Spotify" or "what does the attack do", Claude reads the card and answers from it, as starting points: your ears decide.

## Fitting an instrumental into place

You recorded over a rough beat, and now the real instrumental has arrived: a better master, with a longer intro, or trimmed at the start. Or a vocal from another session has to land on this beat. **fit\_instrumental** slides a track's clips so that its music coincides with another track that holds the same music.

| You say | What Claude does |
| --- | --- |
| "Line the new beat up with the old one" | Renders both tracks as recorded and compares them: first their attacks (every hit, at one-millisecond resolution, over every possible shift), then the waveforms themselves around that shift, to the sample. It tells you how far the track would move, how sure it is, and how much of the two overlaps. Nothing moves yet. |
| "Yes, do it" | Moves the clips as one undoable step, measures again to confirm, and corrects a leftover of more than 2 ms once. |
| "That's a bar off" | Repeating music fits at several places; the answer lists the other shifts that fit, and "use the second one" moves by that instead. |
| "Put the beat at 8 seconds", "move the vocal 1.5 seconds earlier" | A plain move, to a time or by an amount. |

If the instrumental's intro is longer than the project has room for (the music would have to start before 0), RapReaper trims the extra off the start of the clip instead: the clip starts at 0 and reads its recording from further in. The recording file is untouched, the edit is undoable, and the plan says so before anything moves. A speed difference between the two recordings (one was stretched, or they were bounced at different sample rates) is measured and reported, not corrected. A match with a confidence under 0.4 is never applied unless you ask for it anyway.

The reference can be a vocal recorded over the old beat: enough of the beat leaks into a vocal take for the attacks to line up, and the answer's confidence says whether it did.

## Projects

Three 1.4.0 tools do what REAPER's File menu does, after showing you what would happen.

| You say | What Claude does |
| --- | --- |
| "Which projects are there?" | **list\_projects**: the project tabs open in REAPER (which is active, which has unsaved changes) and REAPER's own list of recent projects, numbered. |
| "Open Night Drive", "open the second recent project", "open a new project" | **open\_project** by name, by recent number, or by the path of a project REAPER lists as recent (a project REAPER has never opened is opened once by hand); or a new empty project. Without your yes it only reports; with it, REAPER's own open. |
| "Close this project" | **close\_project**. When it is the only tab, REAPER keeps one project open, so an empty project takes its place; the answer says so. |
| "Save the project" | **save\_project**: REAPER's own Save (Cmd+S), to the project's own file, never Save As. |

Unsaved changes are never dropped without your word. When the open project has them, the plan says so and Claude asks which you want: save first, drop the changes, or keep the project open in its tab and open the other one in a new tab. RapReaper opens only project files that REAPER lists as recent or that lie in your own user folder; a project REAPER has never opened on this Mac is opened once by hand (File > Open project), and from then on RapReaper can open it.

## Remastering a song from another program

Songs made in Pro Tools, FL Studio ("Fruity Loops"), Ableton Live, Logic, Cubase, Studio One, GarageBand or Audacity can be mixed and mastered with RapReaper. REAPER cannot open those programs' project files: a .ptx, .flp or .als holds patterns, plug-in settings and references to samples, not sound. Every one of those programs can export its tracks as audio files, and that is the way in.

1. In the other program, export every track (or at least the vocal and the beat) as a WAV from the very start of the song, 24-bit, at the session's sample rate, without normalizing. Export the finished mix once more as one file to compare against. The exact menu for each program is below.
2. Copy the files into the RapReaper folder under `kit\imports\<the song's name>`.
3. Ask Claude: "Remaster Night Drive" (the folder's name). **remaster** lists what it found: every music file with its length, channels and a guessed role (vocal, beat, mix), and any project file of another program with the export steps for it. Nothing changes yet.
4. Say yes. Every music file goes on a track of the open project named after it, lined up from the same start, or by the timeline positions Pro Tools writes into its exported clips, as one undoable step. Then the usual pass: the vocal chain on the vocal tracks, balance, the final loudness, the export. For one finished mix: measure, fix resonances, the final loudness, the export.

| Program | Where the export is |
| --- | --- |
| Pro Tools (.ptx) | Edit > Select All, Edit > Consolidate Clip (Option+Shift+3), so every file starts at the same moment; then in the Clips List select the new clips and choose Export Clips as Files (Cmd+Shift+K): WAV, 24-bit, into one folder. Newer versions: File > Bounce Mix with every track as its own source. |
| FL Studio (.flp) | Route the vocal and the beat to their own mixer tracks first. File > Export > WAV file, tick "Split mixer tracks": each mixer track becomes a WAV from the song's start. Export the whole mix once more without the tick. |
| Ableton Live (.als) | File > Export Audio/Video, Rendered Track: All Individual Tracks, from 1.1.1 to the end, WAV 24-bit, no normalization. |
| Logic Pro (.logicx) | File > Export > All Tracks as Audio Files, WAV 24-bit. |
| Cubase (.cpr) | File > Export > Audio Mixdown, Channel Selection: the tracks (multiple), WAV 24-bit, the whole range from the start. |
| Studio One (.song) | Song > Export Stems, WAV 24-bit, from the start. |
| GarageBand (.band) | Share > Export Song to Disk gives one mix; for separate tracks solo one at a time and export each, or open the project in Logic. |
| Audacity (.aup3) | File > Export > Export Multiple, split by tracks, WAV 24-bit, "include audio before first clip". |
| REAPER (.rpp) | REAPER's own format: copy the project folder into your own folder, open it once in REAPER, and from then on "open project" does it. |
| OMF / AAF | REAPER does not import these by itself; export the tracks as audio files from the program that made them. |

Files exported from the song's start line up by themselves. Files that carry their original timeline position (Pro Tools clip exports) are placed by it. A finished mix cannot be un-mixed: for a new vocal balance you need the separate tracks.

## Recording well

The recording sets the ceiling. A mix can remove hum and tame sharp S sounds, but it cannot repair a clipped take or a loud room.

- **Level.** Set the interface's gain so your loudest line peaks around -12 dBFS. If the interface's light turns red, the take is clipped: record it again with less gain. A clipped take cannot be repaired afterwards; RapReaper tells you when it finds one.
- **Distance.** Stay about a hand's width from the mic, with a pop filter in between. Very close adds boom; far away adds room sound.
- **P and B pops.** Aim the mic slightly to the side of your mouth, or a little above it.
- **Room.** Record with soft things around you (a closet of clothes, a duvet behind you) and away from the Mac's fan.
- **Input.** Record the mic on a Mono input, as in "Your first session". On a stereo input the voice lands on one side only; RapReaper spots that and tells you the ten-second fix.
- **Dry.** Record without effects. Everything in this guide is added afterwards and can be undone.

When a measurement points at the recording (clipping, a noisy room, a far-away mic), Claude says so and tells you what to change next time. "AUDIO INTERFACE SETUP.txt" covers the interface side for any brand.

## Doing it again

Every mix leaves a record you can reuse: the chain is saved as a numbered version, the whole setup of a song can be saved under a name, and every step is written to a log.

| What | Where it lives (inside the RapReaper folder) | How to use it |
| --- | --- | --- |
| Your chains | `kit\chains\user`, one file per version, never overwritten | "Apply my lead chain to the Verse 2 track", "go back to version 2" |
| Starter chains | `kit\chains`: rap-lead-starter, adlib-tucked, telephone-effect | "Put the telephone effect on the ad-lib track" |
| Song setups | `kit\state\songs`: every track's fader, pan, mute, plug-ins with settings, sends, effect tracks, master limiter, clip layout | "Save this song's setup as Rough Draft 3", "put Rough Draft 3 back" |
| Mix log | `kit\state\mix_log.jsonl` | "What did we do to the vocal last time?" |
| Reference songs | `kit\references`, WAV files you put there | "Compare my mix with the reference", "what effects are on this song's vocal" |
| Imported sessions | `kit\imports\<song>`, the tracks exported from another program | "Remaster Night Drive" |
| Exported songs | `kit\exports`, numbered, never overwritten | "Export the song" |

Two things to know:

- **A saved chain is fitted to one recording.** Thresholds depend on how loud a take is. Applying a saved chain replaces RapReaper's chain on that track: plug-ins the saved chain does not use are switched off, not removed. Claude then measures and adjusts it. For a new song, or for ad-libs, a fresh pass gives a better fit and takes a few minutes.
- **To adjust, name the change.** "Brighter", "less compression", "3 dB of de-essing": Claude re-fits that step and the ones after it, then saves the result as the next version.

## Safety and privacy

RapReaper runs on your Mac: it opens no network port, contains no network code, and your audio never leaves the Mac. The programs it can start are its own vocal-separation worker, the Vocal Remover that came in its folder (the installer allows that one copy, by path and checksum, and RapReaper stops using it the moment the file changes) and the ones you allowed by name yourself (ffmpeg, whisper.cpp), each only for its one job.

**What leaves your computer, and how little.** Only what any Claude chat sends to Anthropic: the words you type and the text the tools return. No audio, ever: RapReaper measures on your Mac and hands Claude only the numbers. The answers never contain the folder paths on your Mac, your macOS user name, the name of your Mac or of an iCloud account: RapReaper writes `<RapReaper folder>` and `~` instead, and Claude is told to call only the tools a request needs, to ask for no personal details and not to repeat paths or names you type.

**Names.** The names of your music files (songs, beats, takes, stems, mixes, exports, the songs in the references and imports folders) and of your saved projects are given to Claude as they are, so it can list what is in a folder and call a song by its name (your choice, 6 October 2026); never a folder or a path (from any drive, network or cloud folder, only the file name goes out: `D:\Music\…\vocal.wav`), never your account name, your computer's name or an e-mail address (they become `<you>`, `<this Mac>`, `<email>`, also inside a song or project name), and never any other kind of file. The privacy level (the **privacy** tool shows it, and Claude can raise it; lowering it, switching off the record below or shortening how long it is kept is your own step: `python3 rapreaper_install.py --privacy standard`, `--activity-log off` or `--expire-days N` in the RapReaper folder) decides the rest: at `standard` every name is plain; at `strict`, the default, a file that is not music (a Pro Tools session, a note) becomes a label such as "file #3.ptx"; at `maximum` track names become labels too ("track #2"). What a label stands for is in `kit\state\private_names.txt` on your Mac, and every tool takes the labels back. `kit\logs\sent_to_claude.log` records every answer exactly as it was sent, so you can see for yourself what left the Mac. Measurement files, vocal-session notes and the activity log older than 30 days are removed (settable).

Three things keep it smaller still:

- Name tracks, songs and the project plainly ("Lead Vox", "Song 3"); those names do go into the chat.
- Leave personal details out of what you type. Mixing never needs your real name, address or account details.
- How Anthropic keeps and uses chats is set by your Claude account's privacy settings and Anthropic's privacy policy; check them once ([support.claude.com](https://support.claude.com)).

| RapReaper can | RapReaper cannot |
| --- | --- |
| Add REAPER's own plug-ins and set their controls; split clips, cut or mute pieces of them, lower a piece's volume, set fades, heal splits, put clips back from a restore point; move clips into place or to another track; import exported tracks; draw volume dips on a plug-in of its own | Delete tracks or your files. It removes only pieces of a clip you asked it to cut, plug-ins it added itself, and effect tracks it made that hold no clips, all undoable |
| Move faders, pan, mute and solo; rename a track | Overwrite a project: it saves only with REAPER's own Save, to the project's own file, when you ask |
| Add a track and a send for reverb or delay | Change REAPER preferences, except switching off the one that blocks measuring |
| Open, close and save projects, after showing you the plan, never over unsaved changes without your word | Open any file but a project REAPER lists as recent or that lies in your own folder |
| Play, stop and move the cursor (never while REAPER is recording) | Reach the internet, or run any program other than its own separation worker and the ones you allowed by name |
| Write measurement files and exports inside the RapReaper folder, and tidy away its own old measurement files there | Write audio anywhere else |

- **Every change to your project is undoable.** Every change is a named entry, "RapReaper: ...", in REAPER's Undo History window (Cmd+Option+Z). One request to Claude makes several entries, so a single Cmd+Z takes back only the last small part. Each answer from RapReaper says which entry to click to take the whole step back; you can also just ask Claude to take it back.
- **Measuring leaves nothing changed.** To measure one step of the chain, the plug-ins after it must be off for a moment, and a very loud track needs its fader down. RapReaper sends that change, the measurement and the change back to REAPER as one command, so REAPER puts everything back by itself, even if Claude or RapReaper stops in the middle. RapReaper also keeps a note of what it changed and puts back at its next step whatever still stands the way a measurement left it.
- **You stay in charge.** Claude asks before the first use of each RapReaper tool. "Always allow" stops the question for that tool; "Allow once" keeps Claude asking every time. The tools that take something away or change what is open (a cut, a mute, removing an effect, applying a chain that switches plug-ins off, opening or closing a project) are marked as such for the Claude app, and Claude is told to do those only after you have said yes to that exact step, and never because a track name, a file name or a note looked like an instruction. Save a new version of the project before each session.
- **What it will not run or load.** RapReaper never starts a program it merely found on the Mac. Besides its own separation worker it can start only the Vocal Remover the installer allowed (after checking it against the package's manifest) and programs you allowed yourself, one at a time, with an "Allow ..." file that records the program's path and checksum; it stops using a program whose file changed. Every neural-network model file is checked against its published checksum before it is loaded, by RapReaper and by the Vocal Remover alike. The bridge script and RapReaper's own helper script are checked against their checksums every time RapReaper starts. A RapReaper zip is installed by the updater only when every file in it matches the zip's own manifest. Audio files are read within fixed limits, one song is separated at a time, and a separation that runs over an hour is stopped.
- **What the installer touched.** Outside the RapReaper folder: REAPER's startup script (two marked blocks), REAPER's settings file (one preference, with REAPER closed), Claude's settings file (two of them if both kinds of the Claude app are installed) and RapReaper's own Python environment inside its folder. A dated copy of each changed file sits beside it, and Uninstall RapReaper.command takes the changes out again. Since 1.4.7, "fix my setup" (and the self-test) also saves that one preference in REAPER's settings file while REAPER is open, so that it stays off after a restart; only that line changes, with a copy of the old file kept beside it.
- **The one third-party part.** The bridge script inside REAPER is "Reaper Daemon" (MIT licence), included unmodified; `NOTICE.txt` lists its checksums. RapReaper only sends it commands from a short allow-list, and the bridge's own switch for saving the project stays off.
- **Its limit.** The bridge takes its commands from files in the RapReaper folder. Another program already running under your macOS account could write there too, just as it could already change any of your files. Inside your own home folder, other macOS accounts on the Mac cannot. Nothing is exposed to the network. Keep one REAPER open at a time.

`SECURITY.txt` in the RapReaper folder lists what was reviewed, found and fixed, version by version.

## What was tested, and what you confirm on your Mac

RapReaper was tested end to end against a simulated REAPER, because the workspace it was built in cannot run Windows, REAPER or Claude Desktop. The self-test (set-up step 10) repeats the decisive checks on your real REAPER, in a few short steps.

| What was tested | How | Result |
| --- | --- | --- |
| The measuring code, the effect detectors, the tempo and key finders and the alignment | Test signals built to the EBU's published definitions for loudness meters, synthetic recordings with defects of known size, half a minute of real speech, sung test voices with effects of known size, drum patterns and chord loops of known tempo and key, and copies of a beat shifted by known amounts | __AUDIO__ checks passed |
| The whole process, every tool | The real RapReaper server, the real bridge script and RapReaper's helper, driving a stand-in for REAPER with models of its plug-ins, project tabs and clips; the installer against stand-in Mac and Windows profiles, including damaged settings files and an update from a newer zip; the song-effects tools with a real separation by the neural network | __E2E__ checks passed |
| Things going wrong on purpose | REAPER crashing or hanging in the middle of a measurement, a cancelled request, file rules, a command file REAPER cannot read, wrong render settings, a slow computer, a mix over full scale, a full disk, an unsaved project when another is opened | Part of the above |
| What reaches Anthropic | Live answers, errors and the log searched for the real folder and user name; the filter itself run on user names, computer names, OneDrive and iCloud names, Mac paths and every kind of Windows and Mac path an independent privacy review could find; every reply checked to pass the filter as it is sent; no answer ever carrying audio data | Part of the above |
| The Vocal Remover | Its signal-processing and model-conversion tests; the whole window driven by a headless browser (loading a song, choosing a part, a separation, the graphics controls); the two networks measured on three MUSDB18-HQ test songs against their true separate tracks (the table in "Instrumentals from a song"); the float16 graphics model checked against the full-precision network | All passed; the graphics processor itself can only be tested on your Mac (the built-in test does that) |
| The bridge script | Its author's own tests, run on the exact files in the kit | 664 of 664 checks passed |
| Security and privacy review | Three independent reviews of the code (every place that opens a file, starts a program or sends a command; what reaches Anthropic and what text from the project could read like an instruction; about 5,000 hostile tool calls with every file the server touched watched), a second pass that tried to bypass each fix, and an acceptance review that ran an ordinary mixing session; the 1.3 and 1.4 additions reviewed against the same rules | Every confirmed finding fixed and pinned by a test; `SECURITY.txt` lists them |

Only your Mac can confirm these things:

- **REAPER's real plug-ins.** The self-test adds the plug-ins of the vocal chain and the limiter, and sets their controls. A control with a different name shows up as "not set", and Claude then sets it by its real name.
- **REAPER's real renders.** With a recording in the project, the self-test checks that a track is measured on its own and at its true level, and that its fader is back where it was. How fast your REAPER renders also shows only there.
- **Claude Desktop.** The installer starts RapReaper once, the way Claude does, and checks that it answers. The RapReaper tools appearing in Claude is the final proof.
- **The sound.** The stand-in models what each plug-in does to levels, not every detail of its sound. Your ears make the final call.
- **Real songs.** The effect detectors, the key finder and the alignment were checked on test material of known size, not yet on a commercial record: the first songs you learn from, and the first instrumental you line up, are that check.

Two limits to know: one measurement covers at most 7 minutes of a project, and the vocal plan looks for the busiest part within the first 3 minutes of a track. A song longer than 7 minutes is exported in REAPER itself (File > Render); RapReaper says so when asked.

## When something goes wrong

Most problems are one of these. RapReaper's tools also say what is wrong in plain words, and **Check RapReaper.command** shows what is and is not set up without changing anything outside the RapReaper folder.

| What you see | Usual cause | Fix |
| --- | --- | --- |
| Claude has no RapReaper tools | Claude was not fully quit, or an update reset its settings | Quit Claude (Claude menu > Quit Claude, Cmd+Q) and reopen it. Still missing: run Install RapReaper.command again. |
| The installer says the tool server "did not answer properly" or took long | The first start after an install, while the maths library loads | Run Install RapReaper.command once more; the second start is quick. If it happens again, the installer has saved the exact answer in `kit/state/installer_selfcheck.json`: send that file to whoever helps you (it holds no paths or names). |
| "RapReaper cannot reach REAPER" | REAPER is closed, has just started, or a pop-up is waiting | Open REAPER, wait a minute, close any pop-up window, ask again. |
| A small REAPER window with the words BRIDGE\_ALREADY\_RUNNING | REAPER was closed and started again within a minute | Harmless: close the window. The connection comes back by itself within a minute. |
| Status says REAPER is still running the previous helper | You updated RapReaper and have not restarted REAPER since | Close REAPER completely and start it again. Everything but the newest tools works in the meantime. |
| Claude says a newer RapReaper is installed than the one running | You updated RapReaper and Claude still runs the tool server it started before | Quit Claude completely (Claude menu > Quit Claude, or Cmd+Q) and open it again. Closing the window is not enough. |
| Claude says a tool timed out or gave no answer | REAPER took longer than Claude waits for one tool (about a minute), or a pop-up held REAPER up | Say "check the status". Claude then carries on where the job stopped. On a Mac that renders slowly RapReaper measures shorter stretches from then on, and says so. |
| "REAPER started a measurement ... and has not answered since" | REAPER is still rendering, a pop-up window is holding it up, or REAPER crashed or was closed during a measurement | If REAPER is open: wait for it, or close the pop-up, then ask again. If it crashed: start REAPER and ask again. RapReaper then clears what the crash left behind. |
| Measurements come back silent | A soloed or muted track, a fader pulled all the way down, or REAPER's preference "Set media items offline when application is not active" | The error names the cause it found. For the preference, say "fix my setup", or run the self-test (both switch it off, and since 1.4.7 save that in REAPER's settings file so it stays off after REAPER restarts). |
| "The disk ... has only X GB free" | The disk that holds RapReaper is nearly full; a render into a full disk would fail half-way | Free some space. Everything in `kit\captures` can go while REAPER is closed; old exports too. |
| "Cannot measure audio yet", mentioning SWS | The SWS extension is missing | Install it through ReaPack (set-up step 3). |
| "REAPER does not have RapReaper's Ducks effect" | The small effect file RapReaper ducks with (Effects/RapReaper/ducks.jsfx in REAPER's settings folder) is not there yet, or REAPER has not been restarted since it was put there | Run Install (Update does it for you), then close and start REAPER again. |
| "REAPER renders too slowly for measuring" | The render speed is set to an online (real-time) mode; more rarely, heavy plug-ins slow the render | File > Render, set Speed to Full-speed Offline, and close the dialog with Save Settings. |
| "This needs ... of audio rendered in one go, and on this Mac REAPER needs ..." | The project renders slower than usual (dozens of pitch plug-ins, say) but faster than real time, so each measurement or export has to be shorter | Ask again (RapReaper measures shorter stretches by itself), export a shorter part, or use File > Render for the whole song. Freezing the heaviest tracks (Track > Render/freeze tracks) makes every render faster. |
| An error that names File > Render (normalizing, Mono, or "Add rendered items to new tracks") | The project's own render settings apply to every measurement and would falsify it | Change the setting the message names in File > Render, close the dialog with Save Settings, ask again. |
| "A measurement did not follow the track's fader" | RapReaper has not yet checked how your REAPER renders a single track, or the render settings change levels | Say "run the self-test" with the recording in the project. |
| A setting is listed as "not set" | A control has a different name in your REAPER version | Ask Claude to look up the real control names and set it. This is routine. |
| Claude says the plan must be run again | The clips on the track changed, a saved chain was applied, or the measurement files were deleted | Say "plan the vocal again", then repeat your request. |
| "The open project has unsaved changes" when opening or closing a project | RapReaper never drops unsaved work on its own | Say whether to save first, drop the changes, or open the other project in a new tab. |
| fit\_instrumental says the match is not clear | The two tracks do not hold the same music, or the overlap is short | Name a reference that really holds the same music (the old beat, or a vocal recorded over it), or move by a time instead. |
| remaster finds only a project file | REAPER cannot open another program's project | Export the tracks as audio files as the answer describes, copy them into `kit\imports\<song>`, ask again. |
| separate\_song or learn\_song\_effects says there is no way to separate | The model is not there yet, or the Vocal Remover is not allowed | Double-click Get Vocal Remover Model.command (it downloads and builds the model), or put the eight `MelRoformer-KimFT2-int8.onnx.part...` files in Downloads and run Install RapReaper.command again; it joins them and allows the program. |
| The graphics test says "not enough memory" or "the parameter is incorrect" | The graphics chip or its driver cannot run the network | Nothing to fix: the Vocal Remover stays on the processor. A newer graphics driver sometimes helps; try the test again afterwards. |
| The Vocal Remover's window does not open | The browser did not start | Copy the address shown in its black window (it starts with http://127.0.0.1) into any browser. |
| "Set the final loudness" stops short of the target | Reaching it would take more than about 6 dB of limiting, which flattens a song | Usually leave it: streaming services turn loud songs down anyway. For a louder master, make the mix denser first ("heavier compression on the vocal"), or tell Claude to push harder regardless. |
| "cannot be opened because it is from an unidentified developer", or a `.command` file does nothing | macOS's download quarantine | System Settings > Privacy & Security, scroll down, Open Anyway; then double-click again. After the installer has run once, it releases every RapReaper file it shipped. |
| The installer refuses the folder | The RapReaper folder is in Downloads, Desktop, Documents or a cloud folder | Move it to your home folder (`/Users/<your name>/RapReaper`) and run the installer again. |
| REAPER or Claude cannot see a file | macOS has not given that program permission for a folder | Keep everything inside the RapReaper folder in your home folder; System Settings > Privacy & Security > Files and Folders shows what each program may read. |
| The voice is in one ear only | The mic was recorded on a stereo input | Click the clip, press F2 and set Channel mode to Mono (left) or Mono (right), whichever side has the voice. Record new takes on a Mono input. |
| No sound, or you cannot record | REAPER is not using your interface | Options > Preferences > Audio > Device ("AUDIO INTERFACE SETUP.txt"). |
| The installer says Python is missing or unsuitable, or a `.command` file says "no Python 3.12" | Only Apple's stub `python3` is present, or Python is older than 3.12 | Install Python from python.org (set-up step 4) or with Homebrew, then double-click the installer again. |
| The vocal sounds worse | Too much of something, or simply taste | "Switch the chain off and on", then "lighter compression" or "less de-essing", or "take the last step back". |

For deeper digging, Claude keeps a log for RapReaper called `mcp-server-rapreaper.log`. In Claude, Help > Troubleshooting > Show Logs opens its folder: `~/Library/Logs/Claude`. RapReaper's own record of what it sent is `kit/logs/sent_to_claude.log`.

## Keeping it working

RapReaper itself uses nothing online (Claude does), so it keeps working until something around it changes. When that happens, run the installer and the self-test again.

| When | Do this |
| --- | --- |
| A new RapReaper zip | Put it in Downloads, double-click Update RapReaper.command, then restart REAPER. |
| REAPER updates | Ask Claude to run the self-test, with a recording in the project. Control names and the way REAPER renders can change between versions, and the test catches both. |
| Claude Desktop updates | If the RapReaper tools vanish, run Install RapReaper.command again. If Claude asks permission again, choose Always allow. |
| macOS updates | Usually nothing. If a `.command` file is refused again, allow it once more in System Settings > Privacy & Security. |
| You install a new Python | Run Install RapReaper.command again; it rebuilds RapReaper's own environment with it. |
| You move the RapReaper folder | Run Install RapReaper.command again from the new place (keep it in your home folder). |
| REAPER's 60-day trial ends | Buy a licence: $60 if you use REAPER personally or earn under $20,000 a year with it. |
| Before each session | File > Save new version of project. |
| Every month or so | Copy your REAPER project folders, `kit/chains/user` and `kit/state/songs` from the RapReaper folder to a USB drive or a cloud folder. |
| You want RapReaper gone | Copy `kit/chains/user` and `kit/state/songs` first if you want to keep them. Run Uninstall RapReaper.command, then delete the folder. Python itself stays; RapReaper's packages live inside the folder and go with it. |

Housekeeping is automatic. `kit/captures` holds the measurement files: RapReaper keeps the newest ones, about 1.5 GB at most, plus those your vocal sessions of the last 45 days still need for their before/after tables, and removes measurement files, vocal-session notes and the activity log older than 30 days. You may empty `kit/captures` at any time; the next vocal step then asks for a fresh plan. Exports and imports are yours to move or delete.

For comfort, create a Project called "Mixing" in Claude and start your sessions there, so the chats stay together.

## Why REAPER instead of Pro Tools

Pro Tools cannot be driven this way. Its scripting interface (Avid's Scripting SDK) can open and save sessions, import and export audio, run the transport and handle markers and clips. It has no command to insert a plug-in, set a plug-in's controls or move a fader, so Claude could not turn an EQ or a compressor there. REAPER's scripting can add any plug-in, read and set every control, and render a track to a file for measuring, which is exactly what RapReaper needs.

You can keep recording or producing in Pro Tools, FL Studio or any other program and mix in REAPER: the section "Remastering a song from another program" says how to export the tracks and bring them in with one request.

## Sources

The facts that can be checked online were checked against these pages between 2 and 6 October 2026. A few menu names inside REAPER, Claude Desktop and the other programs appear on no public page that could be opened; they are given from general knowledge of those programs and may differ slightly in your version.

- REAPER: [download](https://www.reaper.fm/download.php), [licence terms and prices](https://www.reaper.fm/purchase.php), [version history](https://www.reaper.fm/whatsnew.txt), [User Guide](https://www.reaper.fm/userguide.php)
- SWS extension: [its source and releases on GitHub](https://github.com/reaper-oss/sws), installed through [ReaPack](https://reapack.com/) ([user guide](https://reapack.com/user-guide))
- Python: [macOS downloads](https://www.python.org/downloads/macos/), [using Python on a Mac](https://docs.python.org/3/using/mac.html), [NumPy on PyPI](https://pypi.org/project/numpy/), [SciPy on PyPI](https://pypi.org/project/scipy/)
- Claude Desktop: [download](https://claude.com/download), [connecting local tool servers](https://modelcontextprotocol.io/docs/develop/connect-local-servers) (settings and log locations), [plans](https://claude.com/pricing)
- Loudness: [Spotify, loudness normalization](https://support.spotify.com/us/artists/article/loudness-normalization/), [ITU-R BS.1770](https://www.itu.int/rec/R-REC-BS.1770), [EBU R 128](https://tech.ebu.ch/publications/r128)
- Mixing practice (Sound On Sound): [vocal production](https://www.soundonsound.com/techniques/vocal-production), [de-essing](https://www.soundonsound.com/techniques/techniques-vocal-de-essing), [multi-stage compression](https://www.soundonsound.com/techniques/multi-stage-compression), [gates and expanders](https://www.soundonsound.com/techniques/using-your-daws-gates-expanders)
- Speech spectrum used as the "natural speech" yardstick: Byrne et al., J. Acoust. Soc. Am. 96(4), 1994
- Tempo and key: Ellis, "Beat tracking by dynamic programming", J. New Music Research 36(1), 2007; Krumhansl & Kessler, Psychological Review 89(4), 1982
- The bridge script: [Reaper Daemon on GitHub](https://github.com/wretcher207/reaper-daemon)
- Vocal separation: [Ultimate Vocal Remover](https://github.com/Anjok07/ultimatevocalremovergui) and its [public models](https://github.com/TRvlvr/model_repo/releases/tag/all_public_uvr_models); the Mel-Band Roformer by [Kimberley Jensen](https://huggingface.co/KimberleyJSN/melbandroformer) (MIT) and its [FT2 fine-tune by unwa](https://huggingface.co/pcunwa/Kim-Mel-Band-Roformer-FT); Lu, Wang et al., "Music Source Separation with Band-Split RoPE Transformer" (2023) and "Mel-Band RoFormer" (2023); the open implementations by [lucidrains](https://github.com/lucidrains/BS-RoFormer) and [ZFTurbo](https://github.com/ZFTurbo/Music-Source-Separation-Training); [ONNX Runtime](https://onnxruntime.ai/) and its [DirectML](https://onnxruntime.ai/docs/execution-providers/DirectML-ExecutionProvider.html) and [CoreML](https://onnxruntime.ai/docs/execution-providers/CoreML-ExecutionProvider.html) providers; the test songs are from [MUSDB18-HQ](https://sigsep.github.io/datasets/musdb.html)
- Pro Tools: [Scripting SDK](https://www.avid.com/resource-center/pro-tools-scripting-sdk)
