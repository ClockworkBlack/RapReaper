"""Run a Roformer vocal model (the ONNX files this project builds) over a whole stereo recording with NumPy and
ONNX Runtime: the same windows, gain, STFT and blending as the Vocal Remover program (roformer.go), so that
what is measured here is what the program does.

    from roformer_numpy import separate
    vocals = separate(mix, 44100, 'MelRoformer-KimFT2-int8.onnx')   # mix: (n, 2) float32 at 44.1 kHz
"""
import time

import numpy as np

N_FFT, HOP = 2048, 441
INPUT_PEAK = 0.9


def _window():
    return (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(N_FFT) / N_FFT)).astype(np.float64)   # periodic Hann


def stft_full(chunk, T):
    """chunk (2, L) -> (1, 2, 1025, T, 2) float32; frame t is centred at t*hop with reflect padding."""
    L = chunk.shape[1]
    half = N_FFT // 2
    win = _window()
    idx = np.arange(T)[:, None] * HOP - half + np.arange(N_FFT)[None, :]
    idx = np.where(idx < 0, -idx, idx)
    idx = np.where(idx >= L, 2 * (L - 1) - idx, idx)
    out = np.empty((1, 2, N_FFT // 2 + 1, T, 2), dtype=np.float32)
    for c in range(2):
        fr = chunk[c][idx] * win                                   # (T, n)
        sp = np.fft.rfft(fr, axis=1).T                             # (bins, T)
        out[0, c, :, :, 0] = sp.real
        out[0, c, :, :, 1] = sp.imag
    return out


def istft_full(spec, L):
    """inverse of stft_full for a chunk of L samples -> (2, L)."""
    T = spec.shape[3]
    half = N_FFT // 2
    win = _window()
    total = N_FFT + HOP * (T - 1)
    env = np.zeros(total)
    for t in range(T):
        env[t * HOP:t * HOP + N_FFT] += win * win
    out = np.zeros((2, L), dtype=np.float32)
    for c in range(2):
        sp = spec[0, c, :, :, 0].T + 1j * spec[0, c, :, :, 1].T    # (T, bins)
        fr = np.fft.irfft(sp, n=N_FFT, axis=1) * win               # (T, n)
        acc = np.zeros(total)
        for t in range(T):
            acc[t * HOP:t * HOP + N_FFT] += fr[t]
        out[c] = (acc[half:half + L] / np.maximum(env[half:half + L], 1e-11)).astype(np.float32)
    return out


def separate(mix, sr, model_path, fast=True, threads=None, progress=None, providers=None):
    """mix: (n, 2) float at 44.1 kHz. Returns the vocal estimate (n, 2) float32 (instrumental = mix - vocals)."""
    import onnxruntime as ort
    assert sr == 44100
    x = np.asarray(mix, dtype=np.float32).T
    n = x.shape[1]
    so = ort.SessionOptions()
    if threads:
        so.intra_op_num_threads = int(threads)
    sess = ort.InferenceSession(model_path, so, providers=providers or ['CPUExecutionProvider'])
    T = int(sess.get_inputs()[0].shape[3])
    chunk = HOP * (T - 1)
    peak = float(np.abs(x).max())
    if n == 0 or peak == 0:
        return np.zeros((n, 2), dtype=np.float32)
    gain = INPUT_PEAK / peak
    fade = 44100 if fast else 0
    step = chunk - fade if fast else chunk // 2
    starts = []
    off = 0
    while off < n:
        if off + chunk >= n:
            tail = max(0, n - chunk)
            if not starts or starts[-1] != tail:
                starts.append(tail)
            break
        starts.append(off)
        off += step
    hamming = 0.54 - 0.46 * np.cos(2 * np.pi * np.arange(chunk) / (chunk - 1))

    def window_for(k):
        if not fast:
            return hamming
        w = np.ones(chunk)
        if k > 0:
            ov = starts[k - 1] + chunk - starts[k]
            if ov > 0:
                i = np.arange(min(ov, chunk))
                w[:len(i)] = 0.5 - 0.5 * np.cos(np.pi * (i + 0.5) / ov)
        if k + 1 < len(starts):
            ov = starts[k] + chunk - starts[k + 1]
            if ov > 0:
                i = np.arange(min(ov, chunk))
                w[chunk - 1 - i] = 0.5 - 0.5 * np.cos(np.pi * (i + 0.5) / ov)
        return w

    result = np.zeros((2, n), dtype=np.float64)
    weight = np.zeros(n, dtype=np.float64)
    for k, s in enumerate(starts):
        size = min(chunk, n - s)
        part = np.zeros((2, chunk), dtype=np.float32)
        part[:, :size] = x[:, s:s + size]
        if np.any(part):
            spec = stft_full(part * gain, T)
            y = sess.run(None, {'spec': spec})[0]
            est = istft_full(y, chunk)
        else:
            est = np.zeros((2, chunk), dtype=np.float32)
        w = window_for(k)[:size]
        result[:, s:s + size] += est[:, :size] * w
        weight[s:s + size] += w
        if progress:
            progress(k + 1, len(starts))
    result /= np.maximum(weight, 1e-8)[None, :]
    return (result / gain).T.astype(np.float32)


def sdr(ref, est):
    ref = np.asarray(ref, dtype=np.float64)
    est = np.asarray(est, dtype=np.float64)
    return float(10 * np.log10((ref ** 2).sum() / max(((ref - est) ** 2).sum(), 1e-20)))


if __name__ == '__main__':
    import sys
    import soundfile as sf    # optional, for a quick manual run
    data, rate = sf.read(sys.argv[1], dtype='float32', always_2d=True)
    t0 = time.time()
    voc = separate(data, rate, sys.argv[2], progress=lambda d, t: print('%d/%d' % (d, t), end='\r'))
    print('\n%.0f s' % (time.time() - t0))
    sf.write(sys.argv[3], voc, rate)
