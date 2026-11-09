// Package ort is a minimal, cgo-free binding to the ONNX Runtime C API.
//
// Only the handful of functions needed to load a model from memory and run it on the CPU
// are bound. Function positions in the OrtApi table are fixed by ONNX Runtime's ABI
// guarantee (new functions are only ever appended), and were taken from the official
// onnxruntime_c_api.h header.
package ort

import (
	"errors"
	"fmt"
	"os"
	"runtime"
	"sort"
	"unsafe"

	"github.com/ebitengine/purego"
)

// Positions of the functions we use inside the OrtApi function table.
const (
	fnGetErrorMessage                       = 2
	fnCreateEnv                             = 3
	fnDisableTelemetryEvents                = 6
	fnCreateSessionFromArray                = 8
	fnRun                                   = 9
	fnCreateSessionOptions                  = 10
	fnSetSessionExecutionMode               = 13
	fnDisableMemPattern                     = 17
	fnDisableCpuMemArena                    = 19
	fnSetSessionGraphOptimizationLevel      = 23
	fnSetIntraOpNumThreads                  = 24
	fnSetInterOpNumThreads                  = 25
	fnSessionGetInputCount                  = 30
	fnSessionGetOutputCount                 = 31
	fnSessionGetInputTypeInfo               = 33
	fnCreateTensorWithDataAsOrtValue        = 49
	fnGetTensorMutableData                  = 51
	fnCastTypeInfoToTensorInfo              = 55
	fnGetDimensionsCount                    = 61
	fnGetDimensions                         = 62
	fnGetTensorTypeAndShape                 = 65
	fnCreateCpuMemoryInfo                   = 69
	fnReleaseEnv                            = 92
	fnReleaseStatus                         = 93
	fnReleaseMemoryInfo                     = 94
	fnReleaseSession                        = 95
	fnReleaseValue                          = 96
	fnReleaseTypeInfo                       = 98
	fnReleaseTensorTypeAndShapeInfo         = 99
	fnReleaseSessionOptions                 = 100
	fnAddSessionConfigEntry                 = 130 // since API level 7 (ONNX Runtime 1.7)
	fnSessionOptionsAppendExecutionProvider = 216 // the generic "append an execution provider by name" (since 1.12)

	// The API level we ask the library for. Everything above exists since level 1-8, so any
	// ONNX Runtime from the last several years satisfies it.
	apiVersion = 12

	loggingLevelError = 3
	optEnableAll      = 99
	execSequential    = 0
	allocatorArena    = 1
	memTypeDefault    = 0
	elemTypeFloat     = 1
)

// Runtime is a loaded ONNX Runtime library plus one environment.
type Runtime struct {
	api     uintptr
	env     uintptr
	lib     uintptr
	version string
	path    string
}

// at turns an address inside ONNX Runtime's own memory (outside Go's heap, so the garbage collector
// never moves it) into a pointer. Written this way so that go vet does not mistake it for a Go pointer
// kept as a number.
func at(addr uintptr) unsafe.Pointer { return *(*unsafe.Pointer)(unsafe.Pointer(&addr)) }

func (r *Runtime) fn(index int) uintptr {
	return *(*uintptr)(at(r.api + uintptr(index)*unsafe.Sizeof(uintptr(0))))
}

func cstr(s string) []byte { return append([]byte(s), 0) }

func gostr(p uintptr) string {
	if p == 0 {
		return ""
	}
	n := 0
	for *(*byte)(at(p + uintptr(n))) != 0 {
		n++
		if n > 1<<16 {
			break
		}
	}
	return string(unsafe.Slice((*byte)(at(p)), n))
}

// check converts an OrtStatus* into a Go error (and frees the status).
func (r *Runtime) check(status uintptr) error {
	if status == 0 {
		return nil
	}
	msgPtr, _, _ := purego.SyscallN(r.fn(fnGetErrorMessage), status)
	msg := gostr(msgPtr)
	purego.SyscallN(r.fn(fnReleaseStatus), status)
	return errors.New("onnxruntime: " + msg)
}

