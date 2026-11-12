"""Music knowledge for RapReaper: the tempo and key of a beat, note values in milliseconds, and the numbers a
mix engineer looks up (loudness targets, where things live in the sound, usual compressor settings ...).

Everything here is computed or looked up on this computer; nothing is fetched from anywhere. The detectors
work on plain audio arrays (numpy) and return dictionaries with a `confidence` figure and plain-word notes, so
that the AI can say how sure the finding is instead of guessing.

  tempo(x, sr)         beats per minute of a beat or a song, with the half- and double-time readings
  key(x, sr)           the key of a beat (polyphonic: chords and bass, not a single sung line)
  note_times(bpm)      note values in milliseconds at that tempo (for delays, pre-delay, release times)
  card(topic, ...)     a reference card: loudness targets, vocal frequency ranges, compression, de-essing ...

The key finder uses the Krumhansl-Kessler key profiles (Psychological Review 89(4), 1982), as rapreaper_effects
does for a sung line; the tempo finder is the usual onset-strength autocorrelation (Ellis, J. New Music Research
36(1), 2007) with a mild preference for tempos that drum machines are set to.
"""
from __future__ import annotations

import math
from fractions import Fraction

import numpy as np
from scipy import signal

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
FLAT_NAMES = {"C#": "Db", "D#": "Eb", "F#": "Gb", "G#": "Ab", "A#": "Bb"}
_KK_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_KK_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
WORK_SR = 22050


def _r(v, nd=1):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    return round(v, nd) if nd else int(round(v))


def _mono(x):
    x = np.asarray(x, dtype=np.float64)
    if x.ndim == 2:
        x = x.mean(axis=1) if x.shape[1] <= 8 else x.mean(axis=0)
    return x


def _to_work_rate(x, sr):
    """Mono audio at WORK_SR (22.05 kHz is plenty for rhythm and for the notes of chords)."""
    m = _mono(x)
    if sr == WORK_SR:
        return m, sr
    frac = Fraction(WORK_SR, int(sr)).limit_denominator(1000)
    return signal.resample_poly(m, frac.numerator, frac.denominator), WORK_SR


# ---------------------------------------------------------------------------
# tempo
# ---------------------------------------------------------------------------

def _onset_envelope(m, sr, n_fft=1024, hop=256, bands=40):
    """How much new sound starts in each 11.6 ms step: the spectral flux of a log-magnitude spectrogram, summed over
    log-spaced bands and with its slow drift removed (a steady pad adds nothing; a kick, a snare or a word does)."""
    if len(m) < n_fft * 2:
        return np.zeros(0), hop / float(sr)
    win = np.hanning(n_fft)
    n_frames = 1 + (len(m) - n_fft) // hop
    frames = np.lib.stride_tricks.as_strided(m, shape=(n_frames, n_fft), strides=(m.strides[0] * hop, m.strides[0]))
    spec = np.abs(np.fft.rfft(frames * win, axis=1))
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    edges = np.geomspace(40.0, min(8000.0, sr / 2.0 - 1.0), bands + 1)
    idx = np.searchsorted(freqs, edges)
    banded = np.zeros((n_frames, bands))
    for b in range(bands):
        a, z = idx[b], max(idx[b] + 1, idx[b + 1])
        banded[:, b] = spec[:, a:z].mean(axis=1)
    logm = np.log1p(1000.0 * banded)
    flux = np.diff(logm, axis=0, prepend=logm[:1])
    env = np.maximum(flux, 0.0).sum(axis=1)
    # remove the slow drift (a ~0.5 s moving average) so that only the attacks remain
    k = max(1, int(round(0.5 * sr / hop)))
    drift = signal.convolve(env, np.ones(k) / k, mode="same")
    env = np.maximum(env - drift, 0.0)
    return env, hop / float(sr)


def _autocorr(env):
    n = len(env)
    size = 1 << (2 * n - 1).bit_length()
    f = np.fft.rfft(env - env.mean(), size)
    ac = np.fft.irfft(f * np.conj(f))[:n]
    counts = np.arange(n, 0, -1, dtype=float)
    ac = ac / counts                                   # unbiased: a long lag is averaged over fewer pairs
    if ac[0] > 0:
        ac = ac / ac[0]
    return ac


def _tempo_scores(ac, step, lo_bpm, hi_bpm, prior_bpm=110.0, prior_octaves=0.9):
    lags = np.arange(len(ac))
    with np.errstate(divide="ignore"):
        bpms = np.where(lags > 0, 60.0 / (lags * step), np.inf)
    ok = (bpms >= lo_bpm) & (bpms <= hi_bpm)
    score = np.full(len(ac), -np.inf)
    prior = np.exp(-0.5 * (np.log2(bpms[ok] / prior_bpm) / prior_octaves) ** 2)
    base = ac[ok]
    # a beat has energy at its own period and at its multiples: add what the lag's double and quadruple say
    double = np.array([ac[2 * l] if 2 * l < len(ac) else 0.0 for l in lags[ok]])
    quad = np.array([ac[4 * l] if 4 * l < len(ac) else 0.0 for l in lags[ok]])
    score[ok] = (base + 0.5 * double + 0.25 * quad) * prior
    return score, bpms


def _refine_lag(ac, lag):
    """Parabolic interpolation around the autocorrelation peak, for a tempo finer than one step."""
    if lag <= 0 or lag + 1 >= len(ac):
        return float(lag)
    a, b, c = ac[lag - 1], ac[lag], ac[lag + 1]
    den = a - 2.0 * b + c
    if abs(den) < 1e-12:
        return float(lag)
    return float(lag) + 0.5 * (a - c) / den


def tempo(x, sr, lo_bpm=55.0, hi_bpm=210.0):
    """The tempo of a beat or a song: {'bpm', 'confidence', 'also_heard_as': [...], 'steady', 'note'}.

    `bpm` is the reading a drum machine would most likely be set to. `also_heard_as` gives the half- and
    double-time readings with their relative strength: a trap beat at 140 is often felt at 70, and a delay
    timed to one is twice as long as to the other, so both are reported and the person decides."""
    m, sr = _to_work_rate(x, sr)
    if len(m) < sr * 6:
        return {"bpm": None, "confidence": 0.0, "note": "too short: at least six seconds of audio are needed to find a tempo"}
    peak = float(np.max(np.abs(m))) if len(m) else 0.0
    if peak < 1e-5:
        return {"bpm": None, "confidence": 0.0, "note": "silent"}
    m = m / peak
    env, step = _onset_envelope(m, sr)
    if len(env) < 10 or env.max() <= 0:
        return {"bpm": None, "confidence": 0.0, "note": "no attacks found (a pad or a held note has no beat to find)"}
    # are there attacks at all? a beat's onset curve is spiky (its peaks stand far above its mean); a held chord, a
    # pad or steady noise gives a flat curve whose ripples would still correlate with themselves
    peakiness = float(env.max() / (env.mean() + 1e-12))
    if peakiness < 12.0:
        return {"bpm": None, "confidence": 0.0, "peakiness": _r(peakiness, 1),
                "note": "no clear attacks found (a pad, a held note or steady noise has no beat to find)"}
    ac = _autocorr(env)
    score, bpms = _tempo_scores(ac, step, lo_bpm, hi_bpm)
    best = int(np.argmax(score))
    if not np.isfinite(score[best]) or score[best] <= 0:
        return {"bpm": None, "confidence": 0.0, "note": "no steady beat found"}
    lag = _refine_lag(ac, best)
    bpm = 60.0 / (lag * step)
    # how clear the winner is: its autocorrelation against the typical value over the range (a steady beat peaks sharply)
    ok = np.isfinite(score)
    spread = float(np.median(ac[ok])) if ok.any() else 0.0
    conf = max(0.0, min(1.0, (float(ac[best]) - spread) * 2.5))
    # is the beat steady? the first and second half should agree
    steady = None
    half = len(env) // 2
    if half > int(4.0 / step):
        ests = []
        for part in (env[:half], env[half:]):
            ac_p = _autocorr(part)
            sc_p, _b = _tempo_scores(ac_p, step, lo_bpm, hi_bpm)
            bi = int(np.argmax(sc_p))
            if np.isfinite(sc_p[bi]) and bi > 0:
                ests.append(60.0 / (_refine_lag(ac_p, bi) * step))
        if len(ests) == 2:
            a, b = ests
            ratio = max(a, b) / min(a, b)
            steady = bool(abs(ratio - 1.0) < 0.03 or abs(ratio - 2.0) < 0.06)
            if not steady:
                conf *= 0.6
    also = []
    for factor, name in ((0.5, "half-time"), (2.0, "double-time")):
        other = bpm * factor
        if lo_bpm <= other <= hi_bpm:
            l2 = int(round(60.0 / (other * step)))
            if 0 < l2 < len(ac):
                rel = float(ac[l2]) / float(ac[best]) if ac[best] > 0 else 0.0
                other = float(other)
                shown_other = int(round(other)) if abs(other - round(other)) < 0.5 else round(other, 1)
                also.append({"bpm": shown_other, "feel": name, "relative_strength": _r(max(0.0, min(1.5, rel)), 2)})
    # beats are set to whole numbers far more often than not: say the whole number when it is that close
    bpm = float(bpm)
    shown = int(round(bpm)) if abs(bpm - round(bpm)) < 0.25 else round(bpm, 1)
    notes = []
    if conf < 0.35:
        notes.append("The beat is not very steady or its attacks are soft, so take this reading with care: check it against the "
                     "producer's own number if you have it.")
    strong_other = [a for a in also if (a.get("relative_strength") or 0) >= 0.5]
    if strong_other:
        notes.append("Both readings are real: the kick and snare move at one speed and the hi-hats at twice that. Trap and drill "
                     "beats are usually written as the faster number (130-170), boom-bap as the slower (80-100). A delay in "
                     "milliseconds is the same either way; only its note name changes (an eighth at %s is a quarter at %s)."
                     % (max(shown, strong_other[0]["bpm"]), min(shown, strong_other[0]["bpm"])))
    out = {"bpm": shown, "measured_bpm": _r(bpm, 2), "confidence": _r(conf, 2), "also_heard_as": also}
    if steady is not None:
        out["steady"] = steady
    if notes:
        out["note"] = " ".join(notes)
    return out


