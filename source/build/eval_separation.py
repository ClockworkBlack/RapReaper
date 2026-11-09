"""Measure vocal models on songs whose true stems are known (MUSDB18-HQ test tracks, read from the parquet
mirror on Hugging Face), the way the Vocal Remover's README reports them:

  accuracy of the instrumental   10 log10( sum(true_inst^2) / sum((true_inst - est_inst)^2) )   dB, higher = better
  accuracy of the vocals         the same for the vocal
  voice left in the instrumental 10 log10( sum((est_inst - true_inst)^2) / sum(true_voc^2) )     dB, more negative = better

Each song contributes its loudest-vocal 60 seconds (whole songs take four times longer and change the ranking
little). Results go to a JSON file; the README table is written from it.

    eval_separation.py <out.json> <model.onnx> [model.onnx ...] [--seconds=60] [--tracks=Name1;Name2] [--parquet-dir=DIR]
"""
import glob
import io
import json
import os
import sys
import time
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import roformer_numpy as RN   # noqa: E402

PARQUET_DIR = os.environ.get('MUSDB_PARQUET_DIR', 'musdb')   # the MUSDB18-HQ parquet files (or --parquet-dir=)


def read_wav_bytes(b):
    with wave.open(io.BytesIO(b)) as w:
        n, ch, sw, sr = w.getnframes(), w.getnchannels(), w.getsampwidth(), w.getframerate()
        raw = w.readframes(n)
    if sw == 2:
        x = np.frombuffer(raw, dtype='<i2').astype(np.float32) / 32768.0
    elif sw == 3:
        a = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
        x = ((a[:, 0].astype(np.int32) | (a[:, 1].astype(np.int32) << 8) | (a[:, 2].astype(np.int32) << 16)) << 8 >> 8).astype(np.float32) / 8388608.0
    elif sw == 4:
        x = np.frombuffer(raw, dtype='<i4').astype(np.float32) / 2147483648.0
    else:
        raise ValueError('sample width %d' % sw)
    x = x.reshape(-1, ch)
    if ch == 1:
        x = np.repeat(x, 2, axis=1)
    return x[:, :2], sr


def extract_stems(parquet_dir, wav_dir, wanted=None):
    """Write the mixture and vocals of the wanted tracks as WAV files under wav_dir/<track>/, one parquet file
    per child process (the parquet reader keeps hundreds of MB; a process that exits gives them back)."""
    import subprocess
    os.makedirs(wav_dir, exist_ok=True)
    for f in sorted(glob.glob(os.path.join(parquet_dir, '*.parquet'))):
        code = r"""
import sys, os, pyarrow.parquet as pq
f, wav_dir, wanted = sys.argv[1], sys.argv[2], set(a for a in sys.argv[3].split(';') if a)
pf = pq.ParquetFile(f)
names = pf.read(columns=['path', 'instrument'])
rows = list(zip(names.column('path').to_pylist(), names.column('instrument').to_pylist()))
need = [i for i, (p, inst) in enumerate(rows) if inst in ('mixture', 'vocals') and (not wanted or p.split('/')[-2] in wanted)
        and not os.path.isfile(os.path.join(wav_dir, p.split('/')[-2], inst + '.wav'))]
if need:
    t = pf.read(columns=['path', 'instrument', 'audio'])
    for i in need:
        p, inst = rows[i]
        d = os.path.join(wav_dir, p.split('/')[-2]); os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, inst + '.wav'), 'wb') as fh:
            fh.write(t.column('audio')[i].as_py()['bytes'])
"""
        subprocess.run([sys.executable, '-c', code, f, wav_dir, ';'.join(sorted(wanted)) if wanted else ''], check=True)