// Load opens the ONNX Runtime shared library at the given absolute path.
func Load(libPath string) (*Runtime, error) {
	// The Mac and Linux builds of ONNX Runtime carry Microsoft's usage reporting, which (unless told
	// otherwise) writes a device ID under ~/.cache/Microsoft and sends usage events over the internet.
	// This setting switches it off; the library reads it when it is loaded and when it sets itself up,
	// so it is set before the library is opened. (DisableTelemetryEvents below is called as well.)
	os.Setenv("ORT_DISABLE_TELEMETRY", "1")
	h, err := loadLibrary(libPath)
	if err != nil {
		return nil, fmt.Errorf("cannot load %s: %w", libPath, err)
	}
	getBase, err := findSymbol(h, "OrtGetApiBase")
	if err != nil {
		return nil, fmt.Errorf("%s is not an ONNX Runtime library: %w", libPath, err)
	}
	base, _, _ := purego.SyscallN(getBase)
	if base == 0 {
		return nil, errors.New("onnxruntime: OrtGetApiBase returned nothing")
	}
	// struct OrtApiBase { const OrtApi* (*GetApi)(uint32_t); const char* (*GetVersionString)(void); }
	getAPI := *(*uintptr)(at(base))
	getVersion := *(*uintptr)(at(base + unsafe.Sizeof(uintptr(0))))
	r := &Runtime{lib: h, path: libPath}
	v, _, _ := purego.SyscallN(getVersion)
	r.version = gostr(v)
	r.api, _, _ = purego.SyscallN(getAPI, apiVersion)
	if r.api == 0 {
		return nil, fmt.Errorf("onnxruntime %s is too old for this program", r.version)
	}
	logID := cstr("vocalremover")
	var env uintptr
	st, _, _ := purego.SyscallN(r.fn(fnCreateEnv), loggingLevelError, uintptr(unsafe.Pointer(&logID[0])), uintptr(unsafe.Pointer(&env)))
	runtime.KeepAlive(logID)
	if err := r.check(st); err != nil {
		return nil, err
	}
	r.env = env
	// Official Windows builds can emit usage events to the local Windows event tracing
	// system, and the Mac and Linux builds have the usage reporting switched off above.
	// Switch it off here as well: this program reports nothing to anyone.
	st, _, _ = purego.SyscallN(r.fn(fnDisableTelemetryEvents), r.env)
	if err := r.check(st); err != nil {
		return nil, err
	}
	return r, nil
}

// Version reports the library's version string, e.g. "1.30.0".
func (r *Runtime) Version() string { return r.version }

// Path reports where the library was loaded from.
func (r *Runtime) Path() string { return r.path }

// HasSymbol reports whether the library exports a function of this name (for example the
// DirectML or CoreML provider entry point).
func (r *Runtime) HasSymbol(name string) bool {
	_, err := findSymbol(r.lib, name)
	return err == nil
}

// Session is a loaded model with one float32 input and one float32 output of fixed shape.
type Session struct {
	rt       *Runtime
	sess     uintptr
	memInfo  uintptr
	inValue  uintptr
	inName   []byte
	outName  []byte
	Input    []float32 // write the input tensor here before calling Run
	InShape  []int64
	pin      runtime.Pinner
	model    []byte
	inNames  [1]uintptr
	outNames [1]uintptr
	inVals   [1]uintptr
	outVals  [1]uintptr
}

// Options tunes how ONNX Runtime manages memory for one session.
type Options struct {
	Threads int // <= 0 lets ONNX Runtime pick
	// KeepMemPattern leaves ONNX Runtime's memory-pattern planning on. Off, the runtime
	// allocates every intermediate result separately; on, it plans one block for a run,
	// which is much better for networks made of thousands of small nodes.
	KeepMemPattern bool
	// KeepArena leaves the growing memory arena on; off, plain allocations are used.
	KeepArena bool
	// Provider names an execution provider to add in front of the CPU: "DML" (DirectML,
	// Windows graphics processors) or "CoreML" (Apple). Empty means the processor only.
	Provider string
	// ProviderOptions are passed to the provider as key/value strings (keys sorted).
	ProviderOptions map[string]string
	// Config holds session configuration entries (ONNX Runtime's string keys).
	Config map[string]string
	// DeviceID selects the graphics adapter for DirectML (0 = the default one).
	DeviceID int
}

// ErrProvider marks a failure to set up the requested execution provider (the graphics
// processor), as opposed to a problem with the model.
type ErrProvider struct{ Err error }

func (e *ErrProvider) Error() string { return e.Err.Error() }
func (e *ErrProvider) Unwrap() error { return e.Err }

// NewSession loads a model from memory with the MDX-Net defaults (no memory pattern, arena on).
func (r *Runtime) NewSession(model []byte, inputName, outputName string, threads int) (*Session, error) {
	return r.NewSessionWith(model, inputName, outputName, Options{Threads: threads, KeepArena: true})
}