# ---------------------------------------------------------------------------
# key of a beat (polyphonic)
# ---------------------------------------------------------------------------

def _chroma_and_tuning(m, sr, n_fft=8192, hop=4096, lo_hz=55.0, hi_hz=2200.0):
    """Which of the twelve notes sound, how much: spectral peaks between A1 and about C7, each added to its pitch
    class with its magnitude. Peaks rather than the whole spectrum, so that drums (broadband) add little. Also the
    average distance of the peaks from the nearest standard-tuning note, in cents (a beat made from a detuned
    sample sits between two notes everywhere)."""
    if len(m) < n_fft:
        m = np.pad(m, (0, n_fft - len(m)))
    win = np.hanning(n_fft)
    n_frames = 1 + (len(m) - n_fft) // hop
    frames = np.lib.stride_tricks.as_strided(m, shape=(n_frames, n_fft), strides=(m.strides[0] * hop, m.strides[0]))
    spec = np.abs(np.fft.rfft(frames * win, axis=1))
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    band = (freqs >= lo_hz) & (freqs <= hi_hz)
    chroma = np.zeros(12)
    cents_sum, cents_w = 0.0, 0.0
    total_w, held_w = 0.0, 0.0
    previous = {}                                        # semitone -> weight, in the frame before (for persistence)
    for fr in spec:
        s = fr * band
        if s.max() <= 0:
            previous = {}
            continue
        thr = np.median(s[band]) * 4.0 + 1e-9
        pk, _ = signal.find_peaks(s, height=thr, distance=3)
        if len(pk) == 0:
            previous = {}
            continue
        # interpolate the exact frequency of each peak (the bins are 2.7 Hz apart; a semitone at 55 Hz is 3.3 Hz)
        f = freqs[pk].astype(float)
        for j, b in enumerate(pk):
            if 0 < b < len(fr) - 1:
                a, bb, c = fr[b - 1], fr[b], fr[b + 1]
                den = a - 2 * bb + c
                if abs(den) > 1e-12:
                    f[j] = freqs[b] + 0.5 * (a - c) / den * (freqs[1] - freqs[0])
        f = np.maximum(f, 1.0)
        semis = 12.0 * np.log2(f / 440.0)
        nearest = np.round(semis)
        dev = semis - nearest
        w = s[pk] ** 0.5                                  # magnitude, compressed: a loud bass note is not the whole chord
        now = {}
        for k in range(len(pk)):
            if abs(dev[k]) <= 0.35:
                st = int(nearest[k])
                now[st] = now.get(st, 0.0) + w[k]
                total_w += w[k]
                # a note of a chord or a bass line is still there in the next frame (0.19 s later); a drum hit is not.
                # Only sounds that hold on count toward the key, so that a beat of drums alone gives no key.
                if st in previous:
                    pc = (st + 9) % 12                    # A440 is pitch class 9
                    chroma[pc] += w[k]
                    held_w += w[k]
                    cents_sum += dev[k] * 100.0 * w[k]
                    cents_w += w[k]
        previous = now
    tuning = (cents_sum / cents_w) if cents_w > 0 else 0.0
    held_share = (held_w / total_w) if total_w > 0 else 0.0
    return chroma, tuning, held_share


CAMELOT = {("C", False): "8B", ("G", False): "9B", ("D", False): "10B", ("A", False): "11B", ("E", False): "12B",
           ("B", False): "1B", ("F#", False): "2B", ("C#", False): "3B", ("G#", False): "4B", ("D#", False): "5B",
           ("A#", False): "6B", ("F", False): "7B",
           ("A", True): "8A", ("E", True): "9A", ("B", True): "10A", ("F#", True): "11A", ("C#", True): "12A",
           ("G#", True): "1A", ("D#", True): "2A", ("A#", True): "3A", ("F", True): "4A", ("C", True): "5A",
           ("G", True): "6A", ("D", True): "7A"}


def key_name(tonic_index, minor):
    tonic = NOTE_NAMES[tonic_index % 12]
    name = "%s %s" % (tonic, "minor" if minor else "major")
    if tonic in FLAT_NAMES:
        name += " (%s %s)" % (FLAT_NAMES[tonic], "minor" if minor else "major")
    return name


def relative_key(tonic_index, minor):
    """The relative major of a minor key, or the relative minor of a major key: the same notes."""
    return ((tonic_index + 3) % 12, False) if minor else ((tonic_index + 9) % 12, True)


def scale_notes(tonic_index, minor):
    steps = [0, 2, 3, 5, 7, 8, 10] if minor else [0, 2, 4, 5, 7, 9, 11]
    return [NOTE_NAMES[(tonic_index + s) % 12] for s in steps]


def key(x, sr):
    """The key of a beat: {'key', 'tonic', 'minor', 'confidence', 'relative', 'camelot', 'scale_notes', 'tuning_cents',
    'runners_up', 'note'}. Chords and bass lines together, so it works on music rather than on one sung line."""
    m, sr = _to_work_rate(x, sr)
    if len(m) < sr * 4:
        return {"key": None, "confidence": 0.0, "note": "too short: at least four seconds of audio are needed"}
    peak = float(np.max(np.abs(m))) if len(m) else 0.0
    if peak < 1e-5:
        return {"key": None, "confidence": 0.0, "note": "silent"}
    m = m / peak
    chroma, tuning, held_share = _chroma_and_tuning(m, sr)
    if chroma.sum() <= 0 or held_share < 0.25:
        return {"key": None, "confidence": 0.0, "pitched_share": _r(held_share, 2),
                "note": "too little held, pitched sound to tell a key (drums alone have none; a beat needs its bass or chords)"}
    hist = chroma / chroma.sum()
    scores = []
    for tonic in range(12):
        for minor, prof in ((False, _KK_MAJOR), (True, _KK_MINOR)):
            p = np.roll(prof, tonic)
            r = float(np.corrcoef(hist, p)[0, 1])
            scores.append((r, tonic, minor))
    scores.sort(reverse=True)
    best = scores[0]
    r_tonic, r_minor = relative_key(best[1], best[2])
    rel_score = next(sc for sc, t, mi in scores if (t, mi) == (r_tonic, r_minor))
    rel = (best[0] - rel_score) < 0.08                   # the relative key fits about as well: the same notes, after all
    others = [sc for sc, t, mi in scores[1:] if (t, mi) != (r_tonic, r_minor)]
    gap = best[0] - (others[0] if others else 0.0)
    conf = max(0.0, min(1.0, gap * 4.0 + 0.3))
    if best[0] < 0.5:
        conf *= 0.6
    if held_share < 0.5:
        conf *= 0.8
    out = {"key": key_name(best[1], best[2]), "tonic": NOTE_NAMES[best[1]], "minor": bool(best[2]), "confidence": _r(conf, 2),
           "relative": key_name(r_tonic, r_minor) + " (the same notes; a melody can be in either)",
           "camelot": CAMELOT.get((NOTE_NAMES[best[1]], bool(best[2]))),
           "scale_notes": scale_notes(best[1], best[2]),
           "runners_up": [key_name(t, mi) for _s, t, mi in scores[1:3]],
           "notes_heard_percent": {NOTE_NAMES[i]: _r(100 * hist[i], 0) for i in range(12) if hist[i] >= 0.04},
           "tuning_cents": _r(tuning, 0), "pitched_share": _r(held_share, 2)}
    notes = []
    if rel:
        notes.append("The relative %s fits almost as well: the two share every note, so an auto-tune set to either key "
                     "pulls to the same notes." % ("major" if best[2] else "minor"))
    if abs(tuning) >= 15:
        notes.append("The beat is tuned about %d cents %s standard A=440 tuning (its notes sit between the usual pitches). "
                     "Auto-tune to it with ReaTune's reference pitch moved by the same amount, or the voice will fight the beat."
                     % (abs(int(round(tuning))), "below" if tuning < 0 else "above"))
    if conf < 0.4:
        notes.append("Not a clear reading: beats built mostly on drums and one bass note give little to go on. The producer's "
                     "key, or a quick check on a keyboard, beats this number.")
    if notes:
        out["note"] = " ".join(notes)
    return out


