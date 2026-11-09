#!/usr/bin/env python3
"""rapreaper_effects.py -- what was done to a voice, read from the voice itself.

Given a vocal on its own (from a finished song, separated by rapreaper_separate, or a dry recording), these
detectors estimate the effects that were used and how strongly:

  pitch_track()        the sung pitch, 10 ms steps (needed by the next two)
  autotune()           none / natural / tight / hard, from how the pitch sits on the notes and how fast it moves
  key_of()             the key the notes belong to (F# minor ...), with a confidence
  reverb()             decay time (RT60) and level of the tail that follows word endings, and whether it is dark
  echo()               a repeating delay: its time (also as a note value at the song's tempo), feedback, level
  dynamics()           how compressed the voice is (loud-to-quiet range, crest factor) -> light / medium / heavy
  width()              mono / wide / doubled, from the side signal and a late copy on one side
  band_limited()       telephone / lo-fi: the voice's lows and highs cut away
  grit()               saturation: unexpectedly strong high harmonics on vowels (reported as uncertain)
  analyze_effects()    all of the above, plus the tone / de-ess figures rapreaper_audio already gives

Every result carries the numbers it was read from, a confidence, and plain words. The whole thing is a
measurement of a mixed, often mastered recording: it reports what the voice sounds like it was given,
not what plug-in was used.
"""
import math

import numpy as np
from scipy import signal

import rapreaper_audio as A

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
HOP_S = 0.010
WIN_S = 0.040
F0_LO, F0_HI = 65.0, 700.0

# Krumhansl-Kessler key profiles (major, minor), one per pitch class from the tonic
_KK_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_KK_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


def _mono(x):
    x = np.asarray(x, dtype=np.float64)
    return x.mean(axis=1) if x.ndim == 2 else x


def _r(v, nd=1):
    return None if v is None or (isinstance(v, float) and not math.isfinite(v)) else round(float(v), nd)


# ---------------------------------------------------------------------------
# pitch
# ---------------------------------------------------------------------------

def pitch_track(x, sr):
    """{'times', 'f0' (Hz, nan where unvoiced), 'clarity' (0..1), 'level_db'}; 10 ms steps, 40 ms windows.
    Normalised autocorrelation on the band 60 Hz - 1.5 kHz, best lag in the voice's range, parabolic peak
    refinement (a few cents of precision), with a check against picking the octave below."""
    m = _mono(x)
    sos = signal.butter(2, [60.0, 1500.0], btype="band", fs=sr, output="sos")
    xb = signal.sosfiltfilt(sos, m) if len(m) > 100 else m
    hop, win = int(round(HOP_S * sr)), int(round(WIN_S * sr))
    if len(xb) < win:
        xb = np.concatenate([xb, np.zeros(win - len(xb))])
        m = np.concatenate([m, np.zeros(win - len(m))])
    view = np.lib.stride_tricks.sliding_window_view(xb, win)[::hop]
    lview = np.lib.stride_tricks.sliding_window_view(m, win)[::hop]
    nfr = view.shape[0]
    lag_lo, lag_hi = int(sr / F0_HI), int(sr / F0_LO)
    nfft = 1 << int(np.ceil(np.log2(2 * win)))
    w = np.hanning(win)
    f0 = np.full(nfr, np.nan)
    clarity = np.zeros(nfr)
    level = A._adb(np.sqrt(np.mean(lview ** 2, axis=1) + 1e-30)) if hasattr(A, "_adb") else 20 * np.log10(np.sqrt(np.mean(lview ** 2, axis=1)) + 1e-12)
    step = 1024
    for i in range(0, nfr, step):
        blk = view[i:i + step] * w
        spec = np.fft.rfft(blk, nfft, axis=1)
        ac = np.fft.irfft(np.abs(spec) ** 2, axis=1)[:, :lag_hi + 2]
        # normalise by the energy of the two parts that overlap at each lag (a "YIN-like" correction)
        e = np.cumsum(blk ** 2, axis=1)
        tot = e[:, -1:]
        lags = np.arange(lag_hi + 2)
        left = e[:, win - 1 - lags]                                   # energy of x[0:win-lag]
        right = tot - np.concatenate([np.zeros((blk.shape[0], 1)), e[:, lags[1:] - 1]], axis=1)   # energy of x[lag:]
        norm = np.sqrt(np.maximum(left * right, 1e-20))
        r = ac / norm
        r[:, :lag_lo] = -1.0
        for k in range(blk.shape[0]):
            row = r[k]
            if tot[k, 0] < 1e-10:
                continue
            j = int(np.argmax(row[lag_lo:lag_hi + 1])) + lag_lo
            best = row[j]
            if best < 0.5:
                continue
            # the first strong peak below the best lag (a higher pitch) wins when it is nearly as strong:
            # avoids the octave-below error on pulse-like voices
            cand = [p for p in range(lag_lo + 1, j) if row[p] > row[p - 1] and row[p] >= row[p + 1] and row[p] >= best * 0.93]
            if cand:
                j = cand[0]
                best = row[j]
            if 1 <= j < len(row) - 1:
                a, b, c = row[j - 1], row[j], row[j + 1]
                den = (a - 2 * b + c)
                dj = 0.5 * (a - c) / den if abs(den) > 1e-12 else 0.0
                dj = max(-1.0, min(1.0, dj))
            else:
                dj = 0.0
            f0[i + k] = sr / (j + dj)
            clarity[i + k] = best
    times = (np.arange(nfr) * hop + win / 2.0) / sr
    return {"times": times, "f0": f0, "clarity": clarity, "level_db": level}


