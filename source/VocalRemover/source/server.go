package main

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/subtle"
	"embed"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"math"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"time"

	"vocalremover/internal/audio"
	"vocalremover/internal/separate"
)

//go:embed ui
var uiFiles embed.FS

const maxUploadBytes = 1 << 30 // 1 GiB

// tooLargeMessage is what the window and the command line say about a file above maxUploadBytes.
const tooLargeMessage = "This file is larger than 1 GB, which is more than the program can load."

type loadedTrack struct {
	id      string
	name    string
	track   *audio.Track
	peaks   []float32
	preview []byte // 16-bit WAV of the whole song, for listening in the window

	previewOnce sync.Once
}

type job struct {
	id          string
	trackID     string
	state       string // "running", "done", "error", "cancelled"
	stage       string
	fraction    float64
	started     time.Time
	finished    time.Time
	cancel      context.CancelFunc
	err         string
	saved       *savedFiles
	seconds     float64
	quality     string
	format      string
	ranOn       string
	gpuFallback string
	instPrev    []byte
	vocalPrev   []byte
}

// gpuTestJob is the graphics test running in the background for the window.
type gpuTestJob struct {
	state   string // "running", "done", "error"
	stage   string
	lines   []string
	err     string
	started time.Time
}

type server struct {
	engine *separate.Engine
	paths  paths
	token  string
	host   string // "127.0.0.1:port"
	port   string

	mu       sync.Mutex
	track    *loadedTrack
	job      *job
	gpuTest  *gpuTestJob
	gpuMode  string // "" (off), "fused" or "unfused": what the engine is set to right now
	gpuError string // why the graphics processor could not be switched on, if it could not
	// gpuSwitching is set while the window's graphics switch loads the graphics runtime; no song or
	// test starts meanwhile.
	gpuSwitching bool
	pages        map[string]bool
	hadPage      bool
	emptyAt      time.Time
	shutdown     chan struct{}
}

func randomHex(n int) string {
	b := make([]byte, n)
	if _, err := rand.Read(b); err != nil {
		panic(err)
	}
	return hex.EncodeToString(b)
}

func runServer(p paths, threads int, open bool, gpuFlag string) error {
	setConsoleTitle("Vocal Remover")
	fmt.Printf("Vocal Remover %s\n\n", version)
	engine, err := newEngine(p, threads)
	if err != nil {
		return err
	}
	if !engine.Available(separate.MelRoformer) {
		return fmt.Errorf("the model file %s is missing. Put the downloaded model pieces (the files whose names contain \".onnx.part\") into the folder %s and start the program again", separate.MelRoformer.File, p.appDir)
	}
	for _, spec := range separate.AllModels() {
		if !engine.Available(spec) {
			fmt.Printf("Note: the model file %s is not in the models folder; the modes that need it are greyed out.\n", spec.File)
		}
	}
	// Listen on the loopback address only: nothing outside this computer can connect.
	ln, err := (&net.ListenConfig{Control: exclusivePort}).Listen(context.Background(), "tcp4", "127.0.0.1:0")
	if err != nil {
		return fmt.Errorf("cannot open a local port for the program window: %v", err)
	}
	s := &server{engine: engine, paths: p, token: randomHex(16), host: ln.Addr().String(), pages: map[string]bool{}, shutdown: make(chan struct{})}
	_, s.port, _ = net.SplitHostPort(s.host)
	if mode := gpuChoice(p, gpuFlag); mode != "" {
		if err := enableGPU(p, engine, mode, func(msg string) { fmt.Println(msg) }); err != nil {
			s.gpuError = err.Error()
			fmt.Println("Graphics processor not used:", err)
		} else {
			s.gpuMode = mode
			fmt.Println("The", gpuName(), "is switched on ("+mode+").")
		}
	}
	url := "http://" + s.host + "/#" + s.token

	// Time limits for the window's connections: a request's headers must arrive within 10 seconds and
	// the whole request (a song of up to 1 GB, over this computer's own loopback connection) within
	// 5 minutes, and an idle connection is closed after 2 minutes.
	srv := &http.Server{Handler: s.routes(), ReadHeaderTimeout: 10 * time.Second, ReadTimeout: 5 * time.Minute, IdleTimeout: 2 * time.Minute}
	go func() {
		if err := srv.Serve(ln); err != nil && !errors.Is(err, http.ErrServerClosed) {
			fmt.Fprintln(os.Stderr, "window server stopped:", err)
		}
	}()
	go s.watchPages()

	fmt.Println("The program is running. Its window opens in your web browser;")
	fmt.Println("everything stays on this computer and no internet connection is used.")
	fmt.Println()
	fmt.Println("If the window did not open, copy this address into your browser:")
	fmt.Println()
	fmt.Println("   " + url)
	fmt.Println()
	fmt.Println("Keep this black window open while you work. Close it to quit.")
	if open {
		if err := openBrowser(url); err != nil {
			fmt.Println("\n(Could not open the browser automatically: " + err.Error() + ")")
		}
	}
	<-s.shutdown
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	srv.Shutdown(ctx)
	engine.Close()
	return nil
}