# ---------------------------------------------------------------------------
# note values at a tempo
# ---------------------------------------------------------------------------

def note_times(bpm):
    """Note values in milliseconds at `bpm` (4/4): what to type into a delay, a pre-delay or a compressor's release."""
    bpm = float(bpm)
    if not (20.0 <= bpm <= 400.0):
        raise ValueError("bpm must be between 20 and 400")
    q = 60000.0 / bpm
    ms = {"whole_note": 4 * q, "half_note": 2 * q, "quarter_note": q, "dotted_eighth": 0.75 * q, "eighth": q / 2,
          "eighth_triplet": q / 3, "dotted_sixteenth": 0.375 * q, "sixteenth": q / 4, "sixteenth_triplet": q / 6,
          "thirty_second": q / 8}
    return {"bpm": _r(bpm, 1), "bar_seconds_4_4": _r(4 * q / 1000.0, 3),
            "milliseconds": {k: _r(v, 1) for k, v in ms.items()},
            "for_rap_vocals": {
                "slapback_delay": "80-130 ms (not timed to the beat; a thickening, not an echo)",
                "eighth_note_delay": "%s ms: the usual rap vocal echo" % _r(q / 2, 0),
                "dotted_eighth_delay": "%s ms: bounces against the beat, good on hook ad-libs" % _r(0.75 * q, 0),
                "quarter_note_delay": "%s ms: wide and slow; for throws on the last word of a line" % _r(q, 0),
                "reverb_pre_delay": "%s-%s ms (a thirty-second to a sixteenth): keeps the words in front of the reverb"
                                    % (_r(q / 8, 0), _r(q / 4, 0)),
                "compressor_release_in_time": "%s ms (an eighth) or %s ms (a sixteenth) lets the compressor breathe with the beat"
                                              % (_r(q / 2, 0), _r(q / 4, 0))}}


# ---------------------------------------------------------------------------
# reference cards
# ---------------------------------------------------------------------------

LOUDNESS_TARGETS = {
    "what_the_numbers_mean": "Integrated LUFS is how loud the whole song is on average (ITU-R BS.1770). dBTP is the true peak "
                             "after the file is turned back into a waveform; it must stay under the ceiling or the lossy "
                             "version (AAC, Ogg) distorts. A normalizing service turns loud songs DOWN to its level, so a "
                             "master louder than the target only sounds more squashed there, not louder.",
    "services": [
        {"service": "Spotify", "normalizes_to_lufs": -14, "true_peak_max_dbtp": -1,
         "note": "-2 dBTP if the master is louder than -14 LUFS. Listeners can pick Loud (-11) or Quiet (-19). Quiet songs are "
                 "turned up, but only as far as their peaks allow (1 dB of headroom is kept)."},
        {"service": "Apple Music", "normalizes_to_lufs": -16, "true_peak_max_dbtp": -1,
         "note": "Sound Check. A -14 LUFS master plays a little quieter here than a -16 one would, nothing else changes."},
        {"service": "YouTube", "normalizes_to_lufs": -14, "true_peak_max_dbtp": -1, "note": "Turns loud videos down; quiet ones are not turned up."},
        {"service": "Amazon Music", "normalizes_to_lufs": -14, "true_peak_max_dbtp": -1},
        {"service": "Tidal", "normalizes_to_lufs": -14, "true_peak_max_dbtp": -1},
        {"service": "Deezer", "normalizes_to_lufs": -15, "true_peak_max_dbtp": -1, "note": "about -14 to -15 depending on the setting"},
        {"service": "SoundCloud", "normalizes_to_lufs": None, "true_peak_max_dbtp": -1,
         "note": "No loudness normalization when this was written: a loud master plays loud here. Keep the true peak under -1 dBTP "
                 "all the same; the files are transcoded."},
        {"service": "TikTok / Instagram / Reels", "normalizes_to_lufs": None, "true_peak_max_dbtp": -1,
         "note": "Compressed heavily on upload and played on phone speakers: a master around -12 to -10 LUFS with the vocal "
                 "forward survives best. Check it on a phone."},
    ],
    "rap_in_practice": "Most rap releases are mastered between -9 and -7 LUFS with true peaks at -1 to -0.3 dBTP, and are turned down "
                       "by the services. RapReaper's master_loudness stops when the limiter would have to take more than about 6 dB "
                       "off the peaks, because that flattens the song; a denser mix (heavier vocal compression, a tighter 808) is "
                       "how loud records get loud without that.",
    "as_of": "figures as published by the services in October 2026; they change now and then",
}

VOCAL_FREQUENCIES = {
    "how_to_read": "Where things live in a rap vocal, and what a 1-3 dB move there does. Cut before you boost; move 1-2 dB "
                   "at a time; judge at matched loudness.",
    "ranges": [
        {"hz": "below 80-100", "what": "rumble, mic-stand thumps, traffic, the proximity bump of a close mic", "move": "high-pass filter; a male rap voice has nothing useful under about 90 Hz, a higher voice under 100-120"},
        {"hz": "100-250", "what": "weight and body of the voice; also where it fights the 808 and the kick", "move": "leave it if the voice sounds thin; cut 1-3 dB around 150-250 if the vocal and the 808 blur together"},
        {"hz": "250-500", "what": "mud and boxiness (a small room, a cupped mic)", "move": "the usual 2-4 dB cut around 300-400 Hz is what makes a home recording sound recorded in a studio"},
        {"hz": "500-1000", "what": "the 'honk' and nasal part; too much sounds like a phone", "move": "small cuts (1-2 dB) only when it nags"},
        {"hz": "1-2 kHz", "what": "edge and bite; the part that cuts through a loud beat", "move": "a 1-2 dB lift can help a soft voice; too much and it gets hard"},
        {"hz": "2-5 kHz", "what": "presence: the consonants, the words you understand", "move": "the 'clarity' lift, 1-3 dB broad around 3-4 kHz; also where harshness lives, so listen"},
        {"hz": "5-9 kHz", "what": "sibilance: S, T, SH sounds; male voices peak around 5-7 kHz, female 6-9 kHz", "move": "de-esser, not EQ: an EQ cut dulls every word, a de-esser only acts while the S sounds"},
        {"hz": "10 kHz and up", "what": "air and shine", "move": "a 1-4 dB high shelf for the bright modern sound; it also lifts hiss and S's, so de-ess after"},
    ],
    "resonances": "A narrow, ringing peak (often 1-4 kHz in a small room) is cut narrowly, 3-6 dB, exactly at its frequency: "
                  "fix_resonances finds and cuts them.",
}

