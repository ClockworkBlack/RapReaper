#!/usr/bin/env python3
"""sim_render.py -- TEST ONLY. The "audio engine" of the simulated REAPER.

Reads the render request written by fake_reaper.lua (project state plus what
to render) and writes the WAV a render would produce, using simple textbook
models of each plug-in (see plugins.py for the caveats).

Four functions here are Python re-writes, for testing only, of JS effects that
ship with REAPER, made from those effects' published source code. They stay
under their authors' terms (full notice: NOTICE.txt in the RapReaper folder):
  fx_expander       "Downward Expander", Copyright 2007 Thomas Scott Stillwell (BSD-style)
  fx_deesser        "De-esser", (C) 2009 Lubomir I. Ivanov (Liteon), GPL
  fx_masterlimiter  "Master Limiter", (C) 2006 Michael Gruhn (LOSER)
  fx_tonegen        "Tone Generator", Copyright (C) 2004 and later Cockos Incorporated, LGPL
"""
import json
import math
import os
import sys

import numpy as np
from scipy import ndimage, signal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "kit"))

import plugins as P            # noqa: E402
import rapreaper_audio as A       # noqa: E402


def db2a(db):
    return 10.0 ** (db / 20.0)


def vals(fx):
    spec = P.PLUGINS[fx["key"]]["params"]
    out = {}
    for s, p in zip(spec, fx["params"]):
        if s["name"] not in out:              # first occurrence = the plug-in's own control
            out[s["name"]] = P.value(s, p["norm"])
    return out


def biquad(x, b, a):
    return signal.lfilter(np.array(b) / a[0], np.array(a) / a[0], x, axis=0)


def rbj_hp(x, sr, f, q=0.7071):
    w = 2 * math.pi * f / sr
    al = math.sin(w) / (2 * q)
    c = math.cos(w)
    return biquad(x, [(1 + c) / 2, -(1 + c), (1 + c) / 2], [1 + al, -2 * c, 1 - al])


def rbj_lp(x, sr, f, q=0.7071):
    w = 2 * math.pi * min(f, sr * 0.49) / sr
    al = math.sin(w) / (2 * q)
    c = math.cos(w)
    return biquad(x, [(1 - c) / 2, 1 - c, (1 - c) / 2], [1 + al, -2 * c, 1 - al])


def rbj_peak(x, sr, f, gain_db, bw_oct):
    if abs(gain_db) < 0.05:
        return x
    gain_db = max(gain_db, -60.0)
    a_ = 10 ** (gain_db / 40.0)
    w = 2 * math.pi * min(f, sr * 0.45) / sr
    bw = max(bw_oct, 0.01)
    al = math.sin(w) * math.sinh(math.log(2) / 2 * bw * w / math.sin(w))
    c = math.cos(w)
    return biquad(x, [1 + al * a_, -2 * c, 1 - al * a_], [1 + al / a_, -2 * c, 1 - al / a_])


def rbj_shelf(x, sr, f, gain_db, high):
    if abs(gain_db) < 0.05:
        return x
    gain_db = max(gain_db, -60.0)
    a_ = 10 ** (gain_db / 40.0)
    w = 2 * math.pi * min(f, sr * 0.45) / sr
    c, s = math.cos(w), math.sin(w)
    al = s / 2 * math.sqrt(2.0)
    k = 2 * math.sqrt(a_) * al
    if high:
        b = [a_ * ((a_ + 1) + (a_ - 1) * c + k), -2 * a_ * ((a_ - 1) + (a_ + 1) * c), a_ * ((a_ + 1) + (a_ - 1) * c - k)]
        a = [(a_ + 1) - (a_ - 1) * c + k, 2 * ((a_ - 1) - (a_ + 1) * c), (a_ + 1) - (a_ - 1) * c - k]
    else:
        b = [a_ * ((a_ + 1) - (a_ - 1) * c + k), 2 * a_ * ((a_ - 1) - (a_ + 1) * c), a_ * ((a_ + 1) - (a_ - 1) * c - k)]
        a = [(a_ + 1) + (a_ - 1) * c + k, -2 * ((a_ - 1) + (a_ + 1) * c), (a_ + 1) + (a_ - 1) * c - k]
    return biquad(x, b, a)


