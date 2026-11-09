#!/usr/bin/env python3
"""Tests for rapreaper_audio. Run:  python3 tests/test_audio.py

Loudness and true peak are checked against the EBU Tech 3341 synthetic test
signals (the same signals loudness meters are certified with). The detectors
are checked against synthetic recordings with defects of known size.
"""
import os
import sys
import tempfile
import traceback

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "kit"))
sys.path.insert(0, os.path.join(HERE, "..", "sim"))

import rapreaper_audio as A          # noqa: E402
import make_test_audio as M       # noqa: E402

SR = 48000
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS  " if cond else "FAIL  ") + name + ("   " + detail if detail else ""))


def sine(db_peak, seconds, f=1000.0, sr=SR, phase=0.0):
    t = np.arange(int(seconds * sr)) / sr
    return 10 ** (db_peak / 20.0) * np.sin(2 * np.pi * f * t + phase)


def stereo(x):
    return np.stack([x, x], axis=1)


def test_k_weighting():
    sos = A._k_weight_sos(48000)
    want_shelf = [1.53512485958697, -2.69169618940638, 1.19839281085285, 1.0, -1.69065929318241, 0.73248077421585]
    want_hp = [1.0, -2.0, 1.0, 1.0, -1.99004745483398, 0.99007225036621]
    check("K-weighting shelf matches ITU-R BS.1770 coefficients at 48 kHz",
          np.allclose(sos[0], want_shelf, atol=2e-6), str(np.round(sos[0], 6)))
    check("K-weighting high-pass matches ITU-R BS.1770 coefficients at 48 kHz",
          np.allclose(sos[1], want_hp, atol=2e-6), str(np.round(sos[1], 6)))


def test_loudness_ebu3341():
    # case 1 and 2: stereo 1 kHz sine at -23 / -33 dBFS for 20 s
    for case, lvl in ((1, -23.0), (2, -33.0)):
        st = A.loudness_stats(stereo(sine(lvl, 20.0)), SR)
        check("EBU 3341 case %d: %.0f dBFS sine reads %.1f LUFS" % (case, lvl, lvl),
              abs(st["integrated_lufs"] - lvl) <= 0.1, "got %s" % st["integrated_lufs"])
    # case 3: -36 (10 s), -23 (60 s), -36 (10 s) -> -23.0
    x = np.concatenate([sine(-36, 10), sine(-23, 60), sine(-36, 10)])
    st = A.loudness_stats(stereo(x), SR)
    check("EBU 3341 case 3: relative gate ignores the quiet ends (-23.0 LUFS)",
          abs(st["integrated_lufs"] + 23.0) <= 0.1, "got %s" % st["integrated_lufs"])
    # case 4: -72, -36, -23, -36, -72
    x = np.concatenate([sine(-72, 10), sine(-36, 10), sine(-23, 60), sine(-36, 10), sine(-72, 10)])
    st = A.loudness_stats(stereo(x), SR)
    check("EBU 3341 case 4: absolute gate ignores near-silence (-23.0 LUFS)",
          abs(st["integrated_lufs"] + 23.0) <= 0.1, "got %s" % st["integrated_lufs"])
    # case 5: -26 (20 s), -20 (20.1 s), -26 (20 s) -> -23.0
    x = np.concatenate([sine(-26, 20), sine(-20, 20.1), sine(-26, 20)])
    st = A.loudness_stats(stereo(x), SR)
    check("EBU 3341 case 5: mixed levels average to -23.0 LUFS",
          abs(st["integrated_lufs"] + 23.0) <= 0.1, "got %s" % st["integrated_lufs"])
    # case 9-style: short-term loudness of a steady -23 dBFS sine is -23
    check("short-term max of a steady -23 dBFS sine is -23.0 LUFS",
          abs(A.loudness_stats(stereo(sine(-23, 20)), SR)["short_term_max_lufs"] + 23.0) <= 0.1)
    # LRA: EBU 3342 case 1: -20 for 20 s then -30 for 20 s -> LRA 10
    x = np.concatenate([sine(-20, 20), sine(-30, 20)])
    st = A.loudness_stats(stereo(x), SR)
    check("EBU 3342 case 1: loudness range of -20/-30 dBFS halves is 10 LU",
          abs(st["loudness_range_lu"] - 10.0) <= 1.0, "got %s" % st["loudness_range_lu"])
    # other sample rates give the same answer
    for sr in (44100, 96000):
        st = A.loudness_stats(stereo(sine(-23.0, 20.0, sr=sr)), sr)
        check("-23 dBFS sine reads -23.0 LUFS at %d Hz" % sr,
              abs(st["integrated_lufs"] + 23.0) <= 0.1, "got %s" % st["integrated_lufs"])


def test_true_peak():
    # EBU 3341 cases 15-18: 0.5 FS sines at fs/4 (two phases), fs/6, fs/8 -> -6.0 dBTP (+0.2/-0.4)
    for case, (f, ph) in zip((15, 16, 17, 18), ((SR / 4, 0.0), (SR / 4, np.pi / 4), (SR / 6, np.pi / 3), (SR / 8, np.pi / 8))):
        x = sine(-6.0206, 2.0, f=f, phase=ph)
        tp = A.true_peak_db(stereo(x), SR)
        check("EBU 3341 case %d: true peak of a half-scale sine is -6.0 dBTP" % case,
              -6.4 <= tp <= -5.8, "got %.2f" % tp)
    # case 19: 1.41 FS sine at fs/4, 45 degrees: samples sit at 1.0 but the true peak is +3.0
    x = 1.41 * np.sin(2 * np.pi * (SR / 4) * np.arange(SR * 2) / SR + np.pi / 4)
    tp = A.true_peak_db(stereo(x), SR)
    check("EBU 3341 case 19: inter-sample peak of +3.0 dBTP is found",
          2.6 <= tp <= 3.2, "got %.2f (sample peak %.2f)" % (tp, 20 * np.log10(np.max(np.abs(x)))))


