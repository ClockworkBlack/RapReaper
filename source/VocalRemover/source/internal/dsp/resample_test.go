package dsp

import (
	"math"
	"testing"
)

func tone(freq float64, sr, n int) []float32 {
	x := make([]float32, n)
	for i := range x {
		x[i] = float32(0.5 * math.Sin(2*math.Pi*freq*float64(i)/float64(sr)))
	}
	return x
}

func rmsDB(x []float32) float64 {
	var s float64
	for _, v := range x {
		s += float64(v) * float64(v)
	}
	return 10 * math.Log10(s/float64(len(x))+1e-30)
}

func TestResampleTone(t *testing.T) {
	for _, pair := range [][2]int{{48000, 44100}, {44100, 48000}, {96000, 44100}, {22050, 44100}, {32000, 44100}, {44100, 88200}, {8000, 44100}} {
		from, to := pair[0], pair[1]
		for _, f := range []float64{100, 1000, 3000} {
			x := tone(f, from, from) // one second
			y := Resample(x, from, to)
			if want := to; y == nil || len(y) < want-1 || len(y) > want+1 {
				t.Fatalf("%d->%d: length %d", from, to, len(y))
			}
			ref := tone(f, to, len(y))
			skip := to / 10
			d := make([]float32, 0, len(y))
			for i := skip; i < len(y)-skip; i++ {
				d = append(d, y[i]-ref[i])
			}
			snr := rmsDB(ref[skip:len(ref)-skip]) - rmsDB(d)
			if snr < 90 {
				t.Errorf("%d->%d tone %g Hz: SNR %.1f dB", from, to, f, snr)
			}
		}
	}
}

func TestResampleRejectsAliases(t *testing.T) {
	// 23 kHz in a 48 kHz file cannot exist at 44.1 kHz and must be removed, not folded back.
	x := tone(23000, 48000, 48000)
	y := Resample(x, 48000, 44100)
	if lvl := rmsDB(y[4410:len(y)-4410]) - rmsDB(x); lvl > -90 {
		t.Errorf("alias only %.1f dB down", lvl)
	}
	// 15 kHz passes untouched through a down+up round trip
	x = tone(15000, 48000, 48000)
	z := Resample(Resample(x, 48000, 44100), 44100, 48000)
	d := make([]float32, 0, len(x))
	for i := 4800; i < len(x)-4800; i++ {
		d = append(d, z[i]-x[i])
	}
	if snr := rmsDB(x) - rmsDB(d); snr < 85 {
		t.Errorf("round trip SNR %.1f dB", snr)
	}
}

func TestLowPass(t *testing.T) {
	sr := 44100
	low := tone(1000, sr, sr)
	high := tone(19000, sr, sr)
	mix := make([]float32, sr)
	for i := range mix {
		mix[i] = low[i] + high[i]
	}
	y := LowPass(mix, sr, 16800, 17400)
	// ignore the very start and end, where the test tones switch on and off abruptly
	d := make([]float32, 0, len(y))
	for i := 2000; i < len(y)-2000; i++ {
		d = append(d, y[i]-low[i])
	}
	if snr := rmsDB(low) - rmsDB(d); snr < 80 {
		t.Errorf("low tone not preserved / high tone not removed: SNR %.1f dB", snr)
	}
	// silence in, silence out; short inputs must not panic
	for _, n := range []int{0, 1, 100, 5000} {
		if got := LowPass(make([]float32, n), sr, 16800, 17400); len(got) != n {
			t.Errorf("length %d -> %d", n, len(got))
		}
	}
}
