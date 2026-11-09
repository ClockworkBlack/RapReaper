package separate

import (
	"context"
	"errors"
	"fmt"
	"strings"

	"vocalremover/internal/audio"
	"vocalremover/internal/dsp"
)

// Quality selects which networks run and how.
type Quality int

const (
	// Standard runs the full-band MDX-Net instrumental network once (quick).
	Standard Quality = iota
	// Best (MDX-Net) also runs an MDX-Net vocal network and blends both opinions (quick).
	Best
	// Maximum runs the same two MDX-Net networks combined to leave as little voice as possible (quick).
	Maximum
	// Ultimate runs the BS-Roformer network with half-window steps (version 1.3's reference mode).
	Ultimate
	// UltimateMax is Ultimate followed by the extra suppression step.
	UltimateMax
	// UltimateFast runs the BS-Roformer network with the one-second crossfade plan.
	UltimateFast
	// Mel runs the Mel-Roformer network with the crossfade plan: the default since 2.0, measured at
	// least as accurate as the BS-Roformer in well under half the time.
	Mel
	// MelFull runs the Mel-Roformer with half-window steps (about 1.7x the time of Mel).
	MelFull
	// MelMax is Mel followed by the extra suppression step.
	MelMax
	// Ensemble averages the Mel-Roformer's and the BS-Roformer's vocal estimates (the slowest mode; two
	// networks agreeing leave less of either one's mistakes).
	Ensemble
)

// Mode describes one quality level for menus and command lines.
type Mode struct {
	Quality  Quality
	Key      string // command-line name
	Label    string
	Describe string
	Quick    bool // MDX-Net (older, quicker networks)
	Models   []ModelSpec
}

// Modes lists every mode in menu order; the first entry is the default.
var Modes = []Mode{
	{Mel, "mel", "Mel-Roformer (recommended)",
		"the Mel-Band Roformer network: as accurate as the BS-Roformer or better, in well under half the time", false, []ModelSpec{MelRoformer}},
	{MelFull, "melfull", "Mel-Roformer, thorough",
		"the same network with every moment processed twice and blended: the reference behaviour, about 1.7x the time", false, []ModelSpec{MelRoformer}},
	{MelMax, "melmax", "Mel-Roformer + extra suppression",
		"as above, then everything the network still attributes to the voice is turned down further; a little of the music under the voice goes too", false, []ModelSpec{MelRoformer}},
	{Ensemble, "ensemble", "Two networks (Mel + BS-Roformer)",
		"both Roformer networks, their estimates averaged: the most accurate and the slowest (about four times Mel-Roformer)", false, []ModelSpec{MelRoformer, BSRoformer}},
	{UltimateFast, "ultimatefast", "BS-Roformer (version 1.3's default)",
		"the BS-Roformer network alone, crossfade plan; about three times the time of Mel-Roformer", false, []ModelSpec{BSRoformer}},
	{Ultimate, "ultimate", "BS-Roformer, thorough", "the BS-Roformer with half-window steps", false, []ModelSpec{BSRoformer}},
	{UltimateMax, "ultimatemax", "BS-Roformer + extra suppression", "BS-Roformer, then the extra suppression step", false, []ModelSpec{BSRoformer}},
	{Maximum, "max", "Quick: maximum removal", "two MDX-Net networks combined to leave little voice; the music is less accurate", true, []ModelSpec{InstHQ4, KimVocal2}},
	{Best, "best", "Quick: best", "two MDX-Net networks blended for clean music; a faint trace of the voice can remain", true, []ModelSpec{InstHQ4, KimVocal2}},
	{Standard, "standard", "Quick: standard", "one MDX-Net network; the quickest", true, []ModelSpec{InstHQ4}},
}

// ModeFor finds a mode by its key (aliases included) or quality.
func ModeFor(q Quality) Mode {
	for _, m := range Modes {
		if m.Quality == q {
			return m
		}
	}
	return Modes[0]
}

// ParseQuality understands the command-line and menu names (and version 1.3's spellings).
func ParseQuality(s string) (Quality, error) {
	key := strings.ToLower(strings.TrimSpace(s))
	switch key {
	case "":
		return Mel, nil
	case "ultimate-fast", "ufast":
		key = "ultimatefast"
	case "ultimate-max", "ultimate+":
		key = "ultimatemax"
	case "fast":
		key = "standard"
	case "maximum":
		key = "max"
	case "mel-full", "thorough":
		key = "melfull"
	case "mel-max", "mel+", "clean":
		key = "melmax"
	case "both", "two":
		key = "ensemble"
	}
	for _, m := range Modes {
		if m.Key == key {
			return m.Quality, nil
		}
	}
	keys := make([]string, 0, len(Modes))
	for _, m := range Modes {
		keys = append(keys, m.Key)
	}
	return 0, fmt.Errorf("unknown quality %q (use %s)", s, strings.Join(keys, ", "))
}