def test_wav_roundtrip(tmp):
    rng = np.random.default_rng(0)
    x = (rng.standard_normal((SR, 2)) * 0.1).astype(np.float64)
    for bits, tol in ((16, 1 / 32768.0), (24, 1 / 8388608.0), (32, 1e-7)):
        p = os.path.join(tmp, "rt%d.wav" % bits)
        A.write_wav(p, x, SR, bits)
        y, sr, info = A.read_audio(p)
        ok = sr == SR and y.shape == x.shape and np.max(np.abs(y - x)) <= tol * 1.01
        check("WAV round trip, %d-bit" % bits, ok, "max error %.2e" % np.max(np.abs(y - x)))
    p = os.path.join(tmp, "rt24.wav")
    y, sr, info = A.read_audio(p, start=0.25, duration=0.5)
    check("reading part of a file returns exactly that part",
          y.shape[0] == SR // 2 and np.max(np.abs(y - x[SR // 4: SR // 4 + SR // 2])) < 1e-6)
    try:
        A.read_audio(os.path.join(tmp, "missing.wav"))
        check("missing file raises a clear error", False)
    except A.AudioError as e:
        check("missing file raises a clear error", "not found" in str(e))
    bad = os.path.join(tmp, "bad.wav")
    with open(bad, "wb") as f:
        f.write(b"this is not audio")
    try:
        A.read_audio(bad)
        check("non-WAV content raises a clear error", False)
    except A.AudioError:
        check("non-WAV content raises a clear error", True)


def ids(rep):
    return {f["id"]: f["severity"] for f in rep["findings"]}


def test_detectors():
    clean = M.synth_vocal(seconds=40.0)
    rep = A.analyze(clean, SR, "vocal")
    i = ids(rep)
    check("clean synthetic vocal: no hum, rumble, clipping or noise reported",
          not any(k in i for k in ("hum", "rumble", "clipping", "noise")), str(i))
    check("clean synthetic vocal: hum detector says not found", rep["hum"]["checked"] and not rep["hum"]["found"])
    act = rep["activity"]["active_level_dbfs"]

    # hum at a known level
    for mains in (50, 60):
        x = M.add_defects(clean, hum_db=-50.0, hum_hz=mains)
        r = A.analyze(x, SR, "vocal")
        ok = r["hum"]["found"] and r["hum"]["mains_hz"] == mains and abs(r["hum"]["level_dbfs"] + 50.0) <= 1.5
        check("hum at %d Hz, -50 dBFS: found with the right frequency and level" % mains, ok,
              "reported %s Hz at %s dBFS, harmonics %s" % (r["hum"]["mains_hz"], r["hum"]["level_dbfs"], r["hum"]["harmonics_hz"]))
        check("hum at %d Hz shows up as a finding and a notch hint" % mains,
              "hum" in ids(r) and r["treatment_hints"].get("hum_notches_hz", [None])[0] == mains)

    # rumble
    x = M.add_defects(clean, rumble_db=act - 18.0)
    r = A.analyze(x, SR, "vocal")
    check("rumble 18 dB under the voice is flagged severe",
          ids(r).get("rumble") == "severe", "relative %s dB" % r["rumble"]["relative_to_voice_db"])
    check("rumble level is measured within 2 dB",
          abs(r["rumble"]["relative_to_voice_db"] + 18.0) <= 2.0, "got %s" % r["rumble"]["relative_to_voice_db"])

    # noise floor
    x = M.add_defects(clean, hiss_db=-50.0)
    r = A.analyze(x, SR, "vocal")
    ok = r["noise"]["measured"] and abs(r["noise"]["floor_dbfs"] + 50.0) <= 1.5
    check("hiss at -50 dBFS: noise floor measured within 1.5 dB", ok, "got %s" % r["noise"].get("floor_dbfs"))
    check("hiss at -50 dBFS is flagged and an expander is suggested",
          "noise" in ids(r) and r["treatment_hints"]["expander"] is True,
          "SNR %s dB, %s" % (r["noise"].get("signal_to_noise_db"), r["noise"].get("character")))
    x = M.add_defects(clean, hiss_db=-78.0)
    r = A.analyze(x, SR, "vocal")
    check("hiss at -78 dBFS is not flagged", "noise" not in ids(r), "SNR %s dB" % r["noise"].get("signal_to_noise_db"))

    # clipping
    x = np.clip(clean * 4.0, -0.98, 0.98)
    r = A.analyze(x, SR, "vocal")
    check("hard clipping is flagged severe", ids(r).get("clipping") == "severe",
          "%d clipped runs" % r["clipping"]["clipped_runs"])
    check("unclipped audio reports no clipping", rep["clipping"]["clipped_runs"] < 3)

    # DC offset
    x = M.add_defects(clean, dc=0.01)
    r = A.analyze(x, SR, "vocal")
    check("DC offset of 0.01 is measured", abs(r["levels"]["dc_offset"] - 0.01) < 0.002 and "dc_offset" in ids(r))

    # muddy
    x = M.add_defects(clean, mud_db=9.0)
    r = A.analyze(x, SR, "vocal")
    d = r["tone_vs_natural_speech_db"]["mud"] - rep["tone_vs_natural_speech_db"]["mud"]
    check("a +9 dB low-mid boost raises the mud reading", d > 3.0, "mud reading moved %+.1f dB" % d)

    # sibilance scales with the S level
    quiet = A.analyze(M.synth_vocal(seconds=40.0, sib_gain_db=-12.0), SR, "vocal")
    loud = A.analyze(M.synth_vocal(seconds=40.0, sib_gain_db=+16.0), SR, "vocal")
    e0, e1 = quiet["sibilance"].get("excess_db"), loud["sibilance"].get("excess_db")
    check("louder S sounds give a higher sibilance reading",
          loud["sibilance"]["found"] and (e0 is None or e1 - e0 > 12.0), "%s -> %s dB" % (e0, e1))
    check("strong S sounds are flagged with a de-esser frequency between 5 and 9 kHz",
          "sibilance" in ids(loud) and 5000 <= loud["treatment_hints"]["deess"]["centre_hz"] <= 9000,
          "centre %s Hz" % loud["sibilance"].get("centre_hz"))

    # dynamics
    wide = A.analyze(M.synth_vocal(seconds=40.0, level_spread_db=18.0), SR, "vocal")
    narrow = A.analyze(M.synth_vocal(seconds=40.0, level_spread_db=2.0), SR, "vocal")
    check("uneven performance reads a wider level range than an even one",
          wide["dynamics"]["level_range_db"] - narrow["dynamics"]["level_range_db"] > 6.0,
          "%s vs %s dB" % (wide["dynamics"]["level_range_db"], narrow["dynamics"]["level_range_db"]))
    check("uneven performance asks for more compression",
          wide["treatment_hints"]["compression"]["suggested_amount"] in ("medium", "heavy")
          and narrow["treatment_hints"]["compression"]["suggested_amount"] == "light")

    # pitch-based high-pass choice
    low_voice = A.analyze(M.synth_vocal(seconds=30.0, f0=95.0), SR, "vocal")
    high_voice = A.analyze(M.synth_vocal(seconds=30.0, f0=210.0), SR, "vocal")
    check("high-pass suggestion follows the voice pitch (lower voice, lower filter)",
          low_voice["treatment_hints"]["highpass_hz"] < high_voice["treatment_hints"]["highpass_hz"]
          and 70 <= low_voice["treatment_hints"]["highpass_hz"] <= 85,
          "%s Hz for a ~95 Hz voice (pitch floor %s), %s Hz for a ~210 Hz voice (pitch floor %s)" % (
              low_voice["treatment_hints"]["highpass_hz"], low_voice["rumble"]["lowest_voice_pitch_hz"],
              high_voice["treatment_hints"]["highpass_hz"], high_voice["rumble"]["lowest_voice_pitch_hz"]))

    # plosives
    pl = A.analyze(M.synth_vocal(seconds=40.0, with_plosives=True), SR, "vocal")
    check("P/B thumps are counted and flagged", "plosives" in ids(pl) and "plosives" not in i,
          "%s thumps, %s per minute (clean take: %s)" % (pl["plosives"]["count"], pl["plosives"]["per_minute"], rep["plosives"]["count"]))
    check("thumps: a steeper filter UNDER the voice's lowest note is suggested, not a higher one",
          pl["treatment_hints"]["highpass_steep_hz"] <= pl["treatment_hints"]["highpass_hz"]
          and pl["treatment_hints"]["highpass_hz"] == rep["treatment_hints"]["highpass_hz"],
          "one filter at %s Hz, or two at %s Hz" % (pl["treatment_hints"]["highpass_hz"], pl["treatment_hints"]["highpass_steep_hz"]))

    # a smooth low-frequency crest must not be mistaken for clipping
    t = np.arange(SR * 6) / SR
    sub = 0.9 * np.sin(2 * np.pi * 40.0 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 0.2 * t))
    check("a clean 40 Hz bass note is not reported as clipping", A.analyze(sub, SR, "beat")["clipping"]["clipped_runs"] == 0)

    # silence
    r = A.analyze(np.zeros(SR * 3), SR, "vocal")
    check("silent audio is reported as silent, not analysed", r.get("silent") is True and r["findings"][0]["id"] == "silent")


