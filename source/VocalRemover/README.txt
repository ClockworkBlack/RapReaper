VOCAL REMOVER 2.0.3
===================

Splits a song into two WAV files: the instrumental (music without the voice)
and the vocals (voice without the music). Everything happens on this computer.

It comes inside the RapReaper folder (RapReaper\VocalRemover on Windows,
RapReaper/VocalRemover on a Mac). RapReaper's installer sets it up, and
RapReaper itself uses it when Claude is asked for an instrumental
(separate_song) or to learn a song's vocal effects (learn_song_effects).
You can also use it on its own, from its window.

New in 2.0.3 (9 November 2026)
   - Runs on Intel Macs, from RapReaper's -Mac-Intel.zip, with ONNX Runtime
     1.23.2 (the last version Microsoft built for Intel Macs; macOS 13 or
     newer). The program checks that library by its own checksum, as it does
     every other. Nothing else changed; Windows and Apple-chip Macs as before.
   - The model can be built from its authors' public files: RapReaper's
     "Get Vocal Remover Model" downloads them and its installer builds
     MelRoformer-KimFT2-int8.onnx, the same file byte for byte.

New in 2.0.2 (9 November 2026)
   - Built with Go 1.27.3 instead of Go 1.24.7, for the security fixes Go
     has made since (in the parts that serve this program's window). The
     program's own code is the same as in 2.0.1.
   - On a Mac it needs macOS 14 or newer: the ONNX Runtime library it uses
     is built for macOS 14 (this README said macOS 13 before; that was wrong).

New in 2.0.1 (7 October 2026, after an independent audit)
   - ONNX Runtime's usage reporting, built into its Mac library, is now
     switched off before the library loads (it used to reach the internet
     despite the off switch); checked: no connection at all.
   - Windows system libraries are always taken from Windows itself, never
     from this folder; an unexpected DLL in the folder stops the program.
   - Hostile FLAC/Ogg headers can no longer reserve gigabytes of memory; the
     command line refuses files over 1 GB; a damaged optional model no longer
     stops the program; a graphics failure stays switched off.

New in 2.0.0
   - A new default network, the Mel-Band Roformer "Kim FT2": measured as
     accurate as version 1.3's BS-Roformer (within 0.3 dB on the test songs,
     see below) in about a third of the processing time on the processor.
   - The graphics processor can do the work: Windows through DirectML, Macs
     through CoreML. A built-in test tries it on one window, compares the
     result with the processor's, and switches it on only if it works and is
     faster. If the graphics processor ever fails in the middle of a song,
     the program finishes on the processor, tells you, and leaves the
     graphics processor off until you tick it again.
   - FLAC and Ogg Vorbis files open, next to MP3 and WAV.
   - Mac version (Apple silicon; Intel Macs since 2.0.3), the same program and models.
   - "Two networks" mode: the Mel-Roformer and the BS-Roformer averaged, the
     most accurate result and the slowest.
   - Modes whose model file is missing are greyed out instead of stopping the
     program; the window says which pieces are missing. Incomplete or damaged
     pieces of an optional model (anything but the Mel-Roformer) do not stop
     the program either: it says so when it starts and greys out the modes
     that need that model.
   - One settings file (settings.json, next to the program) remembers the
     graphics-processor choice and the last test's verdict. It is the only
     file the program writes besides your results.


START
-----

Windows: double-click VocalRemover.exe in the VocalRemover folder. The first
time, Windows may show a blue box "Windows protected your PC" because the
program is not signed by a company: click "More info", then "Run anyway".

Mac: double-click "Vocal Remover.command" in the VocalRemover folder. The
first time, macOS says it cannot check the file: open System Settings >
Privacy & Security, scroll down and click "Open Anyway" (RapReaper's
installer does this step for you when it has run first). The program runs
on Apple silicon Macs (M1 and later) and, from the -Mac-Intel package, on
Intel Macs.

A black (Windows) or Terminal (Mac) window appears and stays open; that is
the program itself. Its working window opens in your browser. The page is
served from this computer, not from the internet.

The models: the program needs its model files in the "models" folder. They
are delivered as pieces (their names contain ".onnx.part") because of a
download size limit. RapReaper's installer joins them from your Downloads
folder; the program does the same on its first start if it finds them in
Downloads (Windows), next to itself or in the folder it was extracted into.
The Mel-Roformer file (MelRoformer-KimFT2-int8.onnx, 8 pieces) is the one
the default mode needs. The BS-Roformer (BS-Roformer-1297-int8.onnx, 6
pieces, already on the computer if version 1.3 was installed) adds the "Two
networks" and "BS-Roformer" modes; the two MDX-Net files add the "Quick"
modes. Only the Mel-Roformer file is required: if the pieces of one of the
others are incomplete or damaged, the program starts anyway, says which
pieces to download again, and greys out the modes that need that model.

