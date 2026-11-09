package audio

import (
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"math"
	"math/rand"
)

func decodeWAV(data []byte) (*Track, error) {
	le := binary.LittleEndian
	is64 := string(data[:4]) != "RIFF"
	var (
		haveFmt    bool
		format     uint16
		channels   int
		rate       int
		blockAlign int
		bits       int
		ds64Data   uint64
		pcm        []byte
	)
	pos := 12
	for pos+8 <= len(data) {
		id := string(data[pos : pos+4])
		size := uint64(le.Uint32(data[pos+4 : pos+8]))
		body := pos + 8
		if id == "data" {
			if is64 && size == 0xFFFFFFFF && ds64Data > 0 {
				size = ds64Data
			}
			avail := uint64(len(data) - body)
			if size > avail || size == 0 {
				size = avail // truncated file, or a streamed file with an unknown length
			}
			pcm = data[body : body+int(size)]
			break
		}
		if size > uint64(len(data)-body) {
			break
		}
		chunk := data[body : body+int(size)]
		switch id {
		case "ds64":
			if len(chunk) >= 16 {
				ds64Data = le.Uint64(chunk[8:16])
			}
		case "fmt ":
			if len(chunk) < 16 {
				return nil, errors.New("WAV file has a damaged format header")
			}
			format = le.Uint16(chunk[0:2])
			channels = int(le.Uint16(chunk[2:4]))
			rate = int(le.Uint32(chunk[4:8]))
			blockAlign = int(le.Uint16(chunk[12:14]))
			bits = int(le.Uint16(chunk[14:16]))
			if format == 0xFFFE { // WAVE_FORMAT_EXTENSIBLE: real format is in the sub-format GUID
				if len(chunk) < 26 {
					return nil, errors.New("WAV file has a damaged extended format header")
				}
				format = le.Uint16(chunk[24:26])
			}
			haveFmt = true
		}
		pos = body + int(size) + int(size&1)
	}
	if !haveFmt {
		return nil, errors.New("WAV file has no format header")
	}
	if pcm == nil {
		return nil, errors.New("WAV file contains no audio data")
	}
	if channels < 1 || channels > 64 {
		return nil, fmt.Errorf("WAV file has an unsupported channel count (%d)", channels)
	}
	if rate < minRate || rate > maxRate {
		return nil, fmt.Errorf("WAV file has an unsupported sample rate (%d Hz)", rate)
	}
	bytesPer := bits / 8
	var detail string
	switch {
	case format == 1 && (bits == 8 || bits == 16 || bits == 24 || bits == 32):
		detail = fmt.Sprintf("%d-bit PCM", bits)
	case format == 3 && (bits == 32 || bits == 64):
		detail = fmt.Sprintf("%d-bit float", bits)
	default:
		return nil, fmt.Errorf("this WAV encoding is not supported (format tag %d, %d-bit); please export it as normal PCM WAV", format, bits)
	}
	if blockAlign < bytesPer*channels {
		blockAlign = bytesPer * channels
	}
	frames := len(pcm) / blockAlign
	if frames == 0 {
		return nil, errors.New("WAV file contains no audio data")
	}
	if frames > MaxSeconds*rate {
		return nil, ErrTooLong
	}
	t := &Track{Format: "WAV", Detail: detail, Rate: rate, Channels: channels, L: make([]float32, frames)}
	if channels >= 2 {
		t.R = make([]float32, frames)
	}
	sample := func(b []byte) float32 {
		switch {
		case format == 3 && bits == 32:
			return clean(math.Float32frombits(le.Uint32(b)))
		case format == 3:
			return clean(float32(math.Float64frombits(le.Uint64(b))))
		case bits == 8:
			return (float32(b[0]) - 128) / 128
		case bits == 16:
			return float32(int16(le.Uint16(b))) / 32768
		case bits == 24:
			return float32(int32(uint32(b[0])<<8|uint32(b[1])<<16|uint32(b[2])<<24)>>8) / 8388608
		default:
			return float32(float64(int32(le.Uint32(b))) / 2147483648)
		}
	}
	for i := 0; i < frames; i++ {
		fr := pcm[i*blockAlign:]
		t.L[i] = sample(fr)
		if channels >= 2 {
			t.R[i] = sample(fr[bytesPer:])
		}
	}
	if channels == 1 {
		t.R = t.L
	}
	return t, nil
}