def test_measurement_helpers():
    v = M.synth_vocal(seconds=30.0, level_spread_db=14.0)
    # a simple known gain change above a threshold: turn everything above -20 dBFS (block level) down 6 dB
    blk = int(0.05 * SR)
    y = v.copy()
    nb = len(v) // blk
    lv = 10 * np.log10(np.maximum((v[: nb * blk].reshape(nb, blk) ** 2).mean(axis=1), 1e-20))
    thr = np.percentile(lv[lv > -60], 70)
    for i in np.where(lv > thr)[0]:
        y[i * blk:(i + 1) * blk] *= 10 ** (-6.0 / 20.0)
    g = A.gain_reduction(v, y, SR)
    check("gain reduction of a known 6 dB cut on loud parts is measured",
          g["measured"] and abs(g["on_loudest_parts_db"] - 6.0) <= 0.7, str(g))
    g0 = A.gain_reduction(v, v, SR)
    check("identical captures read 0 dB gain reduction", abs(g0["on_loudest_parts_db"]) < 0.05 and abs(g0["average_db"]) < 0.05)

    # de-esser: cut the 5-9 kHz band by 6 dB only where it is strong
    from scipy import signal
    loud = M.synth_vocal(seconds=30.0, sib_gain_db=14.0)
    sos = signal.butter(4, [4500, 10000], btype="band", fs=SR, output="sos")
    hi = signal.sosfiltfilt(sos, loud)
    env = np.sqrt(signal.sosfiltfilt(signal.butter(2, 30, fs=SR, output="sos"), hi * hi).clip(min=0))
    gain = np.where(env > np.percentile(env, 90), 10 ** (-6.0 / 20.0), 1.0)
    de = (loud - hi) + hi * gain
    s = A.sibilance_reduction(loud, de, SR)
    check("de-esser effect: strong S sounds measured about 6 dB lower, the rest untouched",
          s["measured"] and 3.5 <= s["on_strong_s_sounds_db"] <= 7.0 and abs(s["on_everything_else_db"]) < 0.6, str(s))

    # masking: vocal against a beat, then against the same beat 6 dB quieter
    b = M.synth_beat(seconds=30.0)
    m1 = A.masking(v, b, SR)
    m2 = A.masking(v, b * 0.5, SR)
    check("masking report: turning the beat down 6 dB moves the vocal-vs-beat figure by 6 LU",
          m1["measured"] and abs((m2["vocal_minus_beat_lu"] - m1["vocal_minus_beat_lu"]) - 6.0) <= 0.2,
          "%s -> %s LU" % (m1["vocal_minus_beat_lu"], m2["vocal_minus_beat_lu"]))
    check("masking report lists third-octave bands from 125 Hz to 8 kHz", len(m1["bands"]) == 19)

    # dense window
    x = np.concatenate([np.zeros(SR * 10), v[: SR * 12], np.zeros(SR * 10)])
    w = A.find_dense_window(x, SR, 10.0)
    check("dense-window finder lands on the part with audio", 9.0 <= w <= 13.0, "start %.1f s" % w)


def test_roles(tmp):
    b = M.synth_beat(seconds=30.0)
    r = A.analyze(b, SR, "beat")
    check("a loud mastered beat is flagged as leaving no headroom",
          "beat_no_headroom" in ids(r), "true peak %s dBTP, %s LUFS" % (r["levels"]["true_peak_dbtp"], r["loudness"]["integrated_lufs"]))
    check("beat report includes stereo measurements", r["stereo"] is not None and "correlation" in r["stereo"])
    crushed = np.clip(b * 8.0, -0.999, 0.999)
    r2 = A.analyze(crushed, SR, "mix")
    check("an over-limited mix is flagged", "over_limited" in ids(r2) or "clipping" in ids(r2), str(ids(r2)))
    inv = np.stack([b[:, 0], -b[:, 0]], axis=1)
    r3 = A.analyze(inv, SR, "mix")
    check("an out-of-phase mix is flagged", "phase" in ids(r3), "correlation %s" % r3["stereo"]["correlation"])
    # compare()
    v = M.synth_vocal(seconds=30.0)
    dirty = M.add_defects(v, hum_db=-50.0, rumble_db=-36.0, dc=0.01)
    c = A.compare(A.analyze(dirty, SR, "vocal"), A.analyze(v, SR, "vocal"))
    check("compare() lists hum, rumble and DC offset as fixed",
          all(k in c["problems_fixed"] for k in ("hum", "rumble", "dc_offset")), str(c["problems_fixed"]))
    # file-based analysis
    p = os.path.join(tmp, "v.wav")
    A.write_wav(p, v, SR, 24)
    rf = A.analyze(p, role="vocal")
    check("analysing from a file gives the same loudness as from memory",
          abs(rf["loudness"]["integrated_lufs"] - A.analyze(v, SR, "vocal")["loudness"]["integrated_lufs"]) <= 0.1)
    import json
    json.dumps(rf)
    check("reports are plain JSON (no numpy types)", True)


def test_review_fixes():
    """Cases an independent review found wrong; each one is pinned here."""
    v = M.synth_vocal(seconds=40.0)
    noisy = M.add_defects(v, hiss_db=-45.0, hum_db=-50.0, hum_hz=60)
    whole = A.analyze(noisy[: SR * 30], SR, "vocal")
    # the same take as two clips with six seconds of empty timeline between them
    split = np.concatenate([noisy[: SR * 15], np.zeros(SR * 6), noisy[SR * 15: SR * 30]])
    two = A.analyze(split, SR, "vocal")
    a, b = whole["noise"].get("floor_dbfs"), two["noise"].get("floor_dbfs")
    check("empty timeline between two clips is not mistaken for the recording's noise floor",
          two["noise"]["measured"] and a is not None and b is not None and abs(a - b) <= 1.5 and "noise" in ids(two),
          "one clip: %s dBFS, two clips with a gap: %s dBFS" % (a, b))
    check("hum is still found when the clips have empty timeline between them",
          two["hum"].get("found") and two["hum"].get("mains_hz") == 60, str(two["hum"]))
    tail = np.concatenate([noisy[: SR * 20], np.zeros(SR * 10)])
    t = A.analyze(tail, SR, "vocal")
    check("measuring past the end of a clip does not change the noise reading",
          t["noise"]["measured"] and abs(t["noise"]["floor_dbfs"] - a) <= 1.5, "%s dBFS" % t["noise"].get("floor_dbfs"))
    r = A.analyze(np.concatenate([noisy[: SR * 12], np.zeros(SR * 12)]), SR, "vocal")
    check("rumble and empty timeline: the report says how much of the range was empty",
          40.0 <= r["activity"]["empty_percent"] <= 55.0, "%s %%" % r["activity"]["empty_percent"])

    # a mono microphone recorded onto a stereo pair: sound on one side only
    left_only = np.stack([v, np.zeros_like(v)], axis=1)
    one = A.analyze(left_only, SR, "vocal")
    ref = A.analyze(v, SR, "vocal")
    check("a voice on the left channel only is reported, with the fix",
          ids(one).get("one_sided") and "Mono (left)" in [f for f in one["findings"] if f["id"] == "one_sided"][0]["fix"], str(ids(one)))
    check("... and it is measured at its real level, not 6 dB low",
          abs(one["activity"]["active_level_dbfs"] - ref["activity"]["active_level_dbfs"]) <= 0.5,
          "%s vs %s dBFS" % (one["activity"]["active_level_dbfs"], ref["activity"]["active_level_dbfs"]))
    right_only = np.stack([np.zeros_like(v), v], axis=1)
    check("a voice on the right channel only is reported as right",
          "Mono (right)" in "".join(f["fix"] for f in A.analyze(right_only, SR, "vocal")["findings"]))
    # left and right opposite copies of each other
    flipped = np.stack([v, -v], axis=1)
    fl = A.analyze(flipped, SR, "vocal")
    check("a vocal whose two channels cancel each other is reported and still measured",
          "out_of_phase" in ids(fl) and abs(fl["activity"]["active_level_dbfs"] - ref["activity"]["active_level_dbfs"]) <= 0.5,
          str(ids(fl)))
    beat = M.synth_beat(seconds=40.0)
    m = A.masking(flipped, beat, SR)
    check("vocal-against-beat still works on such a track", m["measured"] is True, str(m.get("reason")))
    check("an ordinary centred stereo vocal gets neither finding",
          not any(k in ids(A.analyze(stereo(v), SR, "vocal")) for k in ("one_sided", "out_of_phase")))

    # loudness in the vocal-against-beat report follows BS.1770 (left and right summed)
    rng = np.random.default_rng(5)
    wide = rng.standard_normal((SR * 12, 2)) * 10 ** (-26.0 / 20.0)        # nothing in common between the sides
    tone = stereo(sine(-20.0, 12.0, f=400.0))
    mk = A.masking(tone, wide, SR)
    want_b = A.loudness_stats(wide, SR)["integrated_lufs"]
    want_v = A.loudness_stats(tone, SR)["integrated_lufs"]
    check("vocal-against-beat loudness matches BS.1770 for a wide stereo beat",
          abs(mk["beat_loudness_lufs"] - want_b) <= 0.5 and abs(mk["vocal_loudness_lufs"] - want_v) <= 0.5,
          "beat %s (meter %s), vocal %s (meter %s)" % (mk["beat_loudness_lufs"], want_b, mk["vocal_loudness_lufs"], want_v))

    # one pop is not "twenty a minute"
    short = M.synth_vocal(seconds=6.0, seed=4)
    tt = np.arange(int(0.12 * SR)) / SR
    short[SR * 2: SR * 2 + len(tt)] += 0.5 * np.sin(2 * np.pi * 40.0 * tt) * np.exp(-tt / 0.02)
    sp = A.analyze(short, SR, "vocal")
    check("a single pop in a short clip is not reported as a plosive problem",
          "plosives" not in ids(sp), "%s pops counted" % sp["plosives"]["count"])


