package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"strconv"
	"strings"
	"time"

	"vocalremover/internal/onnxconv"
	"vocalremover/internal/ort"
	"vocalremover/internal/separate"
)

// Checksums (SHA-256) of the libraries shipped next to the program. They are checked before anything is
// loaded, so a swapped or damaged file is refused instead of executed.
var libraryChecksums = map[string]string{
	// ONNX Runtime 1.30.0 for Windows x64, from github.com/microsoft/onnxruntime/releases
	"onnxruntime.dll": "7e39e2bdbba836d98071ef28620735ba36a47c554cf794585269aecc50fab0da",
	// Microsoft Visual C++ runtime 14.40.33728, from the Microsoft.VCLibs.140.00.UWPDesktop
	// package published with github.com/microsoft/winget-cli releases
	"msvcp140.dll":       "fd96b594b1f418852893de4cb960f74b43d6905d9966c5532263ca619c9dafc7",
	"msvcp140_1.dll":     "58c7208525b5dcc34ef0f6482c436dc594040f53f8bb923559bcef636419fc7c",
	"vcruntime140.dll":   "c4ae88c9eb48d63b23560f2e5babbdbf129e3787127688d5383415bbc7347b59",
	"vcruntime140_1.dll": "63b9a905cfb0c822d403e2a511ffa4132b1433285530b3cc062f41e8e0a274ac",
	// The graphics-processor runtime, in the "gpu" folder: ONNX Runtime 1.24.4 built with DirectML
	// (NuGet package Microsoft.ML.OnnxRuntime.DirectML) and DirectML 1.15.4 (Microsoft.AI.DirectML)
	"gpu/onnxruntime.dll": "e7eedec6a6f26dc39dc948276a75ef6d2bee3fff944d874ceed0bbd3b97bff40",
	"gpu/DirectML.dll":    "9c9e6d822561c6c41b90e6994b3e8857cf1d66dbfb1e0c4c799c7c89b4e92da1",
	// the C++ runtime again beside the graphics runtime (a library's own folder is where Windows looks for what it needs)
	"gpu/msvcp140.dll":       "fd96b594b1f418852893de4cb960f74b43d6905d9966c5532263ca619c9dafc7",
	"gpu/msvcp140_1.dll":     "58c7208525b5dcc34ef0f6482c436dc594040f53f8bb923559bcef636419fc7c",
	"gpu/vcruntime140.dll":   "c4ae88c9eb48d63b23560f2e5babbdbf129e3787127688d5383415bbc7347b59",
	"gpu/vcruntime140_1.dll": "63b9a905cfb0c822d403e2a511ffa4132b1433285530b3cc062f41e8e0a274ac",
	// ONNX Runtime 1.30.0 for macOS (Apple silicon), from the same releases page; it carries the CoreML
	// provider for the graphics processor / Neural Engine. (Intel Macs: see init below.)
	"libonnxruntime.dylib": "bcc9110f9d638a119de2db7afb3ba9a1da8085f0cb3401e1c48ae1caf450b6fa",
}

// macIntelRuntimeSHA256 is ONNX Runtime 1.23.2 for macOS x86_64 (lib/libonnxruntime.1.23.2.dylib from
// onnxruntime-osx-x86_64-1.23.2.tgz on the same releases page): the last version Microsoft built for Intel
// Macs. It has the CoreML provider too. The program asks for C API version 12, which it serves.
const macIntelRuntimeSHA256 = "8c9c78de65ea3786f987c0d980e9c1b13a3a5fbc6b3e2965ba05b450e6e4c054"

func init() {
	if runtime.GOOS == "darwin" && runtime.GOARCH == "amd64" {
		libraryChecksums["libonnxruntime.dylib"] = macIntelRuntimeSHA256
	}
}

type paths struct {
	appDir     string
	library    string // the processor runtime
	gpuLibrary string // the runtime that carries the graphics provider ("" when this system has none)
	modelDir   string
	settings   string
}

