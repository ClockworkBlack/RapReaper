#!/usr/bin/env python3
"""voice2.py -- TEST ONLY. A second synthetic voice, written independently of make_test_audio.py
during the second review of RapReaper, with a ground-truth map of what is where.

voice2() builds a rap-like line out of separate, non-overlapping events:
  vowel      pulse train through formants (loud)                 the body of each syllable
  sib        band noise 5-9 kHz                                  S / T sounds
  weak       broadband noise 1.5-7 kHz, 25-32 dB under vowels    F / TH / H sounds
  tail       the last vowel of a phrase dying away over ~0.35 s  word endings
  breath     soft noise 400-3000 Hz, ~35 dB under the vowels     the breath before a phrase
Every event is recorded in `events` as (kind, start_sample, end_sample), so the gain a plug-in chain
applied to each kind can be measured directly instead of trusting the tool's own report."""
import numpy as np
from scipy import signal

SR = 48000

VOWELS = {
    "a": ([730, 1090, 2440, 3400], [90, 110, 160, 250]),
    "e": ([530, 1840, 2480, 3400], [70, 110, 160, 250]),
    "i": ([300, 2250, 3000, 3600], [60, 110, 170, 250]),
    "o": ([570, 840, 2410, 3300], [80, 100, 160, 250]),
    "u": ([350, 900, 2300, 3300], [70, 100, 160, 250]),
}


def _formant(x, sr, freqs, bws, gains=None):
    y = np.zeros_like(x)
    for i, (f, bw) in enumerate(zip(freqs, bws)):
        r = np.exp(-np.pi * bw / sr)
        a = [1.0, -2.0 * r * np.cos(2 * np.pi * f / sr), r * r]
        y += signal.lfilter([1.0 - r], a, x) * (1.0 if gains is None else gains[i])
    return y


def _band_noise(rng, n, lo, hi, sr):
    sos = signal.butter(3, [lo, hi], btype="band", fs=sr, output="sos")
    return signal.sosfilt(sos, rng.standard_normal(n + 400))[400:]


def voice2(seconds=30.0, sr=SR, seed=1, f0=115.0, spread_db=10.0, vowel_peak=0.36, sib_db=-18.0, weak_db=-28.0,
           breath_db=-36.0, tail_s=0.35, f0_jitter=0.012, tilt=0.97, sib_band=(5200, 9000), formant_gains=None,
           phrase_gap=(0.45, 0.8), plosives=False, vibrato=0.06):
    """Returns (mono signal, events). *_db values are RMS levels relative to the vowel PEAK level of that phrase."""
    rng = np.random.default_rng(seed)
    n = int(seconds * sr)
    out = np.zeros(n)
    events = []
    names = list(VOWELS)

    def put(kind, i0, seg):
        seg = seg[: max(0, n - i0)]
        if len(seg) < 8:
            return i0
        out[i0:i0 + len(seg)] += seg
        events.append((kind, i0, i0 + len(seg)))
        return i0 + len(seg)

    t = 0.4
    phrase = 0
    while t < seconds - 2.5:
        level = vowel_peak * 10 ** ((-spread_db * (phrase % 3) / 2.0) / 20.0)
        i = int(t * sr)
        # breath before the phrase
        bl = int(rng.uniform(0.18, 0.3) * sr)
        br = _band_noise(rng, bl, 400, 3000, sr)
        br = br / np.sqrt(np.mean(br ** 2)) * level * 10 ** (breath_db / 20.0) * np.hanning(bl) ** 0.5
        i = put("breath", i, br) + int(0.04 * sr)
        nsyl = int(rng.integers(6, 11))
        for s in range(nsyl):
            r = rng.random()
            if r < 0.30:                                   # S / T
                sl = int(rng.uniform(0.06, 0.11) * sr)
                sn = _band_noise(rng, sl, sib_band[0], sib_band[1], sr)
                sn = sn / np.sqrt(np.mean(sn ** 2)) * level * 10 ** (sib_db / 20.0) * np.hanning(sl) ** 0.5
                i = put("sib", i, sn)
            elif r < 0.55:                                 # F / TH / H
                wl = int(rng.uniform(0.06, 0.1) * sr)
                wn = _band_noise(rng, wl, 1500, 7000, sr)
                wn = wn / np.sqrt(np.mean(wn ** 2)) * level * 10 ** (weak_db / 20.0) * np.hanning(wl) ** 0.5
                i = put("weak", i, wn)
            if plosives and rng.random() < 0.12:
                pl = int(0.045 * sr)
                tt = np.arange(pl) / sr
                put("plosive", i, np.sin(2 * np.pi * 62 * tt) * np.exp(-tt / 0.012) * 1.4 * level)
            last = s == nsyl - 1
            syl = rng.uniform(0.11, 0.2) + (tail_s if last else 0.0)
            ns = int(syl * sr)
            pitch = f0 * (1.0 + vibrato * np.sin(2 * np.pi * 0.7 * (i / sr)) + rng.normal(0, f0_jitter))
            ph = np.cumsum(np.full(ns, pitch / sr))
            pulses = (np.diff(np.floor(ph), prepend=0) > 0).astype(float)
            src = signal.lfilter([1.0], [1.0, -tilt], pulses - pulses.mean())
            fr, bw = VOWELS[names[rng.integers(len(names))]]
            v = _formant(src, sr, fr, bw, formant_gains)
            body = int((syl - (tail_s if last else 0.0)) * sr)
            env = np.ones(ns)
            edge = int(0.012 * sr)
            env[:edge] = np.linspace(0, 1, edge)
            if last:                                       # the word dies away: -40 dB over tail_s
                env[body:] = 10 ** (-40.0 * np.arange(ns - body) / max(1, ns - body) / 20.0)
            else:
                env[-edge:] = np.linspace(1, 0, edge)
            v = v / (np.max(np.abs(v[:body])) + 1e-9) * env * level
            if last:
                put("vowel", i, v[:body])
                i = put("tail", i + body, v[body:])
            else:
                i = put("vowel", i, v)
            i += int(rng.uniform(0.01, 0.04) * sr)
        t = i / sr + rng.uniform(*phrase_gap)
        phrase += 1
    sos = signal.butter(2, 60.0, btype="high", fs=sr, output="sos")
    return signal.sosfilt(sos, out), events