// watchPages quits the program a few seconds after its last window has been closed, unless
// a song is still being processed or the graphics test is running.
func (s *server) watchPages() {
	for range time.Tick(time.Second) {
		s.mu.Lock()
		busy := s.busyLocked()
		quit := s.hadPage && len(s.pages) == 0 && !s.emptyAt.IsZero() && time.Since(s.emptyAt) > 4*time.Second && !busy
		s.mu.Unlock()
		if quit {
			close(s.shutdown)
			return
		}
	}
}

// busyLocked says whether work is under way that must not be interrupted or overlapped: a song being
// processed, the graphics test, or the graphics processor being switched on.
func (s *server) busyLocked() bool {
	return (s.job != nil && s.job.state == "running") || (s.gpuTest != nil && s.gpuTest.state == "running") || s.gpuSwitching
}

func (s *server) routes() http.Handler {
	mux := http.NewServeMux()
	// The window's three files are built into the program. They are served with fixed
	// content types (Windows would otherwise look these up in the registry, where other
	// software sometimes leaves wrong values that make browsers reject the files).
	static, _ := fs.Sub(uiFiles, "ui")
	pages := map[string][2]string{
		"/":          {"index.html", "text/html; charset=utf-8"},
		"/style.css": {"style.css", "text/css; charset=utf-8"},
		"/app.js":    {"app.js", "text/javascript; charset=utf-8"},
	}
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		entry, ok := pages[r.URL.Path]
		if !ok || (r.Method != http.MethodGet && r.Method != http.MethodHead) {
			http.NotFound(w, r)
			return
		}
		data, err := fs.ReadFile(static, entry[0])
		if err != nil {
			http.NotFound(w, r)
			return
		}
		w.Header().Set("Content-Type", entry[1])
		w.Write(data)
	})
	api := func(method string, h func(http.ResponseWriter, *http.Request)) http.HandlerFunc {
		return func(w http.ResponseWriter, r *http.Request) {
			if r.Method != method {
				writeError(w, http.StatusMethodNotAllowed, "method not allowed")
				return
			}
			token := r.Header.Get("X-VR-Token")
			if token == "" {
				token = r.URL.Query().Get("t")
			}
			if subtle.ConstantTimeCompare([]byte(token), []byte(s.token)) != 1 {
				writeError(w, http.StatusForbidden, "This window is no longer connected to the program. Close it and start Vocal Remover again.")
				return
			}
			defer func() {
				if rec := recover(); rec != nil {
					writeError(w, http.StatusInternalServerError, fmt.Sprintf("unexpected problem: %v", rec))
				}
			}()
			h(w, r)
		}
	}
	mux.HandleFunc("/api/hello", api("POST", s.handleHello))
	mux.HandleFunc("/api/bye", api("POST", s.handleBye))
	mux.HandleFunc("/api/upload", api("POST", s.handleUpload))
	mux.HandleFunc("/api/process", api("POST", s.handleProcess))
	mux.HandleFunc("/api/cancel", api("POST", s.handleCancel))
	mux.HandleFunc("/api/status", api("GET", s.handleStatus))
	mux.HandleFunc("/api/preview/", api("GET", s.handlePreview))
	mux.HandleFunc("/api/open-folder", api("POST", s.handleOpenFolder))
	mux.HandleFunc("/api/choose-folder", api("POST", s.handleChooseFolder))
	mux.HandleFunc("/api/gpu", api("POST", s.handleGPU))
	mux.HandleFunc("/api/gpu-test", api("POST", s.handleGPUTest))

	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Only answer requests addressed to this exact local address. This blocks other
		// web sites from reaching the program through tricks with look-alike host names.
		if r.Host != s.host && r.Host != "localhost:"+s.port {
			http.Error(w, "forbidden", http.StatusForbidden)
			return
		}
		if origin := r.Header.Get("Origin"); origin != "" && origin != "http://"+s.host && origin != "http://localhost:"+s.port {
			// Some browsers send "null" as the origin of the closing notification. That one
			// request only says "this window was closed" and still needs the key.
			if !(origin == "null" && r.URL.Path == "/api/bye") {
				http.Error(w, "forbidden", http.StatusForbidden)
				return
			}
		}
		h := w.Header()
		h.Set("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
		h.Set("X-Content-Type-Options", "nosniff")
		h.Set("X-Frame-Options", "DENY")
		h.Set("Referrer-Policy", "no-referrer")
		h.Set("Cross-Origin-Resource-Policy", "same-origin")
		h.Set("Cache-Control", "no-store")
		mux.ServeHTTP(w, r)
	})
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(v)
}