func (q Quality) String() string { return ModeFor(q).Label }

// Key is the command-line name of the quality.
func (q Quality) Key() string { return ModeFor(q).Key }

// Options controls a separation run.
type Options struct {
	Quality Quality
	// Start and End select the part of the song to process, in seconds. End <= 0 means
	// "to the end of the song".
	Start, End float64
	// GPU asks for the graphics processor for the Roformer networks (when the engine has one enabled).
	GPU bool
}

// Progress receives the current stage and overall completion between 0 and 1.
type Progress func(stage string, fraction float64)

// Result holds the two output tracks at the source's sample rate.
type Result struct {
	Rate         int
	Mono         bool
	Instrumental Stereo
	Vocals       Stereo
	StartFrame   int // first source sample included
	EndFrame     int // one past the last source sample included
	Models       []string
	// Where the Roformer networks ran: "graphics processor (DirectML)", "processor" ...; and why the
	// graphics processor was given up on, if it was.
	RanOn       string
	GPUFallback string
}

const (
	overlap = 0.25 // how much neighbouring MDX-Net processing windows overlap
	// Seconds of audio processed before and after the requested span, then discarded, so
	// the networks hear what leads into and out of it.
	contextSeconds = 4.0
	// How hard the suppression modes turn down spots the networks attribute to the voice (0..1).
	maxSuppression = 1.0
	// The MDX vocal network only sees frequencies below 17.6 kHz; above this the full-band
	// instrumental network decides alone.
	blendPassHz = 16800
	blendStopHz = 17400
)

// AllModels lists every network the program can use (processor files; the graphics twins are made
// from them).
func AllModels() []ModelSpec { return []ModelSpec{MelRoformer, BSRoformer, InstHQ4, KimVocal2} }

// ModelsFor lists the networks a quality level needs.
func ModelsFor(q Quality) []ModelSpec { return ModeFor(q).Models }

// vocals estimates the vocal track of a stereo mix sampled at ModelRate.
func (e *Engine) vocals(ctx context.Context, mix Stereo, opt Options, progress Progress, res *Result) (Stereo, error) {
	q := opt.Quality
	if !ModeFor(q).Quick {
		return e.vocalsRoformer(ctx, mix, opt, progress, res)
	}
	specs := ModelsFor(q)
	report := func(index int, spec ModelSpec) func(done, total int) {
		return func(done, total int) {
			if progress != nil {
				frac := (float64(index) + float64(done)/float64(total)) / float64(len(specs))
				progress(fmt.Sprintf("Separating (pass %d of %d)", index+1, len(specs)), frac)
			}
		}
	}
	res.RanOn = "processor"
	report(0, InstHQ4)(0, 1)
	inst, err := e.demix(ctx, InstHQ4, mix, overlap, report(0, InstHQ4))
	if err != nil {
		return Stereo{}, err
	}
	if q == Best || q == Maximum {
		report(1, KimVocal2)(0, 1)
		voc, err := e.demix(ctx, KimVocal2, mix, overlap, report(1, KimVocal2))
		if err != nil {
			return Stereo{}, err
		}
		// Two estimates of the instrumental: the instrumental network's own output, and
		// "mix minus the vocal network's vocals". Average them where both are valid.
		for c := range inst {
			if q == Maximum {
				other := voc[c] // turn "vocals" into the second instrumental estimate, in place
				for i := range other {
					other[i] = mix[c][i] - other[i]
				}
				inst[c] = dsp.SuppressBlend(mix[c], inst[c], other, maxSuppression)
				voc[c] = nil
				continue
			}
			diff := make([]float32, len(inst[c]))
			for i := range diff {
				diff[i] = (mix[c][i] - voc[c][i]) - inst[c][i]
			}
			voc[c] = nil
			diff = dsp.LowPass(diff, ModelRate, blendPassHz, blendStopHz)
			for i := range diff {
				inst[c][i] += 0.5 * diff[i]
			}
		}
	}
	// Vocals are whatever the instrumental does not contain, so the two always add up to
	// exactly the original recording. (The instrumental estimate is overwritten in place.)
	for c := range inst {
		for i := range inst[c] {
			inst[c][i] = mix[c][i] - inst[c][i]
		}
	}
	return inst, nil
}