To quit, close the working window; the black window closes by itself a few
seconds later. You can also just close the black window.


USE
---

1. Drop a song onto the window, or click "Choose a song". MP3, WAV, FLAC and
   Ogg Vorbis files open (M4A/AAC does not: RapReaper's export_mix, or a
   free program such as Audacity, can turn one into a WAV first).
2. To process only part of the song, drag across the waveform, drag the two
   markers, or type times into "From" and "To" (minutes:seconds, for example
   1:07.5). "Play selection" lets you listen to the chosen part first.
   "Whole song" selects everything again.
3. Choose the mode and the file type.
4. Click "Remove vocals".

When it is finished you get two files in the "Output" folder, which is
inside the VocalRemover folder, right next to the program:

   <song name> (Instrumental).wav
   <song name> (Vocals).wav

If you processed only a part, the times are added to the names, for example
"Song [0m30s-1m15s] (Instrumental).wav". Existing files are never
overwritten; a number is added instead.

To save somewhere else for one run, type or paste another folder into
"Save into" (or use "Browse"). The next time the program starts, it goes
back to its own Output folder.

You can listen to both results in the window, and "Open the folder" shows
them in Explorer or the Finder.

Mode:
   Mel-Roformer (recommended)
                    the Mel-Band Roformer network, one-second crossfades
                    between its 8-second windows. The best balance of
                    accuracy and time.
   Mel-Roformer, thorough
                    the same network with every moment processed twice and
                    blended (the reference behaviour). About 1.7x the time;
                    measured the same within 0.1 dB.
   Mel-Roformer + extra suppression
                    as above, then everything the network still attributes
                    to the voice is turned down further. Removes a little
                    more of the voice's remains, and with it a little of the
                    music that sits directly under the voice.
   Two networks (Mel + BS-Roformer)
                    both Roformer networks, their estimates averaged: the
                    most accurate result, about three times the time of
                    Mel-Roformer. Needs the BS-Roformer model file.
   BS-Roformer      version 1.3's "Ultimate Fast": the BS-Roformer alone,
                    a little over twice the time of Mel-Roformer. "Thorough"
                    and "+ extra suppression" variants as in 1.3.
   Quick modes      the two MDX-Net networks of version 1.1 (maximum
                    removal, best, standard): a few minutes per song, less
                    accurate. Need their model files.

File type:
   WAV 24-bit        good default for further work in any audio program
   WAV 16-bit        smaller, CD quality
   WAV 32-bit float  keeps the exact level even where a track peaks above
                     full scale; best for further mixing

The results keep the sample rate of the song you put in (44.1 kHz, 48 kHz,
...), and a mono song gives mono results.

The two files add up to the original: instrumental + vocals = the song,
within the rounding of the file type you chose. Nothing is thrown away, it
is only divided between the two files. With 32-bit float output the sum is
exact apart from the tiny rounding of floating-point numbers; 24-bit and
16-bit files round each sample to their own step size. With 16-bit and 24-bit
output, loud songs can make one of the two files peak above full scale; in
that case both files are lowered by the same small amount (the same gain for
both, so they still add up to the song at that lower level) and the window
tells you by how much.


THE GRAPHICS PROCESSOR
----------------------

Tick "Use the graphics processor" in the window after running "Test the
graphics processor" once (RapReaper's installer offers the same test at the
end of an install). The test runs one 8-second window through the
Mel-Roformer on the processor, then on the graphics processor (Windows:
DirectML, first with the whole network compiled as one piece, then one
operation at a time, which needs less memory; Mac: CoreML), compares the two
results and times them. Each side runs the window twice and only the second
run is timed, so neither time includes warming up. The report states how
much the graphics result differs from the processor's (up to 10 % counts as
agreeing, up to 25 % as roughly agreeing, more as not usable). It switches
the graphics processor on only when it works and is faster, writes its
report to Output\GPU test.txt, and remembers the verdict in settings.json.
The command line does the same: VocalRemover --gpu-test.

