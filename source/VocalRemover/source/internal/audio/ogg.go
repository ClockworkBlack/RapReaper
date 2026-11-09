package audio

import (
	"bytes"
	"errors"
	"fmt"
	"io"

	"github.com/jfreymuth/oggvorbis"
)

// decodeOgg decodes an Ogg Vorbis file (jfreymuth/oggvorbis, pure Go) into float samples.
func decodeOgg(data []byte) (*Track, error) {
	rd, err := oggvorbis.NewReader(bytes.NewReader(data))
	if err != nil {
		return nil, fmt.Errorf("the Ogg file could not be read (%v); only Ogg Vorbis is supported, not Opus or FLAC-in-Ogg", err)
	}
	rate, ch := rd.SampleRate(), rd.Channels()
	if rate < minRate || rate > maxRate {
		return nil, fmt.Errorf("unsupported sample rate %d Hz", rate)
	}
	if ch < 1 || ch > 8 {
		return nil, fmt.Errorf("unsupported channel count %d", ch)
	}
	var l, r []float32
	if n := rd.Length(); n > 0 {
		// The length comes from the file's last page and is only a claim: reserve no more than the
		// file's size can plausibly hold, and let the buffers grow if the file holds more.
		m := preallocFrames(uint64(n), len(data), oggFramesPerByte, rate)
		l = make([]float32, 0, m)
		r = make([]float32, 0, m)
	}
	buf := make([]float32, 4096*ch)
	for {
		n, err := rd.Read(buf)
		for i := 0; i+ch <= n; i += ch {
			l = append(l, clean(buf[i]))
			if ch > 1 {
				r = append(r, clean(buf[i+1]))
			} else {
				r = append(r, clean(buf[i]))
			}
		}
		if len(l) > MaxSeconds*rate {
			return nil, ErrTooLong
		}
		if err == io.EOF {
			break
		}
		if err != nil {
			if len(l) > 0 {
				break
			}
			return nil, fmt.Errorf("the Ogg Vorbis file could not be decoded (%v)", err)
		}
	}
	if len(l) == 0 {
		return nil, errors.New("the Ogg file holds no audio")
	}
	detail := "Vorbis"
	if ch > 2 {
		detail += fmt.Sprintf(", first 2 of %d channels used", ch)
	}
	return &Track{Format: "OGG", Detail: detail, Rate: rate, Channels: minInt(ch, 2), L: l, R: r}, nil
}