func locate(libFlag, modelsFlag string) (paths, error) {
	exe, err := os.Executable()
	if err != nil {
		return paths{}, fmt.Errorf("cannot find the program's own folder: %v", err)
	}
	if real, err := filepath.EvalSymlinks(exe); err == nil {
		exe = real
	}
	p := paths{appDir: filepath.Dir(exe)}
	switch runtime.GOOS {
	case "windows":
		p.library = filepath.Join(p.appDir, "onnxruntime.dll")
		p.gpuLibrary = filepath.Join(p.appDir, "gpu", "onnxruntime.dll")
	case "darwin":
		p.library = filepath.Join(p.appDir, "libonnxruntime.dylib")
		p.gpuLibrary = p.library // CoreML lives in the same library
	default:
		p.library = filepath.Join(p.appDir, "libonnxruntime.so")
	}
	if libFlag != "" {
		p.library = libFlag
	}
	p.modelDir = filepath.Join(p.appDir, "models")
	if modelsFlag != "" {
		p.modelDir = modelsFlag
	}
	p.settings = filepath.Join(p.appDir, "settings.json")
	return p, nil
}

// gpuProvider names the graphics execution provider of this system ("" = none).
func gpuProvider() string {
	switch runtime.GOOS {
	case "windows":
		return "DML"
	case "darwin":
		return "CoreML"
	}
	return ""
}

// gpuName is how the graphics provider is called in messages.
func gpuName() string {
	switch gpuProvider() {
	case "DML":
		return "graphics processor (DirectML)"
	case "CoreML":
		return "graphics processor / Neural Engine (CoreML)"
	}
	return "graphics processor"
}

var partName = regexp.MustCompile(`\.part(\d+)of(\d+)$`)

// assembleModel puts a model file together from downloaded pieces named "<file>.part1of2",
// "<file>.part2of2", ... found in the models folder or next to the program. (The models are delivered
// in pieces because of download size limits.) The joined file is only kept if its checksum is right;
// the pieces are removed afterwards.
func assembleModel(p paths, spec separate.ModelSpec) error {
	target := filepath.Join(p.modelDir, spec.File)
	if _, err := os.Stat(target); err == nil {
		return nil
	}
	parts := map[int]string{}
	total := 0
	// Places to look: the models folder, the program folder, the one or two folders above it (where a
	// ZIP is usually extracted) and the user's Downloads folder (not on a Mac: reading Downloads would
	// make macOS ask for permission; the RapReaper installer moves the pieces instead).
	dirs := []string{p.modelDir, p.appDir, filepath.Dir(p.appDir), filepath.Dir(filepath.Dir(p.appDir))}
	if home, err := os.UserHomeDir(); err == nil && runtime.GOOS != "darwin" {
		dirs = append(dirs, filepath.Join(home, "Downloads"))
	}
	for _, dir := range dirs {
		entries, err := os.ReadDir(dir)
		if err != nil {
			continue
		}
		for _, e := range entries {
			name := e.Name()
			if e.IsDir() || !strings.HasPrefix(strings.ToLower(name), strings.ToLower(spec.File)+".part") {
				continue
			}
			m := partName.FindStringSubmatch(name)
			if m == nil {
				continue
			}
			idx, _ := strconv.Atoi(m[1])
			of, _ := strconv.Atoi(m[2])
			if idx < 1 || of < 1 || of > 99 || idx > of {
				continue
			}
			if total != 0 && of != total {
				return fmt.Errorf("the pieces of %s do not belong together; delete them and download them again", spec.File)
			}
			total = of
			if _, seen := parts[idx]; !seen {
				parts[idx] = filepath.Join(dir, name)
			}
		}
	}
	if total == 0 {
		return nil // no pieces either; the caller reports the missing model
	}
	var missing []string
	for i := 1; i <= total; i++ {
		if _, ok := parts[i]; !ok {
			missing = append(missing, fmt.Sprintf("%s.part%dof%d", spec.File, i, total))
		}
	}
	if len(missing) > 0 {
		return fmt.Errorf("a model file is incomplete. Still missing: %s. Put all downloaded model pieces into the folder %s and start the program again",
			strings.Join(missing, ", "), p.appDir)
	}
	fmt.Printf("Putting %s together from %d pieces (first start only)...\n", spec.File, total)
	if err := os.MkdirAll(p.modelDir, 0o755); err != nil {
		return fmt.Errorf("cannot create the models folder: %v", err)
	}
	tmp := target + ".joining"
	out, err := os.Create(tmp)
	if err != nil {
		return fmt.Errorf("cannot write into the models folder: %v", err)
	}
	hash := sha256.New()
	for i := 1; i <= total && err == nil; i++ {
		var in *os.File
		if in, err = os.Open(parts[i]); err == nil {
			_, err = io.Copy(io.MultiWriter(out, hash), in)
			in.Close()
		}
	}
	if cerr := out.Close(); err == nil {
		err = cerr
	}
	if err != nil {
		os.Remove(tmp)
		return fmt.Errorf("could not put %s together: %v", spec.File, err)
	}
	if hex.EncodeToString(hash.Sum(nil)) != spec.SHA256 {
		os.Remove(tmp)
		return fmt.Errorf("the pieces of %s are damaged or incomplete (checksum does not match). Delete them and download them again", spec.File)
	}
	if err := os.Rename(tmp, target); err != nil {
		os.Remove(tmp)
		return fmt.Errorf("could not put %s together: %v", spec.File, err)
	}
	// Tidy up pieces that sit inside the program's own folders. Pieces found elsewhere (for example in
	// Downloads) are left alone; they are no longer needed and can be deleted.
	for _, part := range parts {
		if d := filepath.Dir(part); d == p.modelDir || d == p.appDir {
			os.Remove(part)
		}
	}
	return nil
}