func writeError(w http.ResponseWriter, status int, msg string) {
	writeJSON(w, status, map[string]string{"error": msg})
}

func (s *server) handleHello(w http.ResponseWriter, r *http.Request) {
	page := randomHex(8)
	s.mu.Lock()
	s.pages[page] = true
	s.hadPage = true
	s.emptyAt = time.Time{}
	resp := map[string]any{
		"page":            page,
		"version":         version,
		"outputFolder":    defaultOutputDir(),
		"canChooseFolder": canPickFolder(),
		"runtime":         "ONNX Runtime " + s.engine.RuntimeVersion(),
		"cores":           runtime.NumCPU(),
		"maxMinutes":      audio.MaxSeconds / 60,
		"extensions":      audio.Extensions,
		"track":           s.trackInfoLocked(),
		"modes":           s.modesLocked(),
		"gpu":             s.gpuInfoLocked(),
	}
	s.mu.Unlock()
	writeJSON(w, http.StatusOK, resp)
}

// modesLocked lists the quality modes with whether their model files are present.
func (s *server) modesLocked() []map[string]any {
	var out []map[string]any
	for _, m := range separate.Modes {
		missing := []string{}
		for _, spec := range m.Models {
			if !s.engine.Available(spec) {
				missing = append(missing, spec.File)
			}
		}
		out = append(out, map[string]any{"key": m.Key, "label": m.Label, "describe": m.Describe, "quick": m.Quick,
			"available": len(missing) == 0, "missing": missing})
	}
	return out
}

// gpuInfoLocked describes the graphics processor for the window.
func (s *server) gpuInfoLocked() map[string]any {
	st := loadSettings(s.paths)
	available, why := gpuAvailable(s.paths)
	info := map[string]any{
		"name":      gpuName(),
		"available": available,
		"reason":    why,
		"mode":      s.gpuMode,
		"on":        s.gpuMode != "",
		"error":     s.gpuError,
		"tested":    st.GPUTested,
		"note":      st.GPUNote,
		"failed":    st.GPUFailed,
		"settings":  st.GPU,
	}
	if s.gpuTest != nil {
		info["test"] = map[string]any{"state": s.gpuTest.state, "stage": s.gpuTest.stage, "lines": s.gpuTest.lines, "error": s.gpuTest.err}
	}
	return info
}

