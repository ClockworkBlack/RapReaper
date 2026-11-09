#!/usr/bin/env python3
"""Stand-in models of the REAPER stock plug-ins RapReaper uses.

TEST ONLY. These describe, for the simulated REAPER, which controls each
plug-in has, how a 0..1 control position maps to a displayed value, and (in
sim_render.py) roughly what the plug-in does to audio. They are deliberately
*not* tuned to match REAPER's real plug-ins exactly: RapReaper never relies on a
mapping -- it sets a control by its displayed value and then measures the
audio -- so the simulation only has to behave like the same *kind* of device.

Control names follow what REAPER reports where that is known:
  * ReaEQ: names and displays from a live parameter scan (REAPER 7, Aug 2026)
  * ReaComp: names from two independent published parameter lists
  * JS effects: slider names and ranges from the effects' own source files
  * ReaLimit: names are a guess (so the real names are resolved at run time)
REAPER appends its own Bypass / Wet / Delta controls to every plug-in; the
simulation does the same, which is why ReaComp ends up with two "Wet"s.
"""
import json
import math

WRAPPER = [
    {"name": "Bypass", "kind": "enum", "labels": ["normal", "bypassed"], "default": 0.0},
    {"name": "Wet", "kind": "lin", "min": 0.0, "max": 100.0, "fmt": "%.0f%%", "default": 1.0},
    {"name": "Delta", "kind": "enum", "labels": ["normal", "enabled"], "default": 0.0},
]


def _eq_band(label, hz, bw=2.0):
    k = 6.0
    n = math.log((hz - 20.0) / 23980.0 * (math.exp(k) - 1.0) + 1.0) / k
    return [
        {"name": "Freq-" + label, "kind": "expk", "min": 20.0, "max": 24000.0, "k": k, "fmt": "%.1f Hz", "default": n},
        {"name": "Gain-" + label, "kind": "ampdb", "amax": 4.0, "fmt": "%.1f dB", "default": 0.25},
        {"name": "BW-" + label, "kind": "lin", "min": 0.0, "max": 4.0, "fmt": "%.2f", "default": bw / 4.0},
    ]


def lin_default(lo, hi, v):
    return (v - lo) / float(hi - lo)


