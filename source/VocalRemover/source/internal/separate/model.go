// Package separate splits a stereo recording into a vocal track and an instrumental track
// using neural networks (Roformer and MDX-Net families) running locally through ONNX Runtime.
package separate

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"runtime/debug"
	"sync"

	"vocalremover/internal/dsp"
	"vocalremover/internal/ort"
)

// ModelRate is the sample rate the networks were trained at.
const ModelRate = 44100

const (
	hop       = 1024
	dimT      = 256
	inputPeak = 0.9 // level the networks see, as in the reference implementation
)

// ModelSpec describes one network file and how to drive it.
type ModelSpec struct {
	Key        string
	Name       string
	File       string
	SHA256     string // the file is refused unless its checksum matches
	NFFT       int
	DimF       int
	Compensate float32 // published per-model gain correction
	Vocals     bool    // true: the network outputs vocals; false: it outputs the instrumental
	Roformer   bool    // true: Roformer layout (see roformer.go); false: MDX-Net layout
	// GPU names the float16 twin of a Roformer model made for the graphics processor (nil: none).
	// It is generated on this computer from the processor file (see MakeGPUModel).
	GPU *ModelSpec
}

// Known models. Parameters come from the Ultimate Vocal Remover model database.
var (
	InstHQ4 = ModelSpec{
		Key: "inst_hq4", Name: "UVR-MDX-NET Inst HQ 4", File: "UVR-MDX-NET-Inst_HQ_4.onnx",
		SHA256: "3c4b5b9b05090fdf238f38ba5046813982d50e2a652e9cb3324ea79720c3c9c8",
		NFFT:   5120, DimF: 2560, Compensate: 1.019, Vocals: false,
	}
	KimVocal2 = ModelSpec{
		Key: "kim_vocal_2", Name: "Kim Vocal 2", File: "Kim_Vocal_2.onnx",
		SHA256: "ce74ef3b6a6024ce44211a07be9cf8bc6d87728cc852a68ab34eb8e58cde9c8b",
		NFFT:   7680, DimF: 3072, Compensate: 1.009, Vocals: true,
	}
)

type model struct {
	spec ModelSpec
	sess *ort.Session
	stft *dsp.STFT
	out  []float32
}

// gpuState says whether and how the graphics processor is used for the Roformer models.
type gpuState struct {
	provider string       // "DML" (Windows, DirectML) or "CoreML" (Mac)
	rt       *ort.Runtime // the library that carries the provider (a separate DLL on Windows)
	mode     string       // "" (off), "fused" or "unfused" (DirectML without graph fusion: slower, less memory)
	device   int
	failed   string // the last failure, if the graphics processor was given up on
}

// Engine owns the ONNX Runtime library and the loaded networks.
type Engine struct {
	rt       *ort.Runtime
	modelDir string
	threads  int
	gpu      gpuState

	mu     sync.Mutex
	models map[string]*model
}

// EnableGPU tells the engine to run Roformer models on the graphics processor through the given
// library and provider; mode is "fused" or "unfused" (DirectML only; CoreML ignores it).
func (e *Engine) EnableGPU(provider string, rt *ort.Runtime, mode string, device int) {
	e.mu.Lock()
	defer e.mu.Unlock()
	if mode == "" {
		mode = "fused"
	}
	e.gpu = gpuState{provider: provider, rt: rt, mode: mode, device: device}
}

// DisableGPU returns to the processor.
func (e *Engine) DisableGPU() {
	e.mu.Lock()
	defer e.mu.Unlock()
	e.gpu = gpuState{}
}

// GPU reports the graphics state: provider, mode ("" when off) and the last failure.
func (e *Engine) GPU() (provider, mode, failed string) {
	e.mu.Lock()
	defer e.mu.Unlock()
	return e.gpu.provider, e.gpu.mode, e.gpu.failed
}

// ModelDir is the folder the model files live in.
func (e *Engine) ModelDir() string { return e.modelDir }

// Runtime gives access to the processor library (for the graphics test).
func (e *Engine) Runtime() *ort.Runtime { return e.rt }

// NewEngine loads the ONNX Runtime library. threads <= 0 uses all processor cores.
func NewEngine(libPath, modelDir string, threads int) (*Engine, error) {
	rt, err := ort.Load(libPath)
	if err != nil {
		return nil, err
	}
	return &Engine{rt: rt, modelDir: modelDir, threads: threads, models: map[string]*model{}}, nil
}

// RuntimeVersion reports the ONNX Runtime version in use.
func (e *Engine) RuntimeVersion() string { return e.rt.Version() }

// SetThreads changes how many processor threads newly loaded networks use.
func (e *Engine) SetThreads(n int) {
	e.mu.Lock()
	defer e.mu.Unlock()
	if n != e.threads {
		e.threads = n
		for k, m := range e.models {
			m.sess.Close()
			delete(e.models, k)
		}
	}
}

// Available reports whether a model file is present (without verifying it).
func (e *Engine) Available(spec ModelSpec) bool {
	st, err := os.Stat(filepath.Join(e.modelDir, spec.File))
	return err == nil && !st.IsDir()
}

// VerifyFile checks a file against an expected SHA-256 checksum and returns its content.
func VerifyFile(path, wantSHA256 string) ([]byte, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	sum := sha256.Sum256(data)
	if got := hex.EncodeToString(sum[:]); got != wantSHA256 {
		return nil, fmt.Errorf("%s has been changed or is damaged (checksum %s…, expected %s…); please restore the original file",
			filepath.Base(path), got[:12], wantSHA256[:12])
	}
	return data, nil
}

