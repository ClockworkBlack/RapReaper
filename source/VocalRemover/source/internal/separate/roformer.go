package separate

import (
	"context"
	"errors"
	"fmt"
	"math"
	"path/filepath"
	"runtime/debug"
	"time"

	"vocalremover/internal/dsp"
	"vocalremover/internal/ort"
)

// The Roformer family: Band-Split (BS) and Mel-Band Roformer vocal models (Lu, Wang et al., ByteDance
// 2023; open implementations by Phil Wang and Roman Solovyev). Each ONNX file holds the network between
// the STFT and the inverse STFT with fixed shapes: input 'spec' (1, 2, 1025, T, 2), output 'masked' of the
// same shape, T frames of hop 441 at 44.1 kHz. The files are built from the public checkpoints by
// tools/build_roformer_onnx.py in the source folder.

// BSRoformer is the ViperX checkpoint "model_bs_roformer_ep_317_sdr_12.9755" (published through the
// Ultimate Vocal Remover project): the strongest model of version 1.3, kept for the ensemble mode and
// for anyone who wants the previous default.
var BSRoformer = ModelSpec{
	Key: "bs_roformer_1297", Name: "BS-Roformer ViperX 1297", File: "BS-Roformer-1297-int8.onnx",
	SHA256:   bsRoformerSHA256,
	Vocals:   true,
	Roformer: true,
}

// MelRoformer is the Mel-Band Roformer vocal model "Kim Mel-Band Roformer FT2" (Kimberley Jensen's
// model, fine-tuned by unwa): measured within 0.3 dB of the BS-Roformer, in well under half the
// processing time. Its graphics-processor twin (float16) is generated from it on this computer.
var MelRoformer = ModelSpec{
	Key: "mel_roformer_kim_ft2", Name: "Mel-Roformer Kim FT2", File: "MelRoformer-KimFT2-int8.onnx",
	SHA256:   melRoformerSHA256,
	Vocals:   true,
	Roformer: true,
	GPU: &ModelSpec{
		Key: "mel_roformer_kim_ft2_gpu", Name: "Mel-Roformer Kim FT2 (graphics)", File: "MelRoformer-KimFT2-fp16.onnx",
		SHA256:   melRoformerGPUSHA256,
		Vocals:   true,
		Roformer: true,
	},
}

const (
	rofNFFT = 2048
	rofHop  = 441
	rofBins = rofNFFT/2 + 1
	// Thorough: windows advance by half a window; each is faded with a Hamming window and the fades are
	// blended, as in the reference implementation (its num_overlap=2 setting).
	rofOverlap = 2
	// Fast: windows overlap by one second, joined with a raised-cosine crossfade. That needs about 43%
	// fewer network runs than Thorough and measured the same within 0.1 dB.
	rofFastFade = 44100
)

// rofPlan says how the network's windows are laid over the signal.
type rofPlan struct {
	fast bool // crossfade plan (true) or Hamming-weighted half-window steps (false)
}

var (
	rofPlanFull = rofPlan{fast: false}
	rofPlanFast = rofPlan{fast: true}
)

// ErrGPU reports that the graphics processor could not be used; the caller falls back to the
// processor. The message explains what happened.
type ErrGPU struct{ Err error }

func (e *ErrGPU) Error() string { return e.Err.Error() }
func (e *ErrGPU) Unwrap() error { return e.Err }

// rofSession is a loaded Roformer network plus the knowledge of where it runs.
type rofSession struct {
	sess   *ort.Session
	frames int
	chunk  int
	gpu    bool
	mode   string // GPU mode used ("fused"/"unfused"), for the report
}