def smooth_gain(target_db, sr, attack_ms, release_ms, hop=32):
    """Attack/release smoothing of a gain-reduction curve (dB, <= 0), at a control rate."""
    n = len(target_db)
    ctl = target_db[::hop]
    at = math.exp(-1.0 / max(1e-4, attack_ms * 0.001 * sr / hop))
    rl = math.exp(-1.0 / max(1e-4, release_ms * 0.001 * sr / hop))
    out = np.empty_like(ctl)
    g = 0.0
    for i, t in enumerate(ctl):
        coef = at if t < g else rl          # more reduction = attack
        g = t + coef * (g - t)
        out[i] = g
    return np.interp(np.arange(n), np.arange(len(ctl)) * hop, out)


def level_db(x, sr, window_ms):
    mono = np.max(np.abs(x), axis=1)
    if window_ms <= 0.2:
        return 20 * np.log10(np.maximum(mono, 1e-9))
    k = math.exp(-1.0 / (window_ms * 0.001 * sr))
    env = signal.lfilter([1 - k], [1, -k], mono * mono)
    return 10 * np.log10(np.maximum(env, 1e-18))


def fx_reacomp(x, sr, v):
    thr, ratio = v["Thresh"], max(v["Ratio"], 1.0)
    lv = level_db(x, sr, v["RMS size"])
    over = lv - thr
    knee = max(v["Knee"], 0.0)
    slope = 1.0 - 1.0 / ratio
    if knee > 0.01:
        gr = np.where(over <= -knee / 2, 0.0,
                      np.where(over >= knee / 2, -slope * over, -slope * (over + knee / 2) ** 2 / (2 * knee)))
    else:
        gr = np.where(over > 0, -slope * over, 0.0)
    gr = smooth_gain(gr, sr, max(v["Attack"], 0.05), max(v["Release"], 1.0))
    makeup = 0.0
    if v["Auto Make Up Gain"] == 1:
        makeup = -thr * slope * 0.5
    wet = x * db2a(gr + makeup)[:, None]
    return wet * db2a(v["Wet"]) + x * (0.0 if v["Dry"] <= -120 else db2a(v["Dry"]))


def _lr2(f, sr):
    """Crossover coefficients exactly as the stock JS de-esser computes them."""
    fpi = math.pi * f
    wc = 2 * fpi
    wc2 = wc * wc
    k = wc / math.tan(fpi / sr)
    k2 = k * k
    wck2 = 2 * wc * k
    tmpk = k2 + wc2 + wck2
    a = [1.0, (-2 * k2 + 2 * wc2) / tmpk, (-wck2 + k2 + wc2) / tmpk]
    lp = [wc2 / tmpk, 2 * wc2 / tmpk, wc2 / tmpk]
    hp = [k2 / tmpk, -2 * k2 / tmpk, k2 / tmpk]
    return lp, hp, a


_DEESS_TIMES = {0: (0.0, 21.20), 1: (9.71, 37.19), 2: (20.97, 62.61)}