func (e *Engine) load(spec ModelSpec) (*model, error) {
	if m, ok := e.models[spec.Key]; ok {
		return m, nil
	}
	data, err := VerifyFile(filepath.Join(e.modelDir, spec.File), spec.SHA256)
	if err != nil {
		return nil, fmt.Errorf("cannot use model %q: %w", spec.Name, err)
	}
	sess, err := e.rt.NewSession(data, "input", "output", e.threads)
	if err != nil {
		return nil, fmt.Errorf("cannot load model %q: %w", spec.Name, err)
	}
	if s := sess.InShape; s[1] != 4 || int(s[2]) != spec.DimF || int(s[3]) != dimT {
		sess.Close()
		return nil, fmt.Errorf("model %q has an unexpected shape %v", spec.Name, s)
	}
	m := &model{spec: spec, sess: sess, stft: dsp.NewSTFT(spec.NFFT, hop, dimT), out: make([]float32, len(sess.Input))}
	e.models[spec.Key] = m
	return m, nil
}

// Close releases all networks.
func (e *Engine) Close() {
	e.mu.Lock()
	defer e.mu.Unlock()
	for k, m := range e.models {
		m.sess.Close()
		delete(e.models, k)
	}
}

// Stereo is a pair of equally long channels.
type Stereo [2][]float32

// NewStereo allocates silent stereo audio.
func NewStereo(n int) Stereo { return Stereo{make([]float32, n), make([]float32, n)} }

// Len returns the number of samples per channel.
func (s Stereo) Len() int { return len(s[0]) }

func peakOf(s Stereo) float32 {
	var p float32
	for _, ch := range s {
		for _, v := range ch {
			if v < 0 {
				v = -v
			}
			if v > p {
				p = v
			}
		}
	}
	return p
}

func isSilent(chunk Stereo) bool {
	for _, ch := range chunk {
		for _, v := range ch {
			if v != 0 {
				return false
			}
		}
	}
	return true
}

// demix runs one network over a whole stereo signal (at ModelRate) and returns the stem the
// network produces, at the same level and length as the input.
//
// The signal is processed in overlapping windows of about six seconds; each window's result
// is faded in and out and the windows are blended, which hides the seams.
func (e *Engine) demix(ctx context.Context, spec ModelSpec, mix Stereo, overlap float64, progress func(done, total int)) (Stereo, error) {
	e.mu.Lock()
	defer e.mu.Unlock()
	m, err := e.load(spec)
	if err != nil {
		return Stereo{}, err
	}
	// A loaded network occupies over a gigabyte of memory while it runs, so it is released
	// as soon as its pass is finished; only one network is ever loaded at a time.
	defer func() {
		m.sess.Close()
		delete(e.models, spec.Key)
		debug.FreeOSMemory()
	}()
	n := mix.Len()
	peak := peakOf(mix)
	if n == 0 || peak == 0 {
		return NewStereo(n), nil
	}
	gain := inputPeak / peak

	chunk := m.stft.Chunk
	trim := spec.NFFT / 2
	gen := chunk - 2*trim
	pad := gen + trim - n%gen
	// The song is treated as if it had `trim` samples of silence in front of it and `pad`
	// samples after it; position p of that padded signal is sample p-trim of the song.
	total := trim + n + pad
	if overlap < 0 {
		overlap = 0
	}
	if overlap > 0.95 {
		overlap = 0.95
	}
	step := int((1 - overlap) * float64(chunk))
	if step < 1 {
		step = 1
	}
	result := NewStereo(total)
	weight := make([]float32, total)
	part := NewStereo(chunk)
	y := NewStereo(chunk)
	window := make([]float32, chunk)
	steps := (total + step - 1) / step

	for i, k := 0, 0; i < total; i, k = i+step, k+1 {
		if err := ctx.Err(); err != nil {
			return Stereo{}, err
		}
		end := i + chunk
		if end > total {
			end = total
		}
		size := end - i
		// copy song samples [i-trim, end-trim) into the chunk, silence elsewhere
		lo, hi := i-trim, end-trim
		srcLo, srcHi := lo, hi
		if srcLo < 0 {
			srcLo = 0
		}
		if srcHi > n {
			srcHi = n
		}
		for c := range part {
			clear(part[c])
			if srcHi > srcLo {
				copy(part[c][srcLo-lo:], mix[c][srcLo:srcHi])
			}
		}
		if !isSilent(part) {
			m.stft.Analyze(part, m.sess.Input, spec.DimF, gain)
			if err := m.sess.Run(m.out); err != nil {
				return Stereo{}, err
			}
			m.stft.Synthesize(m.out, spec.DimF, y)
		} else {
			for c := range y {
				clear(y[c])
			}
		}
		// symmetric Hann window over the part of the chunk that holds real signal
		if size == 1 {
			window[0] = 1
		} else {
			for j := 0; j < size; j++ {
				window[j] = float32(0.5 - 0.5*math.Cos(2*math.Pi*float64(j)/float64(size-1)))
			}
		}
		for c := range result {
			r := result[c][i:end]
			for j := range r {
				r[j] += y[c][j] * window[j]
			}
		}
		w := weight[i:end]
		for j := range w {
			w[j] += window[j]
		}
		if progress != nil {
			progress(k+1, steps)
		}
	}
	scale := spec.Compensate / gain
	var out Stereo
	for c := range result {
		o := result[c][trim : trim+n : trim+n]
		for j := range o {
			wgt := weight[trim+j]
			if wgt < 1e-8 {
				wgt = 1e-8
			}
			o[j] = o[j] / wgt * scale
		}
		out[c] = o
	}
	return out, nil
}
