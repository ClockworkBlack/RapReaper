package dsp

import (
	"math"
	"runtime"
	"sync"
)

// STFT converts between a fixed-length stereo audio chunk and the spectrogram layout the
// MDX-Net models use. It reproduces torch.stft / torch.istft with center=True, reflect
// padding and a periodic Hann window, which is what the models were trained with.
type STFT struct {
	NFFT   int
	Hop    int
	Frames int // number of time frames per chunk (the models use 256)
	Chunk  int // samples per chunk = Hop * (Frames - 1)

	fft    *FFT
	win    []float64
	invEnv []float64 // 1 / (sum of squared windows) for the kept part of the overlap-add
	frames [2][]float32
}

// NewSTFT builds the transform for one model geometry.
func NewSTFT(nfft, hop, frames int) *STFT {
	s := &STFT{NFFT: nfft, Hop: hop, Frames: frames, Chunk: hop * (frames - 1), fft: NewFFT(nfft)}
	s.win = make([]float64, nfft)
	for i := range s.win {
		s.win[i] = 0.5 - 0.5*math.Cos(2*math.Pi*float64(i)/float64(nfft))
	}
	total := nfft + hop*(frames-1)
	env := make([]float64, total)
	for t := 0; t < frames; t++ {
		for i, w := range s.win {
			env[t*hop+i] += w * w
		}
	}
	s.invEnv = make([]float64, s.Chunk)
	for i := range s.invEnv {
		e := env[i+nfft/2]
		if e < 1e-11 {
			e = 1e-11
		}
		s.invEnv[i] = 1 / e
	}
	for c := range s.frames {
		s.frames[c] = make([]float32, frames*nfft)
	}
	return s
}

func parallelFor(n int, body func(worker, lo, hi int)) {
	workers := runtime.GOMAXPROCS(0)
	if workers > n {
		workers = n
	}
	if workers < 1 {
		workers = 1
	}
	var wg sync.WaitGroup
	for w := 0; w < workers; w++ {
		lo, hi := n*w/workers, n*(w+1)/workers
		wg.Add(1)
		go func(w, lo, hi int) {
			defer wg.Done()
			body(w, lo, hi)
		}(w, lo, hi)
	}
	wg.Wait()
}

// Analyze writes the spectrogram of a stereo chunk into dst using the model layout
// [L.re, L.im, R.re, R.im] x dimF x Frames. Only the lowest dimF bins are kept and the
// three lowest bins are zeroed (as in the reference implementation). gain scales the input.
func (s *STFT) Analyze(chunk [2][]float32, dst []float32, dimF int, gain float32) {
	n, hop, T := s.NFFT, s.Hop, s.Frames
	L := s.Chunk
	half := n / 2
	g := float64(gain)
	parallelFor(2*T, func(_, lo, hi int) {
		in := make([]complex128, n)
		out := make([]complex128, n)
		for job := lo; job < hi; job++ {
			c, t := job/T, job%T
			x := chunk[c]
			start := t*hop - half
			for i := 0; i < n; i++ {
				j := start + i
				if j < 0 {
					j = -j
				} else if j >= L {
					j = 2*(L-1) - j
				}
				in[i] = complex(float64(x[j])*g*s.win[i], 0)
			}
			s.fft.Forward(out, in)
			re := dst[(2*c)*dimF*T:]
			im := dst[(2*c+1)*dimF*T:]
			for f := 0; f < dimF; f++ {
				if f < 3 {
					re[f*T+t], im[f*T+t] = 0, 0
					continue
				}
				re[f*T+t] = float32(real(out[f]))
				im[f*T+t] = float32(imag(out[f]))
			}
		}
	})
}

// Synthesize turns a model output spectrogram (same layout as Analyze) back into a stereo
// chunk of s.Chunk samples per channel. Bins above dimF are treated as silent.
func (s *STFT) Synthesize(src []float32, dimF int, out [2][]float32) {
	n, hop, T := s.NFFT, s.Hop, s.Frames
	half := n / 2
	parallelFor(2*T, func(_, lo, hi int) {
		spec := make([]complex128, n)
		td := make([]complex128, n)
		for job := lo; job < hi; job++ {
			c, t := job/T, job%T
			re := src[(2*c)*dimF*T:]
			im := src[(2*c+1)*dimF*T:]
			for i := range spec {
				spec[i] = 0
			}
			for f := 0; f < dimF && f <= half; f++ {
				v := complex(float64(re[f*T+t]), float64(im[f*T+t]))
				if f == 0 || f == half {
					spec[f] = complex(real(v), 0)
					continue
				}
				spec[f] = v
				spec[n-f] = complex(real(v), -imag(v))
			}
			s.fft.Inverse(td, spec)
			fr := s.frames[c][t*n : (t+1)*n]
			for i := 0; i < n; i++ {
				fr[i] = float32(real(td[i]) * s.win[i])
			}
		}
	})
	parallelFor(2, func(_, lo, hi int) {
		for c := lo; c < hi; c++ {
			acc := make([]float64, n+hop*(T-1))
			for t := 0; t < T; t++ {
				fr := s.frames[c][t*n : (t+1)*n]
				a := acc[t*hop : t*hop+n]
				for i, v := range fr {
					a[i] += float64(v)
				}
			}
			o := out[c]
			for i := 0; i < s.Chunk; i++ {
				o[i] = float32(acc[i+half] * s.invEnv[i])
			}
		}
	})
}