// NewSessionWith loads a model from memory.
// The model's single input must be a float tensor with fixed dimensions after the batch
// dimension (rank 2 to 6); batch is fixed to 1.
func (r *Runtime) NewSessionWith(model []byte, inputName, outputName string, o Options) (*Session, error) {
	threads := o.Threads
	if len(model) == 0 {
		return nil, errors.New("empty model")
	}
	s := &Session{rt: r, model: model, inName: cstr(inputName), outName: cstr(outputName)}
	ok := false
	defer func() {
		if !ok {
			s.Close()
		}
	}()

	var opts uintptr
	st, _, _ := purego.SyscallN(r.fn(fnCreateSessionOptions), uintptr(unsafe.Pointer(&opts)))
	if err := r.check(st); err != nil {
		return nil, err
	}
	defer purego.SyscallN(r.fn(fnReleaseSessionOptions), opts)
	st, _, _ = purego.SyscallN(r.fn(fnSetSessionGraphOptimizationLevel), opts, optEnableAll)
	if err := r.check(st); err != nil {
		return nil, err
	}
	if threads > 0 {
		st, _, _ = purego.SyscallN(r.fn(fnSetIntraOpNumThreads), opts, uintptr(threads))
		if err := r.check(st); err != nil {
			return nil, err
		}
	}
	if !o.KeepMemPattern {
		// For the MDX-Net networks the pattern reserves one large block sized for the whole
		// network up front, which roughly doubles memory use and gains no speed.
		st, _, _ = purego.SyscallN(r.fn(fnDisableMemPattern), opts)
		if err := r.check(st); err != nil {
			return nil, err
		}
	}
	st, _, _ = purego.SyscallN(r.fn(fnSetInterOpNumThreads), opts, 1)
	if err := r.check(st); err != nil {
		return nil, err
	}
	for _, kv := range sortedPairs(o.Config) {
		k, v := cstr(kv[0]), cstr(kv[1])
		st, _, _ = purego.SyscallN(r.fn(fnAddSessionConfigEntry), opts, uintptr(unsafe.Pointer(&k[0])), uintptr(unsafe.Pointer(&v[0])))
		runtime.KeepAlive(k)
		runtime.KeepAlive(v)
		if err := r.check(st); err != nil {
			return nil, err
		}
	}
	if o.Provider != "" {
		// Graphics providers need the simple execution plan: one node after another, no
		// memory-pattern planning (DirectML refuses anything else).
		st, _, _ = purego.SyscallN(r.fn(fnSetSessionExecutionMode), opts, execSequential)
		if err := r.check(st); err != nil {
			return nil, err
		}
		if o.KeepMemPattern {
			st, _, _ = purego.SyscallN(r.fn(fnDisableMemPattern), opts)
			if err := r.check(st); err != nil {
				return nil, err
			}
		}
		if err := r.appendProvider(opts, o); err != nil {
			return nil, &ErrProvider{Err: err}
		}
	}
	if !o.KeepArena {
		// Plain allocations instead of the growing arena, which keeps the largest amount
		// of memory a run ever needed.
		st, _, _ = purego.SyscallN(r.fn(fnDisableCpuMemArena), opts)
		if err := r.check(st); err != nil {
			return nil, err
		}
	}

	st, _, _ = purego.SyscallN(r.fn(fnCreateSessionFromArray), r.env,
		uintptr(unsafe.Pointer(&model[0])), uintptr(len(model)), opts, uintptr(unsafe.Pointer(&s.sess)))
	runtime.KeepAlive(model)
	if err := r.check(st); err != nil {
		return nil, err
	}

	var nIn, nOut uintptr
	st, _, _ = purego.SyscallN(r.fn(fnSessionGetInputCount), s.sess, uintptr(unsafe.Pointer(&nIn)))
	if err := r.check(st); err != nil {
		return nil, err
	}
	st, _, _ = purego.SyscallN(r.fn(fnSessionGetOutputCount), s.sess, uintptr(unsafe.Pointer(&nOut)))
	if err := r.check(st); err != nil {
		return nil, err
	}
	if nIn != 1 || nOut != 1 {
		return nil, fmt.Errorf("unexpected model: %d inputs, %d outputs", nIn, nOut)
	}

	shape, err := s.inputShape()
	if err != nil {
		return nil, err
	}
	if len(shape) < 2 || len(shape) > 6 {
		return nil, fmt.Errorf("unexpected model input rank %d", len(shape))
	}
	shape[0] = 1
	n := int64(1)
	for _, d := range shape {
		if d <= 0 || d > 1<<20 {
			return nil, fmt.Errorf("unexpected model input shape %v", shape)
		}
		n *= d
	}
	if n > 1<<28 {
		return nil, fmt.Errorf("model input too large %v", shape)
	}
	s.InShape = shape
	s.Input = make([]float32, n)
	s.pin.Pin(&s.Input[0])
	s.pin.Pin(&s.InShape[0])
	s.pin.Pin(&s.inName[0])
	s.pin.Pin(&s.outName[0])

	st, _, _ = purego.SyscallN(r.fn(fnCreateCpuMemoryInfo), allocatorArena, memTypeDefault, uintptr(unsafe.Pointer(&s.memInfo)))
	if err := r.check(st); err != nil {
		return nil, err
	}
	st, _, _ = purego.SyscallN(r.fn(fnCreateTensorWithDataAsOrtValue), s.memInfo,
		uintptr(unsafe.Pointer(&s.Input[0])), uintptr(len(s.Input)*4),
		uintptr(unsafe.Pointer(&s.InShape[0])), uintptr(len(s.InShape)), elemTypeFloat,
		uintptr(unsafe.Pointer(&s.inValue)))
	if err := r.check(st); err != nil {
		return nil, err
	}
	s.inNames[0] = uintptr(unsafe.Pointer(&s.inName[0]))
	s.outNames[0] = uintptr(unsafe.Pointer(&s.outName[0]))
	s.inVals[0] = s.inValue
	ok = true
	return s, nil
}

