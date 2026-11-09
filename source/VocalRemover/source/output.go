package main

import (
	"bufio"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"unicode"

	"vocalremover/internal/audio"
	"vocalremover/internal/separate"
)

// outputDirName is the folder, next to VocalRemover.exe, that results are saved into.
const outputDirName = "Output"

// appOutputDir is set at start-up to the Output folder in the program's own folder.
var appOutputDir string

func defaultOutputDir() string { return appOutputDir }

// checkWritable creates the folder if needed and makes sure files can be saved into it, so
// a problem is reported before minutes of processing rather than after.
func checkWritable(folder string) error {
	protected := ""
	if folder == defaultOutputDir() {
		protected = " This happens when the program sits in a protected place such as Program Files. Move the VocalRemover folder somewhere like Documents or the Desktop, or choose another folder to save into."
	}
	if err := os.MkdirAll(folder, 0o755); err != nil {
		return fmt.Errorf("the folder %s cannot be created or opened.%s", folder, protected)
	}
	// A new file with a random name: an existing file (or a link to one elsewhere) is never opened.
	f, err := os.CreateTemp(folder, ".vr-probe-*")
	if err != nil {
		if protected != "" {
			return fmt.Errorf("the program is not allowed to save into its own folder (%s).%s", folder, protected)
		}
		return fmt.Errorf("the program is not allowed to save into %s. Choose another folder", folder)
	}
	probe := f.Name()
	f.Close()
	os.Remove(probe)
	return nil
}

// parseTime accepts "90", "90.5", "1:30", "1:30.25" or "1:02:03" and returns seconds.
func parseTime(s string) (float64, error) {
	s = strings.TrimSpace(strings.ReplaceAll(s, ",", "."))
	if s == "" {
		return 0, fmt.Errorf("empty time")
	}
	parts := strings.Split(s, ":")
	if len(parts) > 3 {
		return 0, fmt.Errorf("cannot understand the time %q", s)
	}
	var total float64
	for i, p := range parts {
		v, err := strconv.ParseFloat(strings.TrimSpace(p), 64)
		if err != nil || v < 0 || math.IsNaN(v) || math.IsInf(v, 0) {
			return 0, fmt.Errorf("cannot understand the time %q (use seconds, or minutes:seconds)", s)
		}
		if i > 0 && v >= 60 && len(parts) > 1 {
			return 0, fmt.Errorf("cannot understand the time %q (seconds must be below 60)", s)
		}
		total = total*60 + v
	}
	return total, nil
}

// clock formats seconds for file names, e.g. 75.5 -> "1m15.5s".
func clock(sec float64) string {
	tenths := int(math.Round(sec * 10))
	str := strconv.FormatFloat(float64(tenths%600)/10, 'f', -1, 64)
	return fmt.Sprintf("%dm%ss", tenths/600, str)
}

// reservedNames are the device names Windows still treats as devices, whatever the extension: on
// Windows 10 "nul.backup (Vocals).wav" is the NUL device, so the part before the first dot counts.
var reservedNames = map[string]bool{"CON": true, "PRN": true, "AUX": true, "NUL": true, "CONIN$": true, "CONOUT$": true,
	"COM1": true, "COM2": true, "COM3": true, "COM4": true, "COM5": true, "COM6": true, "COM7": true, "COM8": true, "COM9": true,
	"COM\u00b9": true, "COM\u00b2": true, "COM\u00b3": true,
	"LPT1": true, "LPT2": true, "LPT3": true, "LPT4": true, "LPT5": true, "LPT6": true, "LPT7": true, "LPT8": true, "LPT9": true,
	"LPT\u00b9": true, "LPT\u00b2": true, "LPT\u00b3": true}

// isReservedName reports whether Windows would take a file name starting this way for a device.
func isReservedName(name string) bool {
	first, _, _ := strings.Cut(name, ".")
	return reservedNames[strings.ToUpper(strings.TrimRight(first, " "))]
}

