// Vocal Remover: splits a song into an instrumental track and a vocal track, entirely on
// this computer.
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"time"

	"vocalremover/internal/audio"
	"vocalremover/internal/separate"
)

const version = "2.0.3"

func main() {
	var (
		in      = flag.String("in", "", "song to process (MP3, WAV, FLAC or Ogg Vorbis); without it the program opens its window")
		out     = flag.String("out", "", "folder for the results (default: the Output folder next to the program)")
		start   = flag.String("start", "", "start of the part to process, e.g. 0:30 (default: beginning)")
		end     = flag.String("end", "", "end of the part to process, e.g. 1:15 (default: end of song)")
		quality = flag.String("quality", "mel", "mel (default), melfull, melmax, ensemble, ultimatefast, ultimate, ultimatemax, max, best or standard")
		gpu     = flag.String("gpu", "", "use the graphics processor: 'on' (the mode the last test found), 'fused', 'unfused' or 'off' (default: what settings.json says)")
		gpuTest = flag.Bool("gpu-test", false, "test the graphics processor against the processor on one window, write Output/GPU test.txt and settings.json, then exit")
		makeGPU = flag.Bool("make-gpu-model", false, "prepare the graphics-processor model file from the processor model, then exit")
		format  = flag.String("format", "24", "output WAV format: 16, 24 or 32f")
		threads = flag.Int("threads", 0, "processor threads to use (0 = all cores)")
		lib     = flag.String("lib", "", "path of the ONNX Runtime library (default: next to the program)")
		models  = flag.String("models", "", "folder with the model files (default: models folder next to the program)")
		noOpen  = flag.Bool("no-browser", false, "do not open the window automatically; just print its address")
		pick    = flag.String("pick-folder", "", "internal: show a folder chooser starting at this folder and print the choice")
		showVer = flag.Bool("version", false, "print the version and exit")
	)
	flag.Usage = func() {
		fmt.Fprintf(os.Stderr, "Vocal Remover %s\n\nDouble-click the program to open its window, or run it from a command prompt:\n\n", version)
		fmt.Fprintf(os.Stderr, "  VocalRemover --in song.mp3 [--start 0:30 --end 1:15] [--out folder] [--quality mel|ensemble|...] [--format 16|24|32f] [--gpu on]\n\n")
		flag.PrintDefaults()
	}
	flag.Parse()
	if *showVer {
		fmt.Println("Vocal Remover", version)
		return
	}
	if flag.Lookup("pick-folder").Value.String() != "" || isFlagSet("pick-folder") {
		os.Exit(runFolderPicker(*pick))
	}
	p, err := locate(*lib, *models)
	if err != nil {
		fatal(err)
	}
	appOutputDir = filepath.Join(p.appDir, outputDirName)
	if *makeGPU {
		if err := ensureGPUModel(p, func(msg string) { fmt.Println(msg) }); err != nil {
			fmt.Fprintln(os.Stderr, "Error:", err)
			os.Exit(1)
		}
		fmt.Println("The graphics-processor model file is in place.")
		return
	}
	if *gpuTest {
		if err := runGPUTestCLI(p, *threads); err != nil {
			fmt.Fprintln(os.Stderr, "Error:", err)
			if *in == "" {
				fmt.Fprintln(os.Stderr, "\nPress Enter to close this window.")
				fmt.Scanln()
			}
			os.Exit(1)
		}
		return
	}
	if *in != "" {
		if err := runCLI(p, *in, *out, *start, *end, *quality, *format, *threads, *gpu); err != nil {
			fmt.Fprintln(os.Stderr, "Error:", err)
			os.Exit(1)
		}
		return
	}
	if err := runServer(p, *threads, !*noOpen, *gpu); err != nil {
		fatal(err)
	}
}

// gpuChoice turns the --gpu flag into a mode: "" (off), "fused" or "unfused".
func gpuChoice(p paths, flagValue string) string {
	switch strings.ToLower(strings.TrimSpace(flagValue)) {
	case "", "auto", "settings":
		return loadSettings(p).GPU
	case "on", "yes", "true", "1":
		if m := loadSettings(p).GPU; m != "" {
			return m
		}
		return "fused"
	}
	return gpuMode(p, strings.ToLower(strings.TrimSpace(flagValue)))
}

func runGPUTestCLI(p paths, threads int) error {
	engine, err := newEngine(p, threads)
	if err != nil {
		return err
	}
	defer engine.Close()
	res, err := runGPUTest(p, engine, func(msg string) { fmt.Println("  " + msg) })
	if err != nil {
		return err
	}
	fmt.Println()
	for _, line := range res.Lines {
		fmt.Println(line)
	}
	fmt.Println()
	fmt.Println("Written to", filepath.Join(defaultOutputDir(), "GPU test.txt"))
	return nil
}