// checkLibraries verifies every shipped library file that is present against its checksum.
func checkLibraries(p paths) error {
	if runtime.GOOS == "windows" && !strings.EqualFold(filepath.Base(p.library), "onnxruntime.dll") {
		return fmt.Errorf("the ONNX Runtime library must be named onnxruntime.dll")
	}
	if runtime.GOOS == "windows" {
		if stray := strayLibraries(p.appDir); len(stray) > 0 {
			if len(stray) == 1 {
				return fmt.Errorf("%s is not one of Vocal Remover's files. Windows could load a library file like this into the program in place of "+
					"one of its own, so the program does not start while it is there. Delete it, or extract the Vocal Remover ZIP into a new, "+
					"empty folder, and start the program again", stray[0])
			}
			return fmt.Errorf("these files are not Vocal Remover's: %s. Windows could load library files like these into the program in place of "+
				"its own, so the program does not start while they are there. Delete them, or extract the Vocal Remover ZIP into a new, "+
				"empty folder, and start the program again", strings.Join(stray, ", "))
		}
	}
	for rel, sum := range libraryChecksums {
		path := filepath.Join(p.appDir, filepath.FromSlash(rel))
		if _, err := os.Stat(path); err != nil {
			// Optional files (the C++ runtime, the graphics runtime) may be absent.
			continue
		}
		if _, err := separate.VerifyFile(path, sum); err != nil {
			return err
		}
	}
	return nil
}

// strayLibraries lists the .dll files in the program folder and its gpu folder that are not among the
// shipped libraries. Windows looks in a library's own folder first for the libraries it needs, so an
// unexpected file there could be run as part of the program.
func strayLibraries(appDir string) []string {
	expected := map[string]bool{}
	for rel := range libraryChecksums {
		if strings.HasSuffix(strings.ToLower(rel), ".dll") {
			expected[strings.ToLower(rel)] = true
		}
	}
	var stray []string
	for _, sub := range []string{"", "gpu"} {
		entries, err := os.ReadDir(filepath.Join(appDir, sub))
		if err != nil {
			continue
		}
		for _, e := range entries {
			name := e.Name()
			if !strings.EqualFold(filepath.Ext(name), ".dll") || e.IsDir() {
				continue
			}
			rel := strings.ToLower(name)
			if sub != "" {
				rel = sub + "/" + rel
			}
			if !expected[rel] {
				stray = append(stray, filepath.Join(appDir, sub, name))
			}
		}
	}
	return stray
}

func newEngine(p paths, threads int) (*separate.Engine, error) {
	for _, spec := range separate.AllModels() {
		if err := assembleModel(p, spec); err != nil {
			if spec.File == separate.MelRoformer.File {
				return nil, err // the main model: nothing works without it
			}
			// Any other model is optional: the program starts without it and the modes that need it
			// are not available (greyed out in the window) until its pieces are complete.
			fmt.Printf("Note: %s. Until then the modes that need %s are not available.\n", strings.TrimRight(err.Error(), "."), spec.File)
		}
	}
	if _, err := os.Stat(p.library); err != nil {
		return nil, fmt.Errorf("%s is missing. Please extract the whole ZIP file into one folder and start the program from there", filepath.Base(p.library))
	}
	if err := checkLibraries(p); err != nil {
		return nil, err
	}
	return separate.NewEngine(p.library, p.modelDir, threads)
}