// clean replaces NaN/Inf samples (which would poison every later calculation) with silence.
func clean(v float32) float32 {
	if v != v || v > 1e6 || v < -1e6 {
		return 0
	}
	return v
}

// WAVFormat selects the sample encoding of written files.
type WAVFormat int

const (
	PCM16 WAVFormat = iota
	PCM24
	Float32
)

func (f WAVFormat) String() string {
	switch f {
	case PCM16:
		return "16-bit PCM"
	case PCM24:
		return "24-bit PCM"
	}
	return "32-bit float"
}

// WriteWAV writes one or two channels (pass nil for r to write mono) as a WAV file.
// Every sample is multiplied by gain on the way out. 16-bit output is dithered; integer
// formats clip at full scale, so the caller should pick a gain that makes the signal fit
// (see Peak).
func WriteWAV(w io.Writer, l, r []float32, rate int, format WAVFormat, gain float32) error {
	channels := 1
	if r != nil {
		channels = 2
		if len(r) != len(l) {
			return errors.New("channel lengths differ")
		}
	}
	bytesPer := map[WAVFormat]int{PCM16: 2, PCM24: 3, Float32: 4}[format]
	dataLen := int64(len(l)) * int64(channels) * int64(bytesPer)
	if dataLen > 0xFFFFFFFF-64 {
		return errors.New("the result is too large for a WAV file (over 4 GB)")
	}
	le := binary.LittleEndian
	var hdr []byte
	put := func(s string) { hdr = append(hdr, s...) }
	u32 := func(v uint32) { hdr = le.AppendUint32(hdr, v) }
	u16 := func(v uint16) { hdr = le.AppendUint16(hdr, v) }
	tag := uint16(1)
	extra := 0
	if format == Float32 {
		tag = 3
		extra = 12 // "fact" chunk
	}
	put("RIFF")
	u32(uint32(int64(4+8+16+extra+8) + dataLen + dataLen&1))
	put("WAVE")
	put("fmt ")
	u32(16)
	u16(tag)
	u16(uint16(channels))
	u32(uint32(rate))
	u32(uint32(rate * channels * bytesPer))
	u16(uint16(channels * bytesPer))
	u16(uint16(bytesPer * 8))
	if format == Float32 {
		put("fact")
		u32(4)
		u32(uint32(len(l)))
	}
	put("data")
	u32(uint32(dataLen))
	if _, err := w.Write(hdr); err != nil {
		return err
	}

	const block = 16384
	buf := make([]byte, 0, block*channels*bytesPer)
	rng := rand.New(rand.NewSource(0x5eed))
	chans := [][]float32{l, r}[:channels]
	for start := 0; start < len(l); start += block {
		end := start + block
		if end > len(l) {
			end = len(l)
		}
		buf = buf[:0]
		for i := start; i < end; i++ {
			for _, ch := range chans {
				v := float64(ch[i] * gain)
				switch format {
				case Float32:
					buf = le.AppendUint32(buf, math.Float32bits(ch[i]*gain))
				case PCM24:
					s := math.Round(v * 8388608)
					if s > 8388607 {
						s = 8388607
					} else if s < -8388608 {
						s = -8388608
					}
					u := uint32(int32(s))
					buf = append(buf, byte(u), byte(u>>8), byte(u>>16))
				default:
					// triangular dither of +-1 LSB keeps quiet tails free of quantisation distortion
					d := rng.Float64() - rng.Float64()
					s := math.Round(v*32768 + d)
					if s > 32767 {
						s = 32767
					} else if s < -32768 {
						s = -32768
					}
					buf = le.AppendUint16(buf, uint16(int16(s)))
				}
			}
		}
		if _, err := w.Write(buf); err != nil {
			return err
		}
	}
	if dataLen&1 == 1 {
		if _, err := w.Write([]byte{0}); err != nil {
			return err
		}
	}
	return nil
}

// Peak returns the largest absolute sample value.
func Peak(chans ...[]float32) float32 {
	var p float32
	for _, ch := range chans {
		for _, v := range ch {
			if v < 0 {
				v = -v
			}
			if v > p {
				p = v
			}
		}
	}
	return p
}