func (s *Session) inputShape() ([]int64, error) {
	r := s.rt
	var typeInfo, tensorInfo, count uintptr
	st, _, _ := purego.SyscallN(r.fn(fnSessionGetInputTypeInfo), s.sess, 0, uintptr(unsafe.Pointer(&typeInfo)))
	if err := r.check(st); err != nil {
		return nil, err
	}
	defer purego.SyscallN(r.fn(fnReleaseTypeInfo), typeInfo)
	st, _, _ = purego.SyscallN(r.fn(fnCastTypeInfoToTensorInfo), typeInfo, uintptr(unsafe.Pointer(&tensorInfo)))
	if err := r.check(st); err != nil {
		return nil, err
	}
	if tensorInfo == 0 {
		return nil, errors.New("model input is not a tensor")
	}
	st, _, _ = purego.SyscallN(r.fn(fnGetDimensionsCount), tensorInfo, uintptr(unsafe.Pointer(&count)))
	if err := r.check(st); err != nil {
		return nil, err
	}
	if count == 0 || count > 8 {
		return nil, fmt.Errorf("unexpected model input rank %d", count)
	}
	dims := make([]int64, count)
	st, _, _ = purego.SyscallN(r.fn(fnGetDimensions), tensorInfo, uintptr(unsafe.Pointer(&dims[0])), count)
	if err := r.check(st); err != nil {
		return nil, err
	}
	return dims, nil
}

// Run executes the model on s.Input and copies the result into out, which must have the
// same number of elements as the input (these models map a spectrogram to a spectrogram).
func (s *Session) Run(out []float32) error {
	r := s.rt
	s.outVals[0] = 0
	st, _, _ := purego.SyscallN(r.fn(fnRun), s.sess, 0,
		uintptr(unsafe.Pointer(&s.inNames[0])), uintptr(unsafe.Pointer(&s.inVals[0])), 1,
		uintptr(unsafe.Pointer(&s.outNames[0])), 1, uintptr(unsafe.Pointer(&s.outVals[0])))
	if err := r.check(st); err != nil {
		return err
	}
	val := s.outVals[0]
	defer purego.SyscallN(r.fn(fnReleaseValue), val)

	var info, count uintptr
	st, _, _ = purego.SyscallN(r.fn(fnGetTensorTypeAndShape), val, uintptr(unsafe.Pointer(&info)))
	if err := r.check(st); err != nil {
		return err
	}
	defer purego.SyscallN(r.fn(fnReleaseTensorTypeAndShapeInfo), info)
	st, _, _ = purego.SyscallN(r.fn(fnGetDimensionsCount), info, uintptr(unsafe.Pointer(&count)))
	if err := r.check(st); err != nil {
		return err
	}
	if count == 0 || count > 8 {
		return fmt.Errorf("unexpected model output rank %d", count)
	}
	dims := make([]int64, count)
	st, _, _ = purego.SyscallN(r.fn(fnGetDimensions), info, uintptr(unsafe.Pointer(&dims[0])), count)
	if err := r.check(st); err != nil {
		return err
	}
	n := int64(1)
	for _, d := range dims {
		if d <= 0 {
			return fmt.Errorf("unexpected model output shape %v", dims)
		}
		n *= d
	}
	if n != int64(len(out)) {
		return fmt.Errorf("model output has %d values (shape %v), expected %d", n, dims, len(out))
	}
	var data uintptr
	st, _, _ = purego.SyscallN(r.fn(fnGetTensorMutableData), val, uintptr(unsafe.Pointer(&data)))
	if err := r.check(st); err != nil {
		return err
	}
	if data == 0 {
		return errors.New("model returned no data")
	}
	copy(out, unsafe.Slice((*float32)(at(data)), len(out)))
	return nil
}