def fx_deesser(x, sr, v):
    """Port of REAPER's stock 'JS: De-esser' (Liteon/deesser), written from its source:
    Linkwitz-Riley split, peak follower on the processed band, and its own gain law
    gain = 1 / (1 + rat * (env / thr - 1)) with thr = 10^(slider/40), rat = (ratio-1)/19."""
    mono = int(round(v["Processing"])) == 1
    hipass = int(round(v["Target Type"])) == 1
    monitor = int(round(v["Monitor"])) == 1
    fc, bw = v["Frequency (Hz)"], v["Bandwidth (Oct)"]
    thr = 10.0 ** (v["Threshold (dB)"] / 40.0)
    rat = (v["Ratio"] - 1.0) / 19.0
    a_pct, r_pct = _DEESS_TIMES[int(round(v["Time Constants"]))]
    att = 10.0 ** (-0.002 - 3.97772619 * (a_pct / 100.0))
    rel = 10.0 ** (-3.11 - 1.8698 * (r_pct / 100.0))
    outgain = 0.0 if v["Gain (-inf/+24dB)"] <= -24.0 else db2a(v["Gain (-inf/+24dB)"])
    src = np.repeat(np.mean(x, axis=1, keepdims=True), 2, axis=1) if mono else x
    high = None
    if hipass:
        lp, hp, a = _lr2(fc, sr)
        low = signal.lfilter(lp, a, src, axis=0)
        band = -signal.lfilter(hp, a, src, axis=0)
    else:
        lp, hp, a = _lr2(fc - fc * bw / 4.0, sr)
        low = signal.lfilter(lp, a, src, axis=0)
        hp0 = -signal.lfilter(hp, a, src, axis=0)
        lp1, hp1, a1 = _lr2(min(fc + fc * bw / 2.0, 20000.0), sr)
        band = signal.lfilter(lp1, a1, hp0, axis=0)
        high = -signal.lfilter(hp1, a1, hp0, axis=0)
    dt = np.max(np.abs(band), axis=1).tolist()
    gain = [1.0] * len(dt)
    env = 0.0
    keep = 1.0 - rel
    for i, d in enumerate(dt):
        if d > env:
            env += att * (d - env)
        else:
            env *= keep
        if env > thr:
            gain[i] = 1.0 / (1.0 + rat * (env / thr - 1.0))
        if env < 1e-10:
            env = 0.0
    g = np.asarray(gain)[:, None]
    if monitor:
        return band * g * outgain
    out = low + band * g
    if high is not None:
        out = out + high
    return out * outgain


def fx_expander(x, sr, v):
    """Port of REAPER's stock 'JS: Downward Expander' (sstillwell/expander), from its source:
    level below the threshold is pushed down by (ratio-1)/ratio dB per dB, so even the highest
    ratio is at most a 1:2 expander. 'Attack' is how fast it lets sound back in, 'Release' how
    fast it closes."""
    thr, ratio, makeup = v["Threshold (dB)"], max(v["Ratio"], 1.0), v["Gain"]
    mode = int(round(v["Detection"]))
    left, right = np.abs(x[:, 0]), np.abs(x[:, 1])
    if mode & 1:
        ave = left * left + right * right
        k = math.exp(-1000.0 / (10.0 * sr))
    else:
        ave = np.maximum(left, right) ** 2
        k = math.exp(-1000.0 / (0.0025 * sr))
    det = np.sqrt(np.maximum(signal.lfilter([1.0 - k], [1.0, -k], ave), 0.0))
    scale = 1.0 if (mode & 2) else 2.08136898
    with np.errstate(divide="ignore"):
        over = 20.0 * np.log10(det / db2a(thr)) * scale
    over[~np.isfinite(over)] = 0.0            # the JS language stores an infinite result as 0
    over = np.minimum(over, 0.0).tolist()
    at_s, rl_s = v["Attack (ms)"] * 0.001, v["Release (ms)"] * 0.001
    at = math.exp(-1.0 / (at_s * sr)) if at_s > 0 else 0.0
    rl = math.exp(-1.0 / (rl_s * sr)) if rl_s > 0 else 0.0
    run = 0.0
    out = [0.0] * len(over)
    for i, o in enumerate(over):
        run = o + (at if o > run else rl) * (run - o)
        out[i] = run
    gr = np.asarray(out) * (ratio - 1.0) / ratio
    return x * db2a(gr + makeup)[:, None]