COMPRESSION = {
    "idea": "A compressor is an automatic fader: above the threshold it turns the level down by the ratio. Attack is how fast it "
            "grabs, release how fast it lets go. Two compressors in a row, each doing a little, sound more natural than one doing a lot.",
    "rap_lead_vocal": {
        "first_compressor_fast": {"ratio": "4:1", "attack_ms": "3-10", "release_ms": "40-100 (or timed to the beat: a sixteenth)",
                                  "gain_reduction_db": "3-6 on the loud words", "job": "catches the peaks, keeps every word at the mic"},
        "second_compressor_slow": {"ratio": "2:1 to 3:1", "attack_ms": "10-30", "release_ms": "100-250", "gain_reduction_db": "2-4",
                                   "job": "evens the lines out, the 'glue'; this is where the vocal starts to sound finished"},
        "total": "5-10 dB of gain reduction on the loudest words across both is normal for rap; pop sits at 3-6, and a whispered or "
                 "melodic rap verse wants the lighter end",
        "make_up_gain": "turn the result back up so the compressed and the bypassed vocal are equally loud, then judge",
    },
    "signs": {"too_much": "the voice pumps or breathes, breaths and room noise come up between words, the attack of each word is gone, it sounds small",
              "too_little": "some words jump out of the beat and others disappear; the fader needs riding",
              "attack_too_fast": "the start of each word is softened: the vocal loses its snap (try 10-15 ms on the first compressor)",
              "release_too_slow": "the compressor never lets go: everything sits at one flat level"},
    "sidechain_from_vocal_to_beat": "ducking the beat 1-3 dB under the vocal, or only its 100-400 Hz, makes room without turning the vocal up; "
                                    "balance_vocal_and_beat dips the beat's EQ where it hides the words",
    "parallel": "a heavily compressed copy mixed in under the original (-10 to -6 dB) gives density without crushing the words; "
                "in REAPER: a send to a track with a compressor at 8:1 or more",
}

DE_ESSING = {
    "what": "Turns the 5-9 kHz band down only while an S, T, SH or Z sounds. Needed more after brightening and compression, "
            "which both push S sounds forward.",
    "settings": {"frequency": "find the S: male voices mostly 5-7 kHz, female 6-9 kHz; RapReaper measures it (deess_guide)",
                 "amount": "2-5 dB off the strongest S sounds is typical; more than about 6 dB and the voice lisps",
                 "mode": "split-band (only the top is turned down) keeps the body of the word; wide-band is more natural but duller",
                 "order": "after compression and tone EQ, before reverb and delay (so the echoes do not spit)"},
    "if_one_s_only": "a single harsh S at one spot is better turned down there (duck_section) than with a harder de-esser everywhere",
}

SPACE = {
    "reverb_on_rap": "Short and dark. Rap lives in front of the speakers; a long reverb pushes it back and blurs the words.",
    "reverb_settings": {"type": "plate or small room for the lead; a hall only on hooks or ad-libs",
                        "decay_s": "0.6-1.5 for the lead; 1.5-3 on a hook ad-lib or a sung part",
                        "pre_delay_ms": "20-60, or a thirty-second to a sixteenth note: the word lands first, the room after",
                        "eq_on_the_return": "high-pass 200-400 Hz (keeps the mud out), low-pass 6-10 kHz (keeps the S's out of the tail)",
                        "level": "send around -18 to -12 dB; you should miss it when it is switched off, not hear it when it is on"},
    "delay_on_rap": {"slapback": "80-130 ms, one repeat, mono, tucked under the voice: thickens without an audible echo",
                     "timed_echo": "an eighth (or dotted eighth) note at the song's tempo, 1-3 repeats (feedback 15-35%), filtered like the reverb return",
                     "throws": "the delay send turned up for the last word of a line only (automation on the send): the classic rap trick",
                     "stereo": "a ping-pong delay on ad-libs; keep the lead's delay mono or narrow so the words stay centred",
                     "level": "send around -20 to -14 dB"},
    "timing": "note_times(bpm), or the song_tempo_and_key tool, gives the milliseconds at the song's tempo.",
}

AUTOTUNE = {
    "key_first": "Auto-tune pulls every note to the nearest note of a scale. The wrong key pulls notes the wrong way and sounds "
                 "'off' no matter the speed. Get the key from the producer, the song_tempo_and_key tool (on the Beat track), or "
                 "learn_song_effects (from a finished song).",
    "reatune": {"natural_repair": "attack/speed about 120 ms, the key and scale set: the listener hears a confident singer",
                "tight_pop": "about 40 ms: clearly tuned, still human",
                "hard_effect": "2-5 ms, plus a strong pull: the T-Pain / Travis Scott effect; works on sustained notes, sounds glitchy on fast talk",
                "scale": "minor and major cover most rap; chromatic (no key) only when the melody really uses every note",
                "reference_pitch": "if song_tempo_and_key reports the beat tuned off A=440, move ReaTune's reference by the same cents"},
    "relative_keys": "A minor and C major share every note; so do F# minor and A major, and so on. For auto-tune they are the same setting.",
    "camelot_wheel": "DJs' shorthand for keys: 8A = A minor, 8B = C major; neighbours on the wheel (7A, 9A, 8B) mix well with 8A.",
    "melodic_rap": "Tune the hook and the sung lines; leave spoken verses untuned or at the natural speed, or the talking warbles.",
}

MIX_ORDER = {
    "order": [
        "1. Record well: loudest line peaking around -12 dBFS, a pop filter, a hand's width from the mic, Mono input (the recording sets the ceiling)",
        "2. Edit: cut empty air and loud breaths (cut_empty_air, find_breaths); fix one-sided clips",
        "3. Clean-up: high-pass, hum notches, the mud cut, the expander for the gaps (vocal_chain cleanup)",
        "4. Compression, two stages (vocal_chain compress)",
        "5. Tone EQ: presence and air toward the bright modern sound (vocal_chain tone)",
        "6. De-ess (vocal_chain deess), then finish: level matched, chain saved",
        "7. Effects: slapback or timed delay, short dark reverb, doubles and ad-libs tucked under (voice_effect, add_send_effect)",
        "8. Spot fixes: a loud word, one harsh S, a breath (duck_section)",
        "9. Balance against the beat (balance_vocal_and_beat), ad-libs 4-8 dB under the lead, doubles 6-10 under",
        "10. Master: loudness and true peak for the release (master_loudness), then export_mix and listen on a phone and in a car",
    ],
    "rule": "one change at a time, compare at matched loudness, and switch the chain off and on before deciding",
}

STACKS = {
    "doubles": "a second take of the same line, 6-10 dB under the lead, often only on the hook; or a doubler effect (voice_effect doubler) when there is no second take",
    "ad_libs": "4-8 dB under the lead, panned 30-70% to one side (alternate sides), brighter and wetter than the lead; telephone or megaphone on some",
    "hook_stacks": "lead centre, two doubles panned left and right at -8 to -12 dB, high-passed higher (120-150 Hz) so the centre keeps the body",
    "backing_words": "the last word of each line doubled or echoed (delay throw) is the cheapest way to make a hook feel big",
}

BEAT_AND_VOCAL = {
    "the_808_problem": "the 808 and the voice both want 100-250 Hz. Cut the vocal under 90-120 Hz, and dip the beat 1-3 dB where the words "
                       "sit (balance_vocal_and_beat finds the range); do not boost the vocal's bass to compete",
    "level": "lead vocal about -1 to +3 LU against the beat in rap (laid back to upfront); if the words are lost, check masking before turning up",
    "mono": "check the mix in mono (REAPER: the master's mono button): a wide doubler or a ping-pong delay can vanish on a phone speaker",
    "headroom": "vocal and beat together should peak under about -3 dBFS before the master limiter; a beat bought at full scale needs its fader down first",
}

RECORDING = {
    "level": "loudest line peaking around -12 dBFS on the track meter (24-bit leaves plenty of room); never clip: clipping cannot be repaired",
    "mic": "a hand's width (10-15 cm) from a pop filter; closer gives more bass (proximity) and more pops; a condenser wants a quiet room, a dynamic forgives the room",
    "room": "the quietest, deadest space available; a duvet behind the singer and a closet full of clothes beat a bare room",
    "input": "Mono input, the one the mic is plugged into (Input 1 on a 1st-3rd Gen Scarlett Solo, Input 2 on the 4th Gen); 48 kHz, 24-bit",
    "monitoring": "Direct Monitor on the Scarlett, headphones, record monitoring off on the track so the voice is heard once",
    "takes": "record the hook and the verse on separate tracks; keep takes you like as new items on the same track and name them",
}

