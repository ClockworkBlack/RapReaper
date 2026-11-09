package separate

import (
	"errors"
	"math"
	"time"

	"vocalremover/internal/dsp"
)

// TestWindow runs the Mel-Roformer over one window of a fixed test signal (a melody of tones over a
// beat-like pulse and some noise, deterministic) and returns the network's raw output and the time the
// network took. With the graphics processor enabled the window runs there. The window is run twice and
// only the second run is timed, on the processor as well as on the graphics processor, so that neither
// side's time includes warming up (on the graphics processor the first run also sets it up). The
// 'reference' argument is accepted for symmetry and ignored.
func (e *Engine) TestWindow(_ []float32, _ string) ([]float32, float64, error) {
	e.mu.Lock()
	defer e.mu.Unlock()
	spec := MelRoformer
	useGPU := e.gpu.mode != ""
	s, err := e.openRoformer(spec, useGPU)
	if err != nil {
		var ge *ErrGPU
		if errors.As(err, &ge) {
			return nil, 0, ge.Err
		}
		return nil, 0, err
	}
	defer s.close()
	chunk := s.chunk
	part := NewStereo(chunk)
	for i := 0; i < chunk; i++ {
		t := float64(i) / ModelRate
		note := math.Floor(t * 2) // a new note every half second
		f := 220.0 * math.Pow(2, math.Mod(note*5, 12)/12)
		v := 0.25*math.Sin(2*math.Pi*f*t) + 0.12*math.Sin(2*math.Pi*2*f*t+0.3) + 0.06*math.Sin(2*math.Pi*3*f*t)
		beat := math.Exp(-8*math.Mod(t, 0.5)) * math.Sin(2*math.Pi*55*t)
		noise := 0.02 * math.Sin(1e5*t*t) // a cheap deterministic hiss
		part[0][i] = float32(v*0.6 + beat*0.5 + noise)
		part[1][i] = float32(v*0.5 + beat*0.5 - noise)
	}
	stft := dsp.NewSTFT(rofNFFT, rofHop, s.frames)
	stft.AnalyzeFull(part, s.sess.Input, inputPeak/peakOf(part))
	out := make([]float32, len(s.sess.Input))
	var took float64
	for k := 0; k < 2; k++ {
		began := time.Now()
		if err := s.sess.Run(out); err != nil {
			return nil, 0, err
		}
		took = time.Since(began).Seconds()
	}
	return out, took, nil
}
