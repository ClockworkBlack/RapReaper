#!/usr/bin/env python3
"""RapReaper audio analysis -- the "ears" of the RapReaper kit.

Everything here runs on your own computer. It only READS audio files and
returns numbers and plain-language findings; it never changes a file and never
talks to the network. Dependencies: numpy and scipy.

Main entry points
    read_audio(path, start, duration)      -> samples, sample rate, info
    analyze(path_or_array, sr, role)       -> full report (dict)
    compare(report_before, report_after)   -> what changed (dict)
    gain_reduction(pre, post, sr)          -> how hard a compressor is working
    sibilance_reduction(pre, post, sr)     -> how hard a de-esser is working
    masking(vocal, beat, sr)               -> where the beat covers the vocal
    find_dense_window(mono, sr, seconds)   -> busiest stretch of a recording

Levels are in dBFS (0 dBFS = digital full scale). RMS levels use the plain
mean-square definition, so a full-scale sine reads -3.01 dBFS RMS.
Loudness follows ITU-R BS.1770 (current revision: -5, 2023) and EBU R 128 / Tech 3341 / 3342
(LUFS, loudness range, true peak).
"""

from __future__ import annotations

import math
import os
import struct

import numpy as np
from scipy import signal

__all__ = [
    "AudioError", "read_audio", "write_wav", "analyze", "compare",
    "gain_reduction", "sibilance_reduction", "masking", "find_dense_window",
    "loudness_stats", "true_peak_db", "band_levels_db", "BANDS",
    "clipping_of_file", "expander_effect",
]

MAX_ANALYSIS_SECONDS = 600.0          # a file is analysed up to its first 10 minutes
DIGITAL_SILENCE_DB = -130.0           # a frame under this holds no recording at all (empty timeline, cut-out gap)
EMPTY_SAMPLE = 1e-7                   # ... and a sample smaller than this is "nothing" (-140 dBFS)
EPS_POWER = 1e-20
EPS_AMP = 1e-10

# Named frequency regions used all through the kit and the guide.
BANDS = [
    ("sub", 20.0, 60.0),
    ("bass", 60.0, 100.0),
    ("boom", 100.0, 250.0),
    ("mud", 250.0, 500.0),
    ("body", 500.0, 2000.0),
    ("presence", 2000.0, 5000.0),
    ("sibilance", 5000.0, 9000.0),
    ("air", 9000.0, 16000.0),
]

# Long-term average speech spectrum, used only as a loose "natural voice"
# yardstick for the tone hints. Values are each band's level in dB relative to
# the 500-2000 Hz "body" band, worked out from the combined male/female
# one-third-octave levels in Table II of Byrne et al. (1994), "An international
# comparison of long-term average speech spectra", J. Acoust. Soc. Am. 96(4),
# 2108-2120 (speech at 70 dB SPL, microphone 20 cm from the mouth, 45 degrees
# off its axis). The paper's own figures at 12.5 and 16 kHz are extrapolated,
# so "air" is the least certain. A close-miked rap vocal legitimately differs
# from these, so the tone hints use wide tolerances and are suggestions, not
# faults.
SPEECH_REF_REL_BODY = {
    "boom": -0.4, "mud": 0.9, "presence": -11.8, "sibilance": -16.7, "air": -18.8,
}


SIBILANCE_STRONG_DB = 6.0             # "S sounds vs voice" above this: they hurt
SIBILANCE_NOTICEABLE_DB = 2.0         # ... above this: a little forward; at or under it: fine
HUM_LEAVE_ALONE_DB = -50.0            # mains hum this far (or further) under the voice is not treated


class AudioError(Exception):
    """Raised for unreadable or unsupported audio files."""


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _pdb(power):
    """power (mean square) -> dB"""
    return 10.0 * np.log10(np.maximum(power, EPS_POWER))


def _adb(amp):
    """amplitude -> dB"""
    return 20.0 * np.log10(np.maximum(np.abs(amp), EPS_AMP))


def _r(x, nd=1):
    """Round to a plain Python float (None stays None, non-finite -> None)."""
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x):
        return None
    return round(x, nd)


def _mx(*values):
    """Largest of the values that exist (a band above half the sample rate has no level)."""
    have = [v for v in values if v is not None]
    return max(have) if have else None


def _to_2d(data):
    data = np.asarray(data)
    if data.ndim == 1:
        data = data[:, None]
    return data


def fold_plan(data):
    """How to fold a stereo signal to one channel without losing it: 'mean' normally,
    'left' or 'right' when only that side carries sound (a mono microphone recorded on
    a stereo input pair), and 'left' when the two sides are opposite copies of each
    other (adding them would cancel the sound)."""
    data = _to_2d(data)
    if data.shape[1] == 1:
        return "mean"
    l = data[:, 0].astype(np.float64)
    r = data[:, 1].astype(np.float64)
    el, er = float(np.mean(l * l)) if l.size else 0.0, float(np.mean(r * r)) if r.size else 0.0
    if el <= 1e-14 and er <= 1e-14:
        return "mean"
    if el > 100.0 * er:
        return "left"
    if er > 100.0 * el:
        return "right"
    if float(np.mean(l * r)) / math.sqrt(el * er) < -0.5:
        return "left"
    return "mean"


def _mono(data):
    data = _to_2d(data)
    if data.shape[1] == 1:
        return data[:, 0].astype(np.float64)
    plan = fold_plan(data)
    if plan == "left":
        return data[:, 0].astype(np.float64)
    if plan == "right":
        return data[:, 1].astype(np.float64)
    return data[:, :2].astype(np.float64).mean(axis=1)


def fold(data):
    """One channel that represents the sound (see fold_plan)."""
    return _mono(data)


# ---------------------------------------------------------------------------
# WAV reading / writing (no third-party audio library needed)
# ---------------------------------------------------------------------------