// Close releases the session. Safe to call more than once.
func (s *Session) Close() {
	r := s.rt
	if s.inValue != 0 {
		purego.SyscallN(r.fn(fnReleaseValue), s.inValue)
		s.inValue = 0
	}
	if s.memInfo != 0 {
		purego.SyscallN(r.fn(fnReleaseMemoryInfo), s.memInfo)
		s.memInfo = 0
	}
	if s.sess != 0 {
		purego.SyscallN(r.fn(fnReleaseSession), s.sess)
		s.sess = 0
	}
	s.pin.Unpin()
	s.Input = nil
	s.model = nil
}

func sortedPairs(m map[string]string) [][2]string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	out := make([][2]string, 0, len(keys))
	for _, k := range keys {
		out = append(out, [2]string{k, m[k]})
	}
	return out
}

// appendProvider adds the graphics execution provider to the session options.
//
// DirectML: the library's own entry point OrtSessionOptionsAppendExecutionProvider_DML is
// used when it is exported (every DirectML build of ONNX Runtime exports it); otherwise the
// generic "append by name" call. CoreML: the generic call with its string options, falling
// back to the older OrtSessionOptionsAppendExecutionProvider_CoreML entry point.
func (r *Runtime) appendProvider(opts uintptr, o Options) error {
	switch o.Provider {
	case "DML":
		if fn, err := findSymbol(r.lib, "OrtSessionOptionsAppendExecutionProvider_DML"); err == nil {
			st, _, _ := purego.SyscallN(fn, opts, uintptr(o.DeviceID))
			return r.check(st)
		}
		if o.ProviderOptions == nil {
			o.ProviderOptions = map[string]string{}
		}
		if _, ok := o.ProviderOptions["device_id"]; !ok {
			o.ProviderOptions["device_id"] = fmt.Sprint(o.DeviceID)
		}
		return r.appendByName(opts, "DML", o.ProviderOptions)
	case "CoreML":
		err := r.appendByName(opts, "CoreML", o.ProviderOptions)
		if err == nil {
			return nil
		}
		if fn, ferr := findSymbol(r.lib, "OrtSessionOptionsAppendExecutionProvider_CoreML"); ferr == nil {
			const createMLProgram = 0x010
			st, _, _ := purego.SyscallN(fn, opts, createMLProgram)
			return r.check(st)
		}
		return err
	}
	return r.appendByName(opts, o.Provider, o.ProviderOptions)
}

func (r *Runtime) appendByName(opts uintptr, name string, options map[string]string) error {
	pairs := sortedPairs(options)
	cname := cstr(name)
	keys := make([][]byte, len(pairs))
	vals := make([][]byte, len(pairs))
	keyPtrs := make([]uintptr, len(pairs)+1)
	valPtrs := make([]uintptr, len(pairs)+1)
	for i, kv := range pairs {
		keys[i], vals[i] = cstr(kv[0]), cstr(kv[1])
		keyPtrs[i] = uintptr(unsafe.Pointer(&keys[i][0]))
		valPtrs[i] = uintptr(unsafe.Pointer(&vals[i][0]))
	}
	st, _, _ := purego.SyscallN(r.fn(fnSessionOptionsAppendExecutionProvider), opts, uintptr(unsafe.Pointer(&cname[0])),
		uintptr(unsafe.Pointer(&keyPtrs[0])), uintptr(unsafe.Pointer(&valPtrs[0])), uintptr(len(pairs)))
	runtime.KeepAlive(cname)
	runtime.KeepAlive(keys)
	runtime.KeepAlive(vals)
	return r.check(st)
}