def fx_realimit(x, sr, v):
    thr, ceil_ = v["Threshold"], v["Brickwall ceiling"]
    y = x * db2a(-thr)                     # threshold maps to 0 dBFS ...
    peak = np.max(np.abs(y), axis=1)
    g = np.minimum(1.0, 1.0 / np.maximum(peak, 1e-9))
    look = int(0.002 * sr)
    g = ndimage.minimum_filter1d(g, size=2 * look + 1, mode="nearest")
    g = ndimage.uniform_filter1d(g, size=look, mode="nearest")
    rel = math.exp(-1.0 / (0.08 * sr / 16))
    ctl = g[::16]
    out = np.empty_like(ctl)
    cur = 1.0
    for i, t in enumerate(ctl):
        cur = t if t < cur else t + rel * (cur - t)
        out[i] = cur
    g2 = np.minimum(g, np.interp(np.arange(len(g)), np.arange(len(ctl)) * 16, out))
    y = np.clip(y * g2[:, None], -1.0, 1.0)
    return y * db2a(ceil_)                 # ... and the output is scaled to the ceiling


def fx_masterlimiter(x, sr, v):
    """Port of REAPER's stock 'JS: Master Limiter' (loser/masterLimiter), written from its source:
    the level is raised by (limit - threshold), a gain follower holds peaks at the threshold, and what
    still gets past is clipped at the limit."""
    c = 8.65617025
    tdb = min(v["Threshold (dB)"], -0.1)
    tresh = math.exp(tdb / c)
    look = int(math.floor(max(min(v["Look Ahead (us)"] / 1e6 * sr, 500000), 1)))
    a_s = max(v["Attack (us)"] / 1e6 * sr, 0.0)
    r_s = max(v["Release (ms)"] / 1000.0 * sr, 0.0)
    attack = math.exp(-tdb / a_s / c) if a_s > 0 else float("inf")
    release = math.exp(-tdb / r_s / c) if r_s > 0 else float("inf")
    hold = v["Hold (ms)"] / 1000.0 * sr
    limit = math.exp(v["Limit (dB)"] / c)
    volume = limit / tresh
    n = len(x)
    l_in, r_in = x[:, 0].tolist(), x[:, 1].tolist()
    ld = ([0.0] * look + l_in[: n - look]) if look < n else [0.0] * n
    rd = ([0.0] * look + r_in[: n - look]) if look < n else [0.0] * n
    out = np.empty((n, 2))
    gain = seek = 1.0
    under = 0
    for i in range(n):
        a, b = ld[i], rd[i]
        det = max(abs(a), abs(b), abs(l_in[i]), abs(r_in[i]))
        if det > tresh:
            under = 0
            seek = min(tresh / det, seek) if hold else tresh / det
        else:
            under += 1
            if under > hold:
                seek = 1.0
        gain = max(gain / attack, seek) if gain > seek else min(gain * release, seek)
        a *= gain * volume
        b *= gain * volume
        if max(abs(a), abs(b)) > limit:
            gain = seek
        out[i, 0] = max(min(a, limit), -limit)
        out[i, 1] = max(min(b, limit), -limit)
    return out


def fx_reaeq(x, sr, v):
    y = rbj_shelf(x, sr, v["Freq-Low Shelf"], v["Gain-Low Shelf"], False)
    y = rbj_peak(y, sr, v["Freq-Band 2"], v["Gain-Band 2"], v["BW-Band 2"])
    y = rbj_peak(y, sr, v["Freq-Band 3"], v["Gain-Band 3"], v["BW-Band 3"])
    y = rbj_shelf(y, sr, v["Freq-High Shelf 4"], v["Gain-High Shelf 4"], True)
    return y * db2a(max(v["Global Gain"], -120.0))


def fx_hpflpf(x, sr, v):
    y = x
    if v["HPF"] > 0.5:
        y = rbj_hp(y, sr, v["HPF"])
    if v["LPF"] < 21999.0:
        y = rbj_lp(y, sr, v["LPF"])
    return y * db2a(v["Gain"])