// gpuAvailable says whether this system and this folder can offer the graphics processor at all
// (the right system, the graphics runtime present and intact). The reason is for the window.
func gpuAvailable(p paths) (bool, string) {
	if gpuProvider() == "" {
		return false, "the graphics processor is used on Windows (DirectML) and on a Mac (CoreML) only"
	}
	if p.gpuLibrary == "" {
		return false, "no graphics runtime for this system"
	}
	if _, err := os.Stat(p.gpuLibrary); err != nil {
		return false, "the graphics runtime (" + filepath.Base(filepath.Dir(p.gpuLibrary)) + string(os.PathSeparator) + filepath.Base(p.gpuLibrary) + ") is missing from the program folder"
	}
	if runtime.GOOS == "windows" {
		if _, err := os.Stat(filepath.Join(p.appDir, "gpu", "DirectML.dll")); err != nil {
			return false, "gpu\\DirectML.dll is missing from the program folder"
		}
	}
	return true, ""
}

// loadGPURuntime loads the library that carries the graphics provider (after its checksum check).
func loadGPURuntime(p paths, engine *separate.Engine) (*ort.Runtime, error) {
	if ok, why := gpuAvailable(p); !ok {
		return nil, errors.New(why)
	}
	if err := checkLibraries(p); err != nil {
		return nil, err
	}
	if p.gpuLibrary == p.library {
		return engine.Runtime(), nil
	}
	return ort.Load(p.gpuLibrary)
}

// ensureGPUModel makes the float16 graphics twin of the Mel-Roformer from its processor file, once,
// and checks it against the expected checksum. It takes a few seconds and about 460 MB of disk.
func ensureGPUModel(p paths, progress func(string)) error {
	spec := separate.MelRoformer
	if spec.GPU == nil {
		return nil
	}
	target := filepath.Join(p.modelDir, spec.GPU.File)
	if st, err := os.Stat(target); err == nil && !st.IsDir() {
		if _, err := separate.VerifyFile(target, spec.GPU.SHA256); err == nil {
			return nil
		}
		os.Remove(target) // damaged or from an older version: make it again
	}
	if progress != nil {
		progress("Preparing the graphics-processor model file (once, about half a minute)")
	}
	data, err := separate.VerifyFile(filepath.Join(p.modelDir, spec.File), spec.SHA256)
	if err != nil {
		return fmt.Errorf("the graphics model is made from %s, which %v", spec.File, err)
	}
	conv, n, err := onnxconv.Convert(data)
	data = nil
	if err != nil {
		return fmt.Errorf("could not prepare the graphics model: %v", err)
	}
	sum := sha256.Sum256(conv)
	if got := hex.EncodeToString(sum[:]); got != spec.GPU.SHA256 {
		return fmt.Errorf("the prepared graphics model does not have the expected checksum (%s…); this build of the program does not match its model file", got[:12])
	}
	tmp := target + ".writing"
	if err := os.WriteFile(tmp, conv, 0o644); err != nil {
		os.Remove(tmp)
		return fmt.Errorf("cannot write the graphics model into the models folder: %v", err)
	}
	if err := os.Rename(tmp, target); err != nil {
		os.Remove(tmp)
		return err
	}
	if progress != nil {
		progress(fmt.Sprintf("Graphics model ready (%d layers, %d MB)", n, len(conv)/1000000))
	}
	return nil
}

// ---- settings ---------------------------------------------------------------------------------------

// settings is the one file the program writes besides results: whether to use the graphics processor,
// and what the last graphics test found. It lives next to the program.
type settings struct {
	GPU       string `json:"gpu"`                  // "" / "off", "fused", "unfused"
	GPUTested string `json:"gpu_tested,omitempty"` // when the test last ran
	GPUNote   string `json:"gpu_note,omitempty"`   // the test's one-line verdict, or why it was switched off
	GPUFailed string `json:"gpu_failed,omitempty"` // when it was switched off after failing during a song
	GPUDevice string `json:"gpu_device,omitempty"`
}

func loadSettings(p paths) settings {
	var s settings
	data, err := os.ReadFile(p.settings)
	if err != nil {
		return s
	}
	if len(data) > 1<<16 || json.Unmarshal(data, &s) != nil {
		return settings{}
	}
	if s.GPU != "fused" && s.GPU != "unfused" {
		s.GPU = ""
	}
	return s
}