GLOSSARY = {
    "LUFS": "how loud something is on average, the way the ear hears it (ITU-R BS.1770); -14 LUFS is Spotify's playback level",
    "dBTP": "true peak: the highest instant after the file is turned back into a waveform; keep it under -1",
    "dBFS": "level inside the digital file; 0 is full scale, the ceiling",
    "LRA": "loudness range: how much the loudness moves over the song; rap sits around 4-8 LU",
    "high-pass": "removes everything below a frequency (rumble)",
    "shelf": "an EQ that lifts or lowers everything above (high shelf) or below (low shelf) a frequency",
    "Q / bandwidth": "how narrow an EQ move is: narrow for a hum or a resonance, wide for tone",
    "threshold": "the level where a compressor, expander or limiter starts working",
    "ratio": "how firmly a compressor turns down: 4:1 means 4 dB in becomes 1 dB out above the threshold",
    "attack / release": "how fast a compressor grabs and lets go",
    "make-up gain": "turning the compressed signal back up",
    "expander / gate": "turns the quiet parts (the gaps) down further; a gate shuts them off completely",
    "de-esser": "a compressor that only acts on the S sounds",
    "limiter": "a compressor with a ceiling nothing passes; used last, for loudness",
    "send / return": "a copy of the track goes to an effect track (reverb, delay); the fader on that track sets how much",
    "pre-delay": "the gap between the word and the start of its reverb",
    "slapback": "a single short delay (80-130 ms)",
    "throw": "a delay or reverb turned up for one word only",
    "doubler": "a second copy of the voice, slightly out of tune and late, off to one side",
    "sidechain": "one track controlling a compressor on another (the vocal ducking the beat)",
    "masking": "one sound hiding another in the same frequency range (the beat hiding the words)",
    "key / scale": "the set of notes a song uses; auto-tune pulls to them",
    "cents": "hundredths of a semitone; 20 cents off is clearly out of tune",
    "BPM": "beats per minute; a quarter note lasts 60000 / BPM milliseconds",
    "stem": "one finished track of a mix as its own file (the beat, the vocal)",
    "bounce / export": "render the mix to one file",
}

COMMANDS = {
    "how": "What the person says, and the RapReaper tool for it (the playbook has the full table).",
    "requests": [
        {"they_say": "clean up my vocal / make it sound professional", "tool": "vocal_chain: plan, cleanup, compress, tone, deess, finish; then balance_vocal_and_beat"},
        {"they_say": "brighter / clearer / less muddy / warmer", "tool": "vocal_chain tone with tone_adjust_db, then deess and finish"},
        {"they_say": "more / less compression, punchier, more even", "tool": "vocal_chain compress with amount; punchier = ask (compression or attack)"},
        {"they_say": "the S sounds hurt", "tool": "vocal_chain deess with deess_db; one S: duck_section with s_near_seconds"},
        {"they_say": "cut the dead air / remove the breaths / turn that word down", "tool": "cut_empty_air, find_breaths, duck_section, cut_section (show first, apply on yes)"},
        {"they_say": "what key / what tempo is this beat", "tool": "song_tempo_and_key on the Beat track (or a reference file)"},
        {"they_say": "auto-tune it / autotune the hook", "tool": "voice_effect autotune (or hard_tune) with the key and scale; song_tempo_and_key first if the key is unknown"},
        {"they_say": "add a delay timed to the beat / a dotted eighth", "tool": "song_tempo_and_key for the ms, then voice_effect delay (or set_plugin on ReaDelay)"},
        {"they_say": "reverb / space / double it / telephone voice", "tool": "voice_effect reverb, delay, doubler, telephone, megaphone, grit, chorus"},
        {"they_say": "what effects are on the vocal in this song / make mine sound like that", "tool": "learn_song_effects (song in the references folder), then apply_song_effects"},
        {"they_say": "sit the vocal in the beat / vocal louder", "tool": "balance_vocal_and_beat (plan, then apply)"},
        {"they_say": "make it loud enough for Spotify / master it", "tool": "master_loudness (streaming), repeated until finished"},
        {"they_say": "export the song", "tool": "export_mix"},
        {"they_say": "what does X do / how loud should it be / where does Y live in the sound", "tool": "reference_card with the topic, then explain in plain words"},
        {"they_say": "is everything working", "tool": "self_test (with a recording in the project)"},
        {"they_say": "remaster this song / mix my Pro Tools (FL Studio, Ableton ...) session here / import these stems", "tool": "remaster (the folder in kit/imports; plan first, then apply); then the usual pass: vocal_chain, balance_vocal_and_beat, master_loudness, export_mix"},
    ],
}

# Project files of other programs. REAPER opens none of them (each is that program's own format, and most hold
# patterns, plug-in settings and references to samples rather than audio), so the way in is always the same:
# export every track as an audio file from the song's very start, then bring the files into RapReaper's imports
# folder. These notes say where that export is in each program.
DAW_FILES = {
    ".ptx": ("Pro Tools session", "In Pro Tools: Edit > Select All (from the very start of the session to its end), then "
             "Edit > Consolidate Clip (Alt+Shift+3 on Windows, Option+Shift+3 on a Mac) so every file starts at the same moment; "
             "in the Clips List select the new clips and choose Export Clips as Files (Ctrl+Shift+K / Cmd+Shift+K): WAV, 24-bit, "
             "the session's sample rate, into one folder. Or File > Bounce Mix with every track as its own source (newer versions). "
             "Copy that folder into RapReaper's imports folder."),
    ".ptf": ("Pro Tools session (older)", "Open it in Pro Tools and export its tracks as described for .ptx."),
    ".pts": ("Pro Tools session (older)", "Open it in Pro Tools and export its tracks as described for .ptx."),
    ".flp": ("FL Studio project", "An .flp holds patterns and references to samples and plug-ins; only FL Studio can play it. In "
             "FL Studio, first route the vocal and the beat to their own mixer tracks (each channel's Insert box). Then File > "
             "Export > WAV file, and in the export window tick 'Split mixer tracks': each mixer track becomes a WAV that starts "
             "at the song's start. WAV, 24-bit. Export the whole mix once more without the tick, as a reference. Copy the files "
             "into RapReaper's imports folder."),
    ".als": ("Ableton Live set", "In Live: File > Export Audio/Video; Rendered Track: 'All Individual Tracks'; from 1.1.1 to the end "
             "of the song; WAV, 24-bit, no normalization. Copy the files into RapReaper's imports folder."),
    ".logicx": ("Logic Pro project", "In Logic: File > Export > All Tracks as Audio Files; WAV, 24-bit; untick 'Include Audio Tail' "
                "and tick 'Bypass Effect Plug-ins' only if you want RapReaper to do all the processing. Copy the files into "
                "RapReaper's imports folder."),
    ".cpr": ("Cubase project", "In Cubase: File > Export > Audio Mixdown; under Channel Selection tick the tracks (multiple), WAV, "
             "24-bit, the whole locator range from the start. Copy the files into RapReaper's imports folder."),
    ".song": ("Studio One song", "In Studio One: Song > Export Stems; tick the tracks, WAV, 24-bit, from the start. Copy the files "
              "into RapReaper's imports folder."),
    ".band": ("GarageBand project", "GarageBand exports one mix (Share > Export Song to Disk). For separate tracks, solo one track "
              "at a time and export each; or open the project in Logic Pro and use its All Tracks export. Copy the files into "
              "RapReaper's imports folder."),
    ".aup3": ("Audacity project", "In Audacity: File > Export > Export Multiple, split by tracks, WAV 24-bit (tick 'include audio "
              "before first clip' so every file starts at zero). Copy the files into RapReaper's imports folder."),
    ".rpp": ("REAPER project", "REAPER's own format: copy the project folder (the .rpp and its media) into your own folder, open it "
             "once in REAPER, and from then on RapReaper can open it with open_project."),
    ".omf": ("OMF interchange file", "REAPER does not import OMF by itself. Export the tracks as audio files from the program that "
             "made it instead."),
    ".aaf": ("AAF interchange file", "REAPER does not import AAF by itself. Export the tracks as audio files from the program that "
             "made it instead."),
    ".mid": ("MIDI file", "Notes, not sound. Render the instrument to audio in the program that plays it, then import the audio."),
    ".midi": ("MIDI file", "Notes, not sound. Render the instrument to audio in the program that plays it, then import the audio."),
}
AUDIO_FILES = {".wav": "WAV", ".wave": "WAV", ".aif": "AIFF", ".aiff": "AIFF", ".flac": "FLAC", ".mp3": "MP3", ".m4a": "AAC (M4A)",
               ".ogg": "Ogg Vorbis", ".w64": "Wave64", ".caf": "CAF"}