// handleGPU switches the graphics processor on or off for the coming jobs: {"on": true|false, "mode": ""|"fused"|"unfused"}.
func (s *server) handleGPU(w http.ResponseWriter, r *http.Request) {
	var req struct {
		On   bool   `json:"on"`
		Mode string `json:"mode"`
	}
	if err := json.NewDecoder(io.LimitReader(r.Body, 1<<12)).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "The request could not be understood.")
		return
	}
	// Claim the work slot for the switch, so that no song or test can start while the graphics
	// runtime is loaded (that can take half a minute the first time).
	s.mu.Lock()
	if s.busyLocked() {
		s.mu.Unlock()
		writeError(w, http.StatusConflict, "Wait until the current work has finished before changing the graphics setting.")
		return
	}
	s.gpuSwitching = true
	s.mu.Unlock()
	mode := ""
	if req.On {
		mode = req.Mode
		if mode != "fused" && mode != "unfused" {
			mode = loadSettings(s.paths).GPU
			if mode == "" {
				mode = "fused"
			}
		}
	}
	err := func() (err error) {
		defer func() {
			if rec := recover(); rec != nil {
				err = fmt.Errorf("unexpected problem: %v", rec)
			}
		}()
		return enableGPU(s.paths, s.engine, mode, nil)
	}()
	s.mu.Lock()
	s.gpuSwitching = false
	if err != nil {
		s.gpuMode, s.gpuError = "", err.Error()
	} else {
		s.gpuMode, s.gpuError = mode, ""
	}
	// remember the choice (a test result stays as it is)
	st := loadSettings(s.paths)
	if err == nil {
		st.GPU = mode
		if st.GPUFailed != "" {
			// ticked again after a failure: the failure note has done its job
			st.GPUFailed, st.GPUNote = "", ""
		}
		saveSettings(s.paths, st)
	}
	info := s.gpuInfoLocked()
	s.mu.Unlock()
	if err != nil {
		writeJSON(w, http.StatusOK, map[string]any{"gpu": info, "error": capitalize(err.Error()) + "."})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"gpu": info})
}

// handleGPUTest starts the graphics test in the background; progress comes through /api/hello's gpu.test
// and /api/status.
func (s *server) handleGPUTest(w http.ResponseWriter, r *http.Request) {
	s.mu.Lock()
	if s.busyLocked() {
		s.mu.Unlock()
		writeError(w, http.StatusConflict, "Wait until the current work has finished.")
		return
	}
	if ok, why := gpuAvailable(s.paths); !ok {
		s.mu.Unlock()
		writeError(w, http.StatusBadRequest, capitalize(why)+".")
		return
	}
	t := &gpuTestJob{state: "running", stage: "Starting", started: time.Now()}
	s.gpuTest = t
	s.mu.Unlock()
	go func() {
		keepAwake(true)
		defer keepAwake(false)
		res, err := runGPUTest(s.paths, s.engine, func(msg string) {
			s.mu.Lock()
			t.stage = msg
			s.mu.Unlock()
		})
		s.mu.Lock()
		defer s.mu.Unlock()
		if err != nil {
			t.state, t.err = "error", capitalize(err.Error())+"."
			return
		}
		t.state, t.lines = "done", res.Lines
		// the test decides the setting; make the engine follow it
		mode := loadSettings(s.paths).GPU
		if e := enableGPU(s.paths, s.engine, mode, nil); e != nil {
			s.gpuMode, s.gpuError = "", e.Error()
		} else {
			s.gpuMode, s.gpuError = mode, ""
		}
	}()
	writeJSON(w, http.StatusOK, map[string]any{"started": true})
}

func (s *server) handleBye(w http.ResponseWriter, r *http.Request) {
	page := r.URL.Query().Get("page")
	s.mu.Lock()
	delete(s.pages, page)
	if len(s.pages) == 0 {
		s.emptyAt = time.Now()
	}
	s.mu.Unlock()
	w.WriteHeader(http.StatusNoContent)
}

func (s *server) trackInfoLocked() any {
	t := s.track
	if t == nil {
		return nil
	}
	return map[string]any{
		"id": t.id, "name": t.name, "format": t.track.Format, "detail": t.track.Detail,
		"rate": t.track.Rate, "channels": t.track.Channels, "seconds": t.track.Seconds(), "peaks": t.peaks,
	}
}

// waveformPeaks reduces a track to a few hundred loudness values for drawing.
func waveformPeaks(t *audio.Track, buckets int) []float32 {
	n := t.Frames()
	if n < buckets {
		buckets = n
	}
	out := make([]float32, buckets)
	for b := 0; b < buckets; b++ {
		lo, hi := n*b/buckets, n*(b+1)/buckets
		var p float32
		for i := lo; i < hi; i++ {
			v := t.L[i]
			if v < 0 {
				v = -v
			}
			u := t.R[i]
			if u < 0 {
				u = -u
			}
			if u > v {
				v = u
			}
			if v > p {
				p = v
			}
		}
		if p > 1 {
			p = 1
		}
		out[b] = float32(math.Round(float64(p)*1000) / 1000)
	}
	return out
}

