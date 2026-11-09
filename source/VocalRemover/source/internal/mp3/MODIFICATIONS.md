This directory is a copy of github.com/hajimehoshi/go-mp3 (commit fa70610237c8c313ef920c7cff6c31efbbc55ffa,
Apache License 2.0, see LICENSE and AUTHORS), with these changes:

* import paths rewritten so the package builds inside this program;
* tests and the example player removed;
* the decoder returns two channels of 32-bit float samples (8 bytes per stereo sample) instead of
  16-bit integers, so loud passages are not clipped and no precision is lost
  (internal/frame/frame.go, internal/frameheader/frameheader.go).
* the error values in internal/*/ are written with field names (consts.UnexpectedEOF{At: ...}),
  as go vet asks; no change in behaviour.
