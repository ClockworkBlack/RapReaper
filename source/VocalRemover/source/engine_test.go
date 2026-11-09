package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestGPUFailureIsRemembered(t *testing.T) {
	dir := t.TempDir()
	p := paths{appDir: dir, settings: filepath.Join(dir, "settings.json")}
	if err := saveSettings(p, settings{GPU: "fused", GPUTested: "2026-01-01 12:00", GPUNote: "works: 3.0x"}); err != nil {
		t.Fatal(err)
	}
	rememberGPUFailure(p, "DirectML: device removed\nsecond line")
	data, err := os.ReadFile(p.settings)
	if err != nil {
		t.Fatal(err)
	}
	var raw map[string]string
	if err := json.Unmarshal(data, &raw); err != nil {
		t.Fatal(err)
	}
	if raw["gpu"] != "off" || raw["gpu_failed"] == "" || !strings.Contains(raw["gpu_note"], "device removed second line") {
		t.Fatalf("settings.json after a failure: %s", data)
	}
	if s := loadSettings(p); s.GPU != "" {
		t.Fatalf("the graphics processor is still on in the settings: %+v", s)
	}
	if mode := gpuChoice(p, ""); mode != "" {
		t.Fatalf("a new start would use the graphics processor (%q)", mode)
	}
}

func TestPercent(t *testing.T) {
	for f, want := range map[float64]string{0.0004: "0.04%", 0.023: "2.3%", 0.31: "31%"} {
		if got := percent(f); got != want {
			t.Errorf("percent(%v) = %q, want %q", f, got, want)
		}
	}
}