PLUGINS = {
    "reaeq": {
        "display": "VST: ReaEQ (Cockos)",
        "accept": ["ReaEQ (Cockos)", "ReaEQ", "VST: ReaEQ (Cockos)"],
        "params": (_eq_band("Low Shelf", 100.0) + _eq_band("Band 2", 300.0) + _eq_band("Band 3", 1000.0)
                   + _eq_band("High Shelf 4", 5000.0) + _eq_band("High Pass 5", 80.0)
                   + [{"name": "Global Gain", "kind": "ampdb", "amax": 1.0, "fmt": "%.1f dB", "default": 1.0}]),
    },
    "reacomp": {
        "display": "VST: ReaComp (Cockos)",
        "accept": ["ReaComp (Cockos)", "ReaComp", "VST: ReaComp (Cockos)"],
        "params": [
            {"name": "Thresh", "kind": "ampdb", "amax": 1.0, "fmt": "%.1f", "default": 1.0},
            {"name": "Ratio", "kind": "lin", "min": 1.0, "max": 101.0, "fmt": "%.2f", "default": lin_default(1, 101, 4)},
            {"name": "Attack", "kind": "lin", "min": 0.0, "max": 500.0, "fmt": "%.1f", "default": lin_default(0, 500, 3)},
            {"name": "Release", "kind": "lin", "min": 0.0, "max": 5000.0, "fmt": "%.0f", "default": lin_default(0, 5000, 100)},
            {"name": "Pre-comp", "kind": "lin", "min": 0.0, "max": 250.0, "fmt": "%.1f", "default": 0.0},
            {"name": "resvd", "kind": "lin", "min": 0.0, "max": 1.0, "fmt": "%.2f", "default": 0.0},
            {"name": "Lowpass", "kind": "lin", "min": 0.0, "max": 20000.0, "fmt": "%.0f", "default": 1.0},
            {"name": "Hipass", "kind": "lin", "min": 0.0, "max": 20000.0, "fmt": "%.0f", "default": 0.0},
            {"name": "SignIn", "kind": "enum", "labels": ["Main Input L+R", "Aux L+R", "Feedback"], "default": 0.0},
            {"name": "AudIn", "kind": "enum", "labels": ["Main Input L+R", "Aux L+R"], "default": 0.0},
            {"name": "Dry", "kind": "ampdb", "amax": 2.0, "fmt": "%.1f", "default": 0.0},
            {"name": "Wet", "kind": "ampdb", "amax": 2.0, "fmt": "%.1f", "default": 0.5},
            {"name": "PreviewF", "kind": "enum", "labels": ["Off", "On"], "default": 0.0},
            {"name": "RMS size", "kind": "lin", "min": 0.0, "max": 1000.0, "fmt": "%.1f", "default": lin_default(0, 1000, 5)},
            {"name": "Knee", "kind": "lin", "min": 0.0, "max": 24.0, "fmt": "%.1f", "default": 0.0},
            {"name": "Auto Make Up Gain", "kind": "enum", "labels": ["Off", "On"], "default": 1.0},
            {"name": "AutoRel", "kind": "enum", "labels": ["Off", "On"], "default": 0.0},
            {"name": "ClsAttk", "kind": "enum", "labels": ["Off", "On"], "default": 0.0},
        ],
    },
    "realimit": {
        "display": "VST: ReaLimit (Cockos)",
        "accept": ["ReaLimit (Cockos)", "ReaLimit", "VST: ReaLimit (Cockos)"],
        "params": [
            {"name": "Threshold", "kind": "lin", "min": -60.0, "max": 0.0, "fmt": "%.2f dB", "default": 1.0},
            {"name": "Brickwall ceiling", "kind": "lin", "min": -60.0, "max": 0.0, "fmt": "%.2f dB", "default": 1.0},
            {"name": "Release", "kind": "lin", "min": 0.0, "max": 1000.0, "fmt": "%.0f ms", "default": 0.015},
            {"name": "True peak", "kind": "enum", "labels": ["Off", "On"], "default": 0.0},
        ],
    },
    # The stock JS limiter that RapReaper falls back to on a REAPER without ReaLimit; sliders from its source file.
    "masterlimiter": {
        "display": "JS: Master Limiter",
        "accept": ["JS:loser/masterLimiter", "JS: Master Limiter"],
        "params": [
            {"name": "Threshold (dB)", "kind": "lin", "min": -20.0, "max": -0.1, "fmt": "%.1f", "default": (-3.0 + 20.0) / 19.9},
            {"name": "Look Ahead (us)", "kind": "lin", "min": 0.0, "max": 1000.0, "fmt": "%.0f", "default": 0.2},
            {"name": "Attack (us)", "kind": "lin", "min": 0.0, "max": 1000.0, "fmt": "%.0f", "default": 0.1},
            {"name": "Hold (ms)", "kind": "lin", "min": 0.0, "max": 10.0, "fmt": "%.1f", "default": 0.0},
            {"name": "Release (ms)", "kind": "lin", "min": 0.0, "max": 1000.0, "fmt": "%.0f", "default": 0.25},
            {"name": "Limit (dB)", "kind": "lin", "min": -6.0, "max": 0.0, "fmt": "%.2f", "default": (-0.1 + 6.0) / 6.0},
            {"name": "Reduction", "kind": "lin", "min": -60.0, "max": 0.0, "fmt": "%.1f", "default": 1.0},
        ],
    },
    "hpflpf": {
        "display": "JS: RBJ Highpass/Lowpass Filters",
        "accept": ["JS:sstillwell/hpflpf", "JS: RBJ Highpass/Lowpass Filters"],
        "params": [
            {"name": "HPF", "kind": "lin", "min": 0.0, "max": 1000.0, "fmt": "%.1f", "default": 0.0},
            {"name": "LPF", "kind": "lin", "min": 1000.0, "max": 22000.0, "fmt": "%.1f", "default": 1.0},
            {"name": "Gain", "kind": "lin", "min": -20.0, "max": 20.0, "fmt": "%.1f", "default": 0.5},
        ],
    },
    "deesser": {
        "display": "JS: De-esser",
        "accept": ["JS:Liteon/deesser", "JS: De-esser"],
        "params": [
            {"name": "Processing", "kind": "enum", "labels": ["Stereo", "Mono"], "default": 1.0},
            {"name": "Target Type", "kind": "enum", "labels": ["Bandpass", "Hipass"], "default": 1.0},
            {"name": "Monitor", "kind": "enum", "labels": ["Off", "On"], "default": 0.0},
            {"name": "Frequency (Hz)", "kind": "lin", "min": 1500.0, "max": 12000.0, "fmt": "%.0f", "default": lin_default(1500, 12000, 4000)},
            {"name": "Bandwidth (Oct)", "kind": "lin", "min": 0.1, "max": 3.1, "fmt": "%.2f", "default": lin_default(0.1, 3.1, 1.5)},
            {"name": "Threshold (dB)", "kind": "lin", "min": -80.0, "max": 0.0, "fmt": "%.2f", "default": lin_default(-80, 0, -25)},
            {"name": "Ratio", "kind": "lin", "min": 1.0, "max": 20.0, "fmt": "%.2f", "default": lin_default(1, 20, 4)},
            # "{E9}" stands for the single byte 0xE9 that the real effect file contains here (not valid UTF-8);
            # fake_reaper.lua puts the real byte in, so replies carry exactly what REAPER would send
            {"name": "Time Constants", "kind": "enum", "labels": ["A: 3 {E9}s - R: 50 ms", "A: 30 {E9}s - R: 100 ms", "A: 100 {E9}s - R: 300 ms"], "default": 0.0},
            {"name": "Gain (-inf/+24dB)", "kind": "lin", "min": -24.0, "max": 24.0, "fmt": "%.2f", "default": 0.5},
        ],
    },
    "expander": {
        "display": "JS: Downward Expander",
        "accept": ["JS:sstillwell/expander", "JS: Downward Expander"],
        "params": [
            {"name": "Threshold (dB)", "kind": "lin", "min": -120.0, "max": 0.0, "fmt": "%.1f", "default": 0.0},
            {"name": "Ratio", "kind": "lin", "min": 1.0, "max": 20.0, "fmt": "%.1f", "default": 0.0},
            {"name": "Gain", "kind": "lin", "min": -20.0, "max": 20.0, "fmt": "%.1f", "default": 0.5},
            {"name": "Detector Input", "kind": "enum", "labels": ["Normal", "Sidechain"], "default": 0.0},
            {"name": "Detection", "kind": "enum", "labels": ["Peak (Blown Capacitor)", "RMS (Blown Capacitor)", "Peak", "RMS"], "default": 2.0 / 3.0},
            {"name": "Attack (ms)", "kind": "lin", "min": 0.0, "max": 200.0, "fmt": "%.0f", "default": 0.15},
            {"name": "Release (ms)", "kind": "lin", "min": 0.0, "max": 100.0, "fmt": "%.0f", "default": 0.02},
        ],
    },
    "volume_pan": {
        "display": "JS: Volume/Pan Smoother",
        "accept": ["JS:utility/volume_pan", "JS: Volume/Pan Smoother"],
        "params": [
            {"name": "Volume (dB)", "kind": "lin", "min": -60.0, "max": 12.0, "fmt": "%.1f", "default": lin_default(-60, 12, 0)},
            {"name": "Pan", "kind": "lin", "min": -100.0, "max": 100.0, "fmt": "%.1f", "default": 0.5},
            {"name": "Pan Law (dB)", "kind": "lin", "min": -6.0, "max": 6.0, "fmt": "%.1f", "default": 0.5},
        ],
    },
    # RapReaper's own JS effect (kit/effects/RapReaper/ducks.jsfx): one control, 0..1, 1 = full level, 0 = -40 dB
    "rapreaper_ducks": {
        "display": "JS: RapReaper Ducks (volume dips drawn by RapReaper)",
        "accept": ["JS:RapReaper/ducks.jsfx", "JS: RapReaper Ducks"],
        "params": [
            {"name": "Level (0 = -40 dB, 1 = full)", "kind": "lin", "min": 0.0, "max": 1.0, "fmt": "%.4f", "default": 1.0},
        ],
    },
    "tonegen": {
        "display": "JS: Tone Generator",
        "accept": ["JS:synthesis/tonegenerator", "JS: Tone Generator"],
        "params": [
            {"name": "Wet Mix (dB)", "kind": "lin", "min": -120.0, "max": 6.0, "fmt": "%.0f", "default": lin_default(-120, 6, -12)},
            {"name": "Dry Mix (dB)", "kind": "lin", "min": -120.0, "max": 6.0, "fmt": "%.0f", "default": lin_default(-120, 6, -6)},
            {"name": "Base Frequency (Hz)", "kind": "lin", "min": 20.0, "max": 24000.0, "fmt": "%.0f", "default": lin_default(20, 24000, 440)},
            {"name": "Note", "kind": "enum", "labels": ["A", "A#", "B", "C", "C#", "D", "D#", "E", "F", "F#", "G", "G#"], "default": 0.0},
            {"name": "Octave", "kind": "lin", "min": -4.0, "max": 4.0, "fmt": "%.0f", "default": 0.5},
            {"name": "Fine Tune (cents)", "kind": "lin", "min": -100.0, "max": 100.0, "fmt": "%.0f", "default": 0.5},
            {"name": "Shape", "kind": "enum", "labels": ["Sine", "Triangle", "Saw"], "default": 0.0},
        ],
    },
    # Not a real plug-in: a stand-in for "some third-party VST" whose controls show numbers with
    # decoration ('4.0:1') and whose switches are plain 0..1 controls, as most VST plug-ins' are.
    "testcomp": {
        "display": "VST: TestComp (Sim)",
        "accept": ["TestComp", "TestComp (Sim)", "VST: TestComp (Sim)"],
        "params": [
            {"name": "Ratio", "kind": "lin", "min": 1.0, "max": 21.0, "fmt": "%.1f:1", "default": lin_default(1, 21, 4)},
            {"name": "Mode", "kind": "enum", "labels": ["Clean", "Vintage", "Vintage Hard"], "default": 0.0},
            {"name": "Mix", "kind": "lin", "min": 0.0, "max": 100.0, "fmt": "%.0f%%", "default": 1.0},
            {"name": "Auto", "kind": "enum", "labels": ["0", "1"], "default": 0.0},
            {"name": "Drive", "kind": "steps", "min": 0.0, "max": 10.0, "step": 5.0, "fmt": "%.0f", "default": 0.0},
        ],
    },
    "reaverbate": {
        "display": "VST: ReaVerbate (Cockos)",
        "accept": ["ReaVerbate (Cockos)", "ReaVerbate", "VST: ReaVerbate (Cockos)"],
        "params": [
            {"name": "Wet", "kind": "ampdb", "amax": 2.0, "fmt": "%.1f", "default": 0.25},
            {"name": "Dry", "kind": "ampdb", "amax": 2.0, "fmt": "%.1f", "default": 0.5},
            {"name": "Room size", "kind": "lin", "min": 0.0, "max": 100.0, "fmt": "%.0f", "default": 0.5},
            {"name": "Dampening", "kind": "lin", "min": 0.0, "max": 100.0, "fmt": "%.0f", "default": 0.5},
        ],
    },
    # --- added for RapReaper 1.1: voice effects. Control names for ReaPitch, ReaDelay and the JS effects are the ones
    # a real REAPER 7.81 reported through RapReaper's self-test (Oct 2026); the ranges are the simulation's own.
    "reatune": {
        "display": "VST: ReaTune (Cockos)",
        "accept": ["ReaTune (Cockos)", "ReaTune", "VST: ReaTune (Cockos)"],
        "params": [],
        # ReaTune keeps its settings in a block, not in controls. By default the simulation hands out the 104-byte
        # block a real REAPER 7.81 gave (REATUNE_REAL_CHUNK: correction off, all twelve notes allowed, attack 250 ms),
        # which RapReaper's built-in layout knows. The harness can give it REATUNE_SIM32_CHUNK instead, a 32-byte
        # block of the simulation's own ("RTUN", version, correction on/off, key 0-11, mode 0-9, attack ms (float),
        # stereo on/off, algorithm), which RapReaper does NOT know: that is how the tests cover learning a layout.
        "chunk_default": None,
    },
    "reapitch": {
        "display": "VST: ReaPitch (Cockos)",
        "accept": ["ReaPitch (Cockos)", "ReaPitch", "VST: ReaPitch (Cockos)"],
        "params": [
            {"name": "Wet", "kind": "ampdb", "amax": 2.0, "fmt": "%.1f", "default": 0.5},
            {"name": "Dry", "kind": "ampdb", "amax": 2.0, "fmt": "%.1f", "default": 0.0},
            {"name": "1: Enabled", "kind": "enum", "labels": ["Off", "On"], "default": 1.0},
            {"name": "1: Shift (full range)", "kind": "lin", "min": -24.0, "max": 24.0, "fmt": "%.2f", "default": 0.5},
            {"name": "1: Shift (cents)", "kind": "lin", "min": -100.0, "max": 100.0, "fmt": "%.1f", "default": 0.5},
            {"name": "1: Shift (semitones)", "kind": "steps", "min": -12.0, "max": 12.0, "step": 1.0, "fmt": "%.0f", "default": 0.5},
            {"name": "1: Shift (oct)", "kind": "steps", "min": -2.0, "max": 2.0, "step": 1.0, "fmt": "%.0f", "default": 0.5},
            {"name": "1: Formant adjust (full range)", "kind": "lin", "min": -24.0, "max": 24.0, "fmt": "%.2f", "default": 0.5},
            {"name": "1: Formant adjust (cents)", "kind": "lin", "min": -100.0, "max": 100.0, "fmt": "%.1f", "default": 0.5},
            {"name": "1: Formant adjust (semitones)", "kind": "steps", "min": -12.0, "max": 12.0, "step": 1.0, "fmt": "%.0f", "default": 0.5},
            {"name": "1: Volume", "kind": "ampdb", "amax": 2.0, "fmt": "%.1f", "default": 0.5},
            {"name": "1: Pan", "kind": "lin", "min": -1.0, "max": 1.0, "fmt": "%.2f", "default": 0.5},
        ],
    },
    "readelay": {
        "display": "VST: ReaDelay (Cockos)",
        "accept": ["ReaDelay (Cockos)", "ReaDelay", "VST: ReaDelay (Cockos)"],
        "params": [
            {"name": "Wet", "kind": "ampdb", "amax": 2.0, "fmt": "%.1f", "default": 0.5},
            {"name": "Dry", "kind": "ampdb", "amax": 2.0, "fmt": "%.1f", "default": 0.5},
            {"name": "1: Length (time)", "kind": "lin", "min": 0.0, "max": 10000.0, "fmt": "%.1f ms", "default": 0.03},
            {"name": "1: Length (musical)", "kind": "lin", "min": 0.0, "max": 8.0, "fmt": "%.3f", "default": 0.0},
            {"name": "1: Feedback", "kind": "ampdb", "amax": 1.0, "fmt": "%.1f dB", "default": 0.0},
            {"name": "1: Lowpass", "kind": "lin", "min": 0.0, "max": 24000.0, "fmt": "%.0f Hz", "default": 1.0},
            {"name": "1: Hipass", "kind": "lin", "min": 0.0, "max": 24000.0, "fmt": "%.0f Hz", "default": 0.0},
            {"name": "1: Volume", "kind": "ampdb", "amax": 2.0, "fmt": "%.1f dB", "default": 0.5},
            {"name": "1: Pan", "kind": "lin", "min": -1.0, "max": 1.0, "fmt": "%.2f", "default": 0.5},
            {"name": "1: Enabled", "kind": "enum", "labels": ["Off", "On"], "default": 1.0},
        ],
    },
    # REAPER's stock JS 'Chorus' (Effects/guitar/chorus): its six sliders
    "chorus": {
        "display": "JS: Chorus",
        "accept": ["JS:guitar/chorus", "JS:Guitar/chorus", "JS: Chorus", "JS:Chorus/chorus"],
        "params": [
            {"name": "Chorus length (ms)", "kind": "lin", "min": 0.0, "max": 200.0, "fmt": "%.1f", "default": 0.125},
            {"name": "Number of voices", "kind": "lin", "min": 2.0, "max": 8.0, "fmt": "%.0f", "default": 0.0},
            {"name": "Rate (Hz)", "kind": "lin", "min": 0.0, "max": 8.0, "fmt": "%.2f", "default": 0.125},
            {"name": "Pitch fudge factor", "kind": "lin", "min": 0.0, "max": 4.0, "fmt": "%.2f", "default": 0.125},
            {"name": "Wet mix (dB)", "kind": "lin", "min": -120.0, "max": 6.0, "fmt": "%.1f", "default": lin_default(-120, 6, -6)},
            {"name": "Dry mix (dB)", "kind": "lin", "min": -120.0, "max": 6.0, "fmt": "%.1f", "default": lin_default(-120, 6, 0)},
        ],
    },
    # REAPER's stock JS 'Delay w/ Chorus' (Effects/delay/delay_chorus): RapReaper's fallback when guitar/chorus is missing
    "delay_chorus": {
        "display": "JS: Delay w/ Chorus",
        "accept": ["JS:delay/delay_chorus", "JS:Delay/delay_chorus"],
        "params": [
            {"name": "Delay (ms)", "kind": "lin", "min": 0.0, "max": 4000.0, "fmt": "%.0f", "default": lin_default(0, 4000, 300)},
            {"name": "Feedback (dB)", "kind": "lin", "min": -120.0, "max": 6.0, "fmt": "%.0f", "default": lin_default(-120, 6, -5)},
            {"name": "Output Wet (Chorus) (dB)", "kind": "lin", "min": -120.0, "max": 6.0, "fmt": "%.0f", "default": lin_default(-120, 6, -6)},
            {"name": "Output Wet (Clean) (dB)", "kind": "lin", "min": -120.0, "max": 6.0, "fmt": "%.0f", "default": lin_default(-120, 6, -6)},
            {"name": "Output Dry (dB)", "kind": "lin", "min": -120.0, "max": 6.0, "fmt": "%.0f", "default": lin_default(-120, 6, 0)},
            {"name": "Chorus Period (ms)", "kind": "lin", "min": 1.0, "max": 1000.0, "fmt": "%.0f", "default": lin_default(1, 1000, 200)},
            {"name": "Chorus Length (ms)", "kind": "lin", "min": 0.0, "max": 100.0, "fmt": "%.0f", "default": 0.1},
        ],
    },
    # REAPER's stock JS 'Saturation' (Effects/loser/saturation): one slider
    "saturation": {
        "display": "JS: Saturation",
        "accept": ["JS:loser/saturation", "JS:LOSER/saturation", "JS: Saturation"],
        "params": [
            {"name": "Amount (%)", "kind": "lin", "min": 0.0, "max": 100.0, "fmt": "%.1f", "default": 0.0},
        ],
    },
}

