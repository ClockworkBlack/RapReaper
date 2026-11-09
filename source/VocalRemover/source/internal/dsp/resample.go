package dsp

import "math"

func gcd(a, b int) int {
	for b != 0 {
		a, b = b, a%b
	}
	return a
}

// bessel I0, used by the Kaiser window.
func besselI0(x float64) float64 {
	sum, term := 1.0, 1.0
	q := x * x / 4
	for k := 1; k < 200; k++ {
		term *= q / float64(k*k)
		sum += term
		if term < sum*1e-17 {
			break
		}
	}
	return sum
}

const (
	resampleZeros = 64    // filter half-width, in zero crossings of the band-limiting sinc
	resampleBeta  = 10.06 // Kaiser window shape: about 100 dB stop-band rejection
	resampleRoll  = 0.95  // cut-off as a fraction of the lower Nyquist frequency
)

// Resample converts x from one sample rate to another with a windowed-sinc polyphase filter
// (zero phase: output sample n sits exactly at input time n*from/to).
func Resample(x []float32, from, to int) []float32 {
	if from == to || len(x) == 0 {
		return append([]float32(nil), x...)
	}
	g := gcd(from, to)
	up, down := to/g, from/g // output n is at input position n*down/up
	ratio := math.Min(1, float64(to)/float64(from))
	cut := ratio * resampleRoll
	half := int(math.Ceil(resampleZeros / ratio))
	taps := 2 * half
	i0b := besselI0(resampleBeta)

	coeff := func(phase float64, h []float64) {
		// tap j (0..taps-1) multiplies input sample base-half+1+j; its distance from the
		// output instant is (j - half + 1) - phase input samples.
		var sum float64
		for j := 0; j < taps; j++ {
			u := float64(j-half+1) - phase
			r := u / float64(half)
			var w float64
			if r > -1 && r < 1 {
				w = besselI0(resampleBeta*math.Sqrt(1-r*r)) / i0b
			}
			var sinc float64
			if a := math.Pi * cut * u; math.Abs(a) < 1e-12 {
				sinc = 1
			} else {
				sinc = math.Sin(a) / a
			}
			h[j] = cut * sinc * w
			sum += h[j]
		}
		for j := range h {
			h[j] /= sum
		}
	}

	var table [][]float32
	if up <= 4096 {
		table = make([][]float32, up)
		tmp := make([]float64, taps)
		for p := 0; p < up; p++ {
			coeff(float64(p)/float64(up), tmp)
			row := make([]float32, taps)
			for j, v := range tmp {
				row[j] = float32(v)
			}
			table[p] = row
		}
	}

	nOut := int((int64(len(x))*int64(up) + int64(down) - 1) / int64(down))
	out := make([]float32, nOut)
	nIn := len(x)
	parallelFor(nOut, func(_, lo, hi int) {
		var tmp []float64
		var row []float32
		if table == nil {
			tmp = make([]float64, taps)
			row = make([]float32, taps)
		}
		for n := lo; n < hi; n++ {
			pos := int64(n) * int64(down)
			base := int(pos / int64(up))
			ph := int(pos % int64(up))
			var h []float32
			if table != nil {
				h = table[ph]
			} else {
				coeff(float64(ph)/float64(up), tmp)
				for j, v := range tmp {
					row[j] = float32(v)
				}
				h = row
			}
			first := base - half + 1
			var acc float64
			if first >= 0 && first+taps <= nIn {
				seg := x[first : first+taps]
				for j, c := range h {
					acc += float64(c) * float64(seg[j])
				}
			} else {
				for j, c := range h {
					k := first + j
					if k >= 0 && k < nIn {
						acc += float64(c) * float64(x[k])
					}
				}
			}
			out[n] = float32(acc)
		}
	})
	return out
}