func (s *server) handleUpload(w http.ResponseWriter, r *http.Request) {
	s.mu.Lock()
	busy := s.job != nil && s.job.state == "running"
	s.mu.Unlock()
	if busy {
		writeError(w, http.StatusConflict, "A song is being processed. Wait for it to finish or cancel it first.")
		return
	}
	if r.ContentLength > maxUploadBytes {
		writeError(w, http.StatusRequestEntityTooLarge, tooLargeMessage)
		return
	}
	// The buffer grows as the data arrives (the announced length is not reserved up front), and
	// reading stops one byte after the limit.
	var buf bytes.Buffer
	if r.ContentLength > 0 {
		buf.Grow(int(min(r.ContentLength, 32<<20)))
	}
	if _, err := io.Copy(&buf, io.LimitReader(r.Body, maxUploadBytes+1)); err != nil {
		writeError(w, http.StatusBadRequest, "The file could not be read completely. Please try again.")
		return
	}
	if buf.Len() > maxUploadBytes {
		writeError(w, http.StatusRequestEntityTooLarge, tooLargeMessage)
		return
	}
	track, err := audio.Decode(buf.Bytes())
	buf = bytes.Buffer{}
	if err != nil {
		writeError(w, http.StatusUnprocessableEntity, capitalize(err.Error())+".")
		return
	}
	name := r.URL.Query().Get("name")
	if name == "" {
		name = "song"
	}
	lt := &loadedTrack{id: randomHex(6), name: safeBaseName(name), track: track, peaks: waveformPeaks(track, 1600)}
	s.mu.Lock()
	if s.job != nil && s.job.state == "running" {
		s.mu.Unlock()
		writeError(w, http.StatusConflict, "A song is being processed. Wait for it to finish or cancel it first.")
		return
	}
	s.track = lt
	s.job = nil
	info := s.trackInfoLocked()
	s.mu.Unlock()
	writeJSON(w, http.StatusOK, info)
}

func capitalize(s string) string {
	if s == "" {
		return s
	}
	return strings.ToUpper(s[:1]) + s[1:]
}

type processRequest struct {
	Track   string  `json:"track"`
	Start   float64 `json:"start"`
	End     float64 `json:"end"`
	Quality string  `json:"quality"`
	Format  string  `json:"format"`
	Folder  string  `json:"folder"`
}