def fx_tonegen(x, sr, v):
    """Port of REAPER's stock 'JS: Tone Generator' (sine shape)."""
    vol, dry = 2.0 ** (v["Wet Mix (dB)"] / 6.0), 2.0 ** (v["Dry Mix (dB)"] / 6.0)
    f = v["Base Frequency (Hz)"] * 2.0 ** (v["Octave"] + v["Note"] / 12.0 + v["Fine Tune (cents)"] / 1200.0)
    tone = vol * np.cos(2 * math.pi * f * np.arange(len(x)) / sr)
    return tone[:, None] + x * dry


def fx_volume(x, sr, v):
    return x * db2a(v["Volume (dB)"])


def fx_ducks(x, sr, v):
    return x * db2a(-40.0 * (1.0 - min(1.0, max(0.0, v["Level (0 = -40 dB, 1 = full)"]))))


def fx_reapitch(x, sr, v):
    """A crude pitch shifter for the tests: resample for the pitch, then overlap-add back to the original length.
    Good enough to move a voice's pitch in the right direction by about the right amount."""
    semis = v["1: Shift (semitones)"] + v["1: Shift (cents)"] / 100.0 + 12.0 * v["1: Shift (oct)"]
    ratio = 2.0 ** (semis / 12.0)
    if v["1: Enabled"] < 0.5 or abs(ratio - 1.0) < 1e-4:
        y = x
    else:
        n = len(x)
        idx = np.arange(0, n, ratio)
        idx = idx[idx < n - 1]
        i0 = idx.astype(int)
        fr = (idx - i0)[:, None]
        res = x[i0] * (1 - fr) + x[i0 + 1] * fr          # resampled: pitch moved, length changed
        # stretch back to n samples with overlap-add of 50 ms grains
        g = int(0.05 * sr)
        hop_out = g // 2
        y = np.zeros_like(x)
        win = np.hanning(g)[:, None]
        m = len(res)
        pos_out = 0
        while pos_out + g <= n:
            src = int(pos_out * (m - g) / max(1, n - g))
            y[pos_out:pos_out + g] += res[src:src + g] * win
            pos_out += hop_out
    return y * db2a(v["Wet"]) * db2a(v["1: Volume"]) + x * (0.0 if v["Dry"] <= -120 else db2a(v["Dry"]))


def fx_readelay(x, sr, v):
    d = int(v["1: Length (time)"] / 1000.0 * sr)
    y = np.zeros_like(x)
    if v["1: Enabled"] >= 0.5 and d > 0 and d < len(x):
        y[d:] = x[:-d] * db2a(v["1: Volume"])
    return y * db2a(v["Wet"]) + x * (0.0 if v["Dry"] <= -120 else db2a(v["Dry"]))


def fx_saturation(x, sr, v):
    """One control, 'Amount (%)' (as REAPER's loser/saturation): 0 leaves the sound alone, 100 is a hard drive."""
    amount = v["Amount (%)"] / 100.0
    drive = 1.0 + 9.0 * amount
    wet = np.tanh(x * drive) / np.tanh(drive) if drive > 1.0 else x
    return wet * amount + x * (1 - amount)


def fx_chorus(x, sr, v):
    d = int(v["Chorus length (ms)"] / 1000.0 * sr)
    y = np.zeros_like(x)
    if 0 < d < len(x):
        y[d:] = x[:-d]
    return x * db2a(v["Dry mix (dB)"]) + y * db2a(v["Wet mix (dB)"])


def fx_delay_chorus(x, sr, v):
    d = int(v["Delay (ms)"] / 1000.0 * sr)
    y = np.zeros_like(x)
    if 0 < d < len(x):
        y[d:] = x[:-d]
    return x * db2a(v["Output Dry (dB)"]) + y * (db2a(v["Output Wet (Chorus) (dB)"]) + db2a(v["Output Wet (Clean) (dB)"]))


