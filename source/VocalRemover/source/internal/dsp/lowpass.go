package dsp

import "math"

// LowPass removes everything above the given frequency from x (in place is not supported;
// a new slice is returned). The transition from "kept" to "removed" is a smooth half-cosine
// between passHz and stopHz. It works block-wise in the frequency domain with overlapping
// Hann windows, so it adds no delay and no phase shift.
func LowPass(x []float32, rate int, passHz, stopHz float64) []float32 {
	const n = 4096
	const hopSize = n / 4
	fft := NewFFT(n)
	win := make([]float64, n)
	for i := range win {
		win[i] = 0.5 - 0.5*math.Cos(2*math.Pi*float64(i)/float64(n))
	}
	gain := make([]float64, n/2+1)
	for k := range gain {
		f := float64(k) * float64(rate) / float64(n)
		switch {
		case f <= passHz:
			gain[k] = 1
		case f >= stopHz:
			gain[k] = 0
		default:
			gain[k] = 0.5 + 0.5*math.Cos(math.Pi*(f-passHz)/(stopHz-passHz))
		}
	}
	// With a Hann window applied twice (analysis and synthesis) at 75 % overlap the windows
	// sum to exactly 1.5.
	const norm = 1 / 1.5
	total := len(x) + 2*n
	frames := (total-n)/hopSize + 1
	acc := make([]float64, total+n)
	// Frames overlap, so each worker accumulates into its own buffer; buffers are summed after.
	type span struct {
		lo  int
		buf []float64
	}
	var spans []span
	parts := make(chan span, 64)
	done := make(chan struct{})
	go func() {
		for s := range parts {
			spans = append(spans, s)
		}
		close(done)
	}()
	parallelFor(frames, func(_, lo, hi int) {
		in := make([]complex128, n)
		sp := make([]complex128, n)
		td := make([]complex128, n)
		buf := make([]float64, (hi-lo-1)*hopSize+n)
		for t := lo; t < hi; t++ {
			start := t*hopSize - n // position in x of this frame's first sample
			for i := 0; i < n; i++ {
				j := start + i
				var v float64
				if j >= 0 && j < len(x) {
					v = float64(x[j])
				}
				in[i] = complex(v*win[i], 0)
			}
			fft.Forward(sp, in)
			for k := 0; k <= n/2; k++ {
				g := gain[k]
				sp[k] = complex(real(sp[k])*g, imag(sp[k])*g)
				if k > 0 && k < n/2 {
					sp[n-k] = complex(real(sp[k]), -imag(sp[k]))
				}
			}
			fft.Inverse(td, sp)
			o := buf[(t-lo)*hopSize:]
			for i := 0; i < n; i++ {
				o[i] += real(td[i]) * win[i]
			}
		}
		parts <- span{lo: lo * hopSize, buf: buf}
	})
	close(parts)
	<-done
	for _, s := range spans {
		a := acc[s.lo:]
		for i, v := range s.buf {
			a[i] += v
		}
	}
	out := make([]float32, len(x))
	for i := range out {
		out[i] = float32(acc[i+n] * norm)
	}
	return out
}
