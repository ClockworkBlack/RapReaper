#!/usr/bin/env python3
"""effects_sim.py -- TEST ONLY. Effects of known size, applied to a dry test voice, so that RapReaper's
effect detectors can be checked against the truth.

  melodic_voice()   a sung/rapped line on real notes of a chosen key, with a tuning mode:
                    'natural' (pitch glides, scoop into notes, vibrato), 'tight' (notes reached in ~40 ms,
                    almost no drift), 'hard' (pitch snaps to the note in a few ms, no drift at all)
  reverb()          exponentially decaying noise tail with a chosen RT60 and wet level (dark or bright)
  delay()           feedback echo with a chosen time, feedback and wet level
  doubler()         a second copy, detuned and late, panned to one side
  telephone()       band-pass 300 Hz - 3.4 kHz
  compress()        a plain feed-forward compressor (RMS detector), to a chosen ratio/threshold
"""
import numpy as np
from scipy import signal

SR = 48000
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
MAJOR = [0, 2, 4, 5, 7, 9, 11]
MINOR = [0, 2, 3, 5, 7, 8, 10]
VOWELS = {
    "a": ([730, 1090, 2440, 3400], [90, 110, 160, 250]),
    "e": ([530, 1840, 2480, 3400], [70, 110, 160, 250]),
    "i": ([300, 2250, 3000, 3600], [60, 110, 170, 250]),
    "o": ([570, 840, 2410, 3300], [80, 100, 160, 250]),
    "u": ([350, 900, 2300, 3300], [70, 100, 160, 250]),
}


def _formant(x, sr, freqs, bws):
    y = np.zeros_like(x)
    for f, bw in zip(freqs, bws):
        r = np.exp(-np.pi * bw / sr)
        a = [1.0, -2.0 * r * np.cos(2 * np.pi * f / sr), r * r]
        y += signal.lfilter([1.0 - r], a, x)
    return y


def note_hz(midi):
    return 440.0 * 2.0 ** ((midi - 69) / 12.0)


