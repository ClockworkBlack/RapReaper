// Package audio reads WAV, MP3, FLAC and Ogg Vorbis files into memory and writes WAV files.
package audio

import (
	"bytes"
	"errors"
	"fmt"
	"strings"
)

// Track is decoded audio held in memory as 32-bit float samples at its original sample rate.
// Mono sources are stored with the same data in both channels.
type Track struct {
	Format   string // "WAV", "MP3", "FLAC" or "OGG"
	Detail   string // e.g. "24-bit PCM" or "MPEG-1 Layer III"
	Rate     int    // samples per second
	Channels int    // channel count of the source file
	L, R     []float32
}

// Frames returns the number of samples per channel.
func (t *Track) Frames() int { return len(t.L) }

// Seconds returns the duration.
func (t *Track) Seconds() float64 { return float64(len(t.L)) / float64(t.Rate) }

const (
	// MaxSeconds bounds how much audio is held in memory.
	MaxSeconds = 20 * 60
	minRate    = 8000
	maxRate    = 192000
)

// How many sample frames one byte of a compressed file can be expected to hold, at most, for sizing the
// buffers before decoding: FLAC rarely gets below 2 bytes per stereo frame of real music, and Ogg Vorbis
// at a low 64 kbit/s holds about 5.5 frames per byte. (A file that holds more, such as a long silence,
// still decodes: the buffers grow as needed.)
const (
	flacFramesPerByte = 1
	oggFramesPerByte  = 8
)

// preallocFrames is how many frames to reserve before decoding: what the header claims, but no more than
// the file's size can plausibly hold and never more than MaxSeconds of audio.
func preallocFrames(claimed uint64, fileBytes, framesPerByte, rate int) int {
	limit := uint64(fileBytes) * uint64(framesPerByte)
	if maxFrames := uint64(MaxSeconds) * uint64(rate); limit > maxFrames {
		limit = maxFrames
	}
	if claimed > limit {
		claimed = limit
	}
	return int(claimed)
}

// ErrTooLong is returned for audio longer than MaxSeconds.
var ErrTooLong = fmt.Errorf("the audio is longer than %d minutes; please cut it into shorter files first", MaxSeconds/60)

// Decode detects the file type from its content and decodes it.
func Decode(data []byte) (t *Track, err error) {
	// A damaged file must produce an error message, never a crash.
	defer func() {
		if r := recover(); r != nil {
			t, err = nil, fmt.Errorf("the file is damaged or not a valid audio file (%v)", r)
		}
	}()
	if len(data) < 12 {
		return nil, errors.New("the file is empty or too small to be audio")
	}
	head := string(data[:4])
	switch {
	case (head == "RIFF" || head == "RF64" || head == "BW64") && string(data[8:12]) == "WAVE":
		return decodeWAV(data)
	case head == "fLaC":
		return decodeFLAC(data)
	case head == "OggS":
		return decodeOgg(data)
	case head == "FORM":
		return nil, unsupported("AIFF")
	case string(data[4:8]) == "ftyp":
		return nil, unsupported("M4A/AAC/MP4")
	case strings.HasPrefix(head, "ID3"):
		// an ID3 tag can sit in front of a FLAC stream as well as an MP3
		if i := bytes.Index(data[:minInt(len(data), 1<<20)], []byte("fLaC")); i > 0 && i < 1<<20 {
			return decodeFLAC(data[i:])
		}
		return decodeMP3(data)
	case looksLikeMP3(data):
		return decodeMP3(data)
	}
	return nil, errors.New("this does not look like an MP3, WAV, FLAC or Ogg Vorbis file")
}

// Extensions lists the file types Decode understands.
var Extensions = []string{".mp3", ".wav", ".flac", ".ogg", ".oga"}

func unsupported(kind string) error {
	return fmt.Errorf("%s files are not supported; please use an MP3, WAV, FLAC or Ogg Vorbis file (RapReaper's export_mix, or a free program such as Audacity, can convert)", kind)
}

func looksLikeMP3(data []byte) bool {
	// Look for an MPEG audio frame sync near the start (some files begin with padding).
	limit := len(data) - 4
	if limit > 64*1024 {
		limit = 64 * 1024
	}
	for i := 0; i < limit; i++ {
		if _, ok := parseMP3Header(data[i:]); ok {
			// require a second consistent header where the next frame should start
			h, _ := parseMP3Header(data[i:])
			next := i + h.frameBytes
			if next+4 <= len(data) {
				if h2, ok2 := parseMP3Header(data[next:]); ok2 && h2.rate == h.rate && h2.version == h.version {
					return true
				}
			}
		}
	}
	return false
}