func isFlagSet(name string) bool {
	set := false
	flag.Visit(func(f *flag.Flag) {
		if f.Name == name {
			set = true
		}
	})
	return set
}

// fatal reports a start-up problem and keeps the console window open long enough to read it.
func fatal(err error) {
	fmt.Fprintln(os.Stderr, "\nVocal Remover could not start:\n\n  ", err)
	fmt.Fprintln(os.Stderr, "\nPress Enter to close this window.")
	fmt.Scanln()
	os.Exit(1)
}

func runCLI(p paths, in, out, start, end, quality, format string, threads int, gpuFlag string) error {
	q, err := separate.ParseQuality(quality)
	if err != nil {
		return err
	}
	wavFormat, err := parseFormat(format)
	if err != nil {
		return err
	}
	opt := separate.Options{Quality: q}
	if start != "" {
		if opt.Start, err = parseTime(start); err != nil {
			return err
		}
	}
	if end != "" {
		if opt.End, err = parseTime(end); err != nil {
			return err
		}
	}
	if out == "" {
		out = defaultOutputDir()
	}
	if abs, err := filepath.Abs(out); err == nil {
		out = abs
	}
	if err := checkWritable(out); err != nil {
		return err
	}
	data, err := readSong(in)
	if err != nil {
		return err
	}
	track, err := audio.Decode(data)
	if err != nil {
		return err
	}
	data = nil
	fmt.Printf("Loaded %s: %s %s, %d Hz, %d channel(s), %s\n", filepath.Base(in), track.Format, track.Detail, track.Rate, track.Channels,
		time.Duration(track.Seconds()*float64(time.Second)).Round(time.Millisecond))
	if opt.End > 0 && opt.End <= opt.Start {
		return fmt.Errorf("the end time must be after the start time")
	}
	if opt.Start >= track.Seconds() {
		return fmt.Errorf("the start time is beyond the end of the song")
	}
	engine, err := newEngine(p, threads)
	if err != nil {
		return err
	}
	defer engine.Close()
	if mode := gpuChoice(p, gpuFlag); mode != "" {
		if err := enableGPU(p, engine, mode, func(msg string) { fmt.Println(msg) }); err != nil {
			fmt.Println("Graphics processor not used:", err)
		} else {
			opt.GPU = true
			fmt.Println("Using the", gpuName(), "("+mode+")")
		}
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt)
	defer stop()
	began := time.Now()
	last := -1
	res, err := engine.Separate(ctx, track, opt, func(stage string, f float64) {
		if pct := int(f * 100); pct != last {
			last = pct
			fmt.Printf("\r%-32s %3d%%", stage, pct)
		}
	})
	fmt.Println()
	if err != nil {
		return err
	}

	whole := res.StartFrame == 0 && res.EndFrame == track.Frames()
	saved, err := saveResult(res, out, filepath.Base(in), wavFormat, whole)
	if err != nil {
		return err
	}
	span := float64(res.EndFrame-res.StartFrame) / float64(res.Rate)
	took := time.Since(began).Seconds()
	fmt.Printf("Done in %.0f s (%.1fx real time), %s mode, %s WAV, on the %s\n", took, span/took, q, wavFormat, res.RanOn)
	if res.GPUFallback != "" {
		fmt.Println("Note:", res.GPUFallback)
		// stays off until it is switched on again (--gpu on, or the tick box in the window)
		rememberGPUFailure(p, res.GPUFallback)
		fmt.Println("The graphics processor is now switched off in settings.json; use --gpu on (or tick it in the window) to try it again.")
	}
	fmt.Println("Instrumental:", saved.Instrumental)
	fmt.Println("Vocals:      ", saved.Vocals)
	if saved.Note != "" {
		fmt.Println("Note:", saved.Note)
	}
	return nil
}

// readSong reads a song file into memory, refusing one larger than the window accepts (1 GB) before
// reading it.
func readSong(path string) ([]byte, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	st, err := f.Stat()
	if err != nil {
		return nil, err
	}
	if st.IsDir() {
		return nil, fmt.Errorf("%s is a folder, not a song file", path)
	}
	if st.Size() > maxUploadBytes {
		return nil, errors.New(tooLargeMessage)
	}
	// the size is checked again while reading, in case the file grows in the meantime
	data, err := io.ReadAll(io.LimitReader(f, maxUploadBytes+1))
	if err != nil {
		return nil, err
	}
	if len(data) > maxUploadBytes {
		return nil, errors.New(tooLargeMessage)
	}
	return data, nil
}