// openRoformer loads a Roformer network, on the graphics processor if the engine was told to use it
// (and the model has a graphics twin). A graphics failure at this point is an *ErrGPU.
func (e *Engine) openRoformer(spec ModelSpec, wantGPU bool) (*rofSession, error) {
	if wantGPU && spec.GPU != nil && e.gpu.mode != "" {
		s, err := e.openRoformerGPU(*spec.GPU)
		if err == nil {
			return s, nil
		}
		return nil, &ErrGPU{Err: err}
	}
	data, err := VerifyFile(filepath.Join(e.modelDir, spec.File), spec.SHA256)
	if err != nil {
		return nil, fmt.Errorf("cannot use model %q: %w", spec.Name, err)
	}
	sess, err := e.rt.NewSessionWith(data, "spec", "masked", ort.Options{Threads: e.threads, KeepMemPattern: true})
	data = nil
	if err != nil {
		return nil, fmt.Errorf("cannot load model %q: %w", spec.Name, err)
	}
	return rofCheck(sess, spec)
}

func (e *Engine) openRoformerGPU(spec ModelSpec) (*rofSession, error) {
	rt := e.gpu.rt
	if rt == nil {
		return nil, errors.New("the graphics library is not loaded")
	}
	data, err := VerifyFile(filepath.Join(e.modelDir, spec.File), spec.SHA256)
	if err != nil {
		return nil, fmt.Errorf("cannot use model %q: %w", spec.Name, err)
	}
	opts := ort.Options{Threads: e.threads, KeepMemPattern: false, Provider: e.gpu.provider, DeviceID: e.gpu.device,
		Config: map[string]string{}}
	switch e.gpu.provider {
	case "DML":
		if e.gpu.mode == "unfused" {
			opts.Config["ep.dml.disable_graph_fusion"] = "1"
		}
	case "CoreML":
		opts.ProviderOptions = map[string]string{"ModelFormat": "MLProgram", "MLComputeUnits": "ALL", "RequireStaticInputShapes": "1"}
	}
	sess, err := rt.NewSessionWith(data, "spec", "masked", opts)
	data = nil
	if err != nil {
		return nil, fmt.Errorf("the graphics processor could not load %q: %w", spec.Name, err)
	}
	s, err := rofCheck(sess, spec)
	if err != nil {
		return nil, err
	}
	s.gpu, s.mode = true, e.gpu.mode
	return s, nil
}

func rofCheck(sess *ort.Session, spec ModelSpec) (*rofSession, error) {
	sh := sess.InShape
	if len(sh) != 5 || sh[1] != 2 || sh[2] != rofBins || sh[4] != 2 || sh[3] < 101 || sh[3] > 4001 {
		sess.Close()
		return nil, fmt.Errorf("model %q has an unexpected shape %v", spec.Name, sh)
	}
	T := int(sh[3])
	return &rofSession{sess: sess, frames: T, chunk: rofHop * (T - 1)}, nil
}

func (s *rofSession) close() {
	if s != nil && s.sess != nil {
		s.sess.Close()
		s.sess = nil
	}
}

// RofReport is what a Roformer pass reports back about where it ran.
type RofReport struct {
	GPU      bool
	GPUMode  string
	Fallback string // why the graphics processor was given up on, if it was
	Windows  int
	Seconds  float64 // network time
}

