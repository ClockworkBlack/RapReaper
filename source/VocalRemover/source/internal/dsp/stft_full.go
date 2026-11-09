package dsp

// AnalyzeFull writes the spectrogram of a stereo chunk into dst in the layout the Roformer
// network uses: [channel][bin][frame][re, im] with all NFFT/2+1 bins kept and nothing zeroed.
// gain scales the input. dst needs 2 * (NFFT/2+1) * Frames * 2 values.
func (s *STFT) AnalyzeFull(chunk [2][]float32, dst []float32, gain float32) {
	n, hop, T := s.NFFT, s.Hop, s.Frames
	L := s.Chunk
	half := n / 2
	bins := half + 1
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
			base := c * bins * T
			for f := 0; f < bins; f++ {
				k := (base + f*T + t) * 2
				dst[k] = float32(real(out[f]))
				dst[k+1] = float32(imag(out[f]))
			}
		}
	})
}

// SynthesizeFull is the inverse of AnalyzeFull: it turns a spectrogram in the Roformer layout
// back into a stereo chunk of s.Chunk samples per channel.
func (s *STFT) SynthesizeFull(src []float32, out [2][]float32) {
	n, hop, T := s.NFFT, s.Hop, s.Frames
	half := n / 2
	bins := half + 1
	parallelFor(2*T, func(_, lo, hi int) {
		spec := make([]complex128, n)
		td := make([]complex128, n)
		for job := lo; job < hi; job++ {
			c, t := job/T, job%T
			base := c * bins * T
			for f := 0; f <= half; f++ {
				k := (base + f*T + t) * 2
				v := complex(float64(src[k]), float64(src[k+1]))
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