// safeBaseName turns an uploaded file name into something safe to use in a Windows file
// name: no folders, no reserved characters or device names, reasonable length.
func safeBaseName(name string) string {
	name = strings.ReplaceAll(name, "\\", "/")
	if i := strings.LastIndex(name, "/"); i >= 0 {
		name = name[i+1:]
	}
	if ext := filepath.Ext(name); len(ext) <= 5 {
		name = strings.TrimSuffix(name, ext)
	}
	var b strings.Builder
	for _, r := range name {
		switch {
		case r < 0x20 || r == 0x7F || strings.ContainsRune(`<>:"/\|?*`, r) || !unicode.IsPrint(r):
			b.WriteRune('_')
		default:
			b.WriteRune(r)
		}
	}
	out := strings.Trim(b.String(), " .")
	if r := []rune(out); len(r) > 100 {
		out = strings.Trim(string(r[:100]), " .")
	}
	if out == "" {
		out = "song"
	}
	if isReservedName(out) {
		out = "_" + out
	}
	return out
}

// createUnique creates a new file named stem+ext in dir, or stem (2), (3)... if that name is
// taken. The file is created with "fail if it exists", so an existing file is never
// overwritten, even if it appears at the last moment.
func createUnique(dir, stem, ext string) (*os.File, string, error) {
	for i := 1; i <= 9999; i++ {
		name := stem + ext
		if i > 1 {
			name = fmt.Sprintf("%s (%d)%s", stem, i, ext)
		}
		path := filepath.Join(dir, name)
		f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o644)
		if err == nil {
			return f, path, nil
		}
		if !os.IsExist(err) {
			return nil, "", err
		}
	}
	return nil, "", fmt.Errorf("too many files named %s%s already exist", stem, ext)
}

type savedFiles struct {
	Instrumental string
	Vocals       string
	Folder       string
	Note         string
}

// saveResult writes the two WAV files into dir and returns their paths.
func saveResult(res *separate.Result, dir, sourceName string, format audio.WAVFormat, wholeSong bool) (*savedFiles, error) {
	if dir == "" {
		return nil, fmt.Errorf("no output folder was given")
	}
	abs, err := filepath.Abs(dir)
	if err != nil {
		return nil, fmt.Errorf("the output folder %q is not valid", dir)
	}
	dir = abs
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return nil, fmt.Errorf("cannot create the output folder %s (%v)", dir, err)
	}
	stem := safeBaseName(sourceName)
	if !wholeSong {
		from := float64(res.StartFrame) / float64(res.Rate)
		to := float64(res.EndFrame) / float64(res.Rate)
		stem += fmt.Sprintf(" [%s-%s]", clock(from), clock(to))
	}
	out := &savedFiles{Folder: dir}

	// Integer WAV formats cannot hold samples beyond full scale. If a track peaks above it
	// (common with loud MP3s), lower both tracks by the same amount so they still add up to
	// the original and nothing is clipped.
	inst, voc := res.Instrumental, res.Vocals
	gain := float32(1)
	if format != audio.Float32 {
		if peak := audio.Peak(inst[0], inst[1], voc[0], voc[1]); peak > 0.999 {
			gain = 0.999 / peak
			out.Note = fmt.Sprintf("Both files were lowered by %.1f dB so that peaks do not clip. Choose 32-bit float output to keep the exact original level.", -20*math.Log10(float64(gain)))
		}
	}

	write := func(label string, s separate.Stereo) (string, error) {
		f, path, err := createUnique(dir, stem+" ("+label+")", ".wav")
		if err != nil {
			return "", fmt.Errorf("cannot write into %s (%v)", dir, err)
		}
		w := bufio.NewWriterSize(f, 1<<20)
		r := s[1]
		if res.Mono {
			r = nil
		}
		err = audio.WriteWAV(w, s[0], r, res.Rate, format, gain)
		if err == nil {
			err = w.Flush()
		}
		if cerr := f.Close(); err == nil {
			err = cerr
		}
		if err != nil {
			os.Remove(path)
			return "", fmt.Errorf("cannot write %s (%v)", filepath.Base(path), err)
		}
		return path, nil
	}
	if out.Instrumental, err = write("Instrumental", inst); err != nil {
		return nil, err
	}
	if out.Vocals, err = write("Vocals", voc); err != nil {
		return nil, err
	}
	return out, nil
}

func parseFormat(s string) (audio.WAVFormat, error) {
	switch strings.ToLower(strings.TrimSpace(s)) {
	case "16", "pcm16":
		return audio.PCM16, nil
	case "24", "pcm24", "":
		return audio.PCM24, nil
	case "32", "32f", "float", "float32":
		return audio.Float32, nil
	}
	return 0, fmt.Errorf("unknown output format %q (use 16, 24 or 32f)", s)
}