DAW_EXPORT = {
    "why": "REAPER (and so RapReaper) opens no other program's project file: a Pro Tools session, an FL Studio project or an "
           "Ableton set holds patterns, plug-in settings and references to samples, not sound. Every program can export its "
           "tracks as audio files, and that is the way in.",
    "how": "Export every track (or every mixer bus you care about: at least the vocal and the beat) as a WAV from the very start "
           "of the song, 24-bit at the session's sample rate, without normalizing; export the finished mix once more as one file "
           "to compare against. Copy the files into RapReaper's imports folder (kit\\imports\\<song name>) and ask to "
           "remaster or import that folder. Files that start at the same moment line up by themselves; files exported with "
           "their original timestamps (Pro Tools clips) are placed by them.",
    "programs": {ext: {"file": name, "steps": steps} for ext, (name, steps) in DAW_FILES.items()},
    "formats_reaper_reads": sorted(set(AUDIO_FILES.values())),
}

PHONES = {
    "what_a_phone_plays": "One small speaker (or two close together): nothing under about 350 Hz, little over 12 kHz, mono or "
                          "nearly so, and its own limiter clipping whatever peaks too hard. Half of all listening happens there.",
    "what_goes_wrong": {
        "bass_vanishes": "An 808 or kick that is all sub (40-120 Hz) has nothing a phone can play. The listener hears a thin, "
                         "rattly mix and reaches for the volume. The bass that survives is its harmonics at 150-300 Hz.",
        "words_get_lost": "What stands out on a phone is 1-4 kHz, where consonants and vowels live. Pads, keys and hi-hats in that "
                          "band hide the vocal there even when big speakers show plenty of room.",
        "width_cancels": "Stereo widening, chorus, doublers panned apart and stereo delays rely on two speakers. Summed to one, "
                         "parts of them cancel: the vocal gets smaller, the reverb washes out, levels jump between sections.",
        "harsh_top": "A small speaker exaggerates 5-9 kHz: S sounds and hi-hats turn spitty, and a mix that was 'bright' is "
                     "'tinny'.",
        "clipping": "Taking the bass away leaves the mid-range peaks almost at full scale; the phone's own limiter then clips them.",
    },
    "fixes": {
        "bass": "Harmonics, not more sub: a gentle lift at 180-250 Hz on the beat (+2 dB, wide), or saturation on the 808. Never a "
                "sub boost for the phone: it only wastes headroom everywhere else.",
        "words": "Room in the beat at 2-3.5 kHz (-2 to -4 dB, wide) before any vocal boost; then at most +1 to +2 dB on the vocal "
                 "at 3 kHz. balance_vocal_and_beat and phone_fixes measure how far the words stand above the rest on a phone.",
        "width": "Vocal and bass dead centre; wide effects on their own tracks a few dB quieter (phone_fixes does this); a doubler "
                 "or chorus with less width or mix. Aim for a mono loss under 2.5 dB.",
        "top": "De-ess the lead a little harder (vocal_chain stage deess), and dip the beat 1-2 dB at 6-8 kHz when the hi-hats "
               "are the cause rather than the voice.",
        "peaks": "A limiter ceiling of -1.5 dBTP (phone_fixes sets it, master_loudness's targets leave -1 to -2 dBTP) and the "
                 "MP3/M4A exports with their default headroom.",
    },
    "how_to_check": "device_check (the numbers), phone_fixes (the numbers with a plan, and apply: true to make the moves), then "
                    "export_mix with a short preview and listen on the phone itself: nothing replaces that.",
    "numbers_to_aim_for": {"mono_loss_db": "under 2.5", "phone_loss_db": "5 to 8 for rap", "phone_vocal_over_rest_1to4k_db": "0 or more",
                           "bass_harmonics_150to300_over_sub_db": "-12 or better", "phone_top_5to9k_over_words_1to4k_db": "under -3",
                           "phone_true_peak_dbtp": "under -0.5"},
}

CARDS = {
    "phones": PHONES,
    "loudness_targets": LOUDNESS_TARGETS, "vocal_frequencies": VOCAL_FREQUENCIES, "compression": COMPRESSION,
    "de_essing": DE_ESSING, "reverb_and_delay": SPACE, "autotune_and_keys": AUTOTUNE, "mix_order": MIX_ORDER,
    "doubles_and_adlibs": STACKS, "beat_and_vocal": BEAT_AND_VOCAL, "recording": RECORDING, "glossary": GLOSSARY,
    "commands": COMMANDS, "daw_export": DAW_EXPORT,
}
TOPICS = list(CARDS) + ["note_values", "key_info", "all_topics"]


def key_info(name):
    """Facts about a named key ('F# minor', 'Bb major', 'Am'): its notes, relative key, Camelot code, ReaTune settings."""
    text = str(name).strip()
    m = __import__("re").match(r"^\s*([A-Ga-g])\s*([#b♯♭]?)\s*(.*)$", text)
    if not m:
        raise ValueError("a key looks like 'F# minor', 'Bb major' or 'Am'")
    letter, acc, rest = m.group(1).upper(), m.group(2), m.group(3).strip().lower()
    idx = NOTE_NAMES.index(letter)
    if acc in ("#", "♯"):
        idx = (idx + 1) % 12
    elif acc in ("b", "♭"):
        idx = (idx - 1) % 12
    minor = rest in ("m", "min", "minor", "-", "aeolian") or rest.startswith("min")
    if rest and not minor and rest not in ("maj", "major", "", "ionian") and not rest.startswith("maj"):
        raise ValueError("unknown scale '%s': use major or minor" % rest)
    r_t, r_m = relative_key(idx, minor)
    return {"key": key_name(idx, minor), "scale_notes": scale_notes(idx, minor), "relative": key_name(r_t, r_m),
            "camelot": CAMELOT.get((NOTE_NAMES[idx], minor)),
            "reatune": {"key": NOTE_NAMES[idx], "mode": "minor" if minor else "major",
                        "speed": "natural (120 ms attack) for repair, tight (40 ms) for pop, hard (2-5 ms) for the effect"},
            "neighbours_that_mix_well": [key_name(r_t, r_m), key_name((idx + 7) % 12, minor), key_name((idx + 5) % 12, minor)]}


def card(topic, bpm=None, key_name_=None):
    """A reference card by topic (TOPICS lists them)."""
    t = str(topic or "").strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {"loudness": "loudness_targets", "targets": "loudness_targets", "streaming": "loudness_targets", "lufs": "loudness_targets",
               "eq": "vocal_frequencies", "frequencies": "vocal_frequencies", "frequency": "vocal_frequencies", "tone": "vocal_frequencies",
               "compressor": "compression", "compressors": "compression", "deess": "de_essing", "de_esser": "de_essing", "deessing": "de_essing",
               "sibilance": "de_essing", "reverb": "reverb_and_delay", "delay": "reverb_and_delay", "space": "reverb_and_delay",
               "effects": "reverb_and_delay", "autotune": "autotune_and_keys", "auto_tune": "autotune_and_keys", "keys": "autotune_and_keys",
               "key": "key_info", "order": "mix_order", "workflow": "mix_order", "process": "mix_order", "adlibs": "doubles_and_adlibs",
               "ad_libs": "doubles_and_adlibs", "doubles": "doubles_and_adlibs", "stacks": "doubles_and_adlibs", "808": "beat_and_vocal",
               "beat": "beat_and_vocal", "masking": "beat_and_vocal", "record": "recording", "mic": "recording", "terms": "glossary",
               "words": "glossary", "what_to_say": "commands", "tools": "commands", "notes": "note_values", "note_times": "note_values",
               "daw": "daw_export", "export_from_daw": "daw_export", "pro_tools": "daw_export", "fl_studio": "daw_export",
               "import": "daw_export", "remaster": "daw_export", "stems": "daw_export",
               "tempo": "note_values", "bpm": "note_values", "phone": "phones", "mobile": "phones", "speaker": "phones",
               "mono": "phones", "translation": "phones", "small_speakers": "phones", "earbuds": "phones", "topics": "all_topics", "list": "all_topics", "": "all_topics"}
    t = aliases.get(t, t)
    if t == "all_topics":
        return {"topics": TOPICS, "how": "Ask for one topic; note_values needs a bpm, key_info needs a key such as 'F# minor'."}
    if t == "note_values":
        if bpm is None:
            raise ValueError("note_values needs the tempo: pass bpm (status gives the project's tempo; song_tempo_and_key measures the beat's)")
        return note_times(bpm)
    if t == "key_info":
        if not key_name_:
            raise ValueError("key_info needs a key, such as 'F# minor' or 'Bb major'")
        return key_info(key_name_)
    if t not in CARDS:
        raise ValueError("no card called '%s'. Topics: %s" % (topic, ", ".join(TOPICS)))
    out = dict(CARDS[t])
    out["topic"] = t
    return out


