// Package dsp holds the signal processing the separator needs: a mixed-radix FFT, the
// short-time Fourier transform pair used by the models, and a sample-rate converter.
package dsp

import (
	"math"
	"math/cmplx"
)

// FFT is a complex FFT plan for one size. A plan is read-only after creation and may be
// shared between goroutines; each caller supplies its own buffers.
type FFT struct {
	n       int
	factors []int // pairs (p, m): radix and remaining length, as in Kiss FFT
	tw      []complex128
	maxP    int
}

// NewFFT prepares a transform of length n (any n >= 1; fastest when n factors into 2, 3 and 5).
func NewFFT(n int) *FFT {
	f := &FFT{n: n, tw: make([]complex128, n)}
	for k := 0; k < n; k++ {
		ang := -2 * math.Pi * float64(k) / float64(n)
		f.tw[k] = complex(math.Cos(ang), math.Sin(ang))
	}
	// Factor n, preferring radix 4, then 2, 3, 5, 7, ...
	rem := n
	p := 4
	for rem > 1 {
		for rem%p != 0 {
			switch p {
			case 4:
				p = 2
			case 2:
				p = 3
			default:
				p += 2
			}
			if p*p > rem {
				p = rem
			}
		}
		rem /= p
		f.factors = append(f.factors, p, rem)
		if p > f.maxP {
			f.maxP = p
		}
	}
	if n == 1 {
		f.factors = []int{1, 1}
		f.maxP = 1
	}
	return f
}

// Len returns the transform length.
func (f *FFT) Len() int { return f.n }

// Forward computes out[k] = sum_n in[n] * exp(-2*pi*i*k*n/N). in and out must not overlap.
func (f *FFT) Forward(out, in []complex128) {
	if f.n == 1 {
		out[0] = in[0]
		return
	}
	var scratch [16]complex128
	sc := scratch[:]
	if f.maxP > len(sc) {
		sc = make([]complex128, f.maxP)
	}
	f.work(out[:f.n], in, 0, 1, f.factors, sc)
}

// Inverse computes the unscaled inverse transform divided by N (so Inverse(Forward(x)) == x).
// in is overwritten. in and out must not overlap.
func (f *FFT) Inverse(out, in []complex128) {
	for i := range in[:f.n] {
		in[i] = cmplx.Conj(in[i])
	}
	f.Forward(out, in)
	s := 1 / float64(f.n)
	for i := range out[:f.n] {
		out[i] = complex(real(out[i])*s, -imag(out[i])*s)
	}
}

func (f *FFT) work(out []complex128, in []complex128, inOff, fstride int, factors []int, scratch []complex128) {
	p, m := factors[0], factors[1]
	if m == 1 {
		for i := 0; i < p; i++ {
			out[i] = in[inOff+i*fstride]
		}
	} else {
		for i := 0; i < p; i++ {
			f.work(out[i*m:(i+1)*m], in, inOff+i*fstride, fstride*p, factors[2:], scratch)
		}
	}
	switch p {
	case 2:
		f.bfly2(out, fstride, m)
	case 4:
		f.bfly4(out, fstride, m)
	default:
		f.bflyGeneric(out, fstride, m, p, scratch)
	}
}

func (f *FFT) bfly2(out []complex128, fstride, m int) {
	for i := 0; i < m; i++ {
		t := out[i+m] * f.tw[i*fstride]
		out[i+m] = out[i] - t
		out[i] += t
	}
}

func (f *FFT) bfly4(out []complex128, fstride, m int) {
	m2, m3 := 2*m, 3*m
	for i := 0; i < m; i++ {
		s0 := out[i+m] * f.tw[i*fstride]
		s1 := out[i+m2] * f.tw[i*fstride*2]
		s2 := out[i+m3] * f.tw[i*fstride*3]
		s5 := out[i] - s1
		a := out[i] + s1
		s3 := s0 + s2
		s4 := s0 - s2
		out[i+m2] = a - s3
		out[i] = a + s3
		// forward transform: multiply s4 by -i
		out[i+m] = complex(real(s5)+imag(s4), imag(s5)-real(s4))
		out[i+m3] = complex(real(s5)-imag(s4), imag(s5)+real(s4))
	}
}

func (f *FFT) bflyGeneric(out []complex128, fstride, m, p int, scratch []complex128) {
	n := f.n
	for u := 0; u < m; u++ {
		k := u
		for q1 := 0; q1 < p; q1++ {
			scratch[q1] = out[k]
			k += m
		}
		k = u
		for q1 := 0; q1 < p; q1++ {
			twidx := 0
			acc := scratch[0]
			for q := 1; q < p; q++ {
				twidx += fstride * k
				if twidx >= n {
					twidx %= n
				}
				acc += scratch[q] * f.tw[twidx]
			}
			out[k] = acc
			k += m
		}
	}
}