def load_tracks(parquet_dir, wanted=None, seconds=None):
    """{name: {'mixture': (n,2), 'vocals': (n,2), 'offset': first sample}} for every wanted track whose two
    stems could be found; with seconds, only the loudest-vocal excerpt of that length is kept."""
    wav_dir = os.path.join(parquet_dir, 'wav')
    extract_stems(parquet_dir, wav_dir, wanted)
    out = {}
    for name in sorted(os.listdir(wav_dir)):
        if wanted and name not in wanted:
            continue
        d = os.path.join(wav_dir, name)
        if not (os.path.isfile(os.path.join(d, 'mixture.wav')) and os.path.isfile(os.path.join(d, 'vocals.wav'))):
            continue
        mix, sr = read_wav_bytes(open(os.path.join(d, 'mixture.wav'), 'rb').read())
        voc, sr2 = read_wav_bytes(open(os.path.join(d, 'vocals.wav'), 'rb').read())
        assert sr == sr2 == 44100
        n = min(len(mix), len(voc))
        mix, voc, offset = mix[:n], voc[:n], 0
        if seconds:
            s0, e0 = loudest_vocal_excerpt(voc, 44100, seconds)
            mix, voc, offset = mix[s0:e0].copy(), voc[s0:e0].copy(), s0
        out[name] = {'mixture': mix, 'vocals': voc, 'offset': offset}
    return out


def loudest_vocal_excerpt(voc, sr, seconds):
    n = len(voc)
    L = int(seconds * sr)
    if L >= n:
        return 0, n
    e = (voc ** 2).sum(axis=1)
    cs = np.concatenate(([0.0], np.cumsum(e)))
    hop = sr
    best, best_e = 0, -1.0
    for s in range(0, n - L + 1, hop):
        v = cs[s + L] - cs[s]
        if v > best_e:
            best, best_e = s, v
    return best, best + L


def measure(true_mix, true_voc, est_voc):
    true_inst = true_mix - true_voc
    est_inst = true_mix - est_voc
    res = est_inst - true_inst
    return {
        'instrumental_accuracy_db': round(RN.sdr(true_inst, est_inst), 2),
        'vocal_accuracy_db': round(RN.sdr(true_voc, est_voc), 2),
        'voice_left_db': round(float(10 * np.log10(max((res ** 2).sum(), 1e-20) / max((true_voc ** 2).sum(), 1e-20))), 2),
    }


def main(argv):
    opts = {a.split('=')[0]: a.split('=', 1)[1] for a in argv if a.startswith('--')}
    args = [a for a in argv if not a.startswith('--')]
    out_json, models = args[0], args[1:]
    seconds = float(opts.get('--seconds', 60))
    wanted = set(opts['--tracks'].split(';')) if '--tracks' in opts else None
    tracks = load_tracks(opts.get('--parquet-dir', PARQUET_DIR), wanted)
    results = json.load(open(out_json)) if os.path.isfile(out_json) else {}
    for name, st in sorted(tracks.items()):
        s, e = loudest_vocal_excerpt(st['vocals'], 44100, seconds)
        mix, voc = st['mixture'][s:e], st['vocals'][s:e]
        for mp in models:
            key = os.path.basename(mp)
            if '+' in mp:
                key = 'ensemble(' + '+'.join(os.path.basename(m1) for m1 in mp.split('+')) + ')'
            if results.get(name, {}).get(key, {}).get('instrumental_accuracy_db') is not None:
                continue
            t0 = time.time()
            if '+' in mp:
                # an ensemble: "a.onnx+b.onnx" averages the two models' vocal estimates (the "Two networks" mode)
                parts = [RN.separate(mix, 44100, m1, fast=True, threads=int(opts.get('--threads', 0)) or None) for m1 in mp.split('+')]
                est = sum(parts) / len(parts)
            else:
                est = RN.separate(mix, 44100, mp, fast=True, threads=int(opts.get('--threads', 0)) or None)
            took = time.time() - t0
            r = measure(mix, voc, est)
            r['seconds_processed'] = round((e - s) / 44100.0, 1)
            r['took_s'] = round(took, 1)
            r['excerpt'] = [round(s / 44100.0, 1), round(e / 44100.0, 1)]
            results.setdefault(name, {})[key] = r
            print(name, key, r, flush=True)
            with open(out_json, 'w') as fh:
                json.dump(results, fh, indent=1)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