# ---------------------------------------------------------------------------
# lining up two recordings of the same music
# ---------------------------------------------------------------------------

def _zscore(v):
    v = np.asarray(v, dtype=np.float64)
    sd = v.std()
    return (v - v.mean()) / sd if sd > 1e-12 else np.zeros_like(v)


def _ncc_full(a, b, min_overlap):
    """Normalized cross-correlation of two zero-mean sequences for every shift: value[k] compares b moved by (k - len(b) + 1)
    steps against a. Each shift is normalized by the energy of the two parts that actually overlap, so a short overlap
    at the ends cannot win by chance; shifts whose overlap is under min_overlap steps are left at zero."""
    la, lb = len(a), len(b)
    n = la + lb - 1
    size = 1 << (n - 1).bit_length()
    num = np.fft.irfft(np.fft.rfft(a, size) * np.conj(np.fft.rfft(b, size)), size)
    # irfft of A * conj(B) gives sum_t a[t + k] b[t] at index k (negative k wrap to the end)
    shifts = np.concatenate([np.arange(0, la), np.arange(-(lb - 1), 0)])
    num = np.concatenate([num[:la], num[size - (lb - 1):]])
    ca2 = np.concatenate([[0.0], np.cumsum(a * a)])
    cb2 = np.concatenate([[0.0], np.cumsum(b * b)])
    out = np.zeros(len(shifts))
    for i, k in enumerate(shifts):
        # a[t + k] b[t] over t in [max(0, -k), min(lb, la - k))
        t0, t1 = max(0, -k), min(lb, la - k)
        if t1 - t0 < min_overlap:
            continue
        ea = ca2[t1 + k] - ca2[t0 + k]
        eb = cb2[t1] - cb2[t0]
        if ea <= 0 or eb <= 0:
            continue
        out[i] = num[i] / math.sqrt(ea * eb)
    return shifts, out


def _band_profile(m, sr, n_fft=2048, hop=1024, bands=24):
    """A coarse picture of the arrangement: the level of 24 log-spaced bands every 46 ms (at 22.05 kHz). Where the attacks
    say WHEN things happen, this says WHAT is playing (a verse with no bass, a hook with a pad), so two shifts that fit the
    rhythm equally are told apart by the arrangement."""
    if len(m) < n_fft * 2:
        return np.zeros((0, bands))
    win = np.hanning(n_fft)
    n_frames = 1 + (len(m) - n_fft) // hop
    frames = np.lib.stride_tricks.as_strided(m, shape=(n_frames, n_fft), strides=(m.strides[0] * hop, m.strides[0]))
    spec = np.abs(np.fft.rfft(frames * win, axis=1))
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    edges = np.geomspace(40.0, min(10000.0, sr / 2.0 - 1.0), bands + 1)
    idx = np.searchsorted(freqs, edges)
    out = np.zeros((n_frames, bands))
    for b in range(bands):
        a_, z = idx[b], max(idx[b] + 1, idx[b + 1])
        out[:, b] = np.log1p(1000.0 * spec[:, a_:z].mean(axis=1))
    return out


def _attack_curve(m, sr, rate=1000):
    """When sound gets louder, as a curve with one value per millisecond: the loudness envelope (|x| smoothed over ~10 ms),
    then its rises only. No frames, so a shift of any fraction of a frame costs nothing; the curve is the same shape
    wherever the music starts in the file."""
    env = signal.sosfiltfilt(signal.butter(2, 60.0, btype="low", fs=sr, output="sos"), np.abs(m))
    frac = Fraction(rate, int(sr)).limit_denominator(1000)
    env = signal.resample_poly(np.maximum(env, 0.0), frac.numerator, frac.denominator)
    env = np.log1p(200.0 * np.maximum(env, 0.0))
    rise = np.maximum(np.diff(env, prepend=env[:1]), 0.0)
    # keep a little of the envelope itself, so a held pad counts for something too (where sections differ)
    return rise + 0.05 * (env - env.mean()), 1.0 / rate


def _bandpass(m, sr, lo=80.0, hi=5000.0):
    sos = signal.butter(4, [lo, min(hi, sr / 2.0 - 1.0)], btype="band", fs=sr, output="sos")
    return signal.sosfiltfilt(sos, m)