func saveSettings(p paths, s settings) error {
	if s.GPU == "" {
		s.GPU = "off"
	}
	data, _ := json.MarshalIndent(s, "", "  ")
	tmp := p.settings + ".tmp"
	if err := os.WriteFile(tmp, append(data, '\n'), 0o644); err != nil {
		return err
	}
	return os.Rename(tmp, p.settings)
}

// rememberGPUFailure switches the graphics processor off in settings.json after it failed during a
// song, so that it stays off (also after a restart) until it is ticked again or tested again.
func rememberGPUFailure(p paths, why string) {
	s := loadSettings(p)
	s.GPU = ""
	s.GPUFailed = time.Now().Format("2006-01-02 15:04")
	s.GPUNote = "it failed during a song (" + shortErr(errors.New(why)) + "); tick it again to try it once more"
	saveSettings(p, s)
}

// gpuMode is the mode to use now: the one the settings say works, or an explicit override.
func gpuMode(p paths, override string) string {
	if override == "fused" || override == "unfused" {
		return override
	}
	if override == "off" || override == "false" || override == "0" {
		return ""
	}
	return loadSettings(p).GPU
}

// enableGPU switches the engine to the graphics processor in the given mode (loading the runtime and
// making the graphics model if needed). It never fails the program: an error is returned for the
// caller to show, and the engine stays on the processor.
func enableGPU(p paths, engine *separate.Engine, mode string, progress func(string)) error {
	if mode == "" {
		engine.DisableGPU()
		return nil
	}
	rt, err := loadGPURuntime(p, engine)
	if err != nil {
		engine.DisableGPU()
		return err
	}
	if err := ensureGPUModel(p, progress); err != nil {
		engine.DisableGPU()
		return err
	}
	engine.EnableGPU(gpuProvider(), rt, mode, 0)
	return nil
}

// ---- the graphics test -------------------------------------------------------------------------------

// gpuTestResult is what the graphics test found, for the console, the window and settings.json.
type gpuTestResult struct {
	Works      bool
	Mode       string // the mode to use ("fused" / "unfused"), when it works
	CPUSeconds float64
	GPUSeconds map[string]float64
	Lines      []string
	Note       string
}