def test_second_review(tmp):
    """Cases the second independent review found wrong or untested; each one is pinned here."""
    from scipy import signal
    rng = np.random.default_rng(11)

    # ---- a take that is mostly gaps: half a second of voice every twelve seconds, over room tone
    v = M.synth_vocal(seconds=20.0, seed=5, level_spread_db=0.0)
    blk = int(0.5 * SR)
    energy = [float(np.mean(v[i:i + blk] ** 2)) for i in range(0, len(v) - blk, blk // 5)]
    i0 = int(np.argmax(energy)) * (blk // 5)
    shout = v[i0:i0 + blk] * np.hanning(blk) ** 0.25
    n = SR * 60
    sparse = rng.standard_normal(n) * 10 ** (-62.0 / 20.0)
    marks = np.zeros(n, bool)
    for t0 in (5.0, 17.0, 29.0, 41.0, 53.0):
        a = int(t0 * SR)
        sparse[a:a + blk] += shout
        marks[a:a + blk] = True
    true_level = 10.0 * np.log10(float(np.mean(sparse[marks] ** 2)))
    r = A.analyze(sparse, SR, "vocal")
    check("a take that is mostly gaps: the voice level is the level of the shouts, not of the room",
          abs(r["activity"]["active_level_dbfs"] - true_level) <= 4.0,
          "reported %s dBFS, the shouts are at %.1f dBFS" % (r["activity"]["active_level_dbfs"], true_level))
    check("... and the room tone between the shouts is measured as its noise",
          r["noise"]["measured"] and abs(r["noise"]["floor_dbfs"] + 62.0) <= 3.0, str(r["noise"]))
    squeezed = sparse.copy()
    squeezed[marks] *= 10 ** (-6.0 / 20.0)                 # a compressor that takes 6 dB off the shouts and nothing else
    g = A.gain_reduction(sparse, squeezed, SR)
    check("... a compressor's 6 dB on the shouts is measured as about 6 dB",
          g["measured"] and abs(g["on_loudest_parts_db"] - 6.0) <= 1.0, str(g))
    w = A.find_dense_window(sparse, SR, 20.0)
    check("... and the test section that is picked holds two of the shouts",
          int(sum(1 for t0 in (5.0, 17.0, 29.0, 41.0, 53.0) if w <= t0 and t0 + 0.5 <= w + 20.0)) >= 2, "window starts at %s s" % w)

    # ---- clipping in a take that was not recorded at the rate of the captures
    v441 = M.synth_vocal(seconds=20.0, sr=44100)
    hot = np.clip(v441 * 6.0, -0.98, 0.98)
    path = os.path.join(tmp, "clipped_441.wav")
    A.write_wav(path, hot, 44100, 24)
    in_file = A.clipping_of_file(path)
    as_rendered = signal.resample_poly(hot, 160, 147)       # what a 48 kHz render of that take holds
    seen = A.analyze(as_rendered, SR, "vocal")["clipping"]["clipped_runs"]
    check("a clipped 44.1 kHz take: the check of the file finds the flat tops that a 48 kHz capture cannot show",
          in_file["clipped_runs"] >= 20 and seen < 3 and in_file["sample_rate"] == 44100,
          "file: %s flat tops; the 48 kHz capture shows %s" % (in_file["clipped_runs"], seen))
    check("a mono take on two channels is clipped once, not twice",
          A._clipping(np.stack([hot, hot], axis=1))["clipped_runs"] == A._clipping(hot)["clipped_runs"])

    # ---- a DC offset is not noise
    quiet = M.add_defects(M.synth_vocal(seconds=30.0), hiss_db=-60.0)
    a0, a1 = A.analyze(quiet, SR, "vocal"), A.analyze(quiet + 0.01, SR, "vocal")
    check("a DC offset is reported as such, and not counted as noise or rumble",
          abs(a0["noise"]["floor_dbfs"] - a1["noise"]["floor_dbfs"]) <= 1.0 and "rumble" not in ids(a1) and "dc_offset" in ids(a1),
          "noise floor %s dBFS without, %s dBFS with the offset; findings %s" % (a0["noise"]["floor_dbfs"], a1["noise"]["floor_dbfs"], sorted(ids(a1))))

    # ---- unusual sample rates
    for rate in (8000, 11025, 16000):
        x = signal.resample_poly(M.synth_vocal(seconds=10.0), rate, SR)
        try:
            ok = A.analyze(x, rate, "vocal")["loudness"]["integrated_lufs"] is not None
            why = ""
        except Exception as e:                               # noqa: BLE001
            ok, why = False, repr(e)
        check("audio at %d Hz can be analysed" % rate, ok, why)
    up = signal.resample_poly(M.synth_vocal(seconds=12.0), 4, 1)
    r192 = A.analyze(up, 192000, "vocal")
    check("a clean voice at 192 kHz gets no false rumble or thump findings",
          not any(k in ids(r192) for k in ("rumble", "plosives")), str(sorted(ids(r192))))

    # ---- hum: on the mains frequency, and worth treating
    base = M.add_defects(M.synth_vocal(seconds=30.0), hiss_db=-66.0)
    t = np.arange(len(base)) / SR
    amp = 10 ** (-45.0 / 20.0) * np.sqrt(2.0)
    bleed = base + amp * (np.sin(2 * np.pi * 58.27 * t) + 0.5 * np.sin(2 * np.pi * 116.54 * t))
    check("a bass note leaking from headphones (58.3 Hz and its octave) is not taken for mains hum",
          not A.analyze(bleed, SR, "vocal")["hum"]["found"])
    one_line = A.analyze(base + amp * np.sin(2 * np.pi * 50.0 * t), SR, "vocal")["hum"]
    check("a single clear line exactly on 50 Hz is found as hum", bool(one_line["found"]) and one_line["mains_hz"] == 50, str(one_line))
    faint = A.analyze(M.add_defects(M.synth_vocal(seconds=30.0), hiss_db=-84.0, hum_db=-80.0, hum_hz=60), SR, "vocal")
    check("hum more than 50 dB under the voice is mentioned and left alone (no notches)",
          faint["hum"]["found"] and "hum_faint" in ids(faint) and "hum" not in ids(faint)
          and not faint["treatment_hints"].get("hum_notches_hz"),
          "%s; findings %s" % (faint["hum"], sorted(ids(faint))))
    clear = A.analyze(M.add_defects(M.synth_vocal(seconds=30.0), hiss_db=-66.0, hum_db=-50.0, hum_hz=60), SR, "vocal")
    hints = clear["treatment_hints"]
    check("audible hum gets notches that are as deep as needed (6 to 18 dB), no deeper",
          "hum" in ids(clear) and (hints.get("hum_notches_hz") or [None])[0] == 60 and -18.0 <= hints.get("hum_notch_db", 0) <= -6.0,
          "%s dB relative to the voice -> notch %s dB" % (clear["hum"]["relative_to_voice_db"], hints.get("hum_notch_db")))

    # ---- a deep voice: its lowest notes are not rumble, and no filter lands on them
    deep = A.analyze(M.synth_vocal(seconds=30.0, f0=88.0), SR, "vocal")
    f0 = deep["rumble"]["lowest_voice_pitch_hz"]
    check("a deep voice: no rumble finding, one filter at most 15 % under the lowest note, two at most 25 % under",
          f0 is not None and "rumble" not in ids(deep)
          and deep["treatment_hints"]["highpass_hz"] <= 0.86 * f0 and deep["treatment_hints"]["highpass_steep_hz"] <= 0.76 * f0,
          "lowest note %s Hz: %s Hz / %s Hz; findings %s" % (f0, deep["treatment_hints"]["highpass_hz"],
                                                               deep["treatment_hints"]["highpass_steep_hz"], sorted(ids(deep))))

    # ---- real speech (public-domain recording; see tests/data/README.txt)
    real, rsr, _ = A.read_audio(os.path.join(HERE, "data", "real_speech.wav"))
    real48 = signal.resample_poly(real[:, 0].astype(np.float64), 320, 147)
    rr = A.analyze(real48, SR, "vocal")
    bad = sorted(k for k in ids(rr) if k.startswith(("hum", "rumble", "plosives", "clipping", "resonance", "one_sided", "muddy", "boomy")))
    check("real speech: no false hum, rumble, thump, clipping, mud or resonance findings", not bad, str(bad))
    ex = rr["sibilance"].get("excess_db")
    check("real speech reads between 0 and +6 on the 'S sounds vs voice' scale",
          rr["sibilance"]["found"] and 0.0 <= ex <= 6.0, "%s dB" % ex)
    check("real speech: the suggested filters stay under the voice's lowest note",
          rr["treatment_hints"]["highpass_hz"] <= 0.86 * rr["rumble"]["lowest_voice_pitch_hz"],
          "lowest note %s Hz, filter %s Hz" % (rr["rumble"]["lowest_voice_pitch_hz"], rr["treatment_hints"]["highpass_hz"]))

    # ---- what an expander did, seen from outside
    noisy = M.add_defects(M.synth_vocal(seconds=30.0, level_spread_db=14.0), hiss_db=-55.0)
    b50 = int(0.05 * SR)
    nb = len(noisy) // b50
    lv = 10.0 * np.log10(np.maximum((noisy[:nb * b50].reshape(nb, b50) ** 2).mean(axis=1), 1e-20))
    out = noisy[:nb * b50] * np.repeat(np.where(lv < -45.0, 10 ** (-10.0 / 20.0), 1.0), b50)
    e = A.expander_effect(noisy[:nb * b50], out, SR)
    check("expander_effect: the gaps came down about 10 dB and the voice as a whole was left alone",
          e["measured"] and e["gaps_db"] is not None and e["gaps_db"] <= -8.0 and e["voice_db"] > -2.0, str(e))


def test_breaths_and_s():
    """find_breaths / find_s_sounds / level_change, against voice2's ground-truth map of every event."""
    import voice2 as V
    hits = total = false = 0
    for seed in (1, 2, 3, 4):
        for bdb, noise in ((-36.0, -70.0), (-28.0, -60.0), (-22.0, -55.0)):
            x, ev = V.voice2(24.0, seed=seed, breath_db=bdb)
            x = x + V.room_noise(len(x), level_db=noise, seed=seed + 11)
            got = [(b["start"], b["end"]) for b in A.find_breaths(x, SR)["breaths"]]
            truth = [(a / SR, b / SR) for k, a, b in ev if k == "breath"]
            total += len(truth)
            hits += sum(any(abs(gs - ts) <= 0.04 and abs(ge - te) <= 0.04 for gs, ge in got) for ts, te in truth)
            false += len([g for g in got if not any(g[0] < te and g[1] > ts for ts, te in truth)])
    check("find_breaths: at least 95% of the test voice's breaths found, start and end within 40 ms", hits >= 0.95 * total,
          "%d of %d" % (hits, total))
    check("find_breaths: nothing else called a breath (S, F, word endings, room)", false == 0, "%d false" % false)
    fake = 0
    for seed in (1, 3, 5, 7):
        v = M.add_defects(M.synth_vocal(seconds=40.0, seed=seed, sib_gain_db=12.0, with_plosives=True, level_spread_db=14.0),
                          hum_db=-50, rumble_db=-36, hiss_db=-62, dc=0.004, mud_db=7.0)
        fake += len(A.find_breaths(v, SR)["breaths"])
    check("find_breaths: a voice with no breaths (but S sounds, thumps, hum, hiss, rumble) gives at most a couple of candidates in 160 s",
          fake <= 2, "%d found" % fake)
    off = A.find_breaths(np.zeros(SR * 3), SR)
    check("find_breaths: silence gives an empty list", off["breaths"] == [], str(off))
    x, ev = V.voice2(24.0, seed=5)
    got = A.find_s_sounds(x, SR, offset=10.0)
    sibs = [(a / SR + 10.0, b / SR + 10.0) for k, a, b in ev if k == "sib"]
    covered = sum(any(g["start_seconds"] <= ts + 0.012 and g["end_seconds"] >= te - 0.012 for g in got) for ts, te in sibs)
    wrong = [g for g in got if not any(g["start_seconds"] < te and g["end_seconds"] > ts for ts, te in sibs)]
    check("find_s_sounds: 90% of the S sounds located (within 12 ms of both ends), in project time", covered >= 0.9 * len(sibs),
          "%d of %d" % (covered, len(sibs)))
    check("find_s_sounds: nothing that is not an S", not wrong, str(wrong[:3]))
    y = x.copy()
    a, b = int(5.0 * SR), int(5.5 * SR)
    y[a:b] *= 10 ** (-7.0 / 20.0)
    lc = A.level_change(x, y, SR, [(5.0, 5.5)])
    check("level_change: -7 dB inside the range, 0 outside", abs(lc["inside_db"][0] + 7.0) < 0.01 and abs(lc["outside_db"]) < 0.01, str(lc))


def test_song_effects():
    """rapreaper_effects against effects of known size (sim/effects_sim.py), and rapreaper_separate's arithmetic."""
    import rapreaper_effects as E
    import effects_sim as FX
    import voice2 as V
    got = {}
    for mode in ("natural", "light", "tight", "hard"):
        got[mode] = [E.autotune(FX.melodic_voice(20.0, seed=s, key="F#", minor=True, mode=mode)[0], SR)["amount"] for s in (1, 2)]
    check("auto-tune: an uncorrected singer reads 'none', a 120 ms correction 'natural', 40 ms 'tight', 2 ms 'hard' (2 seeds each)",
          got["natural"] == ["none"] * 2 and got["light"] == ["natural"] * 2 and got["tight"] == ["tight"] * 2 and got["hard"] == ["hard"] * 2, str(got))
    w, _ = V.voice2(20.0, seed=1)
    s_, sr0, _ = A.read_audio(os.path.join(HERE, "data", "real_speech.wav"), max_seconds=None)
    check("auto-tune: speech and the spoken test voice read 'none'", E.autotune(w, SR)["amount"] == "none" and E.autotune(s_, sr0)["amount"] == "none")
    keys = [E.key_of(FX.melodic_voice(24.0, seed=s, key=k, minor=mi, mode="tight")[0], SR) for s, k, mi in ((3, "F#", True), (4, "C", False), (5, "A", True), (6, "Eb" if False else "D#", False))]
    names = [k.get("key") for k in keys]
    check("key: F# minor, C major, A minor, D# major read right (or as the relative key, which shares the notes)",
          all(n == want or (want.split()[0] in (k.get("or") or "")) or want in (k.get("runners_up") or [])
              for n, k, want in zip(names, keys, ("F# minor", "C major", "A minor", "D# major"))), str(names))
    v, _ = FX.melodic_voice(20.0, seed=3, mode="hard")
    pt = E.pitch_track(v, SR)
    errs = []
    for a, b, midi, kind in _:
        if kind != "note":
            continue
        sel = (pt["times"] > a / SR + 0.03) & (pt["times"] < b / SR - 0.02) & np.isfinite(pt["f0"])
        if sel.any():
            errs += list(1200 * np.log2(pt["f0"][sel] / FX.note_hz(midi)))
    errs = np.abs(np.array(errs))
    check("pitch tracker: within 5 cents on 90% of held notes, no octave errors", np.percentile(errs, 90) <= 5.0 and (errs > 600).mean() == 0,
          str((float(np.median(errs)), float(np.percentile(errs, 90)))))
    w, _ = V.voice2(24.0, seed=3)
    rv = [E.reverb(FX.reverb(w, SR, rt60=rt, wet_db=wet, dark=dk), SR) for rt, wet, dk in ((1.2, -12, True), (2.4, -10, False))]
    check("reverb: a 1.2 s dark tail reads medium and dark; a 2.4 s tail reads long",
          rv[0]["amount"] == "medium" and rv[0]["dark"] and 0.9 <= rv[0]["decay_rt60_s"] <= 1.8 and rv[1]["amount"] == "long", str([(r["amount"], r.get("decay_rt60_s")) for r in rv]))
    mel, _ = FX.melodic_voice(20.0, seed=4, mode="tight")
    check("reverb: a dry voice and dry speech read none", E.reverb(mel, SR)["amount"] == "none" and E.reverb(s_, sr0)["amount"] == "none")
    ec = [E.echo(FX.delay(w, SR, ms=ms, feedback=fb, wet_db=wet), SR, bpm=140) for ms, fb, wet in ((428.6, 0.35, -10), (321.4, 0.5, -8), (428.6, 0.35, -20))]
    check("delay: 429 ms (quarter note at 140), 321 ms (dotted eighth) and a quiet 429 ms echo are all found at their time, "
          "feedback within 0.15 of the truth", all(e["found"] for e in ec) and abs(ec[0]["time_ms"] - 429) <= 25 and ec[0]["note_value"] == "quarter note"
          and abs(ec[1]["time_ms"] - 321) <= 25 and ec[1]["note_value"] == "dotted eighth" and abs(ec[1]["feedback"] - 0.5) <= 0.15
          and abs(ec[2]["time_ms"] - 429) <= 25, str([(e.get("time_ms"), e.get("note_value"), e.get("feedback"), e.get("level_db")) for e in ec]))
    check("delay: the echo's level is read within 3 dB (-10 -> %s, -20 -> %s)" % (ec[0]["level_db"], ec[2]["level_db"]),
          abs(ec[0]["level_db"] + 10) <= 3 and abs(ec[2]["level_db"] + 20) <= 3)
    both = E.echo(FX.reverb(FX.delay(w, SR, ms=428.6, feedback=0.35, wet_db=-10), SR, 1.2, -14), SR, bpm=140)
    check("delay: still found under a reverb", both["found"] and abs(both["time_ms"] - 429) <= 25, str(both))
    check("delay: nothing found on a dry voice, dry speech or a reverb alone",
          not E.echo(w, SR)["found"] and not E.echo(s_, sr0)["found"] and not E.echo(FX.reverb(w, SR, 1.2, -12), SR)["found"])
    dy = [E.dynamics(x, SR)["amount"] for x in (w, FX.compress(w, SR, -24, 2.0), FX.compress(w, SR, -28, 4.0))]
    check("compression: light -> medium -> heavy as the ratio rises", dy == ["light", "medium", "heavy"], str(dy))
    wd = [E.width(x, SR)["amount"] for x in (w, FX.doubler(w, SR), FX.reverb(w, SR, 1.2, -12))]
    check("width: mono / doubled / slightly wide (a stereo reverb is not a double)", wd == ["mono", "doubled", "slightly wide"], str(wd))
    tl = [E.band_limited(x, r)["found"] for x, r in ((w, SR), (s_, sr0), (FX.telephone(w, SR), SR), (FX.telephone(s_, sr0), sr0))]
    check("telephone: found on the band-passed voices only", tl == [False, False, True, True], str(tl))
    g = E.grit(FX.grit(w, 20), SR)
    check("grit: soft saturation is honestly reported as not measurable, not guessed", g["found"] is None and "cannot" in g["note"], str(g))
    # separation arithmetic: stft/istft round trip
    import rapreaper_separate as S
    x = np.stack([w[: SR * 3], w[: SR * 3]], axis=0).astype(np.float32)
    y = S._istft(S._stft(x, 7680, 1024), 7680, 1024, x.shape[1])
    check("separation: the STFT / inverse STFT round trip is exact to -60 dB", float(np.max(np.abs(y - x))) < 1e-3 * np.max(np.abs(x)), str(float(np.max(np.abs(y - x)))))


# ---- 1.3.0: fades, device check, clarity, brassiness ------------------------------------------------------------
def test_130_clarity_devices():
    import make_test_audio as M
    sr = 48000
    v = np.asarray(M.synth_vocal(seconds=16.0), dtype=np.float64)
    b = np.asarray(M.synth_beat(seconds=16.0), dtype=np.float64)
    n = min(len(v), len(b))
    v2 = A._to_2d(v)[:n]
    b2 = A._to_2d(b)[:n]
    f = A.apply_fades(v2[: 2 * sr], sr, 0.5, 0.5)
    g = A.apply_fades(np.ones((2 * sr, 1)), sr, 0.5, 0.5)
    check("apply_fades: a constant fades in over 0.5 s and out over 0.5 s, untouched in the middle",
          g[0, 0] < 0.01 and abs(g[int(0.25 * sr), 0] - np.sin(np.pi / 4)) < 0.01 and g[sr, 0] == 1.0 and g[-1, 0] < 0.01
          and f.shape == v2[: 2 * sr].shape, str((g[0, 0], g[int(0.25 * sr), 0], g[sr, 0], g[-1, 0])))
    mono_mix = np.repeat(A._to_2d(A._mono(v2 * 0.5 + b2 * 0.3)), 2, axis=1)
    dc = A.device_check(mono_mix, sr)
    check("device_check: a mono mix loses nothing when summed to mono", dc["mono_loss_db"] is not None and abs(dc["mono_loss_db"]) < 0.3, str(dc["mono_loss_db"]))
    wide = np.stack([v2[:, 0] * 0.5 + b2[:, 0], -(v2[:, 0] * 0.5 + b2[:, 0])], axis=1)      # out of phase: cancels in mono
    dw = A.device_check(wide, sr)
    check("device_check: an out-of-phase mix loses a lot in mono and says so", dw["mono_loss_db"] > 10 and any("mono" in x["what"] for x in dw["findings"]), str(dw["mono_loss_db"]))
    dv = A.device_check(v2 * 0.5 + b2 * 0.3, sr, vocal=v2 * 0.5)
    check("device_check: with the vocal given, the phone word-band ratio is reported", dv.get("phone_vocal_over_rest_1to4k_db") is not None, str(dv))
    quiet_beat = A.vocal_clarity(v2, b2 * 0.02, sr)
    loud_beat = A.vocal_clarity(v2, b2 * 2.0, sr)
    check("vocal_clarity: a vocal over a whisper of a beat scores far higher than the same vocal under a loud beat",
          quiet_beat["clarity_score_0_to_10"] > loud_beat["clarity_score_0_to_10"] + 3
          and quiet_beat["words_band_vocal_over_rest_db"] > loud_beat["words_band_vocal_over_rest_db"] + 20, str((quiet_beat["clarity_score_0_to_10"], loud_beat["clarity_score_0_to_10"])))
    check("vocal_clarity: under a loud beat, stretches of lost words are listed with times inside the range",
          loud_beat["words_likely_lost"] and all(0 <= w["from_seconds"] <= 16.5 for w in loud_beat["words_likely_lost"]), str(loud_beat["words_likely_lost"][:2]))
    t = np.arange(n) / sr
    ring = v2 + 0.12 * np.sin(2 * np.pi * 2400.0 * t)[:, None] * (np.abs(v2) > 0.01)
    bz0, bz1 = A.brassiness(v2, sr), A.brassiness(ring, sr)
    check("brassiness: a 2.4 kHz ring raises the index and is found as a ringing peak near 2.4 kHz",
          bz1["brassiness"] > bz0["brassiness"] and any(2200 <= p["hz"] <= 2600 for p in bz1["ringing_peaks"]), str((bz0, bz1)))
    check("vocal_clarity on silence says it could not measure", A.vocal_clarity(np.zeros((sr, 1)), np.zeros((sr, 1)), sr).get("measured") is False, "")


def _drums(bpm, seconds=30.0, sr=44100, seed=1, hats=True):
    """Kick on 1 and 3, snare on 2 and 4, hats in eighths: a plain drum pattern at a known tempo."""
    from scipy import signal
    rng = np.random.default_rng(seed)
    n = int(seconds * sr)
    y = np.zeros(n)
    beat = 60.0 / bpm
    kt = np.arange(int(0.3 * sr)) / sr
    kick = np.sin(2 * np.pi * (55 * kt + 40 * 0.04 * (1 - np.exp(-kt / 0.04)))) * np.exp(-kt / 0.12)
    st = np.arange(int(0.2 * sr)) / sr
    snare = signal.sosfilt(signal.butter(2, [800, 5000], btype="band", fs=sr, output="sos"), rng.standard_normal(len(st))) * np.exp(-st / 0.05)
    ht = np.arange(int(0.05 * sr)) / sr
    hat = signal.sosfilt(signal.butter(2, 7000, btype="high", fs=sr, output="sos"), rng.standard_normal(len(ht))) * np.exp(-ht / 0.01)

    def put(at, sig, g):
        i = int(at * sr)
        if 0 <= i < n:
            m = min(len(sig), n - i)
            y[i:i + m] += g * sig[:m]
    t, k = 0.0, 0
    while t < seconds:
        if k % 4 in (0, 2):
            put(t, kick, 0.9)
        else:
            put(t, snare, 0.6)
        if hats:
            put(t, hat, 0.25)
            put(t + beat / 2, hat, 0.18)
        t += beat
        k += 1
    return y + 0.002 * rng.standard_normal(n)


def _chord_loop(chords, sr=44100, seconds=24.0, bar=2.0, cents=0.0):
    """Chords (lists of note names) played in turn, each with five harmonics and a slow decay."""
    base = {"C": 0, "C#": 1, "D": 2, "D#": 3, "E": 4, "F": 5, "F#": 6, "G": 7, "G#": 8, "A": 9, "A#": 10, "B": 11}
    n = int(seconds * sr)
    y = np.zeros(n)
    t = np.arange(int(bar * sr)) / sr
    for i in range(int(seconds / bar)):
        seg = np.zeros(len(t))
        for name in chords[i % len(chords)]:
            f = 440.0 * 2 ** ((base[name] - 9) / 12.0 - 1.0 + cents / 1200.0)
            for h in range(1, 6):
                seg += (0.6 ** h) * np.sin(2 * np.pi * f * h * t + h)
        y[i * len(t):(i + 1) * len(t)] += seg * np.exp(-t / 1.5)
    return y / np.max(np.abs(y))


def test_music():
    import rapreaper_music as MU
    for bpm in (72, 92, 140):
        r = MU.tempo(_drums(bpm), 44100)
        readings = [r.get("bpm")] + [a.get("bpm") for a in r.get("also_heard_as") or []]
        check("tempo: a %d BPM drum pattern reads %d (or its half/double time) with high confidence: %s" % (bpm, bpm, readings),
              any(x is not None and abs(float(x) - bpm) <= 1.0 for x in readings) and (r.get("confidence") or 0) >= 0.6
              and r.get("steady") is True, str(r))
    r = MU.tempo(_drums(92, seconds=5.0), 44100)
    check("tempo: five seconds are too short, said plainly", r.get("bpm") is None and "six seconds" in r.get("note", ""), str(r))
    r = MU.tempo(np.zeros(44100 * 10), 44100)
    check("tempo: silence gives no tempo", r.get("bpm") is None, str(r))
    t = np.arange(int(12.0 * 44100)) / 44100.0
    pad = sum(np.sin(2 * np.pi * f * t) for f in (261.6, 329.6, 392.0)) / 3.0
    r = MU.tempo(pad, 44100)
    check("tempo: a steadily held chord has no beat to find (no tempo, or none with confidence)", r.get("bpm") is None or (r.get("confidence") or 0) < 0.5, str(r))
    b = M.synth_beat(seconds=30.0)
    r = MU.tempo(b, M.SR)
    readings = [r.get("bpm")] + [a.get("bpm") for a in r.get("also_heard_as") or []]
    check("tempo: the simulated 140 BPM type beat reads 140 or 70, and says both readings are real",
          any(x is not None and abs(float(x) - 140) <= 1.0 for x in readings) and any(x is not None and abs(float(x) - 70) <= 1.0 for x in readings)
          and "Both readings are real" in r.get("note", ""), str(r))
    nt = MU.note_times(120)["milliseconds"]
    check("note values at 120 BPM: quarter 500 ms, dotted eighth 375, eighth 250, sixteenth 125, eighth triplet 166.7",
          (nt["quarter_note"], nt["dotted_eighth"], nt["eighth"], nt["sixteenth"], nt["eighth_triplet"]) == (500.0, 375.0, 250.0, 125.0, 166.7), str(nt))
    k = MU.key(_chord_loop([["C", "E", "G"], ["F", "A", "C"], ["G", "B", "D"], ["A", "C", "E"]]), 44100)
    check("key: a C major chord loop reads C major (or A minor, named as the relative) with confidence", k.get("tonic") in ("C", "A")
          and (k.get("confidence") or 0) >= 0.5 and "A minor" in (k.get("key", "") + k.get("relative", "")), str(k))
    k = MU.key(_chord_loop([["F#", "A", "C#"], ["D", "F#", "A"], ["A", "C#", "E"], ["E", "G#", "B"]]), 44100)
    check("key: an F# minor loop reads F# minor or its relative A major, scale notes listed, Camelot code given",
          k.get("tonic") in ("F#", "A") and set(k.get("scale_notes") or []) == {"F#", "G#", "A", "B", "C#", "D", "E"} and k.get("camelot") in ("11A", "11B"), str(k))
    k = MU.key(_chord_loop([["F#", "A", "C#"], ["D", "F#", "A"], ["A", "C#", "E"], ["E", "G#", "B"]], cents=-30.0), 44100)
    check("key: a loop tuned 30 cents low is reported as tuned off A=440, with the ReaTune advice",
          k.get("tuning_cents") is not None and -36 <= k["tuning_cents"] <= -24 and "reference pitch" in k.get("note", ""), str(k))
    k = MU.key(_drums(92, hats=True), 44100)
    check("key: drums alone give no key (no false key from the kick's pitch)", k.get("key") is None and "drums" in k.get("note", ""), str(k))
    k = MU.key(np.zeros(44100 * 6), 44100)
    check("key: silence gives no key", k.get("key") is None, str(k))
    ki = MU.key_info("Bb major")
    check("key info: 'Bb major' is A# major, Camelot 6B, relative G minor", ki["key"].startswith("A# major") and ki["camelot"] == "6B" and ki["relative"] == "G minor", str(ki))
    ki = MU.key_info("F#m")
    check("key info: 'F#m' is F# minor with its seven notes", ki["key"].startswith("F# minor") and ki["scale_notes"] == ["F#", "G#", "A", "B", "C#", "D", "E"], str(ki))
    bad = None
    try:
        MU.key_info("H sharp")
    except ValueError as e:
        bad = str(e)
    check("key info: nonsense is refused with the expected form", bad is not None and "F# minor" in bad, str(bad))
    cards = {t: MU.card(t) for t in MU.TOPICS if t not in ("note_values", "key_info")}
    check("reference cards: every topic answers with a dictionary that names its topic (or the list of topics)",
          all(isinstance(c, dict) and (c.get("topic") == t or t == "all_topics") for t, c in cards.items()), str(list(cards)))
    spotify = next(s for s in MU.card("loudness")["services"] if s["service"] == "Spotify")
    check("reference cards: Spotify normalizes to -14 LUFS, true peak -1 dBTP (-2 when louder)", spotify["normalizes_to_lufs"] == -14
          and spotify["true_peak_max_dbtp"] == -1 and "-2 dBTP" in spotify["note"], str(spotify))
    nv = MU.card("note values", bpm=140)
    check("reference cards: note values by alias, with the rap-vocal uses (an eighth at 140 is 214 ms)", nv["milliseconds"]["eighth"] == 214.3
          and "214 ms" in nv["for_rap_vocals"]["eighth_note_delay"], str(nv["for_rap_vocals"]))
    bad = None
    try:
        MU.card("note values")
    except ValueError as e:
        bad = str(e)
    check("reference cards: note values without a bpm say what is missing", bad is not None and "bpm" in bad, str(bad))
    bad = None
    try:
        MU.card("the meaning of life")
    except ValueError as e:
        bad = str(e)
    check("reference cards: an unknown topic lists the topics", bad is not None and "loudness_targets" in bad, str(bad))




def test_alignment():
    """Lining two recordings of the same music up: sample-exact for copies, right for different masters and for a mix against
    its instrumental, honest when the music is not the same."""
    import rapreaper_music as MU
    from scipy import signal
    sr = M.SR
    mono = M.synth_beat(seconds=60.0).mean(axis=1)
    t = np.arange(len(mono)) / sr
    pad = np.zeros_like(mono)
    for k, f in enumerate((220.0, 261.6, 196.0, 293.7)):                   # a pad that changes every 16 s: an arrangement
        seg = (t >= k * 16) & (t < (k + 1) * 16)
        pad[seg] = 0.08 * np.sin(2 * np.pi * f * t[seg]) + 0.04 * np.sin(2 * np.pi * 1.5 * f * t[seg])
    song = mono + pad

    def near(r, want, tol=0.003):
        return r.get("shift_seconds") is not None and abs(r["shift_seconds"] - want) < tol
    r = MU.align(song, sr, np.concatenate([np.zeros(int(7.345 * sr)), song[: int(40 * sr)]]), sr)
    check("align: a copy with 7.345 s of extra intro must move by -7.345 s (to the sample), with full confidence",
          near(r, -7.345, 0.001) and (r.get("confidence") or 0) >= 0.9, str(r)[:300])
    r = MU.align(song, sr, song[int(12.5 * sr):], sr)
    check("align: a copy with its first 12.5 s cut off must move by +12.5 s", near(r, 12.5, 0.001), str(r)[:300])
    sos = signal.butter(4, 9000, btype="low", fs=sr, output="sos")
    other = np.concatenate([np.zeros(int(0.3 * sr)), signal.sosfilt(sos, song) + 0.003 * np.random.default_rng(1).standard_normal(len(song))])
    r = MU.align(song, sr, other, sr)
    check("align: another master (low-passed, noisier) 0.3 s late reads -0.3 s", near(r, -0.3), str(r)[:300])
    vox = M.synth_vocal(seconds=50.0, seed=3)
    vox = vox.mean(axis=1) if vox.ndim == 2 else vox
    r = MU.align(song[: len(vox)] + 0.7 * vox, sr, np.concatenate([np.zeros(2 * sr), song]), sr)
    check("align: the instrumental against a mix of vocal and beat (the vocal recorded over it) reads -2.0 s", near(r, -2.0), str(r)[:300])
    from fractions import Fraction
    fr = Fraction(44100, sr)
    copy = signal.resample_poly(np.concatenate([np.zeros(int(3.21 * sr)), song]), fr.numerator, fr.denominator)
    r = MU.align(song, sr, copy, 44100)
    check("align: a 44.1 kHz copy of a 48 kHz file, 3.21 s late, reads -3.21 s (the rates are handled)", near(r, -3.21), str(r)[:300])
    r = MU.align(song, sr, np.random.default_rng(2).standard_normal(sr * 30) * 0.1, sr)
    check("align: noise against music gives low confidence and says the two may not hold the same music",
          (r.get("confidence") or 0) < 0.4 and "not a clear match" in r.get("note", "").lower(), str(r)[:300])
    r = MU.align(song, sr, song[: sr * 2], sr)
    check("align: two seconds of audio are too short, said plainly", r.get("shift_seconds") is None and "shorter" in r.get("note", ""), str(r))
    check("align: alternatives list other shifts with their rhythm, waveform and arrangement fits",
          all({"shift_seconds", "relative_fit", "waveform_fit", "arrangement_fit", "overlap_seconds"} <= set(a) for a in MU.align(song, sr, song[int(12.5 * sr):], sr).get("alternatives", [])), "")


def main():
    with tempfile.TemporaryDirectory() as tmp:
        for fn in (test_k_weighting, test_loudness_ebu3341, test_true_peak,
                   lambda: test_wav_roundtrip(tmp), test_detectors, test_measurement_helpers,
                   lambda: test_roles(tmp), test_review_fixes, lambda: test_second_review(tmp), test_breaths_and_s, test_song_effects,
                   test_130_clarity_devices, test_music, test_alignment):
            try:
                fn()
            except Exception:
                traceback.print_exc()
                check("%s ran without crashing" % getattr(fn, "__name__", "test"), False)
    bad = [r for r in RESULTS if not r[1]]
    print("\n%d checks, %d passed, %d failed" % (len(RESULTS), len(RESULTS) - len(bad), len(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())