// demixRoformer runs a Roformer network over a whole stereo signal (at ModelRate) and returns its vocal
// estimate, at the same level and length as the input. With the graphics processor on, a failure there
// (at load or in the middle) falls back to the processor and the report says so.
func (e *Engine) demixRoformer(ctx context.Context, spec ModelSpec, mix Stereo, plan rofPlan, wantGPU bool, progress func(done, total int)) (Stereo, *RofReport, error) {
	e.mu.Lock()
	defer e.mu.Unlock()
	report := &RofReport{}
	s, err := e.openRoformer(spec, wantGPU)
	if err != nil {
		var ge *ErrGPU
		if !errors.As(err, &ge) {
			return Stereo{}, nil, err
		}
		report.Fallback = ge.Err.Error()
		e.gpu.failed = report.Fallback
		if s, err = e.openRoformer(spec, false); err != nil {
			return Stereo{}, nil, err
		}
	}
	defer func() {
		s.close()
		debug.FreeOSMemory()
	}()
	report.GPU, report.GPUMode = s.gpu, s.mode
	n := mix.Len()
	peak := peakOf(mix)
	if n == 0 || peak == 0 {
		return NewStereo(n), report, nil
	}
	gain := inputPeak / peak

	chunk := s.chunk
	stft := dsp.NewSTFT(rofNFFT, rofHop, s.frames)
	part := NewStereo(chunk)
	y := NewStereo(chunk)
	hamming := make([]float32, chunk)
	for i := range hamming {
		hamming[i] = float32(0.54 - 0.46*math.Cos(2*math.Pi*float64(i)/float64(chunk-1)))
	}
	// Window start positions: every step samples, plus a last one that ends exactly at the end of the
	// signal (a short signal is padded with silence to one window).
	step := chunk / rofOverlap
	if plan.fast {
		step = chunk - rofFastFade
	}
	var starts []int
	for off := 0; off < n; off += step {
		if off+chunk >= n {
			tail := n - chunk
			if tail < 0 {
				tail = 0
			}
			if len(starts) == 0 || starts[len(starts)-1] != tail {
				starts = append(starts, tail)
			}
			break
		}
		starts = append(starts, off)
	}
	// windowFor returns the weighting of window k. With the crossfade plan each window is flat and
	// fades in over the part it shares with the previous window and out over the part it shares with
	// the next one, so the weights always add up to one.
	windowFor := func(k int) []float32 {
		if !plan.fast {
			return hamming
		}
		w := make([]float32, chunk)
		for i := range w {
			w[i] = 1
		}
		ramp := func(length int, i int) float32 {
			return float32(0.5 - 0.5*math.Cos(math.Pi*(float64(i)+0.5)/float64(length)))
		}
		if k > 0 {
			if ov := starts[k-1] + chunk - starts[k]; ov > 0 {
				for i := 0; i < ov && i < chunk; i++ {
					w[i] = ramp(ov, i)
				}
			}
		}
		if k+1 < len(starts) {
			if ov := starts[k] + chunk - starts[k+1]; ov > 0 {
				for i := 0; i < ov && i < chunk; i++ {
					w[chunk-1-i] = ramp(ov, i)
				}
			}
		}
		return w
	}
	result := NewStereo(n)
	weight := make([]float32, n)
	out := make([]float32, len(s.sess.Input))
	report.Windows = len(starts)
	for k, st := range starts {
		window := windowFor(k)
		if err := ctx.Err(); err != nil {
			return Stereo{}, report, err
		}
		size := n - st
		if size > chunk {
			size = chunk
		}
		for c := range part {
			clear(part[c])
			copy(part[c], mix[c][st:st+size])
		}
		if !isSilent(part) {
			stft.AnalyzeFull(part, s.sess.Input, gain)
			began := time.Now()
			err := s.sess.Run(out)
			if err != nil && s.gpu {
				// The graphics processor failed in the middle: finish on the processor.
				report.Fallback = "the graphics processor failed while processing (" + err.Error() + ")"
				e.gpu.failed = report.Fallback
				s.close()
				if s, err = e.openRoformer(spec, false); err != nil {
					return Stereo{}, report, err
				}
				report.GPU, report.GPUMode = false, ""
				out = make([]float32, len(s.sess.Input))
				stft.AnalyzeFull(part, s.sess.Input, gain)
				err = s.sess.Run(out)
			}
			if err != nil {
				return Stereo{}, report, err
			}
			report.Seconds += time.Since(began).Seconds()
			stft.SynthesizeFull(out, y)
		} else {
			for c := range y {
				clear(y[c])
			}
		}
		for c := range result {
			r := result[c][st : st+size]
			for j := range r {
				r[j] += y[c][j] * window[j]
			}
		}
		w := weight[st : st+size]
		for j := range w {
			w[j] += window[j]
		}
		if progress != nil {
			progress(k+1, len(starts))
		}
	}
	for c := range result {
		o := result[c]
		for j := range o {
			wgt := weight[j]
			if wgt < 1e-8 {
				wgt = 1e-8
			}
			o[j] = o[j] / wgt / gain
		}
	}
	return result, report, nil
}