def _voiced(pt, min_clarity=0.75):
    lv = pt["level_db"]
    aud = lv > (np.nanmax(lv) - 40.0) if np.isfinite(lv).any() else np.zeros_like(lv, bool)
    return np.isfinite(pt["f0"]) & (pt["clarity"] >= min_clarity) & aud


def _cents(f0):
    return 1200.0 * np.log2(f0 / 440.0) + 6900.0                     # MIDI cents (69.00 = A4)


def autotune(x, sr, pt=None):
    """How much the pitch was corrected: 'none' (a spoken rap, or no steady notes), 'natural', 'tight', 'hard'.
    Read from (a) how many voiced frames sit within 10 cents of a semitone, (b) how far they drift inside a
    held note, and (c) how long the pitch takes to arrive on a new note."""
    pt = pt or pitch_track(x, sr)
    v = _voiced(pt)
    if v.sum() < 30:
        return {"amount": "none", "confidence": 0.3, "reason": "too little pitched sound to judge", "frames": int(v.sum())}
    c = _cents(pt["f0"])
    dev = ((c + 50.0) % 100.0) - 50.0                               # distance from the nearest semitone, cents
    # notes: runs of voiced frames whose pitch stays within 70 cents of a running mean
    notes = []
    i = 0
    idx = np.flatnonzero(v)
    runs = []
    a = idx[0]
    prev = idx[0]
    for k in idx[1:]:
        if k != prev + 1:
            runs.append((a, prev))
            a = k
        prev = k
    runs.append((a, prev))
    arrivals = []
    on_note = []
    held_spread = []
    transitions = []                                                 # frames spent between two notes at each jump
    for a, b in runs:
        seg = c[a:b + 1]
        # split the run at pitch jumps of more than 70 cents (between frames 30 ms apart)
        cuts = [0]
        for j in range(3, len(seg)):
            if abs(seg[j] - np.median(seg[max(0, j - 6):j])) > 70.0 and (j - cuts[-1]) >= 4:
                cuts.append(j)
        cuts.append(len(seg))
        for cut in cuts[1:-1]:
            before = seg[max(0, cut - 8):cut]
            after = seg[cut:cut + 10]
            if len(before) < 3 or len(after) < 4:
                continue
            p0 = round(float(np.median(before[: max(1, len(before) - 2)])) / 100.0) * 100.0
            p1 = round(float(np.median(after[3:])) / 100.0) * 100.0
            if abs(p1 - p0) < 70.0:
                continue
            lo_, hi_ = min(p0, p1), max(p0, p1)
            span = hi_ - lo_
            between = seg[max(0, cut - 8):cut + 10]
            inside = (between > lo_ + 0.2 * span) & (between < hi_ - 0.2 * span)
            transitions.append(int(inside.sum()) * HOP_S * 1000.0)
        for s0, s1 in zip(cuts, cuts[1:]):
            n_ = seg[s0:s1]
            if len(n_) < 8:                                          # under 80 ms: not a note
                continue
            target = round(float(np.median(n_[len(n_) // 3:])) / 100.0) * 100.0
            d = n_ - target
            # arrival: first frame within 10 cents of the note that stays there for 2 frames
            arr = None
            for j in range(len(d) - 1):
                if all(abs(d[j:j + 2]) <= 10.0):
                    arr = j
                    break
            if arr is not None:
                arrivals.append(arr * HOP_S * 1000.0)
                tail = d[arr:]
                on_note.append(float(np.mean(np.abs(tail) <= 10.0)))
                if len(tail) >= 10:
                    held_spread.append(float(np.percentile(np.abs(tail), 80)))
            notes.append((a + s0, a + s1, target))
    if len(arrivals) < 4:
        return {"amount": "none", "confidence": 0.4, "reason": "no steady notes found (spoken rather than sung)",
                "notes": len(notes)}
    arrive_ms = float(np.median(arrivals))
    on = float(np.median(on_note))
    spread = float(np.median(held_spread)) if held_spread else 50.0
    glide_ms = float(np.median(transitions)) if len(transitions) >= 3 else None
    # decision: how much of the time the pitch sits on a note, how still it is there, how it moves between notes
    if on >= 0.85 and spread <= 4.5 and arrive_ms <= 15.0 and (glide_ms is None or glide_ms <= 10.0):
        amount, conf = "hard", 0.85
    elif on >= 0.75 and spread <= 12.0 and arrive_ms <= 60.0:
        amount, conf = "tight", 0.75
    elif on >= 0.5 and spread <= 16.0:
        amount, conf = "natural", 0.6
    else:
        amount, conf = "none", 0.7
    words = {"hard": "hard auto-tune: the pitch snaps onto each note in a few milliseconds and never drifts (the robotic effect itself)",
             "tight": "tight pitch correction: notes are reached in a few tens of milliseconds and held within a few cents (the modern pop/rap vocal)",
             "natural": "light pitch correction (or a very steady singer): notes are held in tune, but the pitch still slides into them",
             "none": "no audible pitch correction: the pitch glides and drifts the way an uncorrected voice does"}
    return {"amount": amount, "confidence": conf, "what_it_sounds_like": words[amount],
            "frames_on_the_note_percent": _r(100 * on), "drift_inside_notes_cents": _r(spread),
            "time_to_reach_a_note_ms": _r(arrive_ms, 0), "time_between_notes_ms": _r(glide_ms, 0), "notes": len(notes),
            "reatune_attack_ms": {"hard": 2, "tight": 40, "natural": 120, "none": None}[amount]}


def key_of(x, sr, pt=None):
    """The key the sung notes belong to, by comparing the time each pitch class is held with the
    Krumhansl-Kessler profiles. Returns {'key': 'F# minor', 'tonic', 'minor', 'confidence'}."""
    pt = pt or pitch_track(x, sr)
    v = _voiced(pt)
    if v.sum() < 30:
        return {"key": None, "confidence": 0.0, "reason": "too little pitched sound"}
    c = _cents(pt["f0"][v])
    dev = ((c + 50.0) % 100.0) - 50.0
    stable = np.abs(dev) <= 35.0
    pcs = (np.round(c[stable] / 100.0).astype(int)) % 12
    hist = np.bincount(pcs, minlength=12).astype(float)
    if hist.sum() < 20:
        return {"key": None, "confidence": 0.0, "reason": "too few steady notes"}
    hist /= hist.sum()
    scores = []
    for tonic in range(12):
        for minor, prof in ((False, _KK_MAJOR), (True, _KK_MINOR)):
            p = np.roll(prof, tonic)
            r = np.corrcoef(hist, p)[0, 1]
            scores.append((r, tonic, minor))
    scores.sort(reverse=True)
    best, second = scores[0], scores[1]
    # the relative major/minor share the notes: say so instead of pretending to know
    rel = (second[1] - best[1]) % 12 in (3, 9) and second[2] != best[2]
    conf = max(0.0, min(1.0, (best[0] - second[0]) * 4.0 + 0.35)) if not rel else max(0.0, min(1.0, (best[0] - scores[2][0]) * 4.0 + 0.3))
    name = "%s %s" % (NOTE_NAMES[best[1]], "minor" if best[2] else "major")
    out = {"key": name, "tonic": NOTE_NAMES[best[1]], "minor": bool(best[2]), "confidence": _r(conf, 2),
           "runners_up": ["%s %s" % (NOTE_NAMES[t], "minor" if mi else "major") for _s, t, mi in scores[1:3]],
           "notes_used_percent": {NOTE_NAMES[i]: _r(100 * hist[i], 0) for i in range(12) if hist[i] >= 0.03}}
    if rel:
        out["or"] = "%s %s (the relative %s: same notes)" % (NOTE_NAMES[second[1]], "minor" if second[2] else "major",
                                                           "minor" if second[2] else "major")
    return out


# ---------------------------------------------------------------------------
# space: reverb and echo
# ---------------------------------------------------------------------------

def _env_db(x, sr, hop_s=HOP_S):
    m = _mono(x)
    hop = int(round(hop_s * sr))
    n = len(m) // hop
    e = (m[: n * hop].reshape(n, hop) ** 2).mean(axis=1)
    return 10.0 * np.log10(e + 1e-14), hop


def _phrase_ends(lv, hop_s, voice_ref, min_gap_s=0.45):
    """(end, next_start) frame pairs: the last frame of a stretch of voice (at least 0.25 s within 12 dB of the
    voice's level, holes under 150 ms closed) that is followed by at least min_gap_s without voice."""
    thr = voice_ref - 12.0
    loud = lv >= thr
    hole = int(round(0.15 / hop_s))
    # close short holes
    idx = np.flatnonzero(loud)
    if idx.size == 0:
        return []
    for a, b in zip(idx[:-1], idx[1:]):
        if 1 < b - a <= hole:
            loud[a:b] = True
    n = len(lv)
    ends = []
    i = 0
    while i < n:
        if loud[i]:
            j = i
            while j < n and loud[j]:
                j += 1
            k = j
            while k < n and not loud[k]:
                k += 1
            if (j - i) * hop_s >= 0.25 and (k - j) * hop_s >= min_gap_s:
                ends.append((j, k))
            i = k
        else:
            i += 1
    return ends


def reverb(x, sr, echo_ms=None):
    """The tail after word endings: how fast it decays (RT60) and how loud it is just after the word.
    Returns none / short / medium / long with the numbers. Echo bumps (a repeating delay) sit above the
    smooth decay and are left out of the fit; echo() reads them."""
    lv, hop = _env_db(x, sr)
    hop_s = hop / sr
    if len(lv) < 100:
        return {"amount": "none", "confidence": 0.2, "reason": "too short"}
    ref = float(np.percentile(lv[lv > -90], 95)) if (lv > -90).any() else -30.0
    floor = float(np.percentile(lv[lv > -120], 5)) if (lv > -120).any() else -100.0
    ends = _phrase_ends(lv, hop_s, ref)
    if len(ends) < 3:
        return {"amount": "none", "confidence": 0.3, "reason": "fewer than three clear phrase endings to measure a tail on",
                "phrase_endings": len(ends)}
    slopes, tails, cents, rough = [], [], [], []
    steady = 0
    m = _mono(x)
    for e, nxt in ends:
        top = float(np.max(lv[max(0, e - 3):e + 1]))
        seg = lv[e:nxt]
        if float(np.min(seg)) > top - 16.0:
            steady += 1                                              # a steady background fills the gap: no decay to read
            continue
        lo_lim = max(floor + 6.0, top - 50.0)
        t_, v_ = [], []
        for j, val in enumerate(seg):
            if val < lo_lim:
                break
            if echo_ms and any(abs(j * hop_s - k * echo_ms / 1000.0) < 0.12 for k in (1, 2, 3, 4)):
                continue                                             # an echo lands here: not part of the decay
            if val <= top - 6.0:
                t_.append(j * hop_s)
                v_.append(val)
        if len(t_) >= 5:
            t_, v_ = np.array(t_), np.array(v_)
            k = np.polyfit(t_, v_, 1)[0]
            resid = v_ - np.polyval(np.polyfit(t_, v_, 1), t_)
            keep = resid <= np.percentile(resid, 60)              # the lower envelope: echo bumps sit above it
            if keep.sum() >= 4:
                k = np.polyfit(t_[keep], v_[keep], 1)[0]
                rough.append(float(np.std(v_[keep] - np.polyval(np.polyfit(t_[keep], v_[keep], 1), t_[keep]))))
            if k <= -8.0 and (float(v_.max()) - float(v_.min())) >= 10.0:   # a real decay: at least 10 dB, RT60 under 7.5 s
                slopes.append(k)
            else:
                steady += 1
        j = e + int(0.12 / hop_s)
        if j < nxt:
            tails.append(lv[j] - ref)
        s0, s1 = (e + int(0.08 / hop_s)) * hop, min(nxt, e + int(0.5 / hop_s)) * hop
        if s1 - s0 > sr // 8 and s1 <= len(m):
            f, pt = signal.welch(m[s0:s1], sr, nperseg=2048)
            sel = f >= 150
            cents.append(float((pt[sel] * f[sel]).sum() / (pt[sel].sum() + 1e-20)))
    if not slopes:
        if echo_ms:
            return {"amount": "unknown", "confidence": 0.3, "phrase_endings": len(ends),
                    "reason": "the echoes fill the gaps between phrases, so a reverb under them could not be measured"}
        if steady >= len(ends) / 2.0:
            return {"amount": "unknown", "confidence": 0.3, "phrase_endings": len(ends),
                    "reason": "a steady background fills the gaps between phrases (the beat left in by the separation, or "
                              "room noise), so no decay could be read; a reverb may still be there"}
        return {"amount": "none", "confidence": 0.5, "reason": "word endings stop dead: no tail to measure", "phrase_endings": len(ends)}
    slope = float(np.median(slopes))
    rt60 = 60.0 / -slope
    tail_db = float(np.median(tails)) if tails else -60.0
    centroid = float(np.median(cents)) if cents else 0.0
    dark = centroid < 2500.0
    # a dry voice decays within a fraction of a second and sits 40+ dB under its loud level 120 ms after a word
    if rt60 < 0.35 or tail_db < -42.0:
        amount, conf = "none", 0.7
    elif rt60 < 0.9:
        amount, conf = "short", 0.7 if tail_db > -34.0 else 0.4
    elif rt60 < 1.8:
        amount, conf = "medium", 0.75
    else:
        amount, conf = "long", 0.7
    roughness = float(np.median(rough)) if rough else 0.0
    if roughness > 3.0 and amount != "none":
        conf = min(conf, 0.4)
    out = {"amount": amount, "confidence": conf, "decay_rt60_s": _r(rt60, 2), "tail_after_120ms_db": _r(tail_db),
           "tail_centroid_hz": _r(centroid, 0), "dark": bool(dark), "phrase_endings_used": len(slopes),
           "decay_fit_roughness_db": _r(roughness)}
    if roughness > 3.0 and amount != "none":
        out["note"] = ("the tail is not a smooth decay (echoes, or the beat left in by the separation, are in it), so the "
                       "decay time is a rough figure")
    if amount == "short" and (conf < 0.5 or (rt60 < 0.6 and tail_db < -28.0)):
        out["confidence"] = conf = 0.4
        out["note"] = "a quiet, short tail like this can also be the singer's own word endings"
    if echo_ms:
        out["note"] = (out.get("note", "") + " The echoes were left out of the decay fit; with a delay on the voice the decay "
                       "time is less exact.").strip()
    out["confidence"] = conf
    if amount != "none":
        out["what_it_sounds_like"] = "%s reverb (about %.1f s decay), %s, %s" % (
            amount, rt60, "dark" if dark else "bright",
            "well behind the voice" if tail_db < -30 else "clearly audible" if tail_db < -20 else "very wet")
        out["suggest"] = {"room_size": {"short": 25, "medium": 50, "long": 75}[amount], "dampening": 70 if dark else 35,
                          "send_db": _r(max(-30.0, min(-6.0, tail_db + 10.0)))}
    return out


def _shape_frames(m, sr, hop_s=0.02, win_s=0.04):
    """Per 20 ms: the spectral shape (band levels in dB, 36 bands 150 Hz - 9 kHz, frame mean removed) and the level."""
    hop, win = int(round(hop_s * sr)), int(round(win_s * sr))
    if len(m) < win:
        m = np.concatenate([m, np.zeros(win - len(m))])
    view = np.lib.stride_tricks.sliding_window_view(m, win)[::hop]
    nfft = 1 << int(np.ceil(np.log2(win)))
    w = np.hanning(win)
    freqs = np.fft.rfftfreq(nfft, 1.0 / sr)
    edges = np.geomspace(150.0, min(9000.0, sr * 0.45), 37)
    masks = [(freqs >= a) & (freqs < b) for a, b in zip(edges, edges[1:])]
    shapes = np.empty((view.shape[0], len(masks)))
    level = np.empty(view.shape[0])
    for i in range(0, view.shape[0], 1024):
        blk = view[i:i + 1024] * w
        p = np.abs(np.fft.rfft(blk, nfft, axis=1)) ** 2
        level[i:i + 1024] = 10 * np.log10(p.sum(axis=1) + 1e-20)
        bands = np.stack([p[:, mk].sum(axis=1) for mk in masks], axis=1)
        sh = 10 * np.log10(bands + 1e-20)
        shapes[i:i + 1024] = sh - sh.mean(axis=1, keepdims=True)
    return shapes, level, hop_s


def echo(x, sr, bpm=None):
    """A repeating delay: the same words come back, quieter, a fixed time later. Read from the spectral
    SHAPE: in the gaps between phrases, where nothing new is sung, a frame that is a copy of a frame some
    time earlier has the same shape. The similarity between gap frames and the frames tau earlier peaks
    sharply at the delay time; a reverb smears everything and gives no sharp peak, and the rhythm of the
    words cannot do it, because the words are not in the gaps."""
    m = _mono(x)
    shapes, level, hop_s = _shape_frames(m, sr)
    n = len(level)
    if n < 150:
        return {"found": False, "reason": "too short"}
    ref = float(np.percentile(level[level > -90], 95)) if (level > -90).any() else -30.0
    floor = float(np.percentile(level[level > -120], 5)) if (level > -120).any() else -100.0
    loud = level >= ref - 10.0                                       # the voice itself (an echo 10 dB down is not "loud")
    # a gap frame: quiet, not digital silence, and at least 150 ms after the last loud frame (past the
    # word's own release); so the shortest delay that can be found is about 150 ms
    since = np.full(n, 10 ** 9)
    last = -10 ** 9
    for i in range(n):
        if loud[i]:
            last = i
        since[i] = i - last
    gap = (level < ref - 10.0) & (level > floor + 6.0) & (since >= int(round(0.15 / hop_s)))
    # shapes as deviations from the voice's average shape: two frames of the same voice then match only
    # when they are the same sound, not merely the same singer
    dev = shapes - shapes[loud].mean(axis=0) if loud.any() else shapes
    unit = dev / (np.linalg.norm(dev, axis=1, keepdims=True) + 1e-9)
    lo, hi = int(round(0.15 / hop_s)), min(n - 1, int(round(1.5 / hop_s)))
    sim = np.zeros(hi + 1)
    cnt = np.zeros(hi + 1)
    lvl_ratio = [[] for _ in range(hi + 1)]
    for tau in range(lo, hi + 1):
        pair = gap[tau:] & loud[:-tau]
        if pair.sum() < 10:
            continue
        a_ = unit[tau:][pair]
        b_ = unit[:-tau][pair]
        sim[tau] = float(np.mean((a_ * b_).sum(axis=1)))
        cnt[tau] = pair.sum()
        lvl_ratio[tau] = (level[tau:][pair] - level[:-tau][pair])
    ok = cnt >= 10
    if ok.sum() < 20:
        return {"found": False, "confidence": 0.3, "reason": "too few gaps to look for an echo in"}
    # an echo is a narrow peak; a reverb tail or the word's own fade is a smooth slope
    filled = sim.copy()
    first = int(np.flatnonzero(ok)[0])
    filled[:first] = sim[first]                                       # no cliff at the start of the usable range
    peaks, props = signal.find_peaks(filled, prominence=0.12, wlen=int(0.6 / hop_s) | 1, width=(1, int(0.24 / hop_s)))
    peaks = np.array([p_ for p_ in peaks if ok[p_]], dtype=int)
    if len(peaks):
        props = {"prominences": signal.peak_prominences(filled, peaks, wlen=int(0.6 / hop_s) | 1)[0]}
    if len(peaks) == 0:
        return {"found": False, "confidence": 0.6, "best_similarity": _r(float(sim[ok].max()), 2)}
    strongest = float(props["prominences"].max())
    cands = [p_ for p_, pr in zip(peaks, props["prominences"]) if pr >= 0.6 * strongest]
    tau = int(cands[0])
    prom = float(props["prominences"][list(peaks).index(tau)])
    level_db = float(np.median(lvl_ratio[tau])) if len(lvl_ratio[tau]) else None
    # a real echo: most gap frames at that lag match their source, and all at about the same level under it
    pair = gap[tau:] & loud[:-tau]
    c_all = (unit[tau:][pair] * unit[:-tau][pair]).sum(axis=1)
    share = float(np.mean(c_all > 0.5)) if len(c_all) else 0.0
    lr = np.asarray(lvl_ratio[tau])
    iqr = float(np.percentile(lr, 75) - np.percentile(lr, 25)) if len(lr) >= 4 else 99.0
    found = prom >= 0.12 and level_db is not None and level_db >= -24.0 and share >= 0.4 and iqr <= 10.0
    out = {"found": bool(found), "confidence": _r(min(0.95, 0.3 + prom + 0.3 * share), 2), "time_ms": _r(tau * hop_s * 1000, 0),
           "peak_prominence": _r(prom, 2), "level_db": _r(level_db), "pairs": int(cnt[tau]),
           "matching_share": _r(share, 2), "level_spread_db": _r(iqr)}
    if found:
        two = 2 * tau
        if two <= hi and cnt[two] >= 10 and len(lvl_ratio[two]):
            fb_db = float(np.median(lvl_ratio[two])) - (level_db or 0.0)
            fb = max(0.0, min(0.95, 10 ** (fb_db / 20.0))) if sim[two] >= sim[tau] - 0.35 else 0.1
        else:
            fb = 0.1
        out["feedback"] = _r(fb, 2)
        out["repeats"] = "one echo" if fb < 0.2 else "a few repeats" if fb < 0.5 else "many repeats"
        if bpm:
            beat = 60.0 / float(bpm)
            names = [("quarter note", 1.0), ("dotted eighth", 0.75), ("eighth note", 0.5), ("quarter-note triplet", 2.0 / 3),
                     ("eighth-note triplet", 1.0 / 3), ("sixteenth note", 0.25), ("half note", 2.0), ("dotted quarter", 1.5)]
            best = min(names, key=lambda nv: abs(nv[1] * beat - tau * hop_s))
            if abs(best[1] * beat - tau * hop_s) <= 0.03:
                out["note_value"] = best[0]
                out["note_value_ms"] = _r(best[1] * beat * 1000, 0)
        out["what_it_sounds_like"] = "an echo %d ms after the voice%s, %s, about %d dB under it" % (
            round(tau * hop_s * 1000), " (%s at %s BPM)" % (out["note_value"], _r(bpm, 0)) if out.get("note_value") else "",
            out["repeats"], round(-(level_db or -14.0)))
        out["suggest"] = {"delay_ms": _r(tau * hop_s * 1000, 0), "feedback_percent": _r(100 * fb, 0),
                          "send_db": _r(max(-30.0, min(-6.0, (level_db if level_db is not None else -14.0) - 2.0)))}
    return out


# ---------------------------------------------------------------------------
# dynamics, width, tone extremes, grit
# ---------------------------------------------------------------------------

def dynamics(x, sr):
    """How compressed the voice is: the loud-to-quiet range of its phrases (as the mixing chain measures it)
    and the crest factor of 50 ms blocks (peak over RMS); both shrink with compression."""
    m = _mono(x)
    try:
        rep = A.analyze(np.asarray(m, dtype=np.float32), sr, "vocal")
        spread = (rep.get("dynamics") or {}).get("level_range_db")
    except Exception:
        spread = None
    blk = int(0.05 * sr)
    n = len(m) // blk
    if n < 20 or spread is None:
        return {"amount": "unknown"}
    b = m[: n * blk].reshape(n, blk)
    rms = 10 * np.log10((b ** 2).mean(axis=1) + 1e-14)
    peak = 20 * np.log10(np.abs(b).max(axis=1) + 1e-9)
    act = rms > float(np.percentile(rms, 95)) - 24.0
    crest = float(np.median(peak[act] - rms[act])) if act.sum() >= 10 else None
    spread = float(spread)
    if spread <= 6.0:
        amount, conf = "heavy", 0.75
    elif spread <= 10.0:
        amount, conf = "medium", 0.65
    else:
        amount, conf = "light", 0.65
    return {"amount": amount, "confidence": conf, "loud_to_quiet_range_db": _r(spread), "crest_factor_db": _r(crest),
            "what_it_sounds_like": {"heavy": "very even and in your face: every line at the same level",
                                    "medium": "controlled: loud lines held back, quiet ones brought forward",
                                    "light": "open and dynamic: loud and quiet lines keep their distance"}[amount],
            "suggest": {"compress_amount": amount}}


def width(x, sr):
    """Mono, wide or doubled: level of the side signal against the mid, and whether the side signal is a
    late copy of the voice (a doubler puts a delayed, detuned copy to one side; a reverb's side signal is
    diffuse and matches nothing)."""
    xs = np.asarray(x, dtype=np.float64)
    if xs.ndim == 1 or xs.shape[1] < 2 or np.allclose(xs[:, 0], xs[:, 1], atol=1e-6):
        return {"amount": "mono", "confidence": 0.95, "side_vs_mid_db": None, "what_it_sounds_like": "a single voice in the centre"}
    L, R = xs[:, 0], xs[:, 1]
    mid, side = (L + R) / 2, (L - R) / 2
    blk = int(0.05 * sr)
    n = len(mid) // blk
    e_mid = (mid[: n * blk].reshape(n, blk) ** 2).mean(axis=1)
    loud = e_mid > np.percentile(e_mid, 70)
    s_db = 10 * np.log10((side[: n * blk].reshape(n, blk) ** 2).mean(axis=1)[loud].sum() / (e_mid[loud].sum() + 1e-20) + 1e-14)
    # late copy: normalised cross-correlation of side with mid over 1 s pieces of the loud parts, lags 3..40 ms
    lo, hi = int(0.003 * sr), int(0.04 * sr)
    piece = int(1.0 * sr)
    curves = []
    for k in range(0, len(mid) - piece, piece):
        pm, ps = mid[k:k + piece], side[k:k + piece]
        if (pm ** 2).mean() < np.percentile(e_mid, 60):
            continue
        cc = signal.correlate(ps, pm, mode="full")
        c0 = len(cc) // 2
        seg = np.abs(np.concatenate([cc[c0 - hi:c0 - lo][::-1], cc[c0 + lo:c0 + hi]]))
        norm = np.sqrt((pm ** 2).sum() * (ps ** 2).sum()) + 1e-20
        curves.append(seg / norm)
        if len(curves) >= 12:
            break
    late_ms, late_corr = None, 0.0
    if curves:
        c = np.mean(curves, axis=0)
        j = int(np.argmax(c))
        late_corr = float(c[j])
        if late_corr >= 0.25:
            late = (lo + j) if j < (hi - lo) else (lo + j - (hi - lo))
            late_ms = late / sr * 1000.0
    if s_db < -25:
        amount, conf = "mono", 0.9
    elif late_ms is not None and s_db > -20:
        amount, conf = "doubled", 0.7
    elif s_db > -10:
        amount, conf = "wide", 0.7
    else:
        amount, conf = "slightly wide", 0.6
    words = {"mono": "a single voice in the centre", "slightly wide": "mostly centred, a little spread (a stereo reverb or a light widener)",
             "wide": "spread across the stereo field", "doubled": "a second copy of the voice off to one side (a double or a doubler effect)"}
    out = {"amount": amount, "confidence": conf, "side_vs_mid_db": _r(s_db), "late_copy_ms": _r(late_ms) if late_ms else None,
           "late_copy_match": _r(late_corr, 2), "what_it_sounds_like": words[amount]}
    if amount == "doubled":
        out["suggest"] = {"double_delay_ms": _r(late_ms, 0), "double_level_db": _r(max(-16.0, min(-4.0, s_db + 6.0)))}
    return out


def band_limited(x, sr):
    """Telephone / lo-fi: the voice's lows (<250 Hz) and highs (>4 kHz) both cut far below its middle."""
    m = _mono(x)
    f, p = signal.welch(m, sr, nperseg=4096)
    def band(lo, hi):
        sel = (f >= lo) & (f < hi)
        return 10 * np.log10(p[sel].mean() + 1e-20) if sel.any() else -200
    mid = band(400, 2500)
    lows, highs = band(80, 250) - mid, band(4500, 9000) - mid
    lofi = highs < -34.0
    tele = lofi and lows < -3.0
    kind = "telephone" if tele else "lo-fi" if lofi else None
    return {"found": bool(lofi), "kind": kind, "lows_vs_middle_db": _r(lows), "highs_vs_middle_db": _r(highs),
            "what_it_sounds_like": ("telephone / radio voice: no lows, no highs" if tele else
                                    "muffled, lo-fi: the highs are cut away" if lofi else "full range"),
            "confidence": 0.8 if tele else 0.6 if lofi else 0.7}


def grit(x, sr, pt=None):
    """Distortion. Hard clipping (flat-topped waveforms) can be seen; gentle saturation cannot be told from a
    bright EQ by measurement (checked: 20 dB of soft drive moves a voice's upper harmonics by only 2-4 dB), so
    it is reported as not measurable rather than guessed."""
    m = _mono(x)
    try:
        clip = A._clipping(np.asarray(x, dtype=np.float32))
    except Exception:
        clip = {}
    runs = int(clip.get("clipped_runs") or 0)
    seconds = max(1e-6, len(m) / float(sr))
    per_min = runs / seconds * 60.0
    if per_min >= 20:
        return {"found": True, "kind": "clipping", "confidence": 0.7, "clipped_runs_per_minute": _r(per_min, 0),
                "what_it_sounds_like": "hard distortion: the waveform is flattened at its peaks (clipped) many times a minute"}
    return {"found": None, "confidence": 0.0, "clipped_runs_per_minute": _r(per_min, 0),
            "note": "gentle saturation / grit cannot be told from a bright EQ by measurement; listen for it"}


# ---------------------------------------------------------------------------
# everything
# ---------------------------------------------------------------------------

def analyze_effects(x, sr, bpm=None):
    """All detectors on one vocal; x is (n,) or (n, 2) at sr. Returns a dict keyed by effect."""
    pt = pitch_track(x, sr)
    ech = echo(x, sr, bpm=bpm)
    out = {
        "auto_tune": autotune(x, sr, pt),
        "key": key_of(x, sr, pt),
        "reverb": reverb(x, sr, echo_ms=ech.get("time_ms") if ech.get("found") else None),
        "delay": ech,
        "compression": dynamics(x, sr),
        "width": width(x, sr),
        "telephone": band_limited(x, sr),
        "grit": grit(x, sr, pt),
    }
    try:
        rep = A.analyze(np.asarray(x, dtype=np.float32), sr, "vocal")
        tone = rep.get("tone_vs_natural_speech_db") or {}
        sib = rep.get("sibilance") or {}
        out["tone"] = {k: tone.get(k) for k in ("mud", "presence", "air") if k in tone}
        out["s_sounds"] = {"excess_db": sib.get("excess_db"), "found": bool(sib.get("found"))}
    except Exception as e:                                           # the detectors above stand on their own
        out["tone"] = {"error": str(e)}
    return out