def room_noise(n, sr=SR, level_db=-60.0, seed=9, kind="hiss"):
    """Background noise at an RMS level in dBFS. 'room' = pink-ish with a low hump (computer fan, traffic)."""
    rng = np.random.default_rng(seed)
    w = rng.standard_normal(n)
    if kind == "room":
        b = [0.049922035, -0.095993537, 0.050612699, -0.004408786]
        a = [1, -2.494956002, 2.017265875, -0.522189400]
        w = signal.lfilter(b, a, w)
    return w / np.sqrt(np.mean(w ** 2)) * 10 ** (level_db / 20.0)


def event_levels(x, events, sr=SR, shift=0, trim=0.01):
    """RMS level (dBFS) of every event in x (x starts `shift` samples after the material's start)."""
    rows = {}
    x = np.asarray(x, dtype=np.float64)
    if x.ndim > 1:
        x = x[:, 0]
    k = int(trim * sr)
    for kind, a, b in events:
        a2, b2 = a - shift + k, b - shift - k
        if a2 < 0 or b2 > len(x) or b2 - a2 < int(0.02 * sr):
            continue
        seg = x[a2:b2]
        rows.setdefault(kind, []).append(10 * np.log10(max(float(np.mean(seg * seg)), 1e-24)))
    return rows


def gap_mask(n, events, sr=SR, shift=0, guard=0.08):
    """True where nothing at all is sounding (at least `guard` s away from any event)."""
    m = np.ones(n, bool)
    g = int(guard * sr)
    for _kind, a, b in events:
        a2, b2 = max(0, a - shift - g), min(n, b - shift + g)
        if b2 > a2:
            m[a2:b2] = False
    return m


def tail_profile(x, events, sr=SR, shift=0, parts=4):
    """Level of each quarter of every word ending (the 'tail' events), averaged: shows whether endings get chopped."""
    x = np.asarray(x, dtype=np.float64)
    if x.ndim > 1:
        x = x[:, 0]
    acc = [[] for _ in range(parts)]
    for kind, a, b in events:
        if kind != "tail":
            continue
        a2, b2 = a - shift, b - shift
        if a2 < 0 or b2 > len(x):
            continue
        step = (b2 - a2) // parts
        for p in range(parts):
            seg = x[a2 + p * step:a2 + (p + 1) * step]
            acc[p].append(10 * np.log10(max(float(np.mean(seg * seg)), 1e-24)))
    return [round(float(np.mean(v)), 1) if v else None for v in acc]
