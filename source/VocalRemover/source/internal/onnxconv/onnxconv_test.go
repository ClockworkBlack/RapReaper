package onnxconv

import (
	"math"
	"os"
	"testing"
)

func TestFloat16(t *testing.T) {
	cases := map[float32]uint16{0: 0x0000, 1: 0x3C00, -2: 0xC000, 0.5: 0x3800, 65504: 0x7BFF, 1e-8: 0x0000,
		float32(math.Inf(1)): 0x7C00, 3.14159: 0x4248, 1.0009765625: 0x3C01, 6.1e-5: 0x03FF, 0.000030517578125: 0x0200}
	for f, want := range cases {
		if got := Float16(f); got != want {
			t.Errorf("Float16(%g) = %#04x, want %#04x", f, got, want)
		}
	}
	// round to nearest even at the halfway point: 1 + 2^-11 lies exactly between 1 and 1+2^-10
	if got := Float16(1 + 1.0/2048); got != 0x3C00 {
		t.Errorf("halfway rounds to even: got %#04x", got)
	}
	if got := Float16(1 + 3.0/2048); got != 0x3C02 {
		t.Errorf("halfway rounds to even (odd case): got %#04x", got)
	}
}

// TestConvertFile converts a real processor model when VR_INT8_MODEL names one and writes the result
// to VR_FP16_OUT (checked separately with ONNX Runtime).
func TestConvertFile(t *testing.T) {
	in, out := os.Getenv("VR_INT8_MODEL"), os.Getenv("VR_FP16_OUT")
	if in == "" || out == "" {
		t.Skip("set VR_INT8_MODEL and VR_FP16_OUT")
	}
	data, err := os.ReadFile(in)
	if err != nil {
		t.Fatal(err)
	}
	conv, n, err := Convert(data)
	if err != nil {
		t.Fatal(err)
	}
	t.Logf("%d layers converted, %d -> %d bytes", n, len(data), len(conv))
	if err := os.WriteFile(out, conv, 0o644); err != nil {
		t.Fatal(err)
	}
	// deterministic
	again, _, _ := Convert(data)
	if string(again) != string(conv) {
		t.Fatal("conversion is not deterministic")
	}
}