def melodic_voice(seconds=24.0, sr=SR, seed=1, key="F#", minor=True, low_midi=50, mode="natural", sib=True):
    """A line of 'sung' syllables on notes of the key. Returns (mono, events) where events are
    (start_sample, end_sample, midi_note, kind) with kind 'note' or 'sib'."""
    rng = np.random.default_rng(seed)
    root = NOTE_NAMES.index(key)
    scale = [root + i for i in (MINOR if minor else MAJOR)]
    notes = sorted({m for m in range(low_midi, low_midi + 17) if m % 12 in [s % 12 for s in scale]})
    n = int(seconds * sr)
    out = np.zeros(n)
    events = []
    t = 0.4
    cur = int(rng.choice(notes))
    while t < seconds - 1.0:
        phrase = int(rng.integers(5, 9))
        for s in range(phrase):
            if sib and rng.random() < 0.3:
                sl = int(rng.uniform(0.06, 0.1) * sr)
                i0 = int(t * sr)
                sos = signal.butter(3, [5200, 9000], btype="band", fs=sr, output="sos")
                ns = signal.sosfilt(sos, rng.standard_normal(sl + 400))[400:]
                ns = ns / np.sqrt(np.mean(ns ** 2)) * 0.36 * 10 ** (-18 / 20.0) * np.hanning(sl) ** 0.5
                out[i0:i0 + len(ns[: n - i0])] += ns[: n - i0]
                events.append((i0, i0 + sl, None, "sib"))
                t += sl / sr
            # next note: mostly a step within the scale, now and then a leap; the tonic, fifth and third are
            # favoured the way a real melody favours them, and phrases tend to start and end at home
            weights = []
            for m in notes:
                deg = (m - root) % 12
                w_ = 3.0 if deg == 0 else 2.0 if deg == 7 else 1.6 if deg in (3, 4) else 1.0
                dist = abs(m - cur)
                w_ *= 1.0 / (1.0 + 0.6 * dist) if dist <= 7 else 0.05
                weights.append(w_)
            weights = np.array(weights) / np.sum(weights)
            cur = int(rng.choice(notes, p=weights))
            if s in (0, phrase - 1) and rng.random() < 0.6:
                home = [m for m in notes if (m - root) % 12 in (0, 7)]
                cur = int(min(home, key=lambda m: abs(m - cur)))
            dur = rng.uniform(0.14, 0.32) if s < phrase - 1 else rng.uniform(0.3, 0.5)
            ns_ = int(dur * sr)
            i0 = int(t * sr)
            target = note_hz(cur)
            tt = np.arange(ns_) / sr
            if mode == "natural":
                start_off = rng.uniform(-1.5, 1.5)                    # semitones: scoop into the note
                glide = 0.12
                off = start_off * np.exp(-tt / (glide / 3.0))
                vib = 0.35 * np.sin(2 * np.pi * 5.5 * tt + rng.uniform(0, 6.28)) * np.clip((tt - 0.08) / 0.1, 0, 1)
                drift = rng.uniform(-0.25, 0.25) + 0.1 * np.sin(2 * np.pi * 0.7 * tt)
                cents = (off + vib / 2.0 + drift) * 100.0
            elif mode == "light":                                     # ReaTune-like at ~120 ms: drift gone, slow arrival
                start_off = rng.uniform(-1.5, 1.5)
                off = start_off * np.exp(-tt / 0.04)                   # ~120 ms to arrive
                vib = 0.12 * np.sin(2 * np.pi * 5.5 * tt + rng.uniform(0, 6.28)) * np.clip((tt - 0.08) / 0.1, 0, 1)
                cents = (off + vib) * 100.0
            elif mode == "tight":
                start_off = rng.uniform(-1.5, 1.5)
                off = start_off * np.exp(-tt / 0.013)                  # ~40 ms to arrive
                vib = 0.04 * np.sin(2 * np.pi * 5.5 * tt)
                cents = (off + vib + rng.uniform(-0.05, 0.05)) * 100.0
            else:                                                     # hard
                start_off = rng.uniform(-1.5, 1.5)
                off = start_off * (tt < 0.002)
                cents = off * 100.0
            f = target * 2.0 ** (cents / 1200.0)
            ph = np.cumsum(f / sr)
            pulses = (np.diff(np.floor(ph), prepend=0) > 0).astype(float)
            src = signal.lfilter([1.0], [1.0, -0.97], pulses - pulses.mean())
            fr, bw = VOWELS[list(VOWELS)[int(rng.integers(5))]]
            v = _formant(src, sr, fr, bw)
            env = np.ones(ns_)
            edge = int(0.012 * sr)
            env[:edge] = np.linspace(0, 1, edge)
            env[-edge:] = np.linspace(1, 0, edge)
            v = v / (np.max(np.abs(v)) + 1e-9) * env * 0.36 * 10 ** (rng.uniform(-3, 0) / 20.0)
            seg = v[: max(0, n - i0)]
            out[i0:i0 + len(seg)] += seg
            events.append((i0, i0 + len(seg), cur, "note"))
            t += dur + rng.uniform(0.01, 0.04)
        t += rng.uniform(0.5, 0.9)
    sos = signal.butter(2, 60.0, btype="high", fs=sr, output="sos")
    return signal.sosfilt(sos, out), events


def _stereo(x):
    x = np.asarray(x)
    return np.stack([x, x], axis=1) if x.ndim == 1 else x


def reverb(x, sr=SR, rt60=1.2, wet_db=-12.0, dark=True, seed=5, predelay_ms=20.0):
    """Exponentially decaying noise tail (the classic artificial reverb model) at a wet level given relative
    to the dry signal's RMS. dark: the tail loses its highs faster (6 dB/oct above 2 kHz)."""
    rng = np.random.default_rng(seed)
    n_ir = int(rt60 * 1.2 * sr)
    tt = np.arange(n_ir) / sr
    irs = []
    for ch in range(2):
        noise = rng.standard_normal(n_ir)
        if dark:
            noise = signal.sosfilt(signal.butter(1, 2000.0, btype="low", fs=sr, output="sos"), noise)
        ir = noise * 10.0 ** (-3.0 * tt / rt60)                      # -60 dB at rt60
        ir[: int(predelay_ms / 1000.0 * sr)] = 0.0
        irs.append(ir)
    xs = _stereo(x)
    dry_rms = np.sqrt(np.mean(xs ** 2))
    out = np.zeros_like(xs)
    for ch in range(2):
        tail = signal.fftconvolve(xs[:, ch], irs[ch])[: len(xs)]
        out[:, ch] = tail
    wet_rms = np.sqrt(np.mean(out ** 2)) + 1e-12
    out *= dry_rms * 10.0 ** (wet_db / 20.0) / wet_rms
    return xs + out