// vocalsRoformer estimates the vocal track with one Roformer network or two.
func (e *Engine) vocalsRoformer(ctx context.Context, mix Stereo, opt Options, progress Progress, res *Result) (Stereo, error) {
	q := opt.Quality
	plan := rofPlanFast
	if q == Ultimate || q == UltimateMax || q == MelFull {
		plan = rofPlanFull
	}
	specs := ModelsFor(q)
	var voc Stereo
	var reports []*RofReport
	for i, spec := range specs {
		stage := "Separating (" + spec.Name + ")"
		if len(specs) > 1 {
			stage = fmt.Sprintf("Separating (%d of %d: %s)", i+1, len(specs), spec.Name)
		}
		report := func(done, total int) {
			if progress != nil {
				progress(stage, (float64(i)+float64(done)/float64(total))/float64(len(specs)))
			}
		}
		report(0, 1)
		est, rep, err := e.demixRoformer(ctx, spec, mix, plan, opt.GPU, report)
		if err != nil {
			return Stereo{}, err
		}
		reports = append(reports, rep)
		if i == 0 {
			voc = est
			continue
		}
		// average the estimates in place
		for c := range voc {
			a, b := voc[c], est[c]
			for j := range a {
				a[j] = 0.5 * (a[j] + b[j])
			}
		}
	}
	ranGPU, fallbacks := true, []string{}
	for _, r := range reports {
		if !r.GPU {
			ranGPU = false
		}
		if r.Fallback != "" {
			fallbacks = append(fallbacks, r.Fallback)
		}
	}
	switch {
	case ranGPU && reports[0].GPUMode == "unfused":
		res.RanOn = "graphics processor (" + e.gpu.provider + ", one operation at a time)"
	case ranGPU:
		res.RanOn = "graphics processor (" + e.gpu.provider + ")"
	default:
		res.RanOn = "processor"
	}
	res.GPUFallback = strings.Join(fallbacks, "; ")
	if q == UltimateMax || q == MelMax {
		if progress != nil {
			progress("Extra suppression", 1)
		}
		for c := range voc {
			inst := make([]float32, len(voc[c]))
			for i := range inst {
				inst[i] = mix[c][i] - voc[c][i]
			}
			inst = dsp.SuppressBlend(mix[c], inst, inst, maxSuppression)
			for i := range inst {
				voc[c][i] = mix[c][i] - inst[i]
			}
		}
	}
	return voc, nil
}

// Separate splits the requested span of a track into instrumental and vocals.
func (e *Engine) Separate(ctx context.Context, t *audio.Track, opt Options, progress Progress) (*Result, error) {
	n := t.Frames()
	if n == 0 {
		return nil, errors.New("the audio is empty")
	}
	rate := t.Rate
	start := int(opt.Start*float64(rate) + 0.5)
	end := n
	if opt.End > 0 {
		end = int(opt.End*float64(rate) + 0.5)
	}
	if start < 0 {
		start = 0
	}
	if end > n {
		end = n
	}
	if end-start < rate/10 {
		return nil, errors.New("the selected time span is empty or shorter than a tenth of a second")
	}
	for _, spec := range ModelsFor(opt.Quality) {
		if !e.Available(spec) {
			return nil, fmt.Errorf("model file %s is missing from the models folder", spec.File)
		}
	}
	margin := int(contextSeconds * float64(rate))
	r0, r1 := start-margin, end+margin
	if r0 < 0 {
		r0 = 0
	}
	if r1 > n {
		r1 = n
	}
	if progress != nil {
		progress("Preparing", 0)
	}
	region := Stereo{t.L[r0:r1], t.R[r0:r1]}
	mix := region
	if rate != ModelRate {
		mix = Stereo{dsp.Resample(region[0], rate, ModelRate), dsp.Resample(region[1], rate, ModelRate)}
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	res := &Result{Rate: rate, Mono: t.Channels == 1, StartFrame: start, EndFrame: end}
	voc, err := e.vocals(ctx, mix, opt, progress, res)
	if err != nil {
		return nil, err
	}
	if progress != nil {
		progress("Finishing", 1)
	}
	if rate != ModelRate {
		for c := range voc {
			up := dsp.Resample(voc[c], ModelRate, rate)
			fit := make([]float32, r1-r0)
			copy(fit, up)
			voc[c] = fit
		}
	}
	for _, spec := range ModelsFor(opt.Quality) {
		res.Models = append(res.Models, spec.Name)
	}
	length := end - start
	res.Instrumental = NewStereo(length)
	src := [2][]float32{t.L, t.R}
	for c := 0; c < 2; c++ {
		res.Vocals[c] = voc[c][start-r0 : start-r0+length]
	}
	if res.Mono {
		// A mono source was processed as two identical channels; fold the result back.
		for i := 0; i < length; i++ {
			m := 0.5 * (res.Vocals[0][i] + res.Vocals[1][i])
			res.Vocals[0][i], res.Vocals[1][i] = m, m
		}
	}
	for c := 0; c < 2; c++ {
		o := src[c][start:end]
		for i := 0; i < length; i++ {
			res.Instrumental[c][i] = o[i] - res.Vocals[c][i]
		}
	}
	return res, nil
}
