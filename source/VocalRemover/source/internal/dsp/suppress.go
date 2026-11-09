package dsp

import (
	"math"
	"math/cmplx"
)

// SuppressBlend builds the most vocal-free instrumental it can from two instrumental
// estimates a and b of the same mix. It works on short overlapping blocks in the frequency
// domain and, for every frequency in every block:
//
//  1. keeps whichever estimate is quieter there (a voice that one model let through is
//     usually missing from the other), and
//  2. turns that down further in proportion to how much of the mix at that spot the
//     models attributed to the voice (strength 0 = not at all, 1 = fully).
//
// Step 2 also removes a little of the music that sits directly under the voice; that is
// the price of this mode.
func SuppressBlend(mix, a, b []float32, strength float64) []float32 {
	const n = 2048
	const hopSize = n / 4
	const norm = 1 / 1.5 // Hann analysis x Hann synthesis at 75 % overlap sums to 1.5
	fft := NewFFT(n)
	win := make([]float64, n)
	for i := range win {
		win[i] = 0.5 - 0.5*math.Cos(2*math.Pi*float64(i)/float64(n))
	}
	total := len(mix) + 2*n
	frames := (total-n)/hopSize + 1
	acc := make([]float64, total+n)
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
		sm := make([]complex128, n)
		sa := make([]complex128, n)
		sb := make([]complex128, n)
		td := make([]complex128, n)
		buf := make([]float64, (hi-lo-1)*hopSize+n)
		load := func(x []float32, start int, out []complex128) {
			for i := 0; i < n; i++ {
				j := start + i
				var v float64
				if j >= 0 && j < len(x) {
					v = float64(x[j])
				}
				in[i] = complex(v*win[i], 0)
			}
			fft.Forward(out, in)
		}
		for t := lo; t < hi; t++ {
			start := t*hopSize - n
			load(mix, start, sm)
			load(a, start, sa)
			load(b, start, sb)
			for k := 0; k <= n/2; k++ {
				inst := sa[k]
				if cmplx.Abs(sb[k]) < cmplx.Abs(inst) {
					inst = sb[k]
				}
				voc := sm[k] - inst
				pv := real(voc)*real(voc) + imag(voc)*imag(voc)
				pi := real(inst)*real(inst) + imag(inst)*imag(inst)
				g := 1 - strength*pv/(pv+pi+1e-12)
				if g < 0 {
					g = 0
				}
				inst = complex(real(inst)*g, imag(inst)*g)
				sa[k] = inst
				if k > 0 && k < n/2 {
					sa[n-k] = complex(real(inst), -imag(inst))
				}
			}
			fft.Inverse(td, sa)
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
		dst := acc[s.lo:]
		for i, v := range s.buf {
			dst[i] += v
		}
	}
	out := make([]float32, len(mix))
	for i := range out {
		out[i] = float32(acc[i+n] * norm)
	}
	return out
}