def delay(x, sr=SR, ms=375.0, feedback=0.35, wet_db=-10.0, lowpass_hz=5000.0):
    """Feedback echo: y = x + wet * (x delayed, repeating with `feedback`), the repeats low-passed."""
    xs = _stereo(x)
    d = int(ms / 1000.0 * sr)
    out = np.zeros_like(xs)
    buf = xs.copy()
    sos = signal.butter(1, lowpass_hz, btype="low", fs=sr, output="sos")
    g = 1.0
    for k in range(1, 12):
        buf = np.concatenate([np.zeros((d, 2)), buf[:-d]]) if d < len(buf) else np.zeros_like(buf)
        buf = signal.sosfilt(sos, buf, axis=0)
        g *= feedback if k > 1 else 1.0
        out += buf * g
        if g < 1e-3:
            break
    dry_rms = np.sqrt(np.mean(xs ** 2))
    first = np.sqrt(np.mean((np.concatenate([np.zeros((d, 2)), xs[:-d]])) ** 2)) + 1e-12
    out *= dry_rms * 10.0 ** (wet_db / 20.0) / first                 # wet_db = level of the FIRST echo vs dry
    return xs + out


def doubler(x, sr=SR, ms=18.0, cents=9.0, level_db=-8.0, side=1):
    """A detuned, late copy of the voice, to one side (the way a doubling effect does it)."""
    xs = _stereo(x)
    mono = xs.mean(axis=1)
    ratio = 2.0 ** (cents / 1200.0)
    n2 = int(len(mono) / ratio)
    shifted = signal.resample(mono, n2)                               # crude pitch shift: speed change
    shifted = np.concatenate([shifted, np.zeros(len(mono) - len(shifted))]) if len(shifted) < len(mono) else shifted[: len(mono)]
    d = int(ms / 1000.0 * sr)
    shifted = np.concatenate([np.zeros(d), shifted[:-d]])
    out = xs.copy()
    out[:, side] += shifted * 10.0 ** (level_db / 20.0)
    out[:, 1 - side] += shifted * 10.0 ** ((level_db - 12.0) / 20.0)
    return out


def telephone(x, sr=SR):
    xs = _stereo(x)
    sos = signal.butter(4, [300.0, 3400.0], btype="band", fs=sr, output="sos")
    return signal.sosfilt(sos, xs, axis=0)


def compress(x, sr=SR, threshold_db=-24.0, ratio=4.0, attack_ms=5.0, release_ms=80.0, makeup_db=None):
    xs = _stereo(x)
    mono = np.abs(xs).max(axis=1)
    env = np.zeros(len(mono))
    a_a = np.exp(-1.0 / (attack_ms / 1000.0 * sr))
    a_r = np.exp(-1.0 / (release_ms / 1000.0 * sr))
    e = 0.0
    for i in range(len(mono)):
        v = mono[i]
        e = a_a * e + (1 - a_a) * v if v > e else a_r * e + (1 - a_r) * v
        env[i] = e
    lvl = 20.0 * np.log10(np.maximum(env, 1e-9))
    over = np.maximum(0.0, lvl - threshold_db)
    gain_db = -over * (1.0 - 1.0 / ratio)
    if makeup_db is None:
        makeup_db = -float(np.percentile(gain_db[lvl > threshold_db - 10], 10)) if np.any(lvl > threshold_db - 10) else 0.0
    return xs * (10.0 ** ((gain_db + makeup_db) / 20.0))[:, None]


def grit(x, drive_db=12.0):
    """Soft saturation (tanh) with the level matched back."""
    xs = _stereo(x)
    g = 10.0 ** (drive_db / 20.0)
    y = np.tanh(xs * g) / np.tanh(g * 0.36)
    return y * (np.sqrt(np.mean(xs ** 2)) / (np.sqrt(np.mean(y ** 2)) + 1e-12))
