package main

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func TestSafeBaseNameReservedNames(t *testing.T) {
	cases := map[string]string{
		"song.mp3":         "song",
		"nul.backup.mp3":   "_nul.backup",
		"NUL.mp3":          "_NUL",
		"con":              "_con",
		"Com1.final.wav":   "_Com1.final",
		"lpt9 .x.flac":     "_lpt9 .x",
		"COM¹.mp3":         "_COM¹",
		"lpt³.wav":         "_lpt³",
		"conin$.mp3":       "_conin$",
		"CONOUT$.ogg":      "_CONOUT$",
		"console.mp3":      "console",
		"com10.mp3":        "com10",
		"nulled.wav":       "nulled",
		"aux-mix.wav":      "aux-mix",
		"..\\..\\nul.wav":  "_nul",
		"a/b/prn.wav":      "_prn",
		"":                 "song",
		"...":              "song",
		"my song?.mp3":     "my song_",
		"nul (Vocals).wav": "nul (Vocals)",
	}
	for in, want := range cases {
		if got := safeBaseName(in); got != want {
			t.Errorf("safeBaseName(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestCheckWritableLeavesLinkedFilesAlone(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("symbolic links need extra rights on Windows")
	}
	dir := t.TempDir()
	victim := filepath.Join(t.TempDir(), "important.txt")
	if err := os.WriteFile(victim, []byte("keep me"), 0o644); err != nil {
		t.Fatal(err)
	}
	// the name the old write test used, now pointing somewhere else
	if err := os.Symlink(victim, filepath.Join(dir, ".vocalremover-write-test")); err != nil {
		t.Fatal(err)
	}
	if err := checkWritable(dir); err != nil {
		t.Fatal(err)
	}
	if data, _ := os.ReadFile(victim); string(data) != "keep me" {
		t.Fatalf("the linked file was changed: %q", data)
	}
	entries, _ := os.ReadDir(dir)
	for _, e := range entries {
		if strings.HasPrefix(e.Name(), ".vr-probe-") {
			t.Fatalf("the probe file %s was left behind", e.Name())
		}
	}
}

func TestStrayLibraries(t *testing.T) {
	dir := t.TempDir()
	os.MkdirAll(filepath.Join(dir, "gpu"), 0o755)
	for _, f := range []string{"onnxruntime.dll", "MSVCP140.DLL", "VocalRemover.exe", "gpu/DirectML.dll", "gpu/onnxruntime.dll", "gpu/README.txt"} {
		os.WriteFile(filepath.Join(dir, filepath.FromSlash(f)), nil, 0o644)
	}
	if got := strayLibraries(dir); len(got) != 0 {
		t.Fatalf("shipped files reported as stray: %v", got)
	}
	for _, f := range []string{"dbghelp.dll", "gpu/dxgi.DLL", "gpu/d3d12.dll"} {
		os.WriteFile(filepath.Join(dir, filepath.FromSlash(f)), nil, 0o644)
	}
	got := strayLibraries(dir)
	if len(got) != 3 {
		t.Fatalf("expected 3 stray libraries, got %v", got)
	}
}

func TestReadSongRefusesLargeFiles(t *testing.T) {
	path := filepath.Join(t.TempDir(), "big.wav")
	f, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	// a sparse file: takes no disk space
	if err := f.Truncate(maxUploadBytes + 1); err != nil {
		f.Close()
		t.Skip("cannot make a sparse file here:", err)
	}
	f.Close()
	if _, err := readSong(path); err == nil || err.Error() != tooLargeMessage {
		t.Fatalf("got %v, want the 1 GB message", err)
	}
}
