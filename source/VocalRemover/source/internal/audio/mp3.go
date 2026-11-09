package audio

import (
	"bytes"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"math"

	"vocalremover/internal/mp3"
)

type mp3Header struct {
	version    int // 1, 2 or 25 (MPEG 2.5)
	rate       int
	mono       bool
	frameBytes int
	samples    int // PCM samples per frame
	sideInfo   int // bytes of side information after the 4-byte header (+2 if CRC protected)
}

var (
	mp3Rates    = map[int][3]int{1: {44100, 48000, 32000}, 2: {22050, 24000, 16000}, 25: {11025, 12000, 8000}}
	mp3BitsV1L3 = [16]int{0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0}
	mp3BitsV2L3 = [16]int{0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0}
)

// parseMP3Header validates an MPEG audio Layer III frame header.
func parseMP3Header(b []byte) (mp3Header, bool) {
	var h mp3Header
	if len(b) < 4 || b[0] != 0xFF || b[1]&0xE0 != 0xE0 {
		return h, false
	}
	switch (b[1] >> 3) & 3 {
	case 3:
		h.version = 1
	case 2:
		h.version = 2
	case 0:
		h.version = 25
	default:
		return h, false
	}
	if (b[1]>>1)&3 != 1 { // Layer III only
		return h, false
	}
	bitIdx := int(b[2] >> 4)
	rateIdx := int(b[2]>>2) & 3
	if bitIdx == 0 || bitIdx == 15 || rateIdx == 3 {
		return h, false
	}
	h.rate = mp3Rates[h.version][rateIdx]
	padding := int(b[2]>>1) & 1
	h.mono = b[3]>>6 == 3
	var kbps int
	if h.version == 1 {
		kbps = mp3BitsV1L3[bitIdx]
		h.samples = 1152
		h.frameBytes = 144000*kbps/h.rate + padding
		h.sideInfo = 32
		if h.mono {
			h.sideInfo = 17
		}
	} else {
		kbps = mp3BitsV2L3[bitIdx]
		h.samples = 576
		h.frameBytes = 72000*kbps/h.rate + padding
		h.sideInfo = 17
		if h.mono {
			h.sideInfo = 9
		}
	}
	if b[1]&1 == 0 { // CRC present
		h.sideInfo += 2
	}
	if h.frameBytes < 24 {
		return h, false
	}
	return h, true
}

// firstMP3Frame finds the first audio frame, skipping an ID3v2 tag and any junk before it.
func firstMP3Frame(data []byte) (offset int, h mp3Header, err error) {
	pos := 0
	for pos+10 <= len(data) && string(data[pos:pos+3]) == "ID3" {
		size := int(data[pos+6]&0x7F)<<21 | int(data[pos+7]&0x7F)<<14 | int(data[pos+8]&0x7F)<<7 | int(data[pos+9]&0x7F)
		pos += 10 + size
		if data[pos-10-size+5]&0x10 != 0 { // footer present
			pos += 10
		}
		if pos > len(data) {
			return 0, h, errors.New("MP3 file is damaged (broken tag at the start)")
		}
	}
	for ; pos+4 <= len(data); pos++ {
		hd, ok := parseMP3Header(data[pos:])
		if !ok {
			continue
		}
		next := pos + hd.frameBytes
		if next+4 > len(data) {
			return pos, hd, nil
		}
		if h2, ok2 := parseMP3Header(data[next:]); ok2 && h2.rate == hd.rate && h2.version == hd.version {
			return pos, hd, nil
		}
	}
	return 0, h, errors.New("no MP3 audio found in this file")
}

// hideSeek stops the decoder from pre-scanning the whole file, which we do not need.
type hideSeek struct{ r io.Reader }

func (h hideSeek) Read(p []byte) (int, error) { return h.r.Read(p) }

func decodeMP3(data []byte) (*Track, error) {
	off, h, err := firstMP3Frame(data)
	if err != nil {
		return nil, err
	}
	// Encoders store a silent "info" frame first, plus the number of padding samples they
	// added at the start and end. Removing those keeps the result lined up with what other
	// players and editors show for the same file.
	skipStart, skipEnd := 0, 0
	frame := data[off:]
	if tagAt := 4 + h.sideInfo; len(frame) >= tagAt+4 && h.frameBytes <= len(frame) {
		if tag := string(frame[tagAt : tagAt+4]); tag == "Xing" || tag == "Info" {
			skipStart = h.samples // the info frame itself decodes to silence
			p := tagAt + 8
			flags := binary.BigEndian.Uint32(frame[tagAt+4 : tagAt+8])
			if flags&1 != 0 {
				p += 4
			}
			if flags&2 != 0 {
				p += 4
			}
			if flags&4 != 0 {
				p += 100
			}
			if flags&8 != 0 {
				p += 4
			}
			// LAME extension: 9-byte version string, then 12 more bytes, then delay/padding
			if p+24 <= h.frameBytes && p+24 <= len(frame) {
				d := frame[p+21 : p+24]
				delay := int(d[0])<<4 | int(d[1])>>4
				pad := int(d[1]&0x0F)<<8 | int(d[2])
				ver := frame[p : p+4]
				if (string(ver) == "LAME" || string(ver) == "Lavc" || string(ver) == "Lavf") && (delay > 0 || pad > 0) {
					const decoderDelay = 529
					skipStart += delay + decoderDelay
					if pad > decoderDelay {
						skipEnd = pad - decoderDelay
					}
				}
			}
		}
	}

	dec, err := mp3.NewDecoder(hideSeek{bytes.NewReader(data[off:])})
	if err != nil {
		return nil, fmt.Errorf("cannot decode this MP3 file (%v)", err)
	}
	rate := dec.SampleRate()
	if rate < minRate || rate > maxRate {
		return nil, fmt.Errorf("MP3 file has an unsupported sample rate (%d Hz)", rate)
	}
	maxBytes := int64(MaxSeconds) * int64(rate) * 8
	var pcm bytes.Buffer
	n, err := io.Copy(&pcm, io.LimitReader(dec, maxBytes+8))
	if err != nil && n == 0 {
		return nil, fmt.Errorf("cannot decode this MP3 file (%v)", err)
	}
	if n > maxBytes {
		return nil, ErrTooLong
	}
	raw := pcm.Bytes()
	frames := len(raw) / 8
	if skipStart+skipEnd >= frames {
		skipStart, skipEnd = 0, 0
	}
	raw = raw[skipStart*8 : (frames-skipEnd)*8]
	frames -= skipStart + skipEnd
	if frames == 0 {
		return nil, errors.New("MP3 file contains no audio")
	}
	t := &Track{Format: "MP3", Rate: rate, Channels: 2, L: make([]float32, frames)}
	t.Detail = map[int]string{1: "MPEG-1 Layer III", 2: "MPEG-2 Layer III", 25: "MPEG-2.5 Layer III"}[h.version]
	if h.mono {
		t.Channels = 1
		for i := range t.L {
			t.L[i] = clean(math.Float32frombits(binary.LittleEndian.Uint32(raw[i*8:])))
		}
		t.R = t.L
		return t, nil
	}
	t.R = make([]float32, frames)
	for i := range t.L {
		t.L[i] = clean(math.Float32frombits(binary.LittleEndian.Uint32(raw[i*8:])))
		t.R[i] = clean(math.Float32frombits(binary.LittleEndian.Uint32(raw[i*8+4:])))
	}
	return t, nil
}
