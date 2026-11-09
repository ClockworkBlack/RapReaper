package audio

import (
	"bytes"
	"errors"
	"fmt"
	"io"

	"github.com/mewkiz/flac"
)

// decodeFLAC decodes a FLAC file (mewkiz/flac, pure Go) into float samples.
func decodeFLAC(data []byte) (*Track, error) {
	stream, err := flac.New(bytes.NewReader(data))
	if err != nil {
		return nil, fmt.Errorf("the FLAC file could not be read (%v)", err)
	}
	info := stream.Info
	rate, ch, bps := int(info.SampleRate), int(info.NChannels), int(info.BitsPerSample)
	if rate < minRate || rate > maxRate {
		return nil, fmt.Errorf("unsupported sample rate %d Hz", rate)
	}
	if ch < 1 {
		return nil, errors.New("the FLAC file has no audio channels")
	}
	if bps < 4 || bps > 32 {
		return nil, fmt.Errorf("unsupported bit depth %d", bps)
	}
	if info.NSamples > uint64(MaxSeconds)*uint64(rate) {
		return nil, ErrTooLong
	}
	scale := float32(1) / float32(uint64(1)<<(bps-1))
	var l, r []float32
	if info.NSamples > 0 {
		// The header's sample count is only a claim: reserve no more than the file's size can
		// plausibly hold, and let the buffers grow if the file holds more.
		n := preallocFrames(info.NSamples, len(data), flacFramesPerByte, rate)
		l = make([]float32, 0, n)
		r = make([]float32, 0, n)
	}
	for {
		fr, err := stream.ParseNext()
		if err == io.EOF {
			break
		}
		if err != nil {
			if len(l) > 0 {
				break // keep what was decoded of a file damaged at the end
			}
			return nil, fmt.Errorf("the FLAC file could not be decoded (%v)", err)
		}
		if len(fr.Subframes) == 0 {
			continue
		}
		s0 := fr.Subframes[0].Samples
		s1 := s0
		if len(fr.Subframes) > 1 {
			s1 = fr.Subframes[1].Samples
		}
		for i := range s0 {
			l = append(l, clean(float32(s0[i])*scale))
			if i < len(s1) {
				r = append(r, clean(float32(s1[i])*scale))
			} else {
				r = append(r, clean(float32(s0[i])*scale))
			}
		}
		if len(l) > MaxSeconds*rate {
			return nil, ErrTooLong
		}
	}
	if len(l) == 0 {
		return nil, errors.New("the FLAC file holds no audio")
	}
	detail := fmt.Sprintf("%d-bit", bps)
	if ch > 2 {
		detail += fmt.Sprintf(", first 2 of %d channels used", ch)
	}
	return &Track{Format: "FLAC", Detail: detail, Rate: rate, Channels: minInt(ch, 2), L: l, R: r}, nil
}

func minInt(a, b int) int {
	if a < b {
		return a
	}
	return b
}