func (s *server) handleProcess(w http.ResponseWriter, r *http.Request) {
	var req processRequest
	if err := json.NewDecoder(io.LimitReader(r.Body, 1<<16)).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "The request could not be understood.")
		return
	}
	q, err := separate.ParseQuality(req.Quality)
	if err != nil {
		writeError(w, http.StatusBadRequest, capitalize(err.Error())+".")
		return
	}
	for _, spec := range separate.ModelsFor(q) {
		if !s.engine.Available(spec) {
			writeError(w, http.StatusBadRequest, "This mode needs the model file "+spec.File+", which is not in the models folder. Choose another mode, or put the model's pieces next to the program and start it again.")
			return
		}
	}
	format, err := parseFormat(req.Format)
	if err != nil {
		writeError(w, http.StatusBadRequest, capitalize(err.Error())+".")
		return
	}
	// Windows' "Copy as path" puts quotes around the path; accept that.
	folder := strings.TrimSpace(strings.Trim(strings.TrimSpace(req.Folder), `"`))
	if folder == "" {
		folder = defaultOutputDir()
	}
	if !filepath.IsAbs(folder) {
		writeError(w, http.StatusBadRequest, "Please give the full path of the folder to save into, for example "+defaultOutputDir())
		return
	}
	// Fail early if the folder cannot be used, rather than after minutes of processing.
	if err := checkWritable(folder); err != nil {
		writeError(w, http.StatusBadRequest, capitalize(err.Error())+".")
		return
	}

	s.mu.Lock()
	if s.busyLocked() {
		s.mu.Unlock()
		writeError(w, http.StatusConflict, "A song is already being processed (or the graphics processor is being tested or switched on).")
		return
	}
	useGPU := s.gpuMode != ""
	lt := s.track
	if lt == nil || lt.id != req.Track {
		s.mu.Unlock()
		writeError(w, http.StatusBadRequest, "Choose a song first.")
		return
	}
	total := lt.track.Seconds()
	start, end := req.Start, req.End
	if math.IsNaN(start) || math.IsNaN(end) || start < 0 {
		start = 0
	}
	if end <= 0 || end > total {
		end = total
	}
	if end-start < 0.1 {
		s.mu.Unlock()
		writeError(w, http.StatusBadRequest, "The selected part is empty. Set an end time that is after the start time.")
		return
	}
	whole := start <= 0.0005 && end >= total-0.0005
	ctx, cancel := context.WithCancel(context.Background())
	j := &job{id: randomHex(6), trackID: lt.id, state: "running", stage: "Starting", started: time.Now(), cancel: cancel,
		quality: q.String(), format: format.String()}
	s.job = j
	s.mu.Unlock()

	go func() {
		defer cancel()
		keepAwake(true)
		defer keepAwake(false)
		opt := separate.Options{Quality: q, Start: start, End: end, GPU: useGPU}
		if whole {
			opt.Start, opt.End = 0, 0
		}
		var res *separate.Result
		var saved *savedFiles
		err := func() (err error) {
			defer func() {
				if rec := recover(); rec != nil {
					err = fmt.Errorf("unexpected problem while processing: %v", rec)
				}
			}()
			res, err = s.engine.Separate(ctx, lt.track, opt, func(stage string, f float64) {
				s.mu.Lock()
				j.stage, j.fraction = stage, f
				s.mu.Unlock()
			})
			if err != nil {
				return err
			}
			s.mu.Lock()
			j.stage = "Saving files"
			s.mu.Unlock()
			saved, err = saveResult(res, folder, lt.name, format, whole)
			return err
		}()
		var instPrev, vocalPrev []byte
		if err == nil {
			instPrev = previewWAV(res.Instrumental, res.Rate, res.Mono)
			vocalPrev = previewWAV(res.Vocals, res.Rate, res.Mono)
		}
		s.mu.Lock()
		defer s.mu.Unlock()
		j.finished = time.Now()
		switch {
		case errors.Is(err, context.Canceled):
			j.state = "cancelled"
		case err != nil:
			j.state, j.err = "error", capitalize(err.Error())+"."
		default:
			j.state, j.saved, j.fraction = "done", saved, 1
			j.seconds = float64(res.EndFrame-res.StartFrame) / float64(res.Rate)
			j.instPrev, j.vocalPrev = instPrev, vocalPrev
			j.ranOn, j.gpuFallback = res.RanOn, res.GPUFallback
			if res.GPUFallback != "" {
				// the graphics processor let us down: stay on the processor until it is ticked again,
				// also after a restart
				s.gpuMode, s.gpuError = "", res.GPUFallback
				s.engine.DisableGPU()
				rememberGPUFailure(s.paths, res.GPUFallback)
			}
		}
	}()
	writeJSON(w, http.StatusOK, map[string]string{"job": j.id})
}

// previewWAV renders audio as a 16-bit WAV in memory for the players in the window.
func previewWAV(st separate.Stereo, rate int, mono bool) []byte {
	l, r := st[0], st[1]
	if mono {
		r = nil
	}
	// keep the preview from clipping; the saved files are handled separately
	gain := float32(1)
	if peak := audio.Peak(st[0], st[1]); peak > 0.999 {
		gain = 0.999 / peak
	}
	var buf bytes.Buffer
	buf.Grow(44 + len(l)*2*2)
	if err := audio.WriteWAV(&buf, l, r, rate, audio.PCM16, gain); err != nil {
		return nil
	}
	return buf.Bytes()
}

func (s *server) handleCancel(w http.ResponseWriter, r *http.Request) {
	s.mu.Lock()
	if s.job != nil && s.job.state == "running" {
		s.job.cancel()
	}
	s.mu.Unlock()
	w.WriteHeader(http.StatusNoContent)
}