def align(ref, sr_ref, other, sr_other, min_overlap_s=8.0):
    """Where `other` sits against `ref` (two recordings that hold the same music, perhaps with different starts and ends):
    {'shift_seconds', 'confidence', 'overlap_seconds', 'alternatives', 'speed_ratio', 'note'}.

    shift_seconds is what to ADD to other's position so that its music coincides with ref's (positive: other starts
    later than it should). Coarse: the attacks of both (onset strength, 11.6 ms steps) are cross-correlated over every
    possible shift, normalized by the part that overlaps. Fine: the waveforms themselves around that shift, to a sample.
    Repetitive music gives several good shifts (a bar later fits almost as well): those are listed as alternatives."""
    a, sr = _to_work_rate(ref, sr_ref)
    b, _sr = _to_work_rate(other, sr_other)
    for name, v in (("the reference", a), ("the track to move", b)):
        if len(v) < sr * 3:
            return {"shift_seconds": None, "confidence": 0.0, "note": "%s is shorter than three seconds" % name}
        if float(np.max(np.abs(v))) < 1e-5:
            return {"shift_seconds": None, "confidence": 0.0, "note": "%s is silent" % name}
    a = a / float(np.max(np.abs(a)))
    b = b / float(np.max(np.abs(b)))
    ea, step = _attack_curve(a, sr)
    eb, _s = _attack_curve(b, sr)
    if len(ea) < 10 or len(eb) < 10:
        return {"shift_seconds": None, "confidence": 0.0, "note": "too little sound to line up"}
    min_ov = max(10, int(round(min_overlap_s / step)))
    shifts, ncc = _ncc_full(_zscore(ea), _zscore(eb), min_ov)
    if not np.isfinite(ncc).any() or ncc.max() <= 0:
        return {"shift_seconds": None, "confidence": 0.0, "note": "the two recordings do not seem to hold the same music"}
    # the arrangement, at a coarser step, summed over the bands: a second opinion that tells sections apart
    pa_, pb_ = _band_profile(a, sr), _band_profile(b, sr)
    arr_step = 1024.0 / sr
    arr = None
    if len(pa_) > 10 and len(pb_) > 10:
        za = _zscore(pa_ - pa_.mean(axis=1, keepdims=True))
        zb = _zscore(pb_ - pb_.mean(axis=1, keepdims=True))
        # one sequence per band, correlated and summed (the same shifts for every band)
        acc = None
        for band in range(za.shape[1]):
            sh2, nc2 = _ncc_full(za[:, band], zb[:, band], max(5, int(round(min_overlap_s / arr_step))))
            acc = nc2 if acc is None else acc + nc2
        arr = (sh2 * arr_step, acc / za.shape[1])
    def arrangement_fit(shift_s):
        if arr is None:
            return 0.0
        j = int(np.argmin(np.abs(arr[0] - shift_s)))
        return float(arr[1][j])
    # candidates: the shifts that fit the rhythm nearly as well as the best one (repeating music), a second apart
    order = np.argsort(ncc)[::-1]
    peak = float(ncc[order[0]])
    cands = []
    for i in order[:600]:
        if ncc[i] < 0.9 * peak or len(cands) >= 6:
            break
        if all(abs(int(shifts[i]) - c["steps"]) * step >= 1.0 for c in cands):
            cands.append({"steps": int(shifts[i]), "rhythm": float(ncc[i])})
    la_s, lb_s = len(a) / sr, len(b) / sr
    fa = _bandpass(a, sr)
    fb = _bandpass(b, sr)

    def fine_fit(coarse_s):
        """The waveforms themselves around a coarse shift (+-25 ms, to a sample), over up to 40 s of the overlap:
        (best fine shift, how well the waveforms match there, 0-1)."""
        t0 = max(0.0, -coarse_s)                          # where in other the overlap starts
        span = min(40.0, lb_s - t0, la_s - (t0 + coarse_s))
        if span < 2.0:
            return coarse_s, None
        ia, ib, n = int(round((t0 + coarse_s) * sr)), int(round(t0 * sr)), int(span * sr)
        pa, pb = fa[max(0, ia):ia + n], fb[ib:ib + n]
        n = min(len(pa), len(pb))
        if n <= sr:
            return coarse_s, None
        pa, pb = _zscore(pa[:n]), _zscore(pb[:n])
        wiggle = int(round(0.025 * sr))
        size = 1 << (2 * n - 1).bit_length()
        cc = np.fft.irfft(np.fft.rfft(pa, size) * np.conj(np.fft.rfft(pb, size)), size) / float(n)
        lags = np.arange(-wiggle, wiggle + 1)
        vals = cc[lags % size]
        j = int(np.argmax(vals))
        return coarse_s + lags[j] / float(sr), float(vals[j])

    for c in cands:
        sh = c["steps"] * step
        c["shift"] = sh
        c["overlap"] = max(0.0, min(lb_s, la_s - sh) - max(0.0, -sh))
        c["arrangement"] = arrangement_fit(sh)
        c["fine"], c["fine_fit"] = fine_fit(sh)
        # what decides: the waveforms (sample-exact), then the rhythm and the arrangement; then the overlap (both files
        # hold the whole song, so the right shift keeps the most music inside the reference), then the smaller move
        c["score"] = (c["rhythm"] + 0.6 * c["arrangement"] + 1.5 * (c["fine_fit"] if c["fine_fit"] is not None else 0.0)
                      + 0.002 * c["overlap"] - 0.0005 * abs(sh))
    cands.sort(key=lambda c: -c["score"])
    win = cands[0]
    best = int(np.argmin(np.abs(shifts - win["steps"])))
    alts = [{"shift_seconds": _r(c["fine"], 4), "relative_fit": _r(c["rhythm"] / peak, 2), "waveform_fit": _r(c["fine_fit"], 2),
             "arrangement_fit": _r(c["arrangement"], 2), "overlap_seconds": _r(c["overlap"], 1)} for c in cands[1:4]]
    # how clearly the winner stands out: against the best other candidate, all three fits together
    if len(cands) > 1:
        gap = win["score"] - cands[1]["score"]
    else:
        far = np.abs(shifts - shifts[best]) * step >= 1.0
        gap = win["rhythm"] - (float(ncc[far].max()) if far.any() else 0.0)
    conf = max(0.0, min(1.0, gap * 2.0 + min(win["rhythm"], 0.5)))
    if win["fine_fit"] is not None and win["fine_fit"] < 0.15:
        conf *= 0.7
    coarse = win["shift"]                                 # seconds to add to other
    fine, fine_conf = (win["fine"], win["fine_fit"]) if win["fine_fit"] is not None and win["fine_fit"] > 0.15 else (coarse, win["fine_fit"])
    # does the music drift (one recording running faster than the other)? compare the shift found on the first and
    # last thirds of the overlap
    speed = None
    ov_start = max(0.0, -coarse)
    ov_end = min(len(b) / sr, len(a) / sr - coarse)
    ov = ov_end - ov_start
    if ov >= 45.0:
        part = ov / 3.0
        found = []
        for k in (0, 2):
            s0 = ov_start + k * part
            ia, ib = int(round((s0 + coarse) * sr)), int(round(s0 * sr))
            n = int(part * sr)
            e1, _ = _attack_curve(a[ia:ia + n], sr)
            e2, _ = _attack_curve(b[ib:ib + n], sr)
            if len(e1) > 20 and len(e2) > 20:
                sh, nc = _ncc_full(_zscore(e1), _zscore(e2), int(round(5.0 / step)))
                if nc.max() > 0:
                    found.append((s0 + part / 2.0, int(sh[int(np.argmax(nc))]) * step))
        if len(found) == 2:
            (t1, d1), (t2, d2) = found
            if abs(d2 - d1) >= 0.025 and t2 > t1:
                speed = 1.0 + (d2 - d1) / (t2 - t1)
    out = {"shift_seconds": _r(fine, 4), "coarse_shift_seconds": _r(coarse, 3), "confidence": _r(conf, 2),
           "overlap_seconds": _r(ov, 1), "fit": _r(win["rhythm"], 2), "arrangement_fit": _r(win["arrangement"], 2),
           "alternatives": alts}
    if fine_conf is not None:
        out["fine_fit"] = _r(fine_conf, 2)
    notes = []
    close = [al for al in alts if (al.get("relative_fit") or 0) >= 0.97 and (al.get("waveform_fit") or 0) >= 0.9 * (fine_conf or 1.0)]
    if close:
        notes.append("The music repeats, so %d other shift%s fit%s the rhythm about as well (listed, with how well the arrangement "
                     "fits there). The chosen one keeps the most music inside the reference; if it lands a bar or a section off, "
                     "ask for the next one." % (len(close), "s" if len(close) > 1 else "", "" if len(close) > 1 else "s"))
    if speed is not None:
        out["speed_ratio"] = _r(speed, 5)
        notes.append("The two recordings run at slightly different speeds (about %.2f%%): one of them was stretched, or they were "
                     "bounced at different sample rates. Lining up the start cannot fix that for the whole song; REAPER's item "
                     "playback rate (Item properties, 'Playback rate') set to %.5f would." % (abs(speed - 1.0) * 100.0, 1.0 / speed))
    if conf < 0.4:
        notes.append("Not a clear match: the two recordings may not hold the same music, or the overlap is short. Check by ear "
                     "before trusting the shift.")
    if notes:
        out["note"] = " ".join(notes)
    return out


# ---------------------------------------------------------------------------
# what a WAV file says about itself (for importing)
# ---------------------------------------------------------------------------

def wav_info(path):
    """Sample rate, channels, length in seconds and, when the file carries one, the broadcast-WAV time reference
    (the timeline position the exporting program gave it, in samples since midnight): {'sr', 'channels', 'seconds',
    'bits', 'time_reference_samples' or None}. Reads the header only; None for a file that is not a WAV."""
    import struct
    try:
        with open(path, "rb") as f:
            head = f.read(12)
            if len(head) < 12 or head[8:12] != b"WAVE" or head[0:4] not in (b"RIFF", b"RF64"):
                return None
            info = {"sr": None, "channels": None, "seconds": None, "bits": None, "time_reference_samples": None}
            data_size = None
            ds64 = None
            for _chunk in range(1000):     # (a real WAV has a handful of chunks; a crafted one could have millions)
                hdr = f.read(8)
                if len(hdr) < 8:
                    break
                cid, size = hdr[0:4], struct.unpack("<I", hdr[4:8])[0]
                pos = f.tell()
                if cid == b"ds64":
                    body = f.read(min(size, 28))
                    if len(body) >= 16:
                        ds64 = struct.unpack("<Q", body[8:16])[0]
                elif cid == b"fmt ":
                    body = f.read(min(size, 40))
                    if len(body) >= 16:
                        _tag, ch, sr, _br, _al, bits = struct.unpack("<HHIIHH", body[:16])
                        info.update({"sr": sr, "channels": ch, "bits": bits})
                elif cid == b"bext":
                    body = f.read(min(size, 602))
                    if len(body) >= 346:
                        lo, hi = struct.unpack("<II", body[338:346])
                        info["time_reference_samples"] = lo + (hi << 32)
                elif cid == b"data":
                    data_size = ds64 if (size == 0xFFFFFFFF and ds64) else size
                    break
                f.seek(pos + size + (size & 1))
            if data_size is not None and info["sr"] and info["channels"] and info["bits"]:
                frame = max(1, info["channels"] * max(1, info["bits"] // 8))
                info["seconds"] = round(data_size / float(frame) / float(info["sr"]), 3)
            return info
    except (OSError, struct.error):
        return None
