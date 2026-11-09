#!/usr/bin/env python3
"""Synthetic test material with KNOWN defects, for testing the RapReaper analyser.

Nothing here is real speech; it is a voice-like signal (pitched pulses through
formant filters, noise bursts for S sounds, low thumps for P sounds) plus a
beat-like loop, so that every defect we inject has a known size.
"""
import numpy as np
from scipy import signal

SR = 48000


def _formant(x, sr, freqs, bws):
    y = np.zeros_like(x)
    for f, bw in zip(freqs, bws):
        r = np.exp(-np.pi * bw / sr)
        a = [1.0, -2.0 * r * np.cos(2 * np.pi * f / sr), r * r]
        b = [1.0 - r]
        y += signal.lfilter(b, a, x)
    return y


VOWELS = {
    "a": ([730, 1090, 2440, 3400], [90, 110, 160, 250]),
    "e": ([530, 1840, 2480, 3400], [70, 110, 160, 250]),
    "i": ([300, 2250, 3000, 3600], [60, 110, 170, 250]),
    "o": ([570, 840, 2410, 3300], [80, 100, 160, 250]),
    "u": ([350, 900, 2300, 3300], [70, 100, 160, 250]),
}


def synth_vocal(seconds=40.0, sr=SR, seed=1, f0=115.0, level_spread_db=14.0,
                sib_gain_db=0.0, with_plosives=False, gap_every=2.6):
    """Voice-like signal peaking around -9 dBFS before defects are added.

    Returns mono float array. Phrases alternate loud and quiet (level_spread_db
    apart) with silent gaps, so there is a dynamic range for a compressor and a
    noise floor to measure.
    """
    rng = np.random.default_rng(seed)
    n = int(seconds * sr)
    out = np.zeros(n)
    t = 0.3
    phrase = 0
    vowels = list(VOWELS)
    while t < seconds - 1.5:
        phrase_len = rng.uniform(1.5, 2.3)
        level = 10 ** ((-(level_spread_db) * (phrase % 3) / 2.0) / 20.0)   # 0, -spread/2, -spread
        pos = t
        while pos < t + phrase_len and pos < seconds - 0.6:
            syl = rng.uniform(0.11, 0.2)
            ns = int(syl * sr)
            i0 = int(pos * sr)
            # sibilant before roughly a third of the syllables
            if rng.random() < 0.34:
                sl = int(rng.uniform(0.07, 0.12) * sr)
                noise = rng.standard_normal(sl)
                sos = signal.butter(4, [5200, 9000], btype="band", fs=sr, output="sos")
                s = signal.sosfilt(sos, noise) * np.hanning(sl)
                s *= 0.03 * level * 10 ** (sib_gain_db / 20.0)
                out[i0:i0 + sl] += s[: max(0, min(sl, n - i0))]
                i0 += sl
                pos += sl / sr
            # plosive thump now and then
            if with_plosives and rng.random() < 0.10:
                pl = int(0.045 * sr)
                tt = np.arange(pl) / sr
                thump = np.sin(2 * np.pi * 62 * tt) * np.exp(-tt / 0.012) * 0.5 * level
                out[i0:i0 + pl] += thump[: max(0, min(pl, n - i0))]
            # voiced part: pulse train with small pitch drift
            pitch = f0 * (1.0 + 0.06 * np.sin(2 * np.pi * 0.7 * pos) + rng.normal(0, 0.012))
            ph = np.cumsum(np.full(ns, pitch / sr))
            pulses = (np.diff(np.floor(ph), prepend=0) > 0).astype(float)
            src = signal.lfilter([1.0], [1.0, -0.97], pulses - pulses.mean())   # glottal tilt, no DC
            fr, bw = VOWELS[vowels[rng.integers(len(vowels))]]
            v = _formant(src, sr, fr, bw)
            env = np.hanning(ns) ** 0.5
            v = v / (np.max(np.abs(v)) + 1e-9) * env * 0.36 * level
            seg = v[: max(0, min(ns, n - i0))]
            out[i0:i0 + len(seg)] += seg
            pos += syl + rng.uniform(0.01, 0.05)
        t = pos + rng.uniform(0.45, 0.7)
        if int(t / gap_every) != int((t - phrase_len) / gap_every):
            t += 0.5
        phrase += 1
    sos = signal.butter(2, 70.0, btype="high", fs=sr, output="sos")   # a voice has nothing down there
    return signal.sosfilt(sos, out)