PROCESS = {"reacomp": fx_reacomp, "reatune": lambda x, sr, v: x, "reapitch": fx_reapitch, "readelay": fx_readelay,
           "saturation": fx_saturation, "chorus": fx_chorus, "delay_chorus": fx_delay_chorus,
           "deesser": fx_deesser, "expander": fx_expander, "realimit": fx_realimit,
           "masterlimiter": fx_masterlimiter,
           "reaeq": fx_reaeq, "hpflpf": fx_hpflpf, "volume_pan": fx_volume, "rapreaper_ducks": fx_ducks, "tonegen": fx_tonegen,
           "reaverbate": lambda x, sr, v: x, "testcomp": lambda x, sr, v: x}


def env_curve(env, t0, n, sr):
    """An FX envelope's value at every sample from project time t0: straight lines between points (shape 0, the
    only one RapReaper writes), the first point's value before it and the last one's after it. As in REAPER, the
    value is in the control's OWN units: 0..1 for a VST control, the slider's range for a JS slider (so a JS
    volume slider's envelope holds dB)."""
    pts = sorted(env.get("points") or [], key=lambda p: p["time"])
    t = t0 + np.arange(n) / float(sr)
    return np.interp(t, [p["time"] for p in pts], [p["value"] for p in pts])


def run_chain(x, sr, fxs, t0=0.0):
    for fx in fxs:
        if not fx.get("enabled", True):
            continue
        envs = [e for e in (fx.get("envelopes") or []) if isinstance(e, dict) and e.get("active") and e.get("points")]
        if fx["key"] in ("volume_pan", "rapreaper_ducks") and any(e["param"] == 0 for e in envs):
            # an automated level: the gain follows the envelope sample by sample (test only: the one kind of envelope
            # simulated). The envelope holds the slider's own units: dB on the Volume/Pan Smoother, 0..1 on the Ducks.
            spec = P.PLUGINS[fx["key"]]["params"][0]
            val = np.clip(env_curve([e for e in envs if e["param"] == 0][0], t0, len(x), sr), spec["min"], spec["max"])
            db = val if fx["key"] == "volume_pan" else -40.0 * (1.0 - val)
            x = x * (10.0 ** (db / 20.0))[:, None] if x.ndim == 2 else x * 10.0 ** (db / 20.0)
            continue
        x = PROCESS[fx["key"]](x, sr, vals(fx))
    return x


def track_audio(tr, start, stop, sr):
    n = int(round((stop - start) * sr))
    out = np.zeros((n, 2))
    for it in tr.get("items") or []:
        if it.get("mute"):
            continue
        a, b = it["position"], it["position"] + it["length"]
        lo, hi = max(a, start), min(b, stop)
        if hi <= lo:
            continue
        off = float(it.get("offset") or 0.0)
        data, fsr, _ = A.read_audio(it["file"], start=off + (lo - a), duration=hi - lo)
        data = data.astype(np.float64)
        if fsr != sr:
            data = signal.resample_poly(data, sr, fsr, axis=0)
        if data.shape[1] == 1:
            data = np.repeat(data, 2, axis=1)
        # the clip's own volume and its fades (linear, as REAPER's default fade shape is near enough for a test)
        data = data * float(it.get("vol", 1.0))
        t = lo - a + np.arange(len(data)) / float(sr)              # time within the clip
        fi, fo = float(it.get("fade_in") or 0.0), float(it.get("fade_out") or 0.0)
        g = np.ones(len(data))
        if fi > 0:
            g = np.minimum(g, np.clip(t / fi, 0.0, 1.0))
        if fo > 0:
            g = np.minimum(g, np.clip((it["length"] - t) / fo, 0.0, 1.0))
        data = data * g[:, None]
        i0 = int(round((lo - start) * sr))
        m = min(len(data), n - i0)
        out[i0:i0 + m] += data[:m, :2]
    return out


