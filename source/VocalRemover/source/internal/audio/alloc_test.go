package audio

import (
	"encoding/binary"
	"os"
	"runtime"
	"testing"
)

// allocatedBy reports how many bytes the Go heap handed out while f ran.
func allocatedBy(f func()) uint64 {
	var before, after runtime.MemStats
	runtime.GC()
	runtime.ReadMemStats(&before)
	f()
	runtime.ReadMemStats(&after)
	return after.TotalAlloc - before.TotalAlloc
}

// hostileFLAC is a 64-byte file whose header claims 230 million samples at 192 kHz (just under the
// 20-minute limit) and that holds no audio at all.
func hostileFLAC() []byte {
	b := []byte("fLaC")
	b = append(b, 0x80, 0, 0, 34) // last metadata block, STREAMINFO, 34 bytes
	si := make([]byte, 34)
	binary.BigEndian.PutUint16(si[0:], 4096) // smallest block
	binary.BigEndian.PutUint16(si[2:], 4096) // largest block
	// bytes 4-9: smallest and largest frame size, left at 0 (unknown)
	const rate, channels, bits, samples = 192000, 2, 16, 230_000_000
	v := uint64(rate)<<44 | uint64(channels-1)<<41 | uint64(bits-1)<<36 | uint64(samples)
	binary.BigEndian.PutUint64(si[10:], v)
	b = append(b, si...)
	for len(b) < 64 {
		b = append(b, 0xA5)
	}
	return b
}

func TestFLACHeaderClaimDoesNotReserveMemory(t *testing.T) {
	data := hostileFLAC()
	if len(data) != 64 {
		t.Fatalf("test file is %d bytes", len(data))
	}
	var err error
	used := allocatedBy(func() { _, err = Decode(data) })
	if err == nil {
		t.Fatal("a FLAC file without audio was accepted")
	}
	t.Logf("error: %v; allocated %d bytes", err, used)
	if used > 8<<20 {
		t.Fatalf("decoding a 64-byte file allocated %d MB", used>>20)
	}
}

// oggCRC is the Ogg page checksum (CRC-32, polynomial 0x04c11db7, no reflection, no final xor).
func oggCRC(page []byte) uint32 {
	var crc uint32
	for _, c := range page {
		crc ^= uint32(c) << 24
		for i := 0; i < 8; i++ {
			if crc&0x80000000 != 0 {
				crc = crc<<1 ^ 0x04c11db7
			} else {
				crc <<= 1
			}
		}
	}
	return crc
}

func TestOggLengthClaimDoesNotReserveMemory(t *testing.T) {
	data, err := os.ReadFile("testdata/tone-0.5s.ogg")
	if err != nil {
		t.Fatal(err)
	}
	clean, err := Decode(data)
	if err != nil {
		t.Fatalf("the test file does not decode: %v", err)
	}
	// Make the last page claim 2^40 samples (the decoder reads the length from there).
	last := -1
	for i := 0; i+27 <= len(data); i++ {
		if string(data[i:i+4]) == "OggS" {
			last = i
		}
	}
	if last < 0 {
		t.Fatal("no Ogg page found")
	}
	nseg := int(data[last+26])
	size := 27 + nseg
	for _, s := range data[last+27 : last+27+nseg] {
		size += int(s)
	}
	page := data[last : last+size]
	binary.LittleEndian.PutUint64(page[6:], 1<<40)
	binary.LittleEndian.PutUint32(page[22:], 0)
	binary.LittleEndian.PutUint32(page[22:], oggCRC(page))

	var tr *Track
	used := allocatedBy(func() { tr, err = Decode(data) })
	t.Logf("%d-byte file, allocated %d bytes, err %v", len(data), used, err)
	if used > 32<<20 {
		t.Fatalf("decoding a %d-byte file allocated %d MB", len(data), used>>20)
	}
	if err != nil {
		return // refusing the file is fine too
	}
	if tr.Frames() < clean.Frames()-tr.Rate/10 || tr.Frames() > clean.Frames()+tr.Rate/10 {
		t.Fatalf("decoded %d frames, the unaltered file has %d", tr.Frames(), clean.Frames())
	}
}

func TestPreallocFrames(t *testing.T) {
	cases := []struct {
		claimed       uint64
		bytes, per, r int
		want          int
	}{
		{230_000_000, 64, 1, 192000, 64},
		{1000, 1 << 20, 1, 44100, 1000},
		{1 << 40, 1 << 30, 8, 44100, MaxSeconds * 44100},
		{0, 100, 8, 44100, 0},
	}
	for _, c := range cases {
		if got := preallocFrames(c.claimed, c.bytes, c.per, c.r); got != c.want {
			t.Errorf("preallocFrames(%d, %d, %d, %d) = %d, want %d", c.claimed, c.bytes, c.per, c.r, got, c.want)
		}
	}
}