for _p in PLUGINS.values():
    _p["params"] = _p["params"] + [dict(w) for w in WRAPPER]

# ReaTune's settings block as a real REAPER 7.81 handed it out (default state: correction off, every note allowed,
# attack 250 ms), 104 bytes. RapReaper's built-in layout decodes it; the simulation hands it out by default.
REATUNE_REAL_CHUNK = ("00000000000000001e000000b801000000000000ff0f0000fa000000320000003200000000000000000000000000000000008042"
                      "000040410000000000002042010000000100000000000b0001000000000000000000000000000000000000000000000000000000")
# the simulation's own 32-byte block ("RTUN" ...), which RapReaper does not know: for the tests of learning a layout
REATUNE_SIM32_CHUNK = "UlRVTgEAAAAAAAAAAAAAAAAAAAAAAMhCAAAAAAAAAAA="
PLUGINS["reatune"]["chunk_default"] = __import__("base64").b64encode(bytes.fromhex(REATUNE_REAL_CHUNK)).decode("ascii")

# the layout of the simulation's own 32-byte block, for the tests that learn it (see plugin_state's save_layout)
SIM_FORMATS = {"reatune": {"length": 32, "magic": {"offset": 0, "hex": "5254554e"},
                           "fields": {"version": {"offset": 4, "type": "i32"}, "correction_on": {"offset": 8, "type": "i32"},
                                      "key": {"offset": 12, "type": "i32"}, "mode": {"offset": 16, "type": "i32"},
                                      "attack_ms": {"offset": 20, "type": "f32"}, "stereo": {"offset": 24, "type": "i32"},
                                      "algo": {"offset": 28, "type": "i32"}}}}


def value(spec, norm):
    """Control position (0..1) -> the real value the control represents."""
    kind = spec["kind"]
    n = min(max(float(norm), 0.0), 1.0)
    if kind == "lin":
        return spec["min"] + n * (spec["max"] - spec["min"])
    if kind == "steps":
        return spec["min"] + math.floor(n * (spec["max"] - spec["min"]) / spec["step"] + 0.5) * spec["step"]
    if kind == "expk":
        k = spec["k"]
        return spec["min"] + (spec["max"] - spec["min"]) * (math.exp(k * n) - 1.0) / (math.exp(k) - 1.0)
    if kind == "ampdb":
        amp = n * spec["amax"]
        return -150.0 if amp <= 3.2e-8 else max(-150.0, 20.0 * math.log10(amp))
    if kind == "enum":
        return int(round(n * (len(spec["labels"]) - 1)))
    raise ValueError(kind)


if __name__ == "__main__":
    import sys
    json.dump(PLUGINS, open(sys.argv[1], "w"), indent=1)