def add_defects(x, sr=SR, seed=7, hum_db=None, hum_hz=60, rumble_db=None, hiss_db=None,
                dc=0.0, mud_db=0.0, clip_at=None):
    """Add known defects. *_db values are absolute RMS levels in dBFS."""
    rng = np.random.default_rng(seed)
    n = len(x)
    y = x.copy()
    tt = np.arange(n) / sr
    if mud_db:
        b, a = signal.iirpeak(320.0, 1.2, fs=sr)
        y = y + (10 ** (mud_db / 20.0) - 1.0) * signal.lfilter(b, a, y)
    if hum_db is not None:
        amp = 10 ** (hum_db / 20.0) * np.sqrt(2.0)
        hum = (np.sin(2 * np.pi * hum_hz * tt) + 0.5 * np.sin(2 * np.pi * 2 * hum_hz * tt + 0.4)
               + 0.35 * np.sin(2 * np.pi * 3 * hum_hz * tt + 1.1))
        hum = hum / np.sqrt(np.mean(hum ** 2)) * 10 ** (hum_db / 20.0)
        y = y + hum
    if rumble_db is not None:
        noise = rng.standard_normal(n)
        sos = signal.butter(4, [12, 45], btype="band", fs=sr, output="sos")
        r = signal.sosfilt(sos, noise)
        y = y + r / np.sqrt(np.mean(r ** 2)) * 10 ** (rumble_db / 20.0)
    if hiss_db is not None:
        y = y + rng.standard_normal(n) * 10 ** (hiss_db / 20.0)
    if dc:
        y = y + dc
    if clip_at is not None:
        y = np.clip(y, -clip_at, clip_at)
    return y


def synth_beat(seconds=40.0, sr=SR, seed=3, bpm=140.0, master_peak_db=-0.3):
    """Loud two-track 'type beat': kick, 808, clap, hats and a mid-range pad."""
    rng = np.random.default_rng(seed)
    n = int(seconds * sr)
    L = np.zeros(n)
    R = np.zeros(n)
    beat = 60.0 / bpm
    tt = np.arange(n) / sr

    def put(buf, at, sig):
        i = int(at * sr)
        if i >= n:
            return
        m = min(len(sig), n - i)
        buf[i:i + m] += sig[:m]

    # kick + 808
    kl = int(0.25 * sr)
    kt = np.arange(kl) / sr
    kick = np.sin(2 * np.pi * (50 * kt + 35 * 0.03 * (1 - np.exp(-kt / 0.03)))) * np.exp(-kt / 0.08)
    b8 = int(beat * 2 * sr)
    t8 = np.arange(b8) / sr
    notes = [43.65, 43.65, 51.9, 38.9]
    step = 0
    t = 0.0
    while t < seconds:
        for buf in (L, R):
            put(buf, t, kick * 0.8)
        f = notes[step % 4]
        sub = (np.sin(2 * np.pi * f * t8) + 0.3 * np.sin(2 * np.pi * 2 * f * t8) + 0.12 * np.sin(2 * np.pi * 3 * f * t8))
        sub *= np.exp(-t8 / 0.9) * 0.55
        for buf in (L, R):
            put(buf, t, sub)
        # clap on the off beat
        cl = int(0.16 * sr)
        nz = rng.standard_normal(cl)
        sos = signal.butter(2, [900, 6000], btype="band", fs=sr, output="sos")
        clap = signal.sosfilt(sos, nz) * np.exp(-np.arange(cl) / sr / 0.045) * 0.5
        put(L, t + beat, clap)
        put(R, t + beat, clap * 0.9)
        # hats
        for h in range(8):
            hl = int(0.03 * sr)
            hz = rng.standard_normal(hl)
            sosh = signal.butter(2, 7500, btype="high", fs=sr, output="sos")
            hat = signal.sosfilt(sosh, hz) * np.exp(-np.arange(hl) / sr / 0.008) * 0.16
            put(L if h % 2 else R, t + h * beat / 4.0, hat)
        t += beat * 2
        step += 1
    # pad: detuned saws through a low-pass, wide in stereo
    for i, f in enumerate([220.0, 261.6, 329.6, 392.0]):
        for det, buf in ((0.997, L), (1.003, R)):
            saw = signal.sawtooth(2 * np.pi * f * det * tt + i)
            sos = signal.butter(2, 3800, btype="low", fs=sr, output="sos")
            buf += signal.sosfilt(sos, saw) * 0.085
    mix = np.stack([L, R], axis=1)
    # crude "mastered loud": soft clip then normalise the peak
    mix = np.tanh(mix * 2.2) / np.tanh(2.2)
    mix = mix / np.max(np.abs(mix)) * 10 ** (master_peak_db / 20.0)
    return mix


if __name__ == "__main__":
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "kit"))
    from rapreaper_audio import write_wav
    out = sys.argv[1] if len(sys.argv) > 1 else "."
    os.makedirs(out, exist_ok=True)
    v = synth_vocal()
    dirty = add_defects(v, hum_db=-52, rumble_db=-38, hiss_db=-58, dc=0.004, mud_db=6.0)
    write_wav(os.path.join(out, "vocal_clean.wav"), v, SR, 24)
    write_wav(os.path.join(out, "vocal_dirty.wav"), dirty, SR, 24)
    write_wav(os.path.join(out, "beat.wav"), synth_beat(), SR, 24)
    print("wrote test audio to", out)