def _parse_wav_header(f):
    head = f.read(12)
    if len(head) < 12 or head[8:12] != b"WAVE" or head[0:4] not in (b"RIFF", b"RF64"):
        raise AudioError("not a WAV file (RIFF/WAVE header missing)")
    rf64 = head[0:4] == b"RF64"
    fmt = None
    data_offset = None
    data_size = None
    ds64_data_size = None
    for _chunk in range(1000):     # (a real WAV has a handful of chunks; a crafted one could have millions)
        hdr = f.read(8)
        if len(hdr) < 8:
            break
        cid, size = hdr[0:4], struct.unpack("<I", hdr[4:8])[0]
        pos = f.tell()
        if cid == b"ds64":
            body = f.read(min(size, 28))
            if len(body) >= 16:
                ds64_data_size = struct.unpack("<Q", body[8:16])[0]
        elif cid == b"fmt ":
            body = f.read(min(size, 40))
            if len(body) < 16:
                raise AudioError("damaged WAV file (short fmt chunk)")
            tag, ch, sr, _brate, align, bits = struct.unpack("<HHIIHH", body[:16])
            if tag == 0xFFFE and len(body) >= 26:
                tag = struct.unpack("<H", body[24:26])[0]
            fmt = {"tag": tag, "channels": ch, "sr": sr, "align": align, "bits": bits}
        elif cid == b"data":
            data_offset = pos
            data_size = size
            if rf64 and (size == 0xFFFFFFFF) and ds64_data_size:
                data_size = ds64_data_size
            break
        f.seek(pos + size + (size & 1))
    if fmt is None or data_offset is None:
        raise AudioError("damaged WAV file (no fmt or data chunk)")
    if fmt["tag"] not in (1, 3):
        raise AudioError(
            "unsupported WAV encoding (format tag %d); only plain PCM and "
            "32/64-bit float WAV files are supported" % fmt["tag"])
    # a header can say anything; one that claims the impossible is a damaged (or hostile) file, not audio.
    # The limits are generous for real recordings (REAPER renders at most 768 kHz, 64 channels).
    if not (1 <= fmt["channels"] <= 64) or not (8000 <= fmt["sr"] <= 768000):
        raise AudioError("damaged WAV file (bad channel count or sample rate)")
    if fmt["bits"] not in (8, 16, 24, 32, 64) or fmt["align"] != fmt["channels"] * (fmt["bits"] // 8):
        raise AudioError("damaged WAV file (bits per sample do not match the block size)")
    if fmt["tag"] == 3 and fmt["bits"] not in (32, 64):
        raise AudioError("damaged WAV file (float audio must be 32 or 64 bits)")
    fmt["data_offset"] = data_offset
    fmt["data_size"] = data_size
    return fmt


READ_MAX_BYTES = 256 * 1024 * 1024       # at most this much of one file is read at a time (10 min of 48 kHz 2-ch 24-bit is 170 MB)
MAX_FRAMES = 60_000_000                  # ... and at most this many sample frames (10 min at 96 kHz): the analysis works in
                                         # float64 on one or two channels, so memory stays around 1.5 GB at the very worst


def _decode_pcm(buf, tag, bits, channels):
    if tag == 3:
        if bits == 32:
            x = np.frombuffer(buf, dtype="<f4").astype(np.float32)
        elif bits == 64:
            x = np.frombuffer(buf, dtype="<f8").astype(np.float32)
        else:
            raise AudioError("unsupported float WAV bit depth: %d" % bits)
    else:
        if bits == 16:
            x = np.frombuffer(buf, dtype="<i2").astype(np.float32)
            x /= 32768.0
        elif bits == 24:
            raw = np.frombuffer(buf, dtype=np.uint8)
            raw = raw[: (len(raw) // 3) * 3].reshape(-1, 3)
            v = (raw[:, 0].astype(np.int32)
                 | (raw[:, 1].astype(np.int32) << 8)
                 | (raw[:, 2].astype(np.int32) << 16))
            v = np.where(v & 0x800000, v - 0x1000000, v)
            x = v.astype(np.float32)
            x /= 8388608.0
        elif bits == 32:
            x = np.frombuffer(buf, dtype="<i4").astype(np.float32)
            x /= 2147483648.0
        elif bits == 8:
            x = np.frombuffer(buf, dtype=np.uint8).astype(np.float32)
            x -= 128.0
            x /= 128.0
        else:
            raise AudioError("unsupported WAV bit depth: %d" % bits)
    n = (len(x) // channels) * channels
    return x[:n].reshape(-1, channels)


def read_audio(path, start=0.0, duration=None, max_seconds=MAX_ANALYSIS_SECONDS):
    """Read (part of) an audio file. Returns (samples[n, channels] float32, sr, info).

    WAV files (16/24/32-bit PCM, 32/64-bit float, mono or stereo or more) are
    read directly. Other formats (FLAC, MP3, ...) are read through the optional
    'soundfile' package when it is installed.
    """
    path = os.path.abspath(os.path.expanduser(str(path)))
    if not os.path.isfile(path):
        raise AudioError("file not found: %s" % path)
    ext = os.path.splitext(path)[1].lower()
    if ext not in (".wav", ".wave", ".bwf"):
        return _read_with_soundfile(path, start, duration, max_seconds)
    with open(path, "rb") as f:
        fmt = _parse_wav_header(f)
        sr, ch, align = fmt["sr"], fmt["channels"], fmt["align"]
        f.seek(0, os.SEEK_END)
        file_size = f.tell()
        avail = max(0, min(fmt["data_size"], file_size - fmt["data_offset"]))
        total_frames = avail // align
        first = int(max(0.0, float(start or 0.0)) * sr)
        first = min(first, total_frames)
        want = total_frames - first
        if duration is not None:
            want = min(want, int(float(duration) * sr))
        truncated = False
        if max_seconds and want > int(max_seconds * sr):
            want = int(max_seconds * sr)
            truncated = True
        if want * align > READ_MAX_BYTES or want > MAX_FRAMES:   # whatever the header says, no more than this is held in memory
            want = min(READ_MAX_BYTES // align, MAX_FRAMES)
            truncated = True
        f.seek(fmt["data_offset"] + first * align)
        buf = f.read(want * align)
    data = _decode_pcm(buf, fmt["tag"], fmt["bits"], ch)
    info = {
        "path": path, "sample_rate": sr, "channels": ch,
        "bit_depth": fmt["bits"], "float": fmt["tag"] == 3,
        "file_seconds": total_frames / float(sr),
        "start_seconds": first / float(sr),
        "read_seconds": data.shape[0] / float(sr),
        "truncated": truncated,
    }
    if data.shape[0] == 0:
        raise AudioError("no audio in the requested part of %s" % path)
    return data, sr, info


def _read_with_soundfile(path, start, duration, max_seconds):
    try:
        import soundfile as sf  # optional
    except Exception:
        raise AudioError(
            "only WAV files can be read. Convert %s to WAV first (drag it into "
            "REAPER, then File > Render with Format: WAV)."
            % os.path.basename(path))
    try:
        with sf.SoundFile(path) as fh:
            sr = fh.samplerate
            total = len(fh)
            first = min(int(max(0.0, float(start or 0.0)) * sr), total)
            want = total - first
            if duration is not None:
                want = min(want, int(float(duration) * sr))
            truncated = False
            if max_seconds and want > int(max_seconds * sr):
                want = int(max_seconds * sr)
                truncated = True
            ch = max(1, int(fh.channels or 1))
            if want > MAX_FRAMES or want * ch * 4 > READ_MAX_BYTES:
                want = min(MAX_FRAMES, READ_MAX_BYTES // (ch * 4))
                truncated = True
            fh.seek(first)
            data = fh.read(want, dtype="float32", always_2d=True)
            info = {
                "path": path, "sample_rate": sr, "channels": fh.channels,
                "bit_depth": None, "float": None,
                "file_seconds": total / float(sr),
                "start_seconds": first / float(sr),
                "read_seconds": data.shape[0] / float(sr),
                "truncated": truncated,
            }
    except AudioError:
        raise
    except Exception as e:  # pragma: no cover - depends on optional library
        raise AudioError("could not read %s: %s" % (os.path.basename(path), e))
    if data.shape[0] == 0:
        raise AudioError("no audio in the requested part of %s" % path)
    return data, sr, info


def write_wav(path, data, sr, bits=24):
    """Write a PCM WAV file (16 or 24 bit) or 32-bit float (bits=32)."""
    data = _to_2d(np.asarray(data, dtype=np.float64))
    n, ch = data.shape
    if bits == 32:
        payload = data.astype("<f4").tobytes()
        tag, bps = 3, 32
    elif bits == 16:
        payload = np.clip(np.round(data * 32768.0), -32768, 32767).astype("<i2").tobytes()
        tag, bps = 1, 16
    elif bits == 24:
        v = np.clip(np.round(data * 8388608.0), -8388608, 8388607).astype(np.int32)
        b = np.empty((n * ch, 3), dtype=np.uint8)
        flat = v.reshape(-1)
        b[:, 0] = flat & 0xFF
        b[:, 1] = (flat >> 8) & 0xFF
        b[:, 2] = (flat >> 16) & 0xFF
        payload = b.tobytes()
        tag, bps = 1, 24
    else:
        raise AudioError("write_wav supports 16, 24 or 32(float) bits")
    align = ch * bps // 8
    header = b"RIFF" + struct.pack("<I", 36 + len(payload)) + b"WAVE"
    header += b"fmt " + struct.pack("<IHHIIHH", 16, tag, ch, int(sr), int(sr) * align, align, bps)
    header += b"data" + struct.pack("<I", len(payload))
    tmp = str(path) + ".tmp"
    with open(tmp, "wb") as f:
        f.write(header)
        f.write(payload)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Loudness (ITU-R BS.1770 / EBU R 128) and true peak
# ---------------------------------------------------------------------------

def _k_weight_sos(sr):
    """The two K-weighting filters of BS.1770, for any sample rate."""
    # stage 1: high shelf (+4 dB above ~1.7 kHz)
    f0, gain_db, q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
    k = math.tan(math.pi * f0 / sr)
    vh = 10.0 ** (gain_db / 20.0)
    vb = vh ** 0.4996667741545416
    a0 = 1.0 + k / q + k * k
    b = [(vh + vb * k / q + k * k) / a0, 2.0 * (k * k - vh) / a0, (vh - vb * k / q + k * k) / a0]
    a = [1.0, 2.0 * (k * k - 1.0) / a0, (1.0 - k / q + k * k) / a0]
    # stage 2: high-pass (~38 Hz)
    f0, q = 38.13547087602444, 0.5003270373238773
    k = math.tan(math.pi * f0 / sr)
    a0 = 1.0 + k / q + k * k
    b2 = [1.0, -2.0, 1.0]
    a2 = [1.0, 2.0 * (k * k - 1.0) / a0, (1.0 - k / q + k * k) / a0]
    return np.array([b + a, b2 + a2], dtype=np.float64)


def _window_sums(cs, win, hop):
    """Sliding sums from a cumulative sum array (cs[0] must be 0)."""
    n = len(cs) - 1
    if n < win:
        return np.zeros(0)
    starts = np.arange(0, n - win + 1, hop)
    return cs[starts + win] - cs[starts]


def loudness_stats(data, sr):
    """Integrated loudness, loudness range, short-term and momentary maxima."""
    data = _to_2d(data)
    n, ch = data.shape
    sos = _k_weight_sos(sr)
    use = min(ch, 2)                       # L and R (or mono); extra channels ignored
    m_win, m_hop = int(round(0.4 * sr)), int(round(0.1 * sr))
    s_win, s_hop = int(round(3.0 * sr)), int(round(0.1 * sr))
    m_sum = None
    s_sum = None
    for c in range(use):
        y = signal.sosfilt(sos, data[:, c].astype(np.float64))
        cs = np.concatenate(([0.0], np.cumsum(y * y)))
        del y
        ms = _window_sums(cs, m_win, m_hop) / float(m_win)
        ss = _window_sums(cs, s_win, s_hop) / float(s_win)
        m_sum = ms if m_sum is None else m_sum + ms
        s_sum = ss if s_sum is None else s_sum + ss
    out = {"integrated_lufs": None, "loudness_range_lu": None,
           "short_term_max_lufs": None, "momentary_max_lufs": None}
    if m_sum is None or len(m_sum) == 0:
        return out
    m_l = -0.691 + _pdb(m_sum)
    out["momentary_max_lufs"] = _r(np.max(m_l), 1)
    # integrated: 400 ms blocks with 75 % overlap = every block of the 100 ms hop grid
    gated = m_sum[m_l > -70.0]
    if len(gated):
        rel = -0.691 + _pdb(np.mean(gated)) - 10.0
        gated2 = m_sum[(m_l > -70.0) & (m_l > rel)]
        if len(gated2):
            out["integrated_lufs"] = _r(-0.691 + _pdb(np.mean(gated2)), 1)
    if s_sum is not None and len(s_sum):
        s_l = -0.691 + _pdb(s_sum)
        out["short_term_max_lufs"] = _r(np.max(s_l), 1)
        a = s_sum[s_l > -70.0]
        if len(a) >= 2:
            rel = -0.691 + _pdb(np.mean(a)) - 20.0
            g = s_l[(s_l > -70.0) & (s_l > rel)]
            if len(g) >= 2:
                out["loudness_range_lu"] = _r(np.percentile(g, 95) - np.percentile(g, 10), 1)
    return out


def true_peak_db(data, sr, oversample=4):
    """Inter-sample ("true") peak in dBTP, by 4x oversampling in chunks."""
    data = _to_2d(data)
    peak = 0.0
    chunk, ctx = 1 << 17, 256
    for c in range(data.shape[1]):
        x = data[:, c]
        n = len(x)
        for s in range(0, n, chunk):
            a, b = max(0, s - ctx), min(n, s + chunk + ctx)
            seg = x[a:b].astype(np.float64)
            if seg.size < 8:
                peak = max(peak, float(np.max(np.abs(seg))) if seg.size else 0.0)
                continue
            up = signal.resample_poly(seg, oversample, 1)
            lo = (s - a) * oversample
            hi = lo + (min(n, s + chunk) - s) * oversample
            core = up[lo:hi]
            # A capture is a cut out of longer audio, so it begins and stops abruptly, and that
            # makes the interpolation filter ring: the first and last millisecond of the
            # oversampled signal are left out. (The samples themselves are all counted below.)
            edge = int(0.001 * sr) * oversample
            if s == 0:
                core = core[edge:]
            if s + chunk >= n:
                core = core[: max(0, len(core) - edge)]
            if core.size:
                peak = max(peak, float(np.max(np.abs(core))))
    sample_peak = float(np.max(np.abs(data))) if data.size else 0.0
    return float(_adb(max(peak, sample_peak)))


# ---------------------------------------------------------------------------
# Frame-by-frame spectra
# ---------------------------------------------------------------------------

class _Frames:
    """Short-time power spectra of a mono signal.

    power[f, k] is scaled so that power[f].sum() ~= the mean square of frame f,
    which makes band sums directly comparable with RMS levels in dBFS.
    """

    def __init__(self, mono, sr):
        self.sr = sr
        nfft = 2048 if sr <= 50000 else 4096
        hop = nfft // 2
        x = np.asarray(mono, dtype=np.float32)
        if len(x) < nfft:
            x = np.concatenate([x, np.zeros(nfft - len(x), dtype=np.float32)])
        win = np.hanning(nfft).astype(np.float32)
        view = np.lib.stride_tricks.sliding_window_view(x, nfft)[::hop]
        nfr = view.shape[0]
        scale = 2.0 / (nfft * nfft * float(np.mean(win.astype(np.float64) ** 2)))
        power = np.empty((nfr, nfft // 2 + 1), dtype=np.float32)
        step = 1024
        for i in range(0, nfr, step):
            spec = np.fft.rfft(view[i:i + step] * win, axis=1)
            p = (spec.real ** 2 + spec.imag ** 2) * scale
            p[:, 0] *= 0.5
            p[:, -1] *= 0.5
            power[i:i + step] = p
        self.nfft, self.hop, self.n = nfft, hop, nfr
        self.freqs = np.fft.rfftfreq(nfft, 1.0 / sr)
        self.power = power
        self.times = (np.arange(nfr) * hop + nfft / 2.0) / sr
        self.level_db = _pdb(power.sum(axis=1).astype(np.float64))

    def band_power(self, lo, hi):
        m = (self.freqs >= lo) & (self.freqs < hi)
        if not m.any():
            return np.zeros(self.n)
        return self.power[:, m].sum(axis=1).astype(np.float64)

    def mean_spectrum(self, mask=None):
        p = self.power if mask is None else self.power[mask]
        if p.shape[0] == 0:
            return np.zeros(self.power.shape[1])
        return p.mean(axis=0).astype(np.float64)


def band_levels_db(spectrum, freqs, bands=BANDS):
    """Band levels (dBFS) from a mean power spectrum."""
    out = {}
    for name, lo, hi in bands:
        m = (freqs >= lo) & (freqs < hi)
        out[name] = float(_pdb(spectrum[m].sum())) if m.any() else None
    return out


def _loud_ref(levels_db, frame_seconds):
    """The level of the loud parts of a recording, from its frame (or block) levels.

    For an ordinary take this is the 95th percentile. A take that is mostly gaps (an ad-lib
    track: one shout every few bars) has its 95th percentile down in the room tone, so the
    reference is never taken more than 6 dB under the level of the loudest second. One click or
    mic bump does not move it: it is too short to raise a whole second."""
    lv = np.asarray(levels_db, dtype=np.float64)
    if lv.size == 0:
        return None
    p95 = float(np.percentile(lv, 95))
    k = int(max(1, min(lv.size, round(1.0 / max(float(frame_seconds), 1e-6)))))
    top = np.sort(lv)[-k:]
    top_level = float(_pdb(np.mean(10.0 ** (top / 10.0))))
    return max(p95, top_level - 6.0)


def _activity(fr, role):
    """Split frames into 'active' (signal present) and 'noise' (the gaps)."""
    lv = fr.level_db
    audible = lv > -90.0
    if not audible.any():
        return np.zeros(fr.n, bool), np.zeros(fr.n, bool), None, None
    l95 = _loud_ref(lv[audible], fr.hop / float(fr.sr))
    # Empty timeline between two clips (or a gap that was cut out) is digital silence, not the
    # recording's own background noise. Those frames, and the frames that straddle their edges,
    # are left out when the noise floor is estimated.
    empty = lv < DIGITAL_SILENCE_DB
    edge = empty.copy()
    edge[1:] |= empty[:-1]
    edge[:-1] |= empty[1:]
    real = ~edge
    floor = float(np.percentile(lv[real], 5)) if real.any() else -120.0
    floor = max(floor, -120.0)
    if role in ("vocal", "voice", "adlib"):
        # 8 dB over the noise floor, but never closer than 12 dB to the loud level: a section with
        # no pauses in it has no "floor", and must not end up counted as silent
        thr = max(l95 - 30.0, min(floor + 8.0, l95 - 12.0))
    else:
        thr = l95 - 45.0
    active = lv >= thr
    noise = real & (lv <= floor + 4.0) & (lv < thr - 6.0)
    return active, noise, l95, floor


def find_dense_window(mono, sr, seconds=20.0):
    """Start time (s) of the stretch with the most signal -- used to pick a
    representative part of a recording for before/after captures."""
    x = np.asarray(mono, dtype=np.float64)
    hop = int(0.1 * sr)
    if len(x) < hop * 4:
        return 0.0
    nblk = len(x) // hop
    e = (x[: nblk * hop].reshape(nblk, hop) ** 2).mean(axis=1)
    lv = _pdb(e)
    ref = _loud_ref(lv, 0.1)
    act = (lv > ref - 25.0).astype(np.float64)
    wlen = int(round(seconds / 0.1))
    if nblk <= wlen:
        return 0.0
    # score = how many active blocks, lightly weighted by energy
    score = act + 0.25 * np.clip((lv - (ref - 25.0)) / 25.0, 0.0, 1.0)
    cs = np.concatenate(([0.0], np.cumsum(score)))
    sums = cs[wlen:] - cs[:-wlen]
    start_blk = int(np.argmax(sums))
    return round(start_blk * 0.1, 2)


# ---------------------------------------------------------------------------
# Individual detectors
# ---------------------------------------------------------------------------

def _clipping(data, bit_depth=None):
    """Count flat tops: runs of (nearly) identical samples at the extreme value.

    A real clip repeats the SAME value; a smooth low-frequency crest does not,
    so the test is on sample-to-sample equality, not just closeness to the peak.
    """
    data = _to_2d(data)
    runs_total, longest = 0, 0
    peak = float(np.max(np.abs(data))) if data.size else 0.0
    if peak < 1e-4:
        return {"clipped_runs": 0, "longest_run_samples": 0, "flat_top_level_dbfs": None}
    if bit_depth == 16:
        tol, min_run = 1.5 / 32768.0, 4
    else:
        tol, min_run = max(2.5e-7, peak * 1e-6), 3
    for c in range(data.shape[1]):
        x = data[:, c].astype(np.float64)
        hit = np.abs(x) >= peak * 0.999
        if hit.sum() < min_run:
            continue
        same = np.abs(np.diff(x)) <= tol
        flat = hit[1:] & hit[:-1] & same
        if not flat.any():
            continue
        d = np.diff(np.concatenate(([0], flat.astype(np.int8), [0])))
        starts, ends = np.where(d == 1)[0], np.where(d == -1)[0]
        lens = ends - starts + 1          # number of samples in each flat run
        # the worst channel counts: a mono take rendered onto two channels is clipped once, not twice
        runs_total = max(runs_total, int(np.sum(lens >= min_run)))
        if len(lens):
            longest = max(longest, int(lens.max()))
    return {"clipped_runs": runs_total, "longest_run_samples": longest if runs_total else 0,
            "flat_top_level_dbfs": _r(_adb(peak), 2) if runs_total else None}


def clipping_of_file(path, max_seconds=MAX_ANALYSIS_SECONDS):
    """Flat tops in an audio FILE, read at its own sample rate.

    A capture that REAPER rendered at another rate than the recording's cannot show them:
    resampling turns every flat top into a ripple. So the clips' own files are looked at as well."""
    data, sr, info = read_audio(path, max_seconds=max_seconds)
    out = _clipping(data, info.get("bit_depth"))
    out["sample_rate"] = int(sr)
    return out


def _quiet_spectrum(mono, sr, top_hz=620.0):
    """Low-frequency power spectrum of the QUIETEST moments of a recording.

    The spectrum is taken over many overlapping stretches and, for every
    frequency, the 15th-percentile value is kept. Anything that comes and goes
    (the voice, P-pops) drops out; anything that never stops (mains hum, the
    room's noise floor) stays. Returns (freqs, power) below top_hz, or None.
    """
    n = len(mono)
    nper = 1 << 15 if sr <= 50000 else 1 << 16
    while nper > 4096 and n < nper * 3:
        nper >>= 1
    if n < nper * 3:
        return None
    hop = nper // 4
    x = np.asarray(mono, dtype=np.float32)
    view = np.lib.stride_tricks.sliding_window_view(x, nper)[::hop]
    win = np.hanning(nper).astype(np.float32)
    freqs = np.fft.rfftfreq(nper, 1.0 / sr)
    keep = int(np.searchsorted(freqs, top_hz))
    scale = 2.0 / (float(np.sum(win.astype(np.float64))) ** 2)        # "spectrum" scaling: a tone reads its mean square
    rows = []
    for i in range(0, view.shape[0], 64):
        spec = np.fft.rfft(view[i:i + 64] * win, axis=1)[:, :keep]
        rows.append((spec.real ** 2 + spec.imag ** 2).astype(np.float64) * scale)
    pw = np.concatenate(rows, axis=0)
    # A stretch that runs over empty timeline says nothing about hum: with enough of them the
    # "quietest moments" would all be digital silence and a real hum would be missed.
    nothing = np.concatenate(([0], np.cumsum(np.abs(x) < EMPTY_SAMPLE)))
    starts = np.arange(view.shape[0]) * hop
    whole = (nothing[starts + nper] - nothing[starts]) <= 0.02 * nper
    if int(whole.sum()) < 3:
        return None
    return freqs[:keep], np.percentile(pw[whole], 15, axis=0), freqs[1] - freqs[0]


# The 15th percentile of pure noise sits about 7.9 dB under its average
# (-ln(0.85) of an exponential distribution); a steady tone does not move.
_P15_NOISE_BIAS = 0.1625


def _hum(mono, sr, active_level_db):
    if len(mono) < int(sr * 4):
        return {"checked": False, "reason": "recording shorter than 4 s"}
    q = _quiet_spectrum(mono, sr)
    if q is None:
        return {"checked": False, "reason": "too little continuous recording to check"}
    f, pxx, df = q
    half = max(1.5, 1.6 * df)
    best = None
    for mains in (50.0, 60.0):
        harm = []
        for k in range(1, 9):
            fk = mains * k
            near = (f >= fk - half) & (f <= fk + half)
            ring = ((f >= fk - 14.0) & (f <= fk - half - 2.0)) | ((f >= fk + half + 2.0) & (f <= fk + 14.0))
            if not near.any() or not ring.any():
                continue
            peak = float(pxx[near].max())
            base = float(np.median(pxx[ring])) / _P15_NOISE_BIAS
            # Where exactly is the line? (A bass note bleeding from headphones can sit a hertz or
            # two beside a mains harmonic; mains hum sits on it.) Three-point fit around the peak.
            i = int(np.where(near)[0][0] + np.argmax(pxx[near]))
            f_est = float(f[i])
            if 0 < i < len(pxx) - 1:
                la_, lb_, lc_ = (10.0 * math.log10(max(float(pxx[j]), EPS_POWER)) for j in (i - 1, i, i + 1))
                den = la_ - 2.0 * lb_ + lc_
                if den < -1e-9:
                    f_est += 0.5 * (la_ - lc_) / den * df
            on_mains = abs(f_est - fk) <= max(0.7, 0.35 * df, 0.004 * fk)
            # a Hann-windowed tone spreads over ~1.5 bins: sum them and divide
            harm.append((fk, 10.0 * math.log10(max(peak, EPS_POWER) / max(base, EPS_POWER)),
                         float(pxx[near].sum()) / 1.5, on_mains))
        strong = [h for h in harm if h[1] >= 12.0 and h[3]]
        power = sum(h[2] for h in strong)
        score = (len(strong), power)
        if best is None or score > best[0]:
            best = (score, mains, harm, strong, power)
    _, mains, harm, strong, power = best
    level = float(_pdb(power)) if strong else None
    found = len(strong) >= 2 and level is not None and level > -90.0 and strong[0][0] <= mains * 3
    if not found and len(strong) == 1 and strong[0][0] == mains and strong[0][1] >= 20.0 \
            and level is not None and level > -80.0:
        found = True                    # a single, very clear line on the mains frequency itself (transformer hum)
    out = {
        "checked": True, "found": bool(found),
        "mains_hz": int(mains) if found else None,
        "level_dbfs": _r(level, 1) if found else None,
        "relative_to_voice_db": _r(level - active_level_db, 1)
        if (found and active_level_db is not None) else None,
        "harmonics_hz": [int(h[0]) for h in sorted(strong, key=lambda h: -h[2])] if found else [],
    }
    return out


def _pitch_floor(mono, sr, active_mask_times, fr):
    """Rough lowest typical voice pitch (Hz) from autocorrelation; None if unsure."""
    try:
        target = 4000
        dec = max(1, int(round(sr / target)))
        y = signal.resample_poly(mono, 1, dec) if dec > 1 else np.asarray(mono, np.float64)
        sr2 = sr / float(dec)
        wlen = int(0.064 * sr2)
        if len(y) < wlen * 4:
            return None
        idx = np.where(active_mask_times)[0]
        if len(idx) < 8:
            return None
        order = idx[np.argsort(fr.level_db[idx])[::-1][:240]]
        lo, hi = int(sr2 / 350.0), int(sr2 / 65.0)
        f0s = []
        for i in order:
            c = int(fr.times[i] * sr2)
            a = c - wlen // 2
            if a < 0 or a + wlen > len(y):
                continue
            seg = y[a:a + wlen] - np.mean(y[a:a + wlen])
            e = float(np.dot(seg, seg))
            if e <= 0:
                continue
            ac = signal.correlate(seg, seg, mode="full")[wlen - 1:]
            ac = ac / e
            win = ac[lo:hi]
            if len(win) < 3:
                continue
            j = int(np.argmax(win))
            if win[j] >= 0.55:
                f0s.append(sr2 / float(lo + j))
        if len(f0s) < 12:
            return None
        return float(np.percentile(f0s, 15))
    except Exception:
        return None


def _resonances(spec, freqs, lo=500.0, hi=8000.0, max_n=3):
    """Narrow peaks sticking out of the long-term spectrum."""
    grid = np.geomspace(max(lo / 2.0, 60.0), min(hi * 2.0, freqs[-1] * 0.95), 220)
    edges = np.sqrt(grid[:-1] * grid[1:])
    edges = np.concatenate(([grid[0] / (edges[0] / grid[0])], edges, [grid[-1] * (grid[-1] / edges[-1])]))
    cs = np.concatenate(([0.0], np.cumsum(spec)))
    idx = np.searchsorted(freqs, edges)
    idx = np.clip(idx, 0, len(spec))
    width = np.maximum(idx[1:] - idx[:-1], 1)
    dens = (cs[np.maximum(idx[1:], idx[:-1] + 1).clip(max=len(spec))] - cs[idx[:-1]]) / width
    ldb = _pdb(dens)
    per_oct = len(grid) / math.log2(grid[-1] / grid[0])
    k_fine = max(3, int(per_oct / 8.0) | 1)
    k_wide = max(9, int(per_oct * 1.2) | 1)
    pad = k_wide
    lp = np.pad(ldb, pad, mode="edge")
    fine = np.convolve(lp, np.ones(k_fine) / k_fine, mode="same")[pad:-pad]
    wide = np.convolve(lp, np.ones(k_wide) / k_wide, mode="same")[pad:-pad]
    prom = fine - wide
    out = []
    for i in range(2, len(grid) - 2):
        if not (lo <= grid[i] <= hi):
            continue
        if prom[i] >= 6.0 and prom[i] >= prom[i - 1] and prom[i] >= prom[i + 1] \
                and prom[i] >= prom[i - 2] and prom[i] >= prom[i + 2]:
            out.append((float(prom[i]), float(grid[i])))
    out.sort(reverse=True)
    picked = []
    for p, fq in out:
        if all(abs(math.log2(fq / q)) > 0.5 for _, q in picked):
            picked.append((p, fq))
        if len(picked) >= max_n:
            break
    return [{"hz": int(round(fq / 10.0) * 10), "prominence_db": _r(p, 1)} for p, fq in picked]


def _stereo(data, sr):
    data = _to_2d(data)
    if data.shape[1] < 2:
        return None
    l = data[:, 0].astype(np.float64)
    r = data[:, 1].astype(np.float64)
    el, er = float(np.mean(l * l)), float(np.mean(r * r))
    if el < 1e-12 and er < 1e-12:
        return None
    denom = math.sqrt(max(el * er, 1e-24))
    corr = float(np.mean(l * r) / denom)
    mid, side = 0.5 * (l + r), 0.5 * (l - r)
    em, es = float(np.mean(mid * mid)), float(np.mean(side * side))
    sos = signal.butter(4, 120.0, btype="low", fs=sr, output="sos")
    ll, rl = signal.sosfilt(sos, l), signal.sosfilt(sos, r)
    dl = math.sqrt(max(float(np.mean(ll * ll)) * float(np.mean(rl * rl)), 1e-24))
    low_corr = float(np.mean(ll * rl) / dl) if dl > 1e-10 else None
    return {
        "correlation": _r(corr, 2),
        "low_end_correlation_below_120hz": _r(low_corr, 2),
        "side_to_mid_db": _r(_pdb(es) - _pdb(em), 1),
        "left_right_balance_db": _r(_pdb(el) - _pdb(er), 1),
        "effectively_mono": bool(es < em * 1e-4),
    }


def _sibilance(fr, active):
    hi = _pdb(fr.band_power(5000.0, 10000.0))
    mid = _pdb(fr.band_power(300.0, 3000.0))
    if not active.any():
        return None, np.zeros(fr.n, bool)
    hi_ref = float(np.percentile(hi[active], 95))
    sib = active & (hi - mid > 0.0) & (hi > hi_ref - 15.0)
    voiced = active & ~sib
    if sib.sum() < 3 or voiced.sum() < 3:
        return {"found": False, "frames": int(sib.sum())}, sib
    sib_level = float(np.percentile(hi[sib], 90))
    voiced_level = float(np.median(fr.level_db[voiced]))
    spec = fr.mean_spectrum(sib)
    m = (fr.freqs >= 3500.0) & (fr.freqs <= 12000.0)
    f = fr.freqs[m]
    s = spec[m]
    k = max(3, int(600.0 / (fr.freqs[1] - fr.freqs[0])) | 1)
    sm = np.convolve(np.pad(s, k, mode="edge"), np.ones(k) / k, mode="same")[k:-k]
    centre = float(f[int(np.argmax(sm))]) if len(f) else 6500.0
    return {
        "found": True,
        "frames": int(sib.sum()),
        "share_of_active_percent": _r(100.0 * sib.sum() / max(1, active.sum()), 1),
        "level_dbfs": _r(sib_level, 1),
        "voiced_level_dbfs": _r(voiced_level, 1),
        "excess_db": _r(sib_level - voiced_level, 1),
        "centre_hz": int(round(min(max(centre, 4500.0), 9500.0) / 100.0) * 100),
    }, sib


def _plosives(fr, active, active_level_db, top_hz=70.0):
    """Count short low thumps BELOW the voice's lowest note (P and B pops)."""
    lf = _pdb(fr.band_power(20.0, top_hz))
    if fr.n < 40 or active_level_db is None:
        return {"count": 0, "per_minute": 0.0}
    base = signal.medfilt(lf, 31)
    burst = (lf - base > 12.0) & (lf > active_level_db - 15.0)
    d = np.diff(np.concatenate(([0], burst.astype(np.int8))))
    count = int(np.sum(d == 1))
    minutes = max(fr.times[-1] / 60.0, 1e-6)
    return {"count": count, "per_minute": _r(count / minutes, 1)}


def _phrase_levels(mono, sr, thr_db):
    """Levels of ~0.8 s stretches that are mostly sounding (dBFS)."""
    blk = int(0.05 * sr)
    nblk = len(mono) // blk
    if nblk < 20:
        return np.zeros(0)
    x = np.asarray(mono[: nblk * blk], dtype=np.float64).reshape(nblk, blk)
    e = (x * x).mean(axis=1)
    act = (_pdb(e) >= thr_db).astype(np.float64)
    win, hop = 16, 4                                   # 0.8 s windows every 0.2 s
    cs_e = np.concatenate(([0.0], np.cumsum(e)))
    cs_a = np.concatenate(([0.0], np.cumsum(act)))
    starts = np.arange(0, nblk - win + 1, hop)
    we = (cs_e[starts + win] - cs_e[starts]) / win
    wa = (cs_a[starts + win] - cs_a[starts]) / win
    return _pdb(we[wa >= 0.6])


def _dynamics(mono, sr, thr_db):
    blk = int(0.05 * sr)
    nblk = len(mono) // blk
    if nblk < 4:
        return None
    x = np.asarray(mono[: nblk * blk], dtype=np.float64).reshape(nblk, blk)
    rms = _pdb((x * x).mean(axis=1))
    pk = _adb(np.max(np.abs(x), axis=1))
    act = rms >= thr_db
    if act.sum() < 4:
        return None
    a = rms[act]
    ph = _phrase_levels(mono, sr, thr_db)
    out = {
        "active_level_p20_dbfs": _r(np.percentile(a, 20), 1),
        "active_level_p50_dbfs": _r(np.percentile(a, 50), 1),
        "active_level_p95_dbfs": _r(np.percentile(a, 95), 1),
        "active_peak_p95_dbfs": _r(np.percentile(pk[act], 95), 1),
        "syllable_level_range_db": _r(np.percentile(a, 95) - np.percentile(a, 20), 1),
        "level_range_db": None,
    }
    if len(ph) >= 5:
        out["level_range_db"] = _r(np.percentile(ph, 92) - np.percentile(ph, 8), 1)
        out["loud_phrase_level_dbfs"] = _r(np.percentile(ph, 92), 1)
        out["quiet_phrase_level_dbfs"] = _r(np.percentile(ph, 8), 1)
    return out


# ---------------------------------------------------------------------------
# The main report
# ---------------------------------------------------------------------------

def _load(source, sr, start, duration):
    if isinstance(source, (str, os.PathLike)):
        data, sr, info = read_audio(source, start=start or 0.0, duration=duration)
        return data, sr, info
    data = _to_2d(np.asarray(source, dtype=np.float32))
    if sr is None:
        raise AudioError("sample rate is required when passing samples directly")
    info = {"path": None, "sample_rate": sr, "channels": data.shape[1],
            "bit_depth": None, "float": None,
            "file_seconds": data.shape[0] / float(sr), "start_seconds": 0.0,
            "read_seconds": data.shape[0] / float(sr), "truncated": False}
    return data, sr, info


def analyze(source, sr=None, role="vocal", start=None, duration=None):
    """Measure a recording and list what is wrong with it.

    role: "vocal" (lead, double, ad-lib), "beat" (instrumental / 2-track beat)
          or "mix" (the whole song / master output).
    Returns a dict with: file, levels, loudness, noise, spectrum, dynamics,
    stereo, findings (problems in plain language, most serious first) and, for
    vocals, treatment hints that the chain builder turns into settings.
    """
    role = (role or "vocal").lower()
    if role in ("lead", "voice", "vox", "adlib", "ad-lib", "double"):
        role = "vocal"
    if role in ("instrumental", "2track", "two-track"):
        role = "beat"
    if role in ("master", "song"):
        role = "mix"
    data, sr, info = _load(source, sr, start, duration)
    mono = _mono(data)
    n = len(mono)
    report = {"role": role, "file": {
        "name": os.path.basename(info["path"]) if info["path"] else None,
        "sample_rate": int(sr), "channels": int(info["channels"]),
        "bit_depth": info["bit_depth"],
        "seconds_analysed": _r(n / float(sr), 2),
        "start_seconds": _r(info["start_seconds"], 2),
        "file_seconds": _r(info["file_seconds"], 2),
        "truncated": bool(info["truncated"]),
    }}
    findings = []
    peak = float(np.max(np.abs(data))) if data.size else 0.0
    if peak < 1e-5:
        report["silent"] = True
        report["findings"] = [{
            "id": "silent", "severity": "severe",
            "what": "This audio is silent (nothing above -100 dBFS).",
            "fix": "Check that the right track and time range were used and that the media is online in REAPER."}]
        return report
    report["silent"] = False

    # ---- levels -----------------------------------------------------------
    ms = float(np.mean(mono * mono))
    tp = true_peak_db(data, sr)
    levels = {
        "sample_peak_dbfs": _r(_adb(peak), 2),
        "true_peak_dbtp": _r(tp, 2),
        "rms_dbfs": _r(_pdb(ms), 1),
        "crest_factor_db": _r(_adb(peak) - _pdb(ms), 1),
        "dc_offset": _r(float(np.mean(mono)), 5),
    }
    report["levels"] = levels
    loud = loudness_stats(data, sr)
    report["loudness"] = loud

    # Everything below looks at the sound in frames of about 40 ms. Above 96 kHz the audio is
    # brought down to 88.2/96 kHz first (nothing a voice or a mix needs lives up there), so the
    # frames keep their length.
    if sr > 96000:
        dec = int(math.ceil(sr / 96000.0))
        data = signal.resample_poly(data.astype(np.float64), 1, dec, axis=0).astype(np.float32)
        mono = _mono(data)
        sr = int(round(sr / float(dec)))

    # ---- frames, activity -------------------------------------------------
    # The waveform's offset from the centre line (DC) is reported on its own; it is neither noise
    # nor rumble, so it is taken out here. Empty timeline stays exactly empty.
    there = np.abs(mono) >= EMPTY_SAMPLE
    centred = mono
    if there.any():
        centred = np.where(there, mono - float(np.mean(mono[there])), 0.0)
    fr = _Frames(centred, sr)
    active, noise, l95, floor = _activity(fr, role)
    act_spec = fr.mean_spectrum(active if active.any() else None)
    act_level = float(_pdb(act_spec.sum())) if active.any() else None
    bl = band_levels_db(act_spec, fr.freqs)
    total = _pdb(act_spec.sum())
    report["spectrum"] = {
        "band_levels_dbfs": {k: _r(v, 1) for k, v in bl.items()},
        "band_levels_rel_total_db": {k: _r(v - total, 1) if v is not None else None for k, v in bl.items()},
    }
    lo_m = (fr.freqs >= 100.0) & (fr.freqs <= 10000.0) & (act_spec > 0)
    if lo_m.sum() > 8:
        octs = np.log2(fr.freqs[lo_m] / 1000.0)
        dens = _pdb(act_spec[lo_m]) + 10.0 * np.log10(fr.freqs[lo_m] / 1000.0)  # per-octave density
        slope = float(np.polyfit(octs, dens, 1)[0])
        report["spectrum"]["tilt_db_per_octave"] = _r(slope, 1)
    empty_share = float(np.mean(fr.level_db < DIGITAL_SILENCE_DB)) if fr.n else 0.0
    report["activity"] = {
        "active_percent": _r(100.0 * active.sum() / max(1, fr.n), 1),
        "active_level_dbfs": _r(act_level, 1),
        "empty_percent": _r(100.0 * empty_share, 1),
    }

    # ---- noise floor ------------------------------------------------------
    noise_info = {"measured": False}
    noise_seconds = float(noise.sum() * fr.hop / float(sr))
    if noise_seconds >= 0.4 and act_level is not None:
        nspec = fr.mean_spectrum(noise)
        nlevel = float(_pdb(nspec.sum()))
        nb = band_levels_db(nspec, fr.freqs)
        noise_info = {
            "measured": True,
            "seconds_of_gaps": _r(noise_seconds, 1),
            "floor_dbfs": _r(nlevel, 1),
            "signal_to_noise_db": _r(act_level - nlevel, 1),
            "character": ("rumble/hum (low)" if (_mx(nb["sub"], nb["bass"], nb["boom"]) or -999.0) > nlevel - 3.0
                          else "hiss (high)" if (_mx(nb["sibilance"], nb["air"]) or -999.0) > nlevel - 6.0
                          else "broadband"),
        }
    elif empty_share >= 0.02:
        noise_info["reason"] = ("the gaps in this section are digital silence (empty timeline between clips, or "
                                "gaps that were cut out), so there is no background noise to measure in them")
    report["noise"] = noise_info

    # ---- clipping / DC ----------------------------------------------------
    clip = _clipping(data, info.get("bit_depth"))
    report["clipping"] = clip
    dyn_thr = (max(l95 - 30.0, min(floor + 8.0, l95 - 12.0)) if role == "vocal" else (l95 - 45.0)) if l95 is not None else -60.0
    dyn = _dynamics(mono, sr, dyn_thr)
    report["dynamics"] = dyn
    st = _stereo(data, sr)
    report["stereo"] = st

    if clip["clipped_runs"] >= 3 and clip["flat_top_level_dbfs"] is not None:
        sev = "severe" if clip["clipped_runs"] >= 20 else "moderate"
        findings.append({
            "id": "clipping", "severity": sev,
            "what": "The waveform is clipped (flat-topped) in %d places; that is distortion baked into the file."
                    % clip["clipped_runs"],
            "fix": "This cannot be undone with EQ or compression. Re-record with the interface gain lower "
                   "(aim for the loudest lines to peak around -12 dBFS)." if role == "vocal" else
                   "Lower the level before this point (track faders or the limiter input) and render again."})
    if abs(levels["dc_offset"] or 0.0) > 0.003:
        findings.append({
            "id": "dc_offset", "severity": "minor",
            "what": "The waveform sits off-centre (DC offset %.4f)." % levels["dc_offset"],
            "fix": "The high-pass filter at the start of the chain removes it."})

    if role == "vocal":
        _vocal_part(report, findings, data, mono, sr, fr, active, noise, act_level, bl, levels, dyn, noise_info)
    elif role == "beat":
        _beat_part(report, findings, levels, loud, st)
    else:
        _mix_part(report, findings, levels, loud, st, bl, total)

    order = {"severe": 0, "moderate": 1, "minor": 2, "info": 3}
    findings.sort(key=lambda f: order.get(f["severity"], 9))
    report["findings"] = findings
    return report


def _vocal_part(report, findings, data, mono, sr, fr, active, noise, act_level, bl, levels, dyn, noise_info):
    hints = {}
    # one side only / opposite sides
    st = report.get("stereo") or {}
    plan = fold_plan(data)
    balance = st.get("left_right_balance_db")
    if plan in ("left", "right") and balance is not None and abs(balance) >= 20.0:
        findings.append({
            "id": "one_sided", "severity": "moderate",
            "what": "The voice is only in the %s channel; the other side is empty. That happens when one microphone "
                    "is recorded onto a stereo (two-input) track." % plan,
            "fix": "For this take: click the clip, press F2 (Item properties) and set 'Channel mode' to 'Mono (%s)'; "
                   "then run the vocal pass again. For new takes: set the track's record input to Mono and pick "
                   "the input the microphone is plugged into (the input whose meter moves when you speak; on a "
                   "4th Gen Scarlett Solo, for example, that is Input 2 on the back). Step by step for any "
                   "interface: 'AUDIO INTERFACE SETUP.txt' in the RapReaper folder." % plan})
        hints["channel"] = plan
    elif plan == "left" and st.get("correlation") is not None and st["correlation"] < -0.5:
        findings.append({
            "id": "out_of_phase", "severity": "moderate",
            "what": "Left and right of this track are opposite copies of each other, so the voice disappears when "
                    "the song is played in mono (phones, clubs, some speakers).",
            "fix": "Click the clip, press F2 (Item properties) and set 'Channel mode' to 'Mono (left)'; then run the "
                   "vocal pass again."})
        hints["channel"] = "left"
    # recording level
    pk = levels["sample_peak_dbfs"]
    if pk is not None and pk > -1.0 and report["clipping"]["clipped_runs"] < 3:
        findings.append({
            "id": "recorded_hot", "severity": "minor",
            "what": "The recording peaks at %.1f dBFS, very close to the ceiling." % pk,
            "fix": "Next time turn the interface gain down so the loudest lines peak around -12 dBFS (and never reach -6)."})
    elif pk is not None and pk < -24.0:
        findings.append({
            "id": "recorded_quiet", "severity": "minor",
            "what": "The recording is quiet (peaks at %.1f dBFS), so the noise floor is closer than it needs to be." % pk,
            "fix": "Next time turn the interface gain up so the loudest lines peak around -12 dBFS (and never reach -6)."})

    # noise
    if noise_info.get("measured"):
        snr = noise_info["signal_to_noise_db"]
        hints["noise_floor_dbfs"] = noise_info["floor_dbfs"]
        if snr < 35.0:
            findings.append({
                "id": "noise", "severity": "severe",
                "what": "Background noise is only %.0f dB below the voice (%s)." % (snr, noise_info["character"]),
                "fix": "A downward expander turns the gaps down; a quieter room or closer mic fixes the cause."})
            hints["expander"] = True
        elif snr < 45.0:
            findings.append({
                "id": "noise", "severity": "moderate",
                "what": "Background noise is %.0f dB below the voice (%s)." % (snr, noise_info["character"]),
                "fix": "Compression will bring it up, so a gentle downward expander is used to keep the gaps quiet."})
            hints["expander"] = True
        else:
            hints["expander"] = False
    else:
        hints["expander"] = False

    # hum
    hum = _hum(mono, sr, act_level)
    report["hum"] = hum
    if hum.get("found"):
        rel = hum.get("relative_to_voice_db")
        if rel is not None and rel <= HUM_LEAVE_ALONE_DB:
            findings.append({
                "id": "hum_faint", "severity": "info",
                "what": "A faint mains hum at %d Hz, %.0f dB under the voice." % (hum["mains_hz"], abs(rel)),
                "fix": "Left alone: this far under the voice it is not heard in a mix, and cutting it would take a "
                       "little of the voice's low end with it."})
        else:
            sev = "severe" if (rel is not None and rel > -35.0) else "moderate"
            findings.append({
                "id": "hum", "severity": sev,
                "what": "Mains hum at %d Hz and its harmonics (%s Hz), %s dB relative to the voice."
                        % (hum["mains_hz"], ", ".join(str(h) for h in hum["harmonics_hz"][:5]),
                           "%.0f" % rel if rel is not None else "?"),
                "fix": "Very narrow EQ cuts (notches) at those exact frequencies take it out; the voice loses next to nothing."})
            # always notch the mains frequency itself, then the strongest harmonics
            hints["hum_notches_hz"] = [hum["mains_hz"]] + [h for h in hum["harmonics_hz"] if h != hum["mains_hz"]][:3]
            # deep enough to put the hum about 60 dB under the voice, and no deeper
            depth = 12.0 if rel is None else min(max(rel + 62.0, 6.0), 18.0)
            hints["hum_notch_db"] = -float(round(depth))

    # rumble + pitch-based high-pass choice
    f0_floor = _pitch_floor(mono, sr, active, fr)
    # "Rumble" is what lies BELOW the voice: under 70 Hz, or under four fifths of the lowest note
    # of a deep voice (whose own lowest notes must not be mistaken for rumble).
    rumble_top = 70.0 if f0_floor is None else min(max(0.8 * f0_floor, 40.0), 70.0)
    low = fr.band_power(15.0, rumble_top)
    recorded = fr.level_db >= DIGITAL_SILENCE_DB          # empty timeline is not part of the recording
    low_level = float(_pdb(low[recorded].mean() if recorded.any() else low.mean()))
    rumble_rel = low_level - act_level if act_level is not None else None
    # One filter (12 dB per octave) is 3 dB down at its corner; two in a row are 6 dB down there.
    # The corner therefore sits under the lowest note: 15 % under for one filter, 25 % for two.
    hpf, hpf_steep = 85.0, 75.0
    if f0_floor is not None:
        hpf = min(max(0.85 * f0_floor, 60.0), 120.0)
        hpf_steep = min(max(0.75 * f0_floor, 60.0), 110.0)
    hpf = float(int(math.floor(hpf / 5.0)) * 5)
    hpf_steep = float(int(math.floor(hpf_steep / 5.0)) * 5)
    plos_top = 70.0 if f0_floor is None else min(max(0.75 * f0_floor, 50.0), 80.0)
    plos = _plosives(fr, active, act_level, plos_top)
    report["plosives"] = plos
    if plos["count"] >= 3 and plos["per_minute"] and plos["per_minute"] >= 6.0:
        findings.append({
            "id": "plosives", "severity": "moderate" if plos["per_minute"] < 15 else "severe",
            "what": "%d low thumps from P and B sounds (about %.0f a minute)." % (plos["count"], plos["per_minute"]),
            "fix": "A steeper high-pass filter under the voice's lowest note tames them; a pop filter and aiming "
                   "the mic a little off-axis prevents them."})
    report["rumble"] = {"below_hz": _r(rumble_top, 0), "level_dbfs": _r(low_level, 1),
                        "relative_to_voice_db": _r(rumble_rel, 1),
                        "lowest_voice_pitch_hz": _r(f0_floor, 0)}
    if rumble_rel is not None and rumble_rel > -30.0:
        findings.append({
            "id": "rumble", "severity": "severe" if rumble_rel > -20.0 else "moderate",
            "what": "Low rumble below %.0f Hz is only %.0f dB under the voice." % (rumble_top, abs(rumble_rel)),
            "fix": "The high-pass filter removes what lies below the voice's lowest note."})
    hints["highpass_hz"] = hpf
    hints["highpass_steep_hz"] = hpf_steep

    # Sibilance first. Boom, mud and presence are then judged on the voiced part only, so a loud
    # or de-essed "S" does not change the reading of how clear the voice is. The two top bands are
    # judged on everything that sounds, S and T included, because that is how the reference speech
    # spectrum was measured: most of a voice's energy up there IS its consonants.
    sib, sib_mask = _sibilance(fr, active)
    report["sibilance"] = sib
    voiced = active & ~sib_mask
    vbl = band_levels_db(fr.mean_spectrum(voiced), fr.freqs) if voiced.sum() >= 8 else bl
    body = vbl.get("body")
    tone = {}
    if body is not None:
        for name in ("boom", "mud", "presence"):
            if vbl.get(name) is not None:
                tone[name] = vbl[name] - body - SPEECH_REF_REL_BODY[name]
        for name in ("sibilance", "air"):
            if bl.get(name) is not None and bl.get("body") is not None:
                tone[name] = bl[name] - bl["body"] - SPEECH_REF_REL_BODY[name]
    report["tone_vs_natural_speech_db"] = {k: _r(v, 1) for k, v in tone.items()}
    if tone.get("mud", 0.0) > 4.0:
        findings.append({
            "id": "muddy", "severity": "moderate" if tone["mud"] > 7.0 else "minor",
            "what": "The 250-500 Hz area is %.0f dB heavier than natural speech (muddy / boxy)." % tone["mud"],
            "fix": "A broad EQ cut around 300 Hz clears it."})
    if tone.get("boom", 0.0) > 5.0:
        findings.append({
            "id": "boomy", "severity": "moderate" if tone["boom"] > 8.0 else "minor",
            "what": "The 100-250 Hz area is %.0f dB heavier than natural speech (boomy; usually the mic being very close)."
                    % tone["boom"],
            "fix": "A low-shelf cut around 180 Hz evens it out."})
    if tone.get("presence", 0.0) < -5.0:
        findings.append({
            "id": "dull", "severity": "minor",
            "what": "The 2-5 kHz clarity area is %.0f dB lower than natural speech (dull, words less clear)."
                    % abs(tone["presence"]),
            "fix": "A gentle wide boost around 3.5 kHz brings the words forward."})
    if tone.get("presence", 0.0) > 6.0:
        findings.append({
            "id": "harsh", "severity": "minor",
            "what": "The 2-5 kHz area is %.0f dB higher than natural speech (can sound harsh)." % tone["presence"],
            "fix": "A small cut around 3 kHz softens it."})
    hints["tone_offsets_db"] = {k: _r(v, 1) for k, v in tone.items()}

    # narrow resonances
    res = _resonances(fr.mean_spectrum(voiced if voiced.sum() >= 8 else active), fr.freqs)
    report["resonances"] = res
    for r in res[:2]:
        findings.append({
            "id": "resonance_%d" % r["hz"], "severity": "minor",
            "what": "A narrow peak at about %d Hz sticks out %.0f dB (a ringing or honky tone)." % (r["hz"], r["prominence_db"]),
            "fix": "A narrow EQ cut at that frequency."})
    hints["resonance_cuts"] = [{"hz": r["hz"], "db": -float(min(max(r["prominence_db"] - 3.0, 2.0), 4.0))} for r in res[:2]]

    # sibilance (measured above)
    # The figure compares the hissy band on the strongest S sounds with the typical level of the
    # voiced sounds. Ordinary close-miked speech reads about 0 to +5 on it (a bright audiobook
    # voice: +5); from about +6 the S sounds start to hurt, and under about +2 nobody notices them.
    if sib and sib.get("found"):
        ex = sib["excess_db"]
        if ex > SIBILANCE_STRONG_DB:
            sev, target = "moderate", 5.0
        elif ex > SIBILANCE_NOTICEABLE_DB:
            sev, target = "minor", 3.5
        else:
            sev, target = None, 0.0
        if sev:
            findings.append({
                "id": "sibilance", "severity": sev,
                "what": "S and T sounds are %s: %+.0f dB relative to the voice, centred near %d Hz."
                        % ("strong" if sev == "moderate" else "a little forward", ex, sib["centre_hz"]),
                "fix": "A de-esser turns down just those moments."})
        hints["deess"] = {"centre_hz": sib["centre_hz"], "target_reduction_db": target}
    else:
        hints["deess"] = None

    # dynamics
    if dyn and dyn.get("level_range_db") is not None:
        rng = dyn["level_range_db"]
        if rng >= 10.0:
            amount = "heavy"
        elif rng >= 6.0:
            amount = "medium"
        else:
            amount = "light"
        hints["compression"] = {"level_range_db": rng, "suggested_amount": amount,
                                "peak_p95_dbfs": dyn["active_peak_p95_dbfs"],
                                "level_p50_dbfs": dyn["active_level_p50_dbfs"]}
        if rng >= 7.5:          # a finished, compressed vocal usually sits around 4-7 dB
            findings.append({
                "id": "uneven_level", "severity": "minor" if rng < 10.0 else "moderate",
                "what": "Loud and quiet lines are %.0f dB apart, so some words will jump out and others get lost." % rng,
                "fix": "Compression evens this out (two gentle stages sound more natural than one hard one)."})
    report["treatment_hints"] = hints


def _beat_part(report, findings, levels, loud, st):
    tp = levels["true_peak_dbtp"]
    if tp is not None and tp > -0.3:
        findings.append({
            "id": "beat_no_headroom", "severity": "moderate",
            "what": "The beat peaks at %.1f dBTP -- it is already mastered loud, leaving no room for a vocal." % tp,
            "fix": "Turn the beat track down (start around -6 dB) and mix the vocal against it; the final "
                   "limiter brings the whole song back up."})
    if loud.get("integrated_lufs") is not None and loud["integrated_lufs"] > -11.0:
        findings.append({
            "id": "beat_loud", "severity": "info",
            "what": "The beat is %.1f LUFS, which is finished-master loudness." % loud["integrated_lufs"],
            "fix": "That is normal for a leased two-track beat. Ask the producer for the unmastered version or "
                   "track-outs (stems) if you can; they mix better."})
    if st and st.get("low_end_correlation_below_120hz") is not None and st["low_end_correlation_below_120hz"] < 0.5:
        findings.append({
            "id": "wide_low_end", "severity": "minor",
            "what": "The low end is wide in stereo (correlation %.2f below 120 Hz); bass can thin out on phones and club systems."
                    % st["low_end_correlation_below_120hz"],
            "fix": "Nothing to fix on a two-track beat; just check the mix in mono."})


def _mix_part(report, findings, levels, loud, st, bl, total):
    tp = levels["true_peak_dbtp"]
    if tp is not None and tp > -0.9:
        findings.append({
            "id": "true_peak_high", "severity": "moderate" if tp > 0.0 else "minor",
            "what": "True peak is %.1f dBTP; streaming services recommend staying at or below -1 dBTP." % tp,
            "fix": "Lower the limiter ceiling to -1.0 dB."})
    cf = levels["crest_factor_db"]
    if cf is not None and cf < 7.0:
        findings.append({
            "id": "over_limited", "severity": "moderate",
            "what": "The gap between peaks and average level is only %.1f dB -- the mix is squashed and may sound distorted or flat." % cf,
            "fix": "Ease off the limiter (raise its threshold) until the punch returns."})
    if st and st.get("low_end_correlation_below_120hz") is not None and st["low_end_correlation_below_120hz"] < 0.5:
        findings.append({
            "id": "wide_low_end", "severity": "minor",
            "what": "The low end is wide in stereo (correlation %.2f below 120 Hz)." % st["low_end_correlation_below_120hz"],
            "fix": "Keep kick, 808 and bass in the centre; check the mix in mono."})
    if st and st.get("correlation") is not None and st["correlation"] < 0.0:
        findings.append({
            "id": "phase", "severity": "moderate",
            "what": "Left and right are out of phase overall (correlation %.2f); parts of the mix vanish in mono." % st["correlation"],
            "fix": "Look for a stereo widener or an inverted-phase track and remove it."})


# ---------------------------------------------------------------------------
# Comparisons
# ---------------------------------------------------------------------------

def _get(d, *keys):
    for k in keys:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def _delta(a, b, nd=1):
    if a is None or b is None:
        return None
    return _r(b - a, nd)


def compare(before, after):
    """Plain summary of what changed between two analyze() reports."""
    out = {
        "integrated_lufs": [_get(before, "loudness", "integrated_lufs"), _get(after, "loudness", "integrated_lufs")],
        "true_peak_dbtp": [_get(before, "levels", "true_peak_dbtp"), _get(after, "levels", "true_peak_dbtp")],
        "crest_factor_db": [_get(before, "levels", "crest_factor_db"), _get(after, "levels", "crest_factor_db")],
        "level_range_db": [_get(before, "dynamics", "level_range_db"), _get(after, "dynamics", "level_range_db")],
        "noise_floor_dbfs": [_get(before, "noise", "floor_dbfs"), _get(after, "noise", "floor_dbfs")],
        "signal_to_noise_db": [_get(before, "noise", "signal_to_noise_db"), _get(after, "noise", "signal_to_noise_db")],
        "rumble_rel_voice_db": [_get(before, "rumble", "relative_to_voice_db"), _get(after, "rumble", "relative_to_voice_db")],
        "hum_level_dbfs": [_get(before, "hum", "level_dbfs"), _get(after, "hum", "level_dbfs")],
        "sibilance_excess_db": [_get(before, "sibilance", "excess_db"), _get(after, "sibilance", "excess_db")],
    }
    changes = {k: {"before": v[0], "after": v[1], "change": _delta(v[0], v[1])} for k, v in out.items()}
    bb = _get(before, "spectrum", "band_levels_rel_total_db") or {}
    ab = _get(after, "spectrum", "band_levels_rel_total_db") or {}
    bands = {}
    for name, _lo, _hi in BANDS:
        bands[name] = _delta(bb.get(name), ab.get(name))
    before_ids = {f["id"]: f["severity"] for f in (before.get("findings") or [])}
    after_ids = {f["id"]: f["severity"] for f in (after.get("findings") or [])}
    fixed = sorted(i for i in before_ids if i not in after_ids)
    remaining = sorted(i for i in after_ids if i in before_ids)
    new = sorted(i for i in after_ids if i not in before_ids)
    return {"changes": changes, "band_balance_change_db": bands,
            "problems_fixed": fixed, "problems_remaining": remaining, "problems_new": new}


def _block_levels(mono, sr, seconds=0.05):
    blk = max(1, int(seconds * sr))
    nblk = len(mono) // blk
    x = np.asarray(mono[: nblk * blk], dtype=np.float64).reshape(nblk, blk)
    return _pdb((x * x).mean(axis=1))


def _phrase_range(mono, sr, thr_db):
    ph = _phrase_levels(mono, sr, thr_db)
    if len(ph) < 5:
        return None
    return _r(np.percentile(ph, 92) - np.percentile(ph, 8), 1)


def gain_reduction(pre, post, sr):
    """How much a compressor/limiter turned things down between two captures
    of the SAME time range (make-up gain must be off). Positive = turned down."""
    a, b = _mono(pre), _mono(post)
    n = min(len(a), len(b))
    la, lb = _block_levels(a[:n], sr), _block_levels(b[:n], sr)
    if len(la) < 4:
        return {"measured": False}
    ref = _loud_ref(la, 0.05)
    active = la > ref - 30.0
    if active.sum() < 4:
        return {"measured": False}
    d = la[active] - lb[active]
    loud = la[active] >= np.percentile(la[active], 80)
    return {
        "measured": True,
        "on_loudest_parts_db": _r(float(np.mean(d[loud])), 1),
        "average_db": _r(float(np.mean(d)), 1),
        "max_db": _r(float(np.percentile(d, 98)), 1),
        "level_range_before_db": _phrase_range(a[:n], sr, float(ref) - 30.0),
        "level_range_after_db": _phrase_range(b[:n], sr, float(_loud_ref(lb, 0.05)) - 30.0),
        "seconds_of_voice": _r(float(active.sum()) * 0.05, 1),
    }


def expander_effect(pre, post, sr):
    """What a downward expander did between two captures of the same time range, in dB
    (negative = turned down): to the gaps, to the voice as a whole, and to its quietest quarter
    (word endings, soft syllables), which is where an expander set too high does its damage."""
    a, b = _mono(pre), _mono(post)
    n = min(len(a), len(b))
    fa, fb = _Frames(a[:n], sr), _Frames(b[:n], sr)
    active, noise, _l95, _floor = _activity(fa, "vocal")
    if active.sum() < 8:
        return {"measured": False}
    d = fb.level_db - fa.level_db
    lv = fa.level_db[active]
    quiet = lv <= np.percentile(lv, 25)
    return {
        "measured": True,
        "gaps_db": _r(float(np.mean(d[noise])), 1) if noise.sum() >= 3 else None,
        "voice_db": _r(float(np.mean(d[active])), 1),
        "quiet_words_db": _r(float(np.mean(d[active][quiet])), 1),
    }


def sibilance_reduction(pre, post, sr):
    """How much a de-esser turned the S sounds down (and whether it left the
    rest alone) between two captures of the same time range."""
    a, b = _mono(pre), _mono(post)
    n = min(len(a), len(b))
    fa, fb = _Frames(a[:n], sr), _Frames(b[:n], sr)
    active, _noise, _l95, _floor = _activity(fa, "vocal")
    info, sib = _sibilance(fa, active)
    if not info or not info.get("found"):
        return {"measured": False, "reason": "no clear S sounds in this range"}
    hi_a, hi_b = _pdb(fa.band_power(5000.0, 10000.0)), _pdb(fb.band_power(5000.0, 10000.0))
    d = hi_a[sib] - hi_b[sib]
    strong = hi_a[sib] >= np.percentile(hi_a[sib], 60)
    others = active & ~sib
    side = float(np.mean(fa.level_db[others] - fb.level_db[others])) if others.sum() >= 3 else 0.0
    # the highs of ordinary (non-S) sounds: a de-esser set too low dulls these as well
    loud_hi = others & (hi_a >= np.percentile(hi_a[others], 50)) if others.sum() >= 6 else others
    dull = float(np.mean(hi_a[loud_hi] - hi_b[loud_hi])) if loud_hi.sum() >= 3 else 0.0
    return {
        "measured": True,
        "on_strong_s_sounds_db": _r(float(np.mean(d[strong])), 1),
        "on_all_s_sounds_db": _r(float(np.mean(d)), 1),
        "on_everything_else_db": _r(side, 1),
        "on_highs_of_other_sounds_db": _r(dull, 1),
        "centre_hz": info["centre_hz"],
    }


def deess_guide(samples, sr, split_hz):
    """What a de-esser's detector will see above `split_hz`: the peak level on
    strong S sounds and on ordinary voiced sound (dBFS). Used to pick a
    sensible first threshold; the result is then checked by measurement."""
    mono = _mono(samples)
    fr = _Frames(mono, sr)
    active, _noise, _l95, _floor = _activity(fr, "vocal")
    info, sib = _sibilance(fr, active)
    if not info or not info.get("found"):
        return None
    sos = signal.butter(1, min(float(split_hz), sr * 0.45), btype="high", fs=sr, output="sos")
    env = np.abs(signal.sosfilt(sos, signal.sosfilt(sos, mono.astype(np.float64))))
    if len(env) < fr.nfft:
        env = np.concatenate([env, np.zeros(fr.nfft - len(env))])
    peaks = np.lib.stride_tricks.sliding_window_view(env, fr.nfft)[::fr.hop].max(axis=1)[:fr.n]
    hi = _pdb(fr.band_power(5000.0, 10000.0))
    strong = sib & (hi >= np.percentile(hi[sib], 60))
    voiced = active & ~sib
    out = {"strong_s_peak_dbfs": _r(float(_adb(np.median(peaks[strong]))), 1)}
    if voiced.sum() >= 3:
        out["voiced_peak_dbfs"] = _r(float(_adb(np.percentile(peaks[voiced], 90))), 1)
    return out


_THIRDS = [125, 160, 200, 250, 315, 400, 500, 630, 800, 1000, 1250, 1600, 2000,
           2500, 3150, 4000, 5000, 6300, 8000]


def masking(vocal, beat, sr):
    """Where the beat covers the vocal. Both arrays must be captures of the
    same time range. Levels are measured only while the vocal is sounding."""
    v, b = _mono(vocal), _mono(beat)
    n = min(len(v), len(b))
    fv, fb = _Frames(v[:n], sr), _Frames(b[:n], sr)
    active, _noise, _l95, _floor = _activity(fv, "vocal")
    if active.sum() < 8:
        return {"measured": False, "reason": "the vocal is silent in this range"}
    sv, sb = fv.mean_spectrum(active), fb.mean_spectrum(active)
    rows = []
    for fc in _THIRDS:
        lo, hi = fc / 2 ** (1 / 6.0), fc * 2 ** (1 / 6.0)
        m = (fv.freqs >= lo) & (fv.freqs < hi)
        if not m.any():
            continue
        lv, lb = float(_pdb(sv[m].sum())), float(_pdb(sb[m].sum()))
        rows.append({"hz": fc, "vocal_dbfs": _r(lv, 1), "beat_dbfs": _r(lb, 1), "beat_minus_vocal_db": _r(lb - lv, 1)})
    key = [r for r in rows if 1600 <= r["hz"] <= 5000]
    key_diff = float(np.mean([r["beat_minus_vocal_db"] for r in key])) if key else None
    worst = max(key, key=lambda r: r["beat_minus_vocal_db"]) if key else None
    lv_all, lb_all = float(_pdb(sv.sum())), float(_pdb(sb.sum()))
    # Loudness of each while the vocal is sounding: K-weighted, ungated, and summed over left and
    # right the way BS.1770 does it, so a wide stereo beat is not under-read against a centred voice.
    sos = _k_weight_sos(sr)
    hop = fv.hop
    mask_samples = np.repeat(active, hop)[:n]
    if len(mask_samples) < n:
        mask_samples = np.concatenate([mask_samples, np.zeros(n - len(mask_samples), bool)])

    def while_sounding(x):
        x = _to_2d(x)[:n]
        total = 0.0
        for c in range(min(x.shape[1], 2)):
            k = signal.sosfilt(sos, x[:, c].astype(np.float64))
            total += float(np.mean(k[mask_samples] ** 2))
        return -0.691 + float(_pdb(total))

    lufs_v, lufs_b = while_sounding(vocal), while_sounding(beat)
    return {
        "measured": True,
        "vocal_level_dbfs": _r(lv_all, 1), "beat_level_dbfs": _r(lb_all, 1),
        "vocal_loudness_lufs": _r(lufs_v, 1), "beat_loudness_lufs": _r(lufs_b, 1),
        "vocal_minus_beat_lu": _r(lufs_v - lufs_b, 1),
        "clarity_band_beat_minus_vocal_db": _r(key_diff, 1),
        "worst_band_hz": worst["hz"] if worst else None,
        "bands": rows,
    }


# ---------------------------------------------------------------------------
# Breaths and single S sounds, located in time (for ducking)
# ---------------------------------------------------------------------------

_EV_HOP = 0.010            # 10 ms steps: fine enough to place a duck around one S sound
_EV_WIN = 0.030


def _event_frames(mono, sr):
    """Per 10 ms: level (dBFS), the level in three bands, and how strongly pitched the sound is (0..1)."""
    x = np.asarray(mono, dtype=np.float64)
    hop, win = int(round(_EV_HOP * sr)), int(round(_EV_WIN * sr))
    if len(x) < win:
        x = np.concatenate([x, np.zeros(win - len(x))])
    nfft = 1 << int(np.ceil(np.log2(win)))
    view = np.lib.stride_tricks.sliding_window_view(x, win)[::hop]
    w = np.hanning(win)
    scale = 2.0 / (win * win * float(np.mean(w ** 2)))
    freqs = np.fft.rfftfreq(nfft, 1.0 / sr)
    bands = {"low": (80.0, 300.0), "mid": (300.0, 3000.0), "breath": (400.0, 4000.0), "hi": (5000.0, 10000.0)}
    masks = {k: (freqs >= a) & (freqs < b) for k, (a, b) in bands.items()}
    out = {k: np.empty(view.shape[0]) for k in list(bands) + ["level", "centroid"]}
    voicing = np.empty(view.shape[0])
    lag_lo, lag_hi = int(sr / 400.0), int(sr / 70.0)
    sos = signal.butter(2, [70.0, 1000.0], btype="band", fs=sr, output="sos")
    xl = signal.sosfilt(sos, x)
    lview = np.lib.stride_tricks.sliding_window_view(xl, win)[::hop]
    step = 2048
    for i in range(0, view.shape[0], step):
        blk = view[i:i + step] * w
        p = np.abs(np.fft.rfft(blk, nfft, axis=1)) ** 2 * scale
        tot = p.sum(axis=1)
        out["level"][i:i + step] = _pdb(np.mean(view[i:i + step] ** 2, axis=1))
        for k, m in masks.items():
            out[k][i:i + step] = _pdb(p[:, m].sum(axis=1))
        band = (freqs >= 300.0) & (freqs < 12000.0)
        out["centroid"][i:i + step] = (p[:, band] * freqs[band]).sum(axis=1) / np.maximum(p[:, band].sum(axis=1), 1e-30)
        # pitchedness: the highest normalised autocorrelation in the voice's pitch range (band-limited signal)
        lb = lview[i:i + step] * w
        spec = np.fft.rfft(lb, 2 * nfft, axis=1)
        ac = np.fft.irfft(np.abs(spec) ** 2, axis=1)[:, :lag_hi + 1]
        voicing[i:i + step] = np.clip(ac[:, lag_lo:lag_hi + 1].max(axis=1) / np.maximum(ac[:, 0], 1e-30), 0.0, 1.0)
    out["voicing"] = voicing
    out["times"] = (np.arange(view.shape[0]) * hop + win / 2.0) / sr
    return out


def _runs_inclusive(mask, bridge_frames=0):
    """[(first, last)] index runs of True, joining runs separated by at most bridge_frames False frames."""
    idx = np.flatnonzero(mask)
    if idx.size == 0:
        return []
    runs, a, b = [], idx[0], idx[0]
    for k in idx[1:]:
        if k - b <= bridge_frames + 1:
            b = k
        else:
            runs.append((a, b))
            a = b = k
    runs.append((a, b))
    return runs


def _voice_reference(f):
    """Level of the loud, pitched parts (the voice), and of the quiet gaps (the room)."""
    lv = f["level"]
    audible = lv > DIGITAL_SILENCE_DB
    if audible.sum() < 10:
        return None, None
    voiced = audible & (f["voicing"] > 0.55)
    ref = _loud_ref(lv[voiced] if voiced.sum() >= 10 else lv[audible], _EV_HOP)
    real = lv > DIGITAL_SILENCE_DB + 1.0
    floor = float(np.percentile(lv[real], 5)) if real.any() else -120.0
    return ref, max(floor, -120.0)


def find_s_sounds(samples, sr, offset=0.0):
    """S / SH / T sounds located in time: runs where the highs (5-10 kHz) outweigh the voice's own range and
    are near the loudest highs of the passage. Times in seconds from `offset`."""
    f = _event_frames(_mono(samples), sr)
    ref, floor = _voice_reference(f)
    if ref is None:
        return []
    hi, mid, lv = f["hi"], f["mid"], f["level"]
    audible = lv > max(floor + 6.0, DIGITAL_SILENCE_DB)
    if not audible.any():
        return []
    hi_ref = float(np.percentile(hi[audible], 98))
    cand = audible & (hi - mid > 4.0) & (hi > hi_ref - 18.0)
    out = []
    for a, b in _runs_inclusive(cand, bridge_frames=1):
        t0 = f["times"][a] - _EV_HOP
        t1 = f["times"][b] + _EV_HOP
        if not (0.025 <= t1 - t0 <= 0.4):
            continue
        seg = slice(a, b + 1)
        out.append({"start_seconds": _r(offset + max(0.0, t0), 3), "end_seconds": _r(offset + t1, 3),
                    "seconds": _r(t1 - t0, 3), "highs_peak_dbfs": _r(float(np.max(hi[seg])), 1),
                    "highs_over_voice_db": _r(float(np.max(hi[seg] - mid[seg])), 1)})
    return out


def level_change(before, after, sr, ranges, offset=0.0, edge=0.0, exclude=()):
    """Mean level difference after - before (dB) inside each (start, end) range (seconds from `offset`, `edge`
    seconds left off at both ends), and over everything outside all ranges. For checking a volume duck."""
    a, b = _mono(before), _mono(after)
    n = min(len(a), len(b))
    a, b = a[:n].astype(np.float64), b[:n].astype(np.float64)
    inside_all = np.zeros(n, bool)
    rows = []
    for s, e in ranges:
        i0 = max(0, int(round((s - offset + edge) * sr)))
        i1 = min(n, int(round((e - offset - edge) * sr)))
        j0 = max(0, int(round((s - offset) * sr)))
        j1 = min(n, int(round((e - offset) * sr)))
        inside_all[j0:j1] = True
        pa, pb = (float(np.mean(a[i0:i1] ** 2)), float(np.mean(b[i0:i1] ** 2))) if i1 - i0 > 8 else (0.0, 0.0)
        rows.append(_r(float(_pdb(pb) - _pdb(pa)), 2) if pa > 1e-12 and pb > 0 else None)
    for s, e in exclude:                       # (other dips: neither inside nor outside)
        inside_all[max(0, int(round((s - offset) * sr))):max(0, min(n, int(round((e - offset) * sr))))] = True
    out_mask = ~inside_all
    pa, pb = float(np.mean(a[out_mask] ** 2)) if out_mask.any() else 0.0, float(np.mean(b[out_mask] ** 2)) if out_mask.any() else 0.0
    outside = _r(float(_pdb(pb) - _pdb(pa)), 2) if pa > 1e-12 and pb > 0 else None
    return {"inside_db": rows, "outside_db": outside}


# ---------------------------------------------------------------------------
# Gaps and breaths (for cutting empty air)
# ---------------------------------------------------------------------------

def _runs(mask):
    """[(first, last_exclusive)] of the True runs in a boolean array."""
    m = np.asarray(mask, bool)
    if m.size == 0:
        return []
    d = np.diff(np.concatenate(([0], m.astype(np.int8), [0])))
    starts, ends = np.where(d == 1)[0], np.where(d == -1)[0]
    return list(zip(starts.tolist(), ends.tolist()))


def _hiss(fr):
    """Per frame: dB of energy above 1.5 kHz against energy under 1 kHz. A vowel is strongly negative, an S
    strongly positive, a breath a little positive."""
    hi = fr.band_power(1500.0, 8000.0)
    lo = fr.band_power(80.0, 1000.0)
    return _pdb(hi + 1e-30) - _pdb(lo + 1e-30)


def _breath_frames(fr, lv, l95, floor, hiss):
    """Frames that look like a breath: soft (10 dB or more under the loud parts, 6 dB or more over the room
    tone), hissy rather than voiced, lasting 0.08-0.9 s, and coming out of a pause rather than out of a word
    (an S or an F sits between vowels; a breath follows at least 0.1 s of near-silence)."""
    hop_s = fr.hop / float(fr.sr)
    soft = (lv >= floor + 6.0) & (lv < l95 - 10.0) & (hiss > -2.0)
    mask = np.zeros(fr.n, bool)
    rows = []
    quiet = lv < floor + 6.0
    need = max(1, int(round(0.1 / hop_s)))
    for a, b in _runs(soft):
        dur = (b - a) * hop_s
        if dur < 0.08 or dur > 0.9:
            continue
        before = quiet[max(0, a - need):a]
        if a > 0 and before.size and float(before.mean()) < 0.6:
            continue
        # a sibilant is louder near its middle and short; a breath rolls on. Reject runs that are mostly very hissy
        # AND short: that is an S at the start of a phrase.
        if dur < 0.15 and float(np.median(hiss[a:b])) > 15.0:
            continue
        mask[a:b] = True
        rows.append((a, b))
    return mask, rows


def _voice_map(mono, sr, words_db=24.0, cut_breaths=True):
    """Frames where the voice itself is sounding, and the levels the decision rests on.

    words_db: a frame quieter than this many dB under the loud parts does not count as a word; the default
    keeps every ordinary quiet word of a rap take and leaves lip noise and room tone out.
    cut_breaths: frames that look like a breath (soft and hissy, coming out of a pause) are not voice either."""
    fr = _Frames(mono, sr)
    lv = fr.level_db
    audible = lv > -90.0
    if not audible.any():
        fr.hiss, fr.breath_rows, fr.breath_mask = None, [], np.zeros(fr.n, bool)     # digital silence: no voice, no levels
        return fr, np.zeros(fr.n, bool), None, None, None
    l95 = _loud_ref(lv[audible], fr.hop / float(fr.sr))
    empty = lv < DIGITAL_SILENCE_DB
    real = ~empty
    floor = float(np.percentile(lv[real], 5)) if real.any() else -120.0
    thr = max(l95 - float(words_db), floor + 10.0)
    voice = lv >= thr
    hiss = _hiss(fr)
    bmask, brows = _breath_frames(fr, lv, l95, floor, hiss)
    if cut_breaths:
        voice &= ~bmask
    # a dip of under 120 ms inside a word (a stop consonant) is still the word -- unless it is a breath
    hole = int(round(0.12 * sr / fr.hop))
    for a, b in _runs(~voice):
        if b - a <= hole and a > 0 and b < fr.n and not (cut_breaths and bmask[a:b].any()):
            voice[a:b] = True
    fr.hiss = hiss
    fr.breath_rows = brows
    fr.breath_mask = bmask
    return fr, voice, l95, floor, thr


def find_gaps(mono, sr, min_gap=0.5, keep_before=0.08, keep_after=0.20, words_db=24.0, cut_breaths=True):
    """The stretches of a recording with no voice in them, as (start, end) seconds within `mono`.

    Each gap is reported with the part of it that can be cut: the voice before it keeps `keep_after`
    seconds of tail, the voice after it keeps `keep_before` seconds of run-up (the first consonant). Gaps
    shorter than `min_gap` are left alone. With cut_breaths, a breath taken in a pause counts as part of the
    pause. Returns a dict with the gaps, the levels used and a plain-language note about what counted as a
    word."""
    mono = np.asarray(mono, dtype=np.float64)
    total = len(mono) / float(sr)
    fr, voice, l95, floor, thr = _voice_map(mono, sr, words_db, cut_breaths)
    out = {"seconds": _r(total, 2), "gaps": [], "voice_seconds": 0.0}
    if l95 is None:
        out["note"] = "This audio is silent; there is nothing to cut."
        return out
    hop_s = fr.hop / float(sr)
    out.update({"loud_level_dbfs": _r(l95, 1), "noise_floor_dbfs": _r(floor, 1), "word_threshold_dbfs": _r(thr, 1),
                "voice_seconds": _r(float(voice.sum()) * hop_s, 2)})
    gaps = []
    for a, b in _runs(~voice):
        g0 = 0.0 if a == 0 else max(0.0, fr.times[a] - hop_s / 2.0)
        g1 = total if b >= fr.n else min(total, fr.times[b - 1] + hop_s / 2.0)
        if g1 - g0 < min_gap:
            continue
        c0 = g0 if a == 0 else g0 + keep_after
        c1 = g1 if b >= fr.n else g1 - keep_before
        if c1 - c0 < 0.1:
            continue
        seg = mono[int(g0 * sr):int(g1 * sr)]
        lvl = _pdb(float(np.mean(seg * seg))) if seg.size else -150.0
        breaths = int(sum(1 for (x, y) in fr.breath_rows if x >= a and y <= b))
        gaps.append({"gap_start": _r(g0, 3), "gap_end": _r(g1, 3), "cut_start": _r(c0, 3), "cut_end": _r(c1, 3),
                     "seconds_cut": _r(c1 - c0, 2), "level_dbfs": _r(lvl, 1), "breaths_inside": breaths,
                     "where": "before the first word" if a == 0 else ("after the last word" if b >= fr.n else "between words")})
    out["gaps"] = gaps
    out["seconds_cut_total"] = _r(sum(g["seconds_cut"] for g in gaps), 2)
    out["breaths_in_gaps"] = int(sum(g["breaths_inside"] for g in gaps))
    out["note"] = ("Anything quieter than %.0f dB under the loud parts counted as 'not a word'%s. Lower words_db to be "
                   "stricter about what is kept, raise it to keep more."
                   % (float(words_db), ", and so did a breath taken in a pause" if cut_breaths else ""))
    return out


def find_breaths(mono, sr, words_db=24.0):
    """Breath candidates: short, soft, hissy bursts in the pauses of a recording, as (start, end) seconds.

    Each candidate says how long before the next word it ends; one that runs straight into a word cannot be
    cut without touching the word, so for those a turn-down (duck) is the safer treatment."""
    mono = np.asarray(mono, dtype=np.float64)
    total = len(mono) / float(sr)
    fr, voice, l95, floor, thr = _voice_map(mono, sr, words_db, True)
    out = {"seconds": _r(total, 2), "breaths": []}
    if l95 is None:
        return out
    lv = fr.level_db
    hop_s = fr.hop / float(sr)
    onsets = [a for a, _ in _runs(voice)]
    found = []
    for a, b in fr.breath_rows:
        nxt = [o for o in onsets if o >= b]
        gap_to_word = (nxt[0] - b) * hop_s if nxt else None
        seg_lvl = float(_pdb(np.mean(10.0 ** (lv[a:b] / 10.0))))
        t0 = max(0.0, fr.times[a] - hop_s / 2.0)
        t1 = min(total, fr.times[b - 1] + hop_s / 2.0)
        found.append({"start": _r(t0, 3), "end": _r(t1, 3), "seconds": _r(t1 - t0, 2),
                      "level_below_voice_db": _r(l95 - seg_lvl, 1), "hiss_db": _r(float(np.median(fr.hiss[a:b])), 1),
                      "seconds_before_next_word": _r(gap_to_word, 2) if gap_to_word is not None else None,
                      "touches_word": bool(gap_to_word is not None and gap_to_word < 0.05)})
    out.update({"breaths": found, "loud_level_dbfs": _r(l95, 1), "noise_floor_dbfs": _r(floor, 1),
                "word_threshold_dbfs": _r(thr, 1)})
    return out


# ---------------------------------------------------------------------------
# 1.3.0: fades, how a mix comes across on small speakers, and whether the words can be heard
# ---------------------------------------------------------------------------

def apply_fades(data, sr, fade_in=0.0, fade_out=0.0):
    """A copy of the audio with an equal-power fade in and/or out (seconds)."""
    x = _to_2d(np.asarray(data, dtype=np.float64)).copy()
    n = x.shape[0]
    fi = int(round(max(0.0, fade_in) * sr))
    fo = int(round(max(0.0, fade_out) * sr))
    if fi > 0:
        fi = min(fi, n)
        x[:fi] *= np.sin(0.5 * np.pi * np.linspace(0.0, 1.0, fi, endpoint=False))[:, None]
    if fo > 0:
        fo = min(fo, n)
        x[n - fo:] *= np.cos(0.5 * np.pi * np.linspace(0.0, 1.0, fo, endpoint=False))[:, None]
    return x


def _phone_filter(mono, sr):
    """What a phone's own small speaker lets through: a high-pass at 350 Hz (2nd order) and a low-pass at 12 kHz."""
    sos_hp = signal.butter(2, 350.0 / (sr / 2.0), btype="highpass", output="sos")
    y = signal.sosfilt(sos_hp, mono)
    if sr / 2.0 > 12500.0:
        sos_lp = signal.butter(2, 12000.0 / (sr / 2.0), btype="lowpass", output="sos")
        y = signal.sosfilt(sos_lp, y)
    return y


def device_check(data, sr, vocal=None):
    """How a mix comes across away from good speakers.

    phone: the mix folded to mono and heard through a phone speaker (nothing under 350 Hz) -- its loudness, how much
    quieter that is than the full mix, and how much of the mix's energy was in the bass the phone cannot play.
    mono: the two channels summed (a Bluetooth speaker, many phones, a club with one speaker stack): the loss against
    stereo tells whether wide parts of the mix cancel.
    vocal (optional, a capture of the vocal alone over the same range): how far the vocal stands above the rest of the
    mix on the phone, in the 1-4 kHz band where words live. A mix that works on good speakers can lose its words here."""
    x = _to_2d(np.asarray(data, dtype=np.float64))
    n = x.shape[0]
    full = loudness_stats(x, sr)
    # a plain (L+R)/2: what a single speaker does (not fold(), which steps around cancellation on purpose for measuring)
    mono = x[:, :2].mean(axis=1) if x.shape[1] >= 2 else x[:, 0]
    # the mono and phone versions are measured as what a listener hears from them: the same signal in both ears
    # (dual mono), so a mix whose channels are identical reads the same loudness as its mono sum
    mono_l = loudness_stats(np.stack([mono, mono], axis=1), sr)
    phone = _phone_filter(mono, sr)
    phone_l = loudness_stats(np.stack([phone, phone], axis=1), sr)
    lufs = full.get("integrated_lufs")
    lufs_m = mono_l.get("integrated_lufs")
    lufs_p = phone_l.get("integrated_lufs")
    if lufs is not None and lufs_m is None:
        lufs_m = lufs - 60.0                 # the mono sum is silence: the two channels cancel each other completely
    if lufs is not None and lufs_p is None:
        lufs_p = lufs - 60.0
    # share of the energy under 120 Hz (what a small speaker cannot reproduce)
    fr = _Frames(mono, sr)
    tot = fr.power.sum()
    low = fr.band_power(20.0, 120.0).sum()
    low_share_db = float(_pdb(low / tot)) if tot > 0 and low > 0 else None
    # the bass a phone CAN play: its harmonics between 150 and 300 Hz, against the sub (40-120 Hz) it cannot. A kick or
    # 808 with no harmonics disappears on a phone; one with them is still heard as bass.
    sub = fr.band_power(40.0, 120.0).sum()
    harm = fr.band_power(150.0, 300.0).sum()
    harm_db = float(_pdb(harm) - _pdb(sub)) if sub > 0 and harm > 0 else None
    # the top on a phone: a small speaker makes 5-9 kHz (S sounds, hi-hats, cymbal wash) stand out against the 1-4 kHz
    # where the words are
    fp = _Frames(phone, sr)
    top = fp.band_power(5000.0, 9000.0).sum()
    mid = fp.band_power(1000.0, 4000.0).sum()
    top_db = float(_pdb(top) - _pdb(mid)) if top > 0 and mid > 0 else None
    out = {"full_mix_lufs": _r(lufs, 1), "mono_lufs": _r(lufs_m, 1),
           "mono_loss_db": _r((lufs - lufs_m) if (lufs is not None and lufs_m is not None) else None, 1),
           "phone_speaker_lufs": _r(lufs_p, 1),
           "phone_loss_db": _r((lufs - lufs_p) if (lufs is not None and lufs_p is not None) else None, 1),
           "phone_true_peak_dbtp": _r(true_peak_db(phone[:, None], sr), 2),
           "bass_under_120hz_share_db": _r(low_share_db, 1),
           "bass_harmonics_150to300_over_sub_db": _r(harm_db, 1),
           "phone_top_5to9k_over_words_1to4k_db": _r(top_db, 1)}
    findings = []
    if harm_db is not None and harm_db < -12.0:
        findings.append({"what": "The bass is %.0f dB weaker between 150 and 300 Hz than in the sub: on a phone, which plays nothing "
                                 "under about 350 Hz, the kick and 808 all but vanish." % (-harm_db), "severity": "moderate",
                         "fix": "Give the beat harmonics the phone can play: a gentle lift at 180-250 Hz on the beat's EQ (+2 dB, wide), "
                                "or saturation on the beat. phone_fixes does the EQ lift."})
    if top_db is not None and top_db > -3.0:
        findings.append({"what": "On a phone the top (5-9 kHz: S sounds, hi-hats) is within %.1f dB of the word band (1-4 kHz): "
                                 "a small speaker makes that sound harsh and spitty." % (-top_db if top_db < 0 else 0.0),
                         "severity": "minor",
                         "fix": "De-ess the vocal a little harder (vocal_chain stage deess, or set_plugin on deess), and dip the beat "
                                "1-2 dB around 6-8 kHz if the hi-hats are the cause."})
    if out["mono_loss_db"] is not None and out["mono_loss_db"] > 2.5:
        findings.append({"what": "Summed to mono the mix loses %.1f dB: wide stereo parts cancel each other on a phone or a single "
                                 "speaker." % out["mono_loss_db"], "severity": "moderate",
                         "fix": "Keep the vocal and the bass in the middle; narrow wide effects (chorus, doubler, stereo delay) or "
                                "turn their tracks down."})
    if out["phone_loss_db"] is not None and out["phone_loss_db"] > 7.0:
        findings.append({"what": "On a phone speaker the mix is %.1f dB quieter than on full-range speakers: most of its weight is in "
                                 "bass the phone cannot play." % out["phone_loss_db"], "severity": "moderate",
                         "fix": "Give the bass harmonics above 150 Hz (saturation on the beat, or a small boost at 150-250 Hz) rather "
                                "than more sub; keep the vocal's body (200-800 Hz) and clarity (2-4 kHz) up."})
    if out["phone_true_peak_dbtp"] is not None and out["phone_true_peak_dbtp"] > -0.5:
        findings.append({"what": "The phone-speaker version peaks at %.1f dBTP: taking the bass away leaves the mid-range peaks "
                                 "almost at full scale." % out["phone_true_peak_dbtp"], "severity": "minor",
                         "fix": "A little more limiter headroom (ceiling -1.5 dB) or less mid-range peak energy."})
    if vocal is not None:
        v2 = _to_2d(np.asarray(vocal, dtype=np.float64))
        v = v2[:, :2].mean(axis=1) if v2.shape[1] >= 2 else v2[:, 0]
        m = min(len(v), n)
        rest = mono[:m] - v[:m]
        vp, rp = _phone_filter(v[:m], sr), _phone_filter(rest, sr)
        fv, fr2 = _Frames(vp, sr), _Frames(rp, sr)
        active, _noise, _l95, _floor = _activity(fv, "vocal")
        if active.sum() >= 8:
            lo, hi = 1000.0, 4000.0
            pv = fv.band_power(lo, hi)[active].mean()
            pr = fr2.band_power(lo, hi)[active].mean()
            diff = float(_pdb(pv) - _pdb(pr)) if pv > 0 and pr > 0 else None
            out["phone_vocal_over_rest_1to4k_db"] = _r(diff, 1)
            if diff is not None and diff < 0.0:
                findings.append({"what": "On a phone, in the 1-4 kHz band where the words are, the rest of the mix is %.1f dB louder "
                                         "than the vocal while the vocal sounds." % (-diff), "severity": "moderate",
                                 "fix": "Cut the beat 2-4 dB around 2-3.5 kHz, or lift the vocal there; lower effect tracks that sit "
                                        "in that band."})
    out["findings"] = findings
    return out


def _syllable_rate_band(level_db_frames, hop_s):
    """How much the level moves at syllable rates (2-16 Hz): the modulation depth that lets a listener separate words.
    0 = a flat drone, 1 = fully articulated. (The idea behind the speech transmission index, in one number.)"""
    lv = np.asarray(level_db_frames, dtype=np.float64)
    if len(lv) < 16:
        return None
    env = 10.0 ** (lv / 10.0)
    env = env / (env.mean() + 1e-20)
    env = env - env.mean()
    spec = np.abs(np.fft.rfft(env * np.hanning(len(env))))
    f = np.fft.rfftfreq(len(env), hop_s)
    band = (f >= 2.0) & (f <= 16.0)
    allf = f > 0.3
    if not band.any() or spec[allf].sum() <= 0:
        return None
    return float(np.clip(spec[band].sum() / spec[allf].sum(), 0.0, 1.0))


def _brassiness_of(fv, active):
    sv = fv.mean_spectrum(active)
    res = _resonances(sv, fv.freqs, lo=900.0, hi=4500.0, max_n=4)
    pres = sv[(fv.freqs >= 1000.0) & (fv.freqs < 4000.0)].sum()
    body = sv[(fv.freqs >= 200.0) & (fv.freqs < 800.0)].sum()
    pres_over_body = float(_pdb(pres) - _pdb(body)) if pres > 0 and body > 0 else None
    # natural speech (Byrne et al.): 1-4 kHz sits roughly 8-12 dB under 200-800 Hz; a close-miked rap vocal with a
    # presence lift sits around -6; above -2 it starts to sound brassy/honky, with ringing peaks making it worse
    brass = 0.0
    if pres_over_body is not None:
        brass += max(0.0, (pres_over_body + 4.0) / 6.0)
    brass += sum(max(0.0, (p["prominence_db"] - 6.0) / 6.0) for p in res) * 0.5
    return {"ringing_peaks": res, "presence_over_body_db": pres_over_body, "brassiness": float(np.clip(brass, 0.0, 3.0))}


def brassiness(vocal, sr):
    """How brassy / honky a vocal sounds: 0 natural, 1 noticeable, 2+ honky; with the ringing peaks behind it."""
    v = _mono(_to_2d(np.asarray(vocal, dtype=np.float64)))
    fv = _Frames(v, sr)
    active, _noise, _l95, _floor = _activity(fv, "vocal")
    if active.sum() < 8:
        return {"measured": False, "ringing_peaks": [], "presence_over_body_db": None, "brassiness": None}
    out = _brassiness_of(fv, active)
    out["measured"] = True
    out["presence_over_body_db"] = _r(out["presence_over_body_db"], 1)
    out["brassiness"] = _r(out["brassiness"], 1)
    return out


def vocal_clarity(vocal, rest, sr, offset=0.0):
    """Can the words be heard? The vocal and the rest of the mix (everything but this vocal), captured over the same
    range, compared while the vocal sounds.

    Reports, in time: the stretches where the rest of the mix covers the vocal in the 1-4 kHz band (words likely lost),
    and overall: the vocal-over-rest ratio in that band and in the 4-8 kHz consonant band, the syllable-rate modulation
    of the vocal on its own and inside the mix (how much the words still stand out), a brassiness index (energy and
    ringing peaks in 1-4 kHz against the body of the voice) and the resonances behind it, with what to change first."""
    v = _mono(_to_2d(np.asarray(vocal, dtype=np.float64)))
    r = _mono(_to_2d(np.asarray(rest, dtype=np.float64)))
    n = min(len(v), len(r))
    v, r = v[:n], r[:n]
    fv, fr = _Frames(v, sr), _Frames(r, sr)
    active, _noise, l95, _floor = _activity(fv, "vocal")
    if active.sum() < 8:
        return {"measured": False, "reason": "the vocal is silent in this range"}
    hop_s = fv.hop / float(sr)
    words_v = fv.band_power(1000.0, 4000.0)
    words_r = fr.band_power(1000.0, 4000.0)
    cons_v = fv.band_power(4000.0, 8000.0)
    cons_r = fr.band_power(4000.0, 8000.0)
    body_v = fv.band_power(200.0, 800.0)

    def ratio_db(a, b):
        a, b = a[active].mean(), b[active].mean()
        return float(_pdb(a) - _pdb(b)) if a > 0 and b > 0 else None

    words_db = ratio_db(words_v, words_r)
    cons_db = ratio_db(cons_v, cons_r)
    # per-frame: the rest of the mix louder than the vocal in the word band, while the vocal sounds
    frame_diff = _pdb(np.maximum(words_v, 1e-20)) - _pdb(np.maximum(words_r, 1e-20))
    covered = active & (frame_diff < -1.0)
    lost = []
    for a, last in _runs_inclusive(covered, bridge_frames=1):
        b = last + 1
        if (b - a) * hop_s >= 0.18:                       # a syllable or more
            lost.append({"from_seconds": _r(offset + fv.times[a] - hop_s / 2, 2), "to_seconds": _r(offset + fv.times[b - 1] + hop_s / 2, 2),
                         "rest_over_vocal_db": _r(float(-frame_diff[a:b].mean()), 1)})
    lost.sort(key=lambda x: -x["rest_over_vocal_db"])
    lost_seconds = float(covered.sum()) * hop_s
    voice_seconds = float(active.sum()) * hop_s
    # articulation: how much the vocal's level moves at syllable rates, alone and inside the mix
    mix = v + r
    fm = _Frames(mix, sr)
    mod_alone = _syllable_rate_band(fv.level_db[active], hop_s)
    mod_mix = _syllable_rate_band(fm.level_db[active], hop_s)
    # brassiness: the 1-4 kHz band against the body of the voice, and ringing peaks in it
    bz = _brassiness_of(fv, active)
    res, pres_over_body, brass = bz["ringing_peaks"], bz["presence_over_body_db"], bz["brassiness"]
    out = {"measured": True, "voice_seconds": _r(voice_seconds, 1),
           "words_band_vocal_over_rest_db": _r(words_db, 1), "consonants_band_vocal_over_rest_db": _r(cons_db, 1),
           "words_likely_lost_seconds": _r(lost_seconds, 1),
           "words_likely_lost_share": _r(lost_seconds / voice_seconds, 2) if voice_seconds > 0 else None,
           "words_likely_lost": lost[:12],
           "articulation_alone": _r(mod_alone, 2), "articulation_in_mix": _r(mod_mix, 2),
           "articulation_kept": _r(mod_mix / mod_alone, 2) if mod_alone and mod_mix is not None else None,
           "presence_over_body_db": _r(pres_over_body, 1), "brassiness": _r(brass, 1),
           "ringing_peaks": res}
    # a 0-10 score that follows what a listener reports: word band ratio first, consonants second, articulation third
    score = 5.0
    if words_db is not None:
        score += np.clip(words_db, -6.0, 6.0) * 0.5
    if cons_db is not None:
        score += np.clip(cons_db, -6.0, 6.0) * 0.2
    if out["articulation_kept"] is not None:
        score += (out["articulation_kept"] - 0.8) * 5.0
    score -= brass * 0.8
    if out["words_likely_lost_share"]:
        score -= out["words_likely_lost_share"] * 6.0
    out["clarity_score_0_to_10"] = _r(float(np.clip(score, 0.0, 10.0)), 1)
    findings = []
    if words_db is not None and words_db < 2.0:
        findings.append({"what": "In the 1-4 kHz band where the words are, the vocal stands only %.1f dB above the rest of the mix "
                                 "while it sounds (3-6 dB reads clearly)." % words_db, "severity": "major" if words_db < 0 else "moderate",
                         "fix": "First make room in the beat (cut 2-5 dB around 2-3.5 kHz on the beat, wide), then lift the vocal 1-2 dB "
                                "at 3-3.5 kHz; raising the whole vocal fader is the last resort because it also raises the body."})
    if cons_db is not None and cons_db < 0.0:
        findings.append({"what": "The consonant band (4-8 kHz) of the vocal is %.1f dB under the rest of the mix: word endings and S/T/K "
                                 "sounds blur." % (-cons_db), "severity": "moderate",
                         "fix": "Ease the de-esser or the high shelf on the vocal 1-2 dB, or cut the beat's hi-hats/cymbals 1-2 dB at 5-8 kHz."})
    if out["articulation_kept"] is not None and out["articulation_kept"] < 0.7:
        findings.append({"what": "Inside the mix the vocal's syllables stand out %.0f%% less than on their own: the beat fills the "
                                 "gaps between words." % ((1 - out["articulation_kept"]) * 100), "severity": "moderate",
                         "fix": "Less compression on the vocal (quiet syllables stay quiet, loud ones poke through), and let the beat "
                                "breathe: duck it 1-2 dB under the vocal, or lower pads and sustained parts."})
    if brass >= 1.0:
        findings.append({"what": "The vocal sounds brassy (1-4 kHz is %.1f dB against the body of the voice%s)."
                                 % (pres_over_body if pres_over_body is not None else 0.0,
                                    ", with ringing peaks at " + ", ".join("%d Hz" % p["hz"] for p in res) if res else ""),
                         "severity": "moderate" if brass < 2.0 else "major",
                         "fix": "Take the presence boost back 1-2 dB and cut each ringing peak with a narrow EQ band (fix_resonances); "
                                "clarity then comes from room in the beat, not from more upper-mid boost."})
    if lost and out["words_likely_lost_share"] and out["words_likely_lost_share"] > 0.15:
        findings.append({"what": "%.0f%% of the time the vocal sounds, the rest of the mix is louder than it in the word band; the worst "
                                 "stretches are listed with their times." % (out["words_likely_lost_share"] * 100), "severity": "major",
                         "fix": "Duck the beat under those lines (duck_section on the beat track with these ranges, 2-4 dB), or turn "
                                "the beat's clip down there (cut_section mode duck on the beat)."})
    out["findings"] = findings
    return out