def main():
    req = json.load(open(sys.argv[1]))
    r = req["render"]
    flags = req.get("flags") or {}
    sr = int(r["srate"] or 48000)
    start, stop = float(r["start"]), float(r["stop"])
    n = int(round((stop - start) * sr))
    any_solo = any(t["solo"] for t in req["tracks"])

    # Sends (one level deep, as RapReaper's effect tracks are): a track's post-fader output, scaled by the send level,
    # arrives at the destination track's input alongside its own clips; the destination then runs its chain and fader.
    # (mode 0 post-fader; 3 pre-fader = before the fader; 1 pre-fx = the clips alone.)
    by_guid = {t["guid"]: t for t in req["tracks"]}
    received = {}

    def own_out(t):
        return run_chain(track_audio(t, start, stop, sr), sr, t["fx"], start) * t["vol"]

    for t in req["tracks"]:
        for sd in t.get("sends") or []:
            if sd.get("dest") not in by_guid or t["mute"]:
                continue                 # (REAPER's solo-in-place: a soloed track still receives from its senders)
            mode = int(sd.get("mode") or 0)
            if mode == 1:
                sig = track_audio(t, start, stop, sr)
            elif mode == 3:
                sig = run_chain(track_audio(t, start, stop, sr), sr, t["fx"], start)
            else:
                sig = own_out(t)
            received.setdefault(sd["dest"], np.zeros((n, 2)))
            received[sd["dest"]] += sig * float(sd.get("vol", 1.0))

    def track_out(t):
        x = track_audio(t, start, stop, sr)
        if t["guid"] in received:
            x = x + received[t["guid"]]
        return run_chain(x, sr, t["fx"], start) * t["vol"]

    def master_mix():
        out = np.zeros((n, 2))
        for t in req["tracks"]:
            if t["mute"] or (any_solo and not t["solo"]):
                continue
            out += track_out(t)
        return run_chain(out, sr, req["master"]["fx"]) * req["master"]["vol"]

    if r.get("offline"):
        out = np.zeros((n, 2))
    elif r["selected"] == "master":
        out = master_mix()
    else:
        t = next(t for t in req["tracks"] if t["guid"] == r["selected"])
        has_items = bool(t.get("items"))
        if has_items and flags.get("stems_full_mix"):
            # test switch: a single track with clips comes back as the whole mix
            # (what the bridge's own notes warn can happen)
            out = master_mix()
        elif t["mute"]:
            out = np.zeros((n, 2))                  # a muted track renders as silence
        elif any_solo and not t["solo"] and flags.get("solo_silences_stems", True):
            out = np.zeros((n, 2))                  # another track's solo silences this one, also in a render
        else:
            vol = 1.0 if (has_items and flags.get("stems_pre_fader")) else t["vol"]
            x = track_audio(t, start, stop, sr)
            if t["guid"] in received:
                x = x + received[t["guid"]]
            out = run_chain(x, sr, t["fx"], start) * vol
    # test switches for what the project's own render settings can do to every render
    # "always" / "too_loud": to -14 LUFS (every render, or only what is louder); "peak": every render up to full scale
    norm = flags.get("render_normalize")
    if norm == "peak":
        top = float(np.max(np.abs(out))) if out.size else 0.0
        if top > 1e-6:
            out = out / top
    elif norm:
        lufs = A.loudness_stats(out, sr)["integrated_lufs"]
        if lufs is not None and (norm == "always" or lufs > -14.0):
            out = out * db2a(-14.0 - lufs)
    if flags.get("render_mono"):
        out = out.mean(axis=1)
    os.makedirs(os.path.dirname(r["target"]) or ".", exist_ok=True)
    A.write_wav(r["target"], out, sr, 32 if flags.get("float_wav") else 24)


if __name__ == "__main__":
    main()