// runGPUTest runs one 8-second window of a test signal through the Mel-Roformer on the processor and
// then on the graphics processor (first with the graph fused, then one operation at a time), checks that
// the graphics result agrees with the processor's, times both, and writes the verdict into settings.json
// and Output/GPU test.txt.
func runGPUTest(p paths, engine *separate.Engine, progress func(string)) (*gpuTestResult, error) {
	say := func(f string, a ...any) {
		if progress != nil {
			progress(fmt.Sprintf(f, a...))
		}
	}
	res := &gpuTestResult{GPUSeconds: map[string]float64{}}
	add := func(f string, a ...any) { res.Lines = append(res.Lines, fmt.Sprintf(f, a...)) }
	add("Vocal Remover %s: graphics processor test, %s", version, time.Now().Format("2006-01-02 15:04"))
	add("One window (8 s) of a test signal through the Mel-Roformer network, on the processor and then on the %s.", gpuName())
	add("Each runs it twice and the second run is timed, so neither time includes warming up.")
	add("")
	if ok, why := gpuAvailable(p); !ok {
		add("Not possible: %s.", why)
		res.Note = why
		finishGPUTest(p, res)
		return res, nil
	}
	spec := separate.MelRoformer
	if !engine.Available(spec) {
		return nil, fmt.Errorf("the model file %s is missing; the test needs it", spec.File)
	}
	say("Preparing the graphics model")
	if err := ensureGPUModel(p, progress); err != nil {
		add("Could not prepare the graphics model: %v", err)
		res.Note = "the graphics model could not be prepared"
		finishGPUTest(p, res)
		return res, nil
	}
	say("Processor run")
	cpuOut, cpuSec, err := engine.TestWindow(nil, "")
	if err != nil {
		return nil, fmt.Errorf("the processor run failed: %v", err)
	}
	res.CPUSeconds = cpuSec
	add("Processor (ONNX Runtime %s, %d cores): %.1f s", engine.RuntimeVersion(), runtime.NumCPU(), cpuSec)
	rt, err := loadGPURuntime(p, engine)
	if err != nil {
		add("The graphics runtime could not be loaded: %v", err)
		res.Note = "the graphics runtime could not be loaded"
		finishGPUTest(p, res)
		return res, nil
	}
	modes := []string{"fused", "unfused"}
	if gpuProvider() == "CoreML" {
		modes = []string{"fused"}
	}
	best := ""
	for _, mode := range modes {
		label := gpuName()
		if mode == "unfused" {
			label += ", one operation at a time"
		}
		say("Graphics run (%s)", mode)
		engine.EnableGPU(gpuProvider(), rt, mode, 0)
		gpuOut, gpuSec, err := engine.TestWindow(cpuOut, mode)
		engine.DisableGPU()
		if err != nil {
			add("%s (ONNX Runtime %s): failed: %v", label, rt.Version(), shortErr(err))
			continue
		}
		diff := relativeDifference(cpuOut, gpuOut)
		res.GPUSeconds[mode] = gpuSec
		// diff is the size of the difference between the two results relative to the processor's result
		verdict := fmt.Sprintf("agrees with the processor (%s difference)", percent(diff))
		if diff > 0.25 {
			verdict = fmt.Sprintf("DIFFERS from the processor's result (%s difference; above 25%% counts as not usable)", percent(diff))
		} else if diff > 0.1 {
			verdict = fmt.Sprintf("agrees with the processor roughly (%s difference)", percent(diff))
		}
		add("%s (ONNX Runtime %s): %.1f s (%.1fx the processor's speed), %s", label, rt.Version(), gpuSec, cpuSec/gpuSec, verdict)
		if diff <= 0.25 && best == "" {
			best = mode
		}
	}
	add("")
	switch {
	case best == "":
		res.Note = "the graphics processor could not run the network; the processor is used"
		add("Result: no graphics mode ran correctly. The program keeps using the processor.")
	case res.GPUSeconds[best] >= res.CPUSeconds*0.9:
		res.Works, res.Mode = true, best
		res.Note = fmt.Sprintf("works, but not faster than the processor (%.1f s against %.1f s); left off", res.GPUSeconds[best], res.CPUSeconds)
		add("Result: the graphics processor works but is not faster than the processor here, so it stays off. You can still tick it in the window.")
	default:
		res.Works, res.Mode = true, best
		res.Note = fmt.Sprintf("works: %.1fx the processor's speed (%s)", res.CPUSeconds/res.GPUSeconds[best], best)
		add("Result: the graphics processor works (%s mode) and is %.1fx faster than the processor. It is now switched on.", best, res.CPUSeconds/res.GPUSeconds[best])
	}
	finishGPUTest(p, res)
	return res, nil
}

// percent shows a fraction as a percentage with enough digits for small values (0.04%, 2.3%, 31%).
func percent(f float64) string {
	switch p := f * 100; {
	case p < 1:
		return fmt.Sprintf("%.2f%%", p)
	case p < 10:
		return fmt.Sprintf("%.1f%%", p)
	default:
		return fmt.Sprintf("%.0f%%", p)
	}
}

func shortErr(err error) string {
	s := err.Error()
	if len(s) > 400 {
		s = strings.ToValidUTF8(s[:400], "") + "…"
	}
	return strings.ReplaceAll(s, "\n", " ")
}

func finishGPUTest(p paths, res *gpuTestResult) {
	s := loadSettings(p)
	s.GPUTested = time.Now().Format("2006-01-02 15:04")
	s.GPUNote = res.Note
	s.GPUFailed = ""
	if res.Works && res.GPUSeconds[res.Mode] < res.CPUSeconds*0.9 {
		s.GPU = res.Mode
	} else {
		s.GPU = ""
	}
	if err := saveSettings(p, s); err != nil {
		res.Lines = append(res.Lines, "(settings.json could not be written: "+err.Error()+")")
	}
	dir := defaultOutputDir()
	if err := os.MkdirAll(dir, 0o755); err == nil {
		os.WriteFile(filepath.Join(dir, "GPU test.txt"), []byte(strings.Join(res.Lines, "\n")+"\n"), 0o644)
	}
}

// relativeDifference is the RMS of (a-b) over the RMS of a.
func relativeDifference(a, b []float32) float64 {
	if len(a) != len(b) || len(a) == 0 {
		return 1
	}
	var num, den float64
	for i := range a {
		d := float64(a[i] - b[i])
		num += d * d
		den += float64(a[i]) * float64(a[i])
	}
	if den == 0 {
		return 0
	}
	return math.Sqrt(num / den)
}