func (s *server) handleStatus(w http.ResponseWriter, r *http.Request) {
	s.mu.Lock()
	defer s.mu.Unlock()
	j := s.job
	if j == nil {
		resp := map[string]any{"state": "idle"}
		if t := s.gpuTest; t != nil {
			resp["gpuTest"] = map[string]any{"state": t.state, "stage": t.stage, "lines": t.lines, "error": t.err}
			resp["gpu"] = s.gpuInfoLocked()
		}
		writeJSON(w, http.StatusOK, resp)
		return
	}
	resp := map[string]any{"job": j.id, "track": j.trackID, "state": j.state, "stage": j.stage, "fraction": j.fraction,
		"quality": j.quality, "format": j.format}
	if t := s.gpuTest; t != nil {
		resp["gpuTest"] = map[string]any{"state": t.state, "stage": t.stage, "lines": t.lines, "error": t.err}
		resp["gpu"] = s.gpuInfoLocked()
	}
	switch j.state {
	case "running":
		el := time.Since(j.started).Seconds()
		resp["elapsed"] = el
		if j.fraction > 0.03 {
			resp["remaining"] = el * (1 - j.fraction) / j.fraction
		}
	case "done":
		resp["elapsed"] = j.finished.Sub(j.started).Seconds()
		resp["seconds"] = j.seconds
		resp["instrumental"] = j.saved.Instrumental
		resp["vocals"] = j.saved.Vocals
		resp["folder"] = j.saved.Folder
		resp["note"] = j.saved.Note
		resp["ranOn"] = j.ranOn
		resp["gpuFallback"] = j.gpuFallback
	case "error":
		resp["error"] = j.err
	}
	writeJSON(w, http.StatusOK, resp)
}

func (s *server) handlePreview(w http.ResponseWriter, r *http.Request) {
	kind := strings.TrimPrefix(r.URL.Path, "/api/preview/")
	var data []byte
	var stamp time.Time
	switch kind {
	case "original":
		s.mu.Lock()
		lt := s.track
		s.mu.Unlock()
		if lt != nil {
			// built once per song, outside the lock so progress updates are never held up
			lt.previewOnce.Do(func() {
				lt.preview = previewWAV(separate.Stereo{lt.track.L, lt.track.R}, lt.track.Rate, lt.track.Channels == 1)
			})
			data = lt.preview
		}
	case "instrumental", "vocals":
		s.mu.Lock()
		if j := s.job; j != nil && j.state == "done" {
			stamp = j.finished
			if kind == "instrumental" {
				data = j.instPrev
			} else {
				data = j.vocalPrev
			}
		}
		s.mu.Unlock()
	}
	if data == nil {
		http.NotFound(w, r)
		return
	}
	w.Header().Set("Content-Type", "audio/wav")
	http.ServeContent(w, r, kind+".wav", stamp, bytes.NewReader(data))
}

func (s *server) handleOpenFolder(w http.ResponseWriter, r *http.Request) {
	s.mu.Lock()
	var dir string
	if j := s.job; j != nil && j.state == "done" {
		dir = j.saved.Folder
	}
	s.mu.Unlock()
	if dir == "" {
		writeError(w, http.StatusBadRequest, "There is no result folder to open yet.")
		return
	}
	if err := openFolder(dir); err != nil {
		writeError(w, http.StatusInternalServerError, "The folder could not be opened automatically. You can find it at "+dir)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func (s *server) handleChooseFolder(w http.ResponseWriter, r *http.Request) {
	if !canPickFolder() {
		writeError(w, http.StatusNotImplemented, "Type or paste the folder path instead.")
		return
	}
	var req struct {
		Folder string `json:"folder"`
	}
	json.NewDecoder(io.LimitReader(r.Body, 1<<16)).Decode(&req)
	exe, err := os.Executable()
	if err != nil {
		writeError(w, http.StatusInternalServerError, "Type or paste the folder path instead.")
		return
	}
	// The folder chooser runs as a separate short-lived copy of this program, so a problem
	// in the Windows dialog can never take the main program down with it.
	// It is deliberately never killed: the dialog disables the window behind it while open,
	// and only a dialog that closes normally re-enables that window.
	out, err := exec.Command(exe, "--pick-folder", req.Folder).Output()
	var exit *exec.ExitError
	switch {
	case err == nil:
		if p := strings.TrimSpace(string(out)); p != "" && filepath.IsAbs(p) {
			writeJSON(w, http.StatusOK, map[string]string{"folder": p})
			return
		}
		writeJSON(w, http.StatusOK, map[string]string{"folder": ""})
	case errors.As(err, &exit) && exit.ExitCode() == 1:
		writeJSON(w, http.StatusOK, map[string]string{"folder": ""}) // cancelled
	default:
		writeError(w, http.StatusInternalServerError, "The folder chooser could not be opened. Type or paste the folder path instead.")
	}
}