What to expect: a laptop graphics chip that shares the computer's memory
may run out of it on the "whole network" attempt and work "one operation at
a time", or not at all; a dedicated graphics card is usually several times
faster than the processor. If the graphics processor fails in the middle of
a song, the song is finished on the processor and the result says so; the
graphics processor is then switched off, also in settings.json, so it stays
off after a restart until you tick it again (or run the test again).
Seen so far: on a Ryzen 7 3700U laptop (Radeon RX Vega 10 graphics, a 2022
driver) version 1.3's test found that DirectML refuses the 8-bit model
outright ("the parameter is incorrect": that is why the float16 copy
exists) and that the whole network compiled as one piece ran out of memory;
the "one operation at a time" attempt was added for exactly that chip and
has not been tried on it yet. Run the test; the report says what happened.

The graphics-processor model is a float16 copy of the processor model that
the program makes itself, once, from the processor file (about half a
minute, 460 MB of disk, in the models folder). Nothing is downloaded. Its
checksum is known in advance and checked like every other model file.


WHAT TO EXPECT FROM THE RESULT
------------------------------

Please read this before relying on the instrumental.

No program can remove a voice from a finished mix perfectly, because the
voice and the music are stored as one signal. This program uses open models
from the Ultimate Vocal Remover community and was measured on songs for
which the true separate tracks are known (three test tracks of the MUSDB18-HQ
collection, 60 seconds each around the loudest singing: a rap track, an
electronic track and a rock track). Two measures: how closely the
instrumental matches the true instrumental (its accuracy, in dB; higher is
better), and the same for the vocal.

   mode                                  rap track        electronic       rock             time
                                         instr. / vocal   instr. / vocal   instr. / vocal   (60 s of song)
   Mel-Roformer (recommended)            23.5 / 18.5      18.4 / 12.8      15.4 / 13.5      81 s
   Two networks (Mel + BS)               24.3 / 19.3      18.7 / 13.1      15.9 / 14.0      262 s
   BS-Roformer (version 1.3's default)   23.8 / 18.8      18.4 / 12.8      15.8 / 13.8      181 s

   (instrumental / vocal accuracy in dB; the test songs: Little Chicago's Finest - My Own,
   BKS - Bulldozer, Angels In Amplifiers - I'm Alright, from MUSDB18-HQ)

   (processor times: a 2-core test system, for 60 seconds of song)

In short: the Mel-Roformer gives an instrumental within about 0.3 dB of the
BS-Roformer's in well under half the time, and the two networks together
are the most accurate. "Extra suppression" trades accuracy for fewer
remains of the voice; use it on a song where a ghost of the voice still
bothers you in a gap, and listen to both.

   - what remains is mostly traces of "s" and "t" sounds and of reverb or
     delay tails of the old voice;
   - heavily processed vocals (ad-libs, layered doubles, vocal chops used
     as an instrument, heavy auto-tune effects, long echoes) are the
     hardest case and may leave more behind.

In REAPER: ask Claude "make an instrumental from <song> and put it in
REAPER" (separate_song with import_to_reaper), or drag the "(Instrumental)
.wav" file onto a track yourself, add a new track for your voice and record.
The file keeps the sample rate of the original song, so set the project to
that rate (File, Project settings) to avoid resampling. Your new vocal will
cover most of what is left of the old one; where the old voice is still
audible in a gap, a short volume dip on the instrumental at that spot hides
it (RapReaper's duck_section does this).

So: expect an instrumental you can record over on most material, not a
guaranteed 100 % removal on every song. Listen to the result before you
build on it. MP3 input works the same as WAV input, but an instrumental can
never be better than the file it was made from; use the best source you
have.


PRIVACY AND SECURITY
--------------------

- The program never connects to the internet. It contains no update check,
  no licence check, no statistics and no advertising. You can pull the
  network cable and it works the same.
- The working window is a page that the program serves to your browser on
  the address 127.0.0.1, which exists only inside your computer. Other
  computers on your network cannot reach it. Each time the program starts
  it makes a new random key. The window's own three page files (the page,
  app.js and style.css) are served without the key, but they are the same
  for everyone and contain nothing private; every action (opening a song,
  processing, the results, the settings) needs the key, so other web pages
  open in your browser cannot use the program.
- Your songs are read into memory, processed, and written only to the
  Output folder (or a folder you choose). Besides that the program writes
  one file, settings.json next to itself (the graphics-processor choice and
  the last test's verdict), and the graphics model file in its models
  folder when you first use the graphics processor. Nothing in the
  registry, nothing elsewhere.
- Before loading them, the program checks the library files (ONNX Runtime,
  DirectML, the Microsoft C++ runtime) against known checksums and refuses
  to start if one has been changed. On Windows it also refuses to start
  while a library file (.dll) that is not one of its own sits in its folder
  or in the gpu folder, because Windows could load such a file into the
  program, and it loads the Windows libraries that ONNX Runtime and DirectML
  need from the Windows system folder only. The model files are checked
  against their checksums each time a job loads them (not at start-up): a
  changed or damaged model file is refused and the job says which file to
  restore.
- The Microsoft library that runs the models (ONNX Runtime) has a built-in
  usage reporting: on Windows it reports to Windows' own diagnostics, and
  the Mac and Linux versions would send usage events to Microsoft over the
  internet and keep a device ID in a hidden folder. The program switches it
  off in two ways: it sets the ORT_DISABLE_TELEMETRY setting before the
  library is loaded (on a Mac, "Vocal Remover.command" sets it as well), and
  it calls the library's own "disable telemetry" function right after
  loading it. Checked on the Linux test system: no network connection is
  made and no such files are written.
- Antivirus programs sometimes distrust small unsigned programs. If yours
  complains, that is a judgment about "unknown publisher", not about
  anything the program does. The complete source code is in the "source"
  folder, and RapReaper's MANIFEST.sha256 lists the checksum of every file
  in the package.

Honest note: the program is built and tested on a Linux test system (same
program code, same models, same ONNX Runtime version), where the whole flow
including the window was exercised, and the Windows version of 1.3 ran on a
real Windows 10 PC. The parts that exist only on Windows or only on a Mac
(opening the window, the folder chooser, "Open the folder", keeping the
computer awake, loading the libraries, the graphics processor itself) were
reviewed line by line but could not be run here. The graphics-processor
test exists for exactly that reason: it tells you, on your computer, what
works. If something does not work, see "If something goes wrong" below.


REQUIREMENTS AND SPEED
----------------------

- Windows 10 or 11, 64-bit; a Mac with Apple silicon (macOS 14 or newer);
  or an Intel Mac (macOS 13 or newer, the -Mac-Intel package; on the
  processor it is about as fast as a Windows PC of the same age).
- Memory: the Roformer modes about 2.3 GB, the Quick modes about 1.6 GB,
  plus roughly 0.15 GB per minute of song while processing. 8 GB of RAM is
  comfortable; 4 GB is enough for normal songs if not much else is open.
- Songs up to 20 minutes and files up to 1 GB. Cut longer recordings into
  parts first.
- Speed on the processor: on the two-core test system (a server processor
  with the 8-bit vector instructions) a 3-minute song takes about 4 minutes
  in Mel-Roformer, about 9 minutes in BS-Roformer and about 13 minutes with
  both networks. A laptop processor without those instructions is much
  slower: on a Ryzen 7 3700U one 8-second window of the BS-Roformer took
  131-189 s against about 20 s on the test system, so count on roughly half
  an hour for a 3-minute song in Mel-Roformer and over an hour in
  BS-Roformer there. This is where the graphics processor can help most.
  The window shows the progress and the remaining time, and you can cancel.
  Processing only the part you need (From / To) saves time in proportion,
  and long jobs can simply be left to run.
- The computer is kept awake while a song is being processed.
- About 230 MB of disk space for the program and its runtime, plus the
  model files (240 MB for the Mel-Roformer, 170 MB for the BS-Roformer, 460
  MB for the graphics copy when it is made), plus the results.


IF SOMETHING GOES WRONG
-----------------------

"The program is not allowed to save into its own folder."
   The VocalRemover folder is in a protected place such as Program Files.
   Keep the whole RapReaper folder where the RapReaper installer puts it
   (your user folder).

The window does not open.
   Look at the black window: it shows an address that starts with
   http://127.0.0.1. Copy it into the address bar of any browser.

The black window shows an error and waits for Enter.
   It says what is missing. Usually a model piece was not downloaded, the
   ZIP was not fully extracted, or a file was removed (often by an
   antivirus program). Download the missing piece again, or extract the
   ZIP again.

"... is not one of Vocal Remover's files" (Windows).
   A .dll file that the program did not bring with it is in the VocalRemover
   folder or its gpu folder. Delete it, or extract the ZIP into a new, empty
   folder.

"This does not look like an MP3, WAV, FLAC or Ogg Vorbis file."
   Only those four kinds open. Convert M4A/AAC or AIFF to WAV first (with
   RapReaper's export_mix if the song is in REAPER, or Audacity).

"Browse" or "Open the folder" does nothing.
   Type or paste the folder path into "Save into" instead, and open the
   folder yourself; the full paths of both files are shown in the window.

The program disappears in the middle of a long song.
   The computer most likely ran out of memory. Close other programs, or
   process the song in two halves using "From" and "To", or switch the
   graphics processor off.

The graphics test says "not enough memory" or "the parameter is incorrect".
   The graphics processor (or its driver) cannot run the network. The
   program keeps using the processor; nothing else changes. A newer
   graphics driver sometimes helps.

A WAV file with more than two channels (surround).
   Only the first two channels (front left and right) are used.


WITHOUT THE WINDOW (OPTIONAL)
-----------------------------

The program can also be used from a command prompt (this is how RapReaper
uses it), for example to process several songs from a batch file:

   VocalRemover.exe --in "C:\Music\song.mp3"
   VocalRemover.exe --in song.flac --start 0:30 --end 1:15 --out D:\Stems
   VocalRemover.exe --in song.mp3 --quality ensemble --format 32f --gpu on
   VocalRemover.exe --gpu-test

Options: --in (file), --out (folder, default: the Output folder), --start
and --end (times), --quality mel (default) | melfull | melmax | ensemble |
ultimatefast | ultimate | ultimatemax | max | best | standard, --format
16 | 24 | 32f, --gpu on | off | fused | unfused (default: what the test
decided), --threads N (limit processor use), --gpu-test, --make-gpu-model.
"VocalRemover.exe --help" lists them. On a Mac the program is called
VocalRemover (no extension) and runs from Terminal the same way.


WHAT IS IN THIS FOLDER
----------------------

VocalRemover.exe / VocalRemover   the program (source code in "source")
Vocal Remover.command             (Mac) double-click to start it
Output\                           your results are saved here
settings.json                     the graphics-processor choice (made when needed)
onnxruntime.dll / libonnxruntime.dylib
                                  Microsoft ONNX Runtime 1.30.0, the engine
                                  that runs the models (unchanged official
                                  file from github.com/microsoft/onnxruntime)
gpu\onnxruntime.dll, gpu\DirectML.dll   (Windows) ONNX Runtime 1.24.4 built
                                  for DirectML and DirectML 1.15.4, from
                                  Microsoft's packages, with a second copy of
                                  the C++ runtime beside them; used only when
                                  the graphics processor is on
msvcp140.dll, msvcp140_1.dll, vcruntime140.dll, vcruntime140_1.dll
                                  (Windows) Microsoft Visual C++ runtime
                                  14.40, unchanged signed files. If you
                                  prefer, delete these four files and
                                  install Microsoft's "Visual C++
                                  Redistributable" (2022, x64) instead.
models\                           the neural network models, joined from the
                                  downloaded pieces: MelRoformer-KimFT2-int8
                                  .onnx (and MelRoformer-KimFT2-fp16.onnx,
                                  made here for the graphics processor),
                                  BS-Roformer-1297-int8.onnx, UVR-MDX-NET-
                                  Inst_HQ_4.onnx, Kim_Vocal_2.onnx
licenses\                         licence texts and credits
source\                           complete source code and build
                                  instructions, including the tools that
                                  build the model files from the public
                                  checkpoints and the measurement scripts

You can check a Microsoft file yourself: right-click it, Properties,
"Digital Signatures".


CREDITS
-------

The separation models come from the Ultimate Vocal Remover community:
"Kim Mel-Band Roformer FT2" by unwa, a fine-tune of Kimberley Jensen's
Mel-Band Roformer (MIT licence); "BS-Roformer-Viperx-1297" by viperx;
"UVR-MDX-NET Inst HQ 4" by the UVR team (Anjok07 and aufr33); "Kim Vocal 2"
by KimberleyJSN. The Band-Split and Mel-Band RoPE Transformer architectures
are by Lu, Wang et al. (ByteDance, 2023); the open implementations by Phil
Wang (lucidrains) and Roman Solovyev (ZFTurbo). Without their work this
program would not exist. https://github.com/Anjok07/ultimatevocalremovergui

The processing method follows UVR and the open-source "audio-separator"
project by Nomad Karaoke. The Roformer networks were re-implemented for
this program from the published architecture and run the unchanged
published weights (converted to 8-bit integers for the processor and 16-bit
floats for the graphics processor; the conversions were measured against
the full-precision network). MP3 decoding uses go-mp3 by Hajime Hoshi; FLAC
decoding uses mewkiz/flac; Ogg Vorbis decoding uses jfreymuth/oggvorbis;
library loading uses purego by the Ebitengine authors. See the "licenses"
folder for the licence of each component.
