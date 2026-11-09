package dsp

import (
	"math"
	"math/cmplx"
	"math/rand"
	"testing"
)

func naiveDFT(x []complex128) []complex128 {
	n := len(x)
	out := make([]complex128, n)
	for k := 0; k < n; k++ {
		var s complex128
		for j := 0; j < n; j++ {
			ang := -2 * math.Pi * float64(k*j%n) / float64(n)
			s += x[j] * complex(math.Cos(ang), math.Sin(ang))
		}
		out[k] = s
	}
	return out
}

func TestFFTAgainstNaive(t *testing.T) {
	rng := rand.New(rand.NewSource(1))
	for _, n := range []int{1, 2, 3, 4, 5, 6, 7, 8, 12, 15, 16, 30, 49, 60, 64, 77, 120, 128, 640, 768, 960, 1001} {
		x := make([]complex128, n)
		for i := range x {
			x[i] = complex(rng.NormFloat64(), rng.NormFloat64())
		}
		want := naiveDFT(x)
		f := NewFFT(n)
		got := make([]complex128, n)
		f.Forward(got, x)
		var maxErr float64
		for i := range got {
			if e := cmplx.Abs(got[i] - want[i]); e > maxErr {
				maxErr = e
			}
		}
		if maxErr > 1e-9*float64(n) {
			t.Errorf("n=%d forward error %g", n, maxErr)
		}
		back := make([]complex128, n)
		tmp := append([]complex128(nil), got...)
		f.Inverse(back, tmp)
		maxErr = 0
		for i := range back {
			if e := cmplx.Abs(back[i] - x[i]); e > maxErr {
				maxErr = e
			}
		}
		if maxErr > 1e-10*float64(n) {
			t.Errorf("n=%d roundtrip error %g", n, maxErr)
		}
	}
}

func TestFFTModelSizes(t *testing.T) {
	rng := rand.New(rand.NewSource(2))
	for _, n := range []int{5120, 6144, 7680} {
		x := make([]complex128, n)
		for i := range x {
			x[i] = complex(rng.NormFloat64(), 0)
		}
		f := NewFFT(n)
		got := make([]complex128, n)
		f.Forward(got, x)
		// spot-check some bins against the definition
		for _, k := range []int{0, 1, 2, 3, 17, n/2 - 1, n / 2, n - 1} {
			var s complex128
			for j := 0; j < n; j++ {
				ang := -2 * math.Pi * float64((k*j)%n) / float64(n)
				s += x[j] * complex(math.Cos(ang), math.Sin(ang))
			}
			if e := cmplx.Abs(got[k] - s); e > 1e-7 {
				t.Errorf("n=%d bin %d error %g", n, k, e)
			}
		}
		back := make([]complex128, n)
		f.Inverse(back, got)
		for i := range back {
			if e := cmplx.Abs(back[i] - x[i]); e > 1e-9 {
				t.Fatalf("n=%d roundtrip error %g at %d", n, e, i)
			}
		}
	}
}

func BenchmarkFFT6144(b *testing.B) {
	n := 6144
	f := NewFFT(n)
	x := make([]complex128, n)
	for i := range x {
		x[i] = complex(float64(i%13), 0)
	}
	out := make([]complex128, n)
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		f.Forward(out, x)
	}
}
