"""Write a BS-Roformer or Mel-Band Roformer vocal model as an ONNX graph with fixed shapes, straight from the
public checkpoint, without PyTorch (torch_ckpt.py reads the checkpoint) and without the onnx package
(onnxwriter.py writes the file).

The graph is the network between the STFT and the inverse STFT, so the program around it (Go or Python) does
the transforms:

    input  'spec'    float32 (1, 2, F, T, 2)   F = n_fft/2+1 = 1025 bins, T frames, last = (re, im)
    output 'masked'  float32 (1, 2, F, T, 2)   the masked STFT of the vocal

Usage:
    build_roformer_onnx.py <config.yaml> <checkpoint.ckpt> <out.onnx> <frames> [options]
      --int8             int8 weights with dynamic activation quantisation: the CPU file (about 2.5x faster than
                         float32 on a processor, 4x smaller, no measurable quality loss)
      --fp16             float16 weights and float16 matrix products, everything else float32: the graphics
                         processor file (half the size of float32; DirectML and CoreML run float16 natively)
      --group-bands=N    bands per group in the time transformer (memory; default 2)
      --group-frames=N   frames per group in the frequency transformer (memory; default 27)
      --min-in=N         only linear layers with at least N inputs are quantised / halved (default 256)

Both architectures come from the same code (Lu, Wang et al., ByteDance 2023; open implementation by Phil Wang
and Roman Solovyev): a band split, a stack of (time transformer, frequency transformer) pairs with rotary
position embeddings and gated attention, and one mask estimator per band. The Mel-Band variant takes its bands
from a mel filter bank (overlapping; a bin covered by two bands gets the average of their masks); the layer
structure (which mask layers exist, whether a transformer ends in a norm) is read from the checkpoint itself.

Shipped files (see the NOTICE / credits):
    build_roformer_onnx.py config_kimmel_unwa_ft.yaml kimmel_unwa_ft2.ckpt  MelRoformer-KimFT2-int8.onnx 801 --int8
    build_roformer_onnx.py config_kimmel_unwa_ft.yaml kimmel_unwa_ft2.ckpt  MelRoformer-KimFT2-fp16.onnx 401 --fp16 --group-bands=6 --group-frames=101
"""
import math
import os
import re
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from onnxwriter import Graph          # noqa: E402
from torch_ckpt import load_state_dict   # noqa: E402


class ConfigError(ValueError):
    pass


def _yaml_scalar(text):
    """A plain YAML 1.1 scalar as PyYAML's safe_load reads the ones these config files use."""
    t = text.strip()
    if len(t) >= 2 and t[0] == t[-1] and t[0] in "'\"":
        return t[1:-1]
    low = t.lower()
    if low in ("", "~", "null"):
        return None
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if re.fullmatch(r'[-+]?(0|[1-9][0-9_]*)', t):
        return int(t.replace('_', ''))
    if re.fullmatch(r'[-+]?([0-9][0-9_]*)?\.[0-9_]*([eE][-+][0-9]+)?', t) and re.search(r'[0-9]', t):
        return float(t.replace('_', ''))
    if t[0] in '[{&*!|>%@`':
        raise ConfigError('unsupported YAML value: %r' % t)
    return t


def _strip_comment(line):
    """The line without its comment: a # at the start or after a space, outside quotes."""
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"" and (i == 0 or line[i - 1] in ' :-'):
            quote = ch
        elif ch == '#' and (i == 0 or line[i - 1] in ' \t'):
            return line[:i]
    return line


def load_yaml(path):
    """The training config (a model's .yaml) as a dict. These files use only block mappings, block lists ("- x", also
    at the indentation of their key), plain scalars, comments and the tag !!python/tuple, so this reads them with the
    standard library alone (RapReaper's Python has no PyYAML). Anything else is refused rather than guessed: the
    built model is checked against its published checksum in any case."""
    with open(path, encoding='utf-8') as f:
        raw = f.read().replace('!!python/tuple', '')
    lines = []
    for n, line in enumerate(raw.split('\n'), 1):
        if '\t' in line[:len(line) - len(line.lstrip())]:
            raise ConfigError('line %d: tab in indentation' % n)
        body = _strip_comment(line).rstrip()
        if body.strip() and body.strip() != '---':
            lines.append((len(body) - len(body.lstrip()), body.strip(), n))

    def block(i, indent):
        """The block starting at lines[i] (indentation `indent`): (value, next index)."""
        if lines[i][1].startswith('- ') or lines[i][1] == '-':
            out = []
            while i < len(lines) and lines[i][0] == indent and (lines[i][1].startswith('- ') or lines[i][1] == '-'):
                item = lines[i][1][1:].strip()
                if not item or ':' in item.split('#')[0] and re.match(r'[^\'"]+:\s', item + ' '):
                    raise ConfigError('line %d: only plain list items are supported' % lines[i][2])
                out.append(_yaml_scalar(item))
                i += 1
            return out, i
        out = {}
        while i < len(lines) and lines[i][0] == indent:
            ind, text, n = lines[i]
            m = re.match(r'^([^:\'"]+?):(\s+(.*))?$', text)
            if not m:
                raise ConfigError('line %d: expected "key: value"' % n)
            key, rest = m.group(1).strip(), (m.group(3) or '').strip()
            if key in out:
                raise ConfigError('line %d: key %r twice' % (n, key))
            i += 1
            if rest:
                out[key] = _yaml_scalar(rest)
            elif i < len(lines) and (lines[i][0] > ind or (lines[i][0] == ind and lines[i][1].startswith('-'))):
                out[key], i = block(i, lines[i][0])
            else:
                out[key] = None
        return out, i

    if not lines:
        return {}
    cfg, i = block(0, lines[0][0])
    if i != len(lines):
        raise ConfigError('line %d: unexpected indentation' % lines[i][2])
    return cfg


# ---- mel filter bank (librosa.filters.mel with its defaults: Slaney scale, fmin 0, fmax sr/2) ----------------

def _hz_to_mel(f):
    f = np.asarray(f, dtype=np.float64)
    f_sp = 200.0 / 3
    mels = f / f_sp
    min_log_hz = 1000.0
    min_log_mel = min_log_hz / f_sp
    logstep = np.log(6.4) / 27.0
    if mels.ndim == 0:
        return float(min_log_mel + np.log(f / min_log_hz) / logstep) if f >= min_log_hz else float(mels)
    log_t = f >= min_log_hz
    mels[log_t] = min_log_mel + np.log(f[log_t] / min_log_hz) / logstep
    return mels


def _mel_to_hz(m):
    m = np.asarray(m, dtype=np.float64)
    f_sp = 200.0 / 3
    freqs = f_sp * m
    min_log_hz = 1000.0
    min_log_mel = min_log_hz / f_sp
    logstep = np.log(6.4) / 27.0
    log_t = m >= min_log_mel
    freqs[log_t] = min_log_hz * np.exp(logstep * (m[log_t] - min_log_mel))
    return freqs


def mel_band_support(sr, n_fft, n_mels):
    """Boolean (n_mels, n_fft//2+1): which STFT bins each mel band covers (librosa's filter > 0, with the first
    and last bin forced into the first and last band, as the model code does)."""
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    mel_f = _mel_to_hz(np.linspace(_hz_to_mel(0.0), _hz_to_mel(sr / 2.0), n_mels + 2))
    fdiff = np.diff(mel_f)
    ramps = np.subtract.outer(mel_f, freqs)
    weights = np.zeros((n_mels, len(freqs)), dtype=np.float32)
    for i in range(n_mels):
        lower = -ramps[i] / fdiff[i]
        upper = ramps[i + 2] / fdiff[i + 1]
        weights[i] = np.maximum(0, np.minimum(lower, upper))
    weights[0, 0] = 1.0
    weights[-1, -1] = 1.0
    support = weights > 0
    assert support.any(axis=0).all(), 'every bin must belong to a band'
    return support


def band_ranges(support):
    """Each band's bins form one run; return [(lo, hi_exclusive)] and check it."""
    out = []
    for row in support:
        idx = np.nonzero(row)[0]
        assert idx.size and idx[-1] - idx[0] + 1 == idx.size, 'a mel band must cover consecutive bins'
        out.append((int(idx[0]), int(idx[-1]) + 1))
    return out


# ---- the builder -----------------------------------------------------------------------------------------------

def build(cfg_path, ckpt_path, out_path, T, mode='fp32', group_bands=2, group_frames=27, min_in=256, verbose=True):
    cfg = load_yaml(cfg_path)
    m = cfg['model']
    dim, heads, dh = m['dim'], m['heads'], m['dim_head']
    S = 2 if m['stereo'] else 1
    C = 2
    n_fft = m['stft_n_fft']
    F = n_fft // 2 + 1
    assert m.get('time_transformer_depth', 1) == 1 and m.get('freq_transformer_depth', 1) == 1, \
        'only depth-1 time/freq transformers are implemented'
    W = load_state_dict(ckpt_path)
    depth = 1 + max(int(k.split('.')[1]) for k in W if k.startswith('layers.'))
    assert depth == m['depth'], (depth, m['depth'])

    mel = 'num_bands' in m and 'freqs_per_bands' not in m
    if mel:
        support = mel_band_support(m['sample_rate'], n_fft, m['num_bands'])
        ranges = band_ranges(support)
        assert len(ranges) == m['num_bands']
    else:
        fpb = tuple(m['freqs_per_bands'])
        assert sum(fpb) == F
        ranges, lo = [], 0
        for f in fpb:
            ranges.append((lo, lo + f))
            lo += f
    nb = len(ranges)
    widths = [S * C * (hi - lo) for lo, hi in ranges]       # values per frame per band: (f, s, c)
    for i, w in enumerate(widths):                            # the checkpoint knows the band widths: check ours
        got = W['band_split.to_features.%d.1.weight' % i].shape[1]
        assert got == w, 'band %d: checkpoint expects %d inputs, the filter bank gives %d' % (i, got, w)

    g = Graph()
    cache = {}

    def named_const(name, arr):
        if name not in cache:
            cache[name] = g.const(np.asarray(arr, dtype=np.float32), name)
        return cache[name]

    def linear(x, prefix, bias=True):
        wmat = W[prefix + '.weight']                          # (out, in)
        big = wmat.shape[1] >= min_in
        key = prefix + '.w'
        if mode == 'int8' and big:
            if key not in cache:
                w = np.asarray(wmat, dtype=np.float32).T      # (in, out)
                scale = np.maximum(np.abs(w).max(axis=0), 1e-12) / 127.0
                wq = np.clip(np.round(w / scale), -127, 127).astype(np.int8)
                cache[key] = (g.const(wq, key + '.q'), g.const(scale.astype(np.float32), key + '.qs'))
            wq, ws = cache[key]
            xq, xs, xzp = g.op('DynamicQuantizeLinear', [x], n_out=3)
            yi = g.op('MatMulInteger', [xq, wq, xzp, g.const(np.int8(0), 'wzp')])
            y = g.mul(g.op('Cast', [yi], to=1), g.mul(xs, ws))
        elif mode == 'fp16' and big:
            if key not in cache:
                cache[key] = g.const(np.asarray(wmat, dtype=np.float32).T.astype(np.float16), key + '.h')
            y = g.op('Cast', [g.matmul(g.op('Cast', [x], to=10), cache[key])], to=1)
        else:
            if key not in cache:
                cache[key] = g.const(np.asarray(wmat, dtype=np.float32).T, key)
            y = g.matmul(x, cache[key])
        if bias and (prefix + '.bias') in W:
            y = g.add(y, named_const(prefix + '.b', W[prefix + '.bias']))
        return y

    def rmsnorm(x, gamma_key, d):
        # F.normalize(x, dim=-1) * sqrt(d) * gamma
        n = g.op('ReduceL2', [x], axes=[-1], keepdims=1)
        n = g.op('Max', [n, g.const(np.float32(1e-12), 'eps')])
        return g.mul(g.div(x, n), named_const(gamma_key + '.scaled', W[gamma_key] * math.sqrt(d)))

    def gelu(x):
        e = g.op('Erf', [g.div(x, g.const(np.float32(math.sqrt(2.0)), 'sqrt2'))])
        e = g.add(e, g.const(np.float32(1.0), 'one'))
        return g.mul(g.mul(x, e), g.const(np.float32(0.5), 'half'))

    rot = {}

    def rotary_tables(n, freqs):
        key = (n, freqs.tobytes())
        if key not in rot:
            pos = np.arange(n, dtype=np.float32)
            fr = np.repeat(pos[:, None] * freqs[None, :].astype(np.float32), 2, axis=-1)   # interleaved (n, dh)
            rot[key] = (g.const(np.cos(fr).astype(np.float32), 'cos'), g.const(np.sin(fr).astype(np.float32), 'sin'))
        return rot[key]

    def apply_rotary(x, cos, sin, n):
        x5 = g.reshape(x, [0, 0, n, dh // 2, 2])
        x1, x2 = g.split(x5, [1, 1], axis=-1)
        r = g.reshape(g.concat([g.op('Neg', [x2]), x1], axis=-1), [0, 0, n, dh])
        return g.add(g.mul(x, cos), g.mul(r, sin))

    def attention(x, prefix, n, cos, sin):
        h = rmsnorm(x, prefix + '.norm.gamma', dim)
        qkv = g.reshape(linear(h, prefix + '.to_qkv', bias=False), [0, n, 3, heads, dh])
        qkv = g.transpose(qkv, [2, 0, 3, 1, 4])
        q, k, v = (g.squeeze(t, [0]) for t in g.split(qkv, [1, 1, 1], axis=0))
        q, k = apply_rotary(q, cos, sin, n), apply_rotary(k, cos, sin, n)
        sim = g.mul(g.matmul(q, g.transpose(k, [0, 1, 3, 2])), g.const(np.float32(dh ** -0.5), 'scale'))
        out = g.matmul(g.op('Softmax', [sim], axis=-1), v)                      # (B, h, n, dh)
        gates = g.op('Sigmoid', [g.unsqueeze(g.transpose(linear(h, prefix + '.to_gates'), [0, 2, 1]), [-1])])
        out = g.reshape(g.transpose(g.mul(out, gates), [0, 2, 1, 3]), [0, n, heads * dh])
        return linear(out, prefix + '.to_out.0', bias=False)

    def feedforward(x, prefix):
        h = gelu(linear(rmsnorm(x, prefix + '.net.0.gamma', dim), prefix + '.net.1'))
        return linear(h, prefix + '.net.4')

    def transformer(x, prefix, n, cos, sin, batch, group):
        def layer(t):
            t = g.add(attention(t, prefix + '.layers.0.0', n, cos, sin), t)
            return g.add(feedforward(t, prefix + '.layers.0.1'), t)
        if batch <= group:
            y = layer(x)
        else:
            sizes = [group] * (batch // group) + ([batch % group] if batch % group else [])
            y = g.concat([layer(gr) for gr in g.split(x, sizes, axis=0)], axis=0)
        if (prefix + '.norm.gamma') in W:                   # the transformer's own output norm (norm_output)
            y = rmsnorm(y, prefix + '.norm.gamma', dim)
        return y

    # ---- graph ----
    spec = g.input('spec', [1, S, F, T, C])
    x = g.reshape(g.transpose(spec, [0, 3, 2, 1, 4]), [1, T, F * S * C])      # (1, T, (f s c))
    feats = []
    for i, (lo, hi) in enumerate(ranges):
        p = g.op('Slice', [x, g.i64([S * C * lo]), g.i64([S * C * hi]), g.i64([2])])
        h = rmsnorm(p, 'band_split.to_features.%d.0.gamma' % i, widths[i])
        feats.append(g.unsqueeze(linear(h, 'band_split.to_features.%d.1' % i), [2]))
    x = g.concat(feats, axis=2)                                                   # (1, T, nb, dim)

    tcos, tsin = rotary_tables(T, W['layers.0.0.layers.0.0.rotary_embed.freqs'])
    fcos, fsin = rotary_tables(nb, W['layers.0.1.layers.0.0.rotary_embed.freqs'])
    for L in range(depth):
        x = g.reshape(g.transpose(x, [0, 2, 1, 3]), [nb, T, dim])
        x = transformer(x, 'layers.%d.0' % L, T, tcos, tsin, nb, group_bands)
        x = g.reshape(g.transpose(g.reshape(x, [1, nb, T, dim]), [0, 2, 1, 3]), [T, nb, dim])
        x = transformer(x, 'layers.%d.1' % L, nb, fcos, fsin, T, group_frames)
        x = g.reshape(x, [1, T, nb, dim])
    if 'final_norm.gamma' in W:
        x = rmsnorm(x, 'final_norm.gamma', dim)

    # mask estimator (stem 0): per band an MLP (as many layers as the checkpoint has) ending in a GLU
    bands = g.split(x, [1] * nb, axis=2)
    band_mask = []
    for i, bnd in enumerate(bands):
        h = g.squeeze(bnd, [2])                                                   # (1, T, dim)
        pre = 'mask_estimators.0.to_freqs.%d.0' % i
        js = sorted(int(mm.group(1)) for k in W for mm in [re.match(re.escape(pre) + r'\.(\d+)\.weight$', k)] if mm)
        for j in js[:-1]:
            h = g.op('Tanh', [linear(h, '%s.%d' % (pre, j))])
        h = linear(h, '%s.%d' % (pre, js[-1]))                                   # (1, T, 2 * width)
        a, b_ = g.split(h, [widths[i], widths[i]], axis=-1)
        band_mask.append(g.mul(a, g.op('Sigmoid', [b_])))                        # GLU -> (1, T, width)

    # put the bands' masks together over the whole spectrum; a bin two bands share gets their average
    cover = [[] for _ in range(F)]
    for i, (lo, hi) in enumerate(ranges):
        for f in range(lo, hi):
            cover[f].append(i)
    segments, f = [], 0
    while f < F:
        f2 = f
        while f2 + 1 < F and cover[f2 + 1] == cover[f]:
            f2 += 1
        segments.append((f, f2 + 1, cover[f]))
        f = f2 + 1
    pieces = []
    for lo, hi, bs in segments:
        parts = []
        for i in bs:
            blo = ranges[i][0]
            parts.append(g.op('Slice', [band_mask[i], g.i64([S * C * (lo - blo)]), g.i64([S * C * (hi - blo)]), g.i64([2])]))
        acc = parts[0]
        for p in parts[1:]:
            acc = g.add(acc, p)
        if len(bs) > 1:
            acc = g.mul(acc, g.const(np.float32(1.0 / len(bs)), 'avg'))
        pieces.append(acc)
    mask = pieces[0] if len(pieces) == 1 else g.concat(pieces, axis=-1)          # (1, T, (f s c))
    mask = g.transpose(g.reshape(mask, [1, T, F, S, C]), [0, 3, 2, 1, 4])         # (1, S, F, T, C)

    sr_, si_ = g.split(spec, [1, 1], axis=-1)
    mr, mi = g.split(mask, [1, 1], axis=-1)
    re_ = g.sub(g.mul(sr_, mr), g.mul(si_, mi))
    im_ = g.add(g.mul(sr_, mi), g.mul(si_, mr))
    y = g.concat([re_, im_], axis=-1)
    if m.get('zero_dc', not mel):      # BS-Roformer zeroes the DC bin (lucidrains default True); Mel-Band does not
        dc = np.ones((1, 1, F, 1, 1), dtype=np.float32)
        dc[0, 0, 0] = 0
        y = g.mul(y, g.const(dc, 'zero_dc'))
    y = g.op('Identity', [y], out_names=['masked'])
    g.output(y, [1, S, F, T, C])
    data = g.serialize('mel_band_roformer' if mel else 'bs_roformer', opset=17)
    with open(out_path, 'wb') as fh:
        fh.write(data)
    if verbose:
        print('wrote %s: %.0f MB, %d nodes, T=%d, mode=%s, %d bands, %d segments' % (
            out_path, len(data) / 1e6, len(g.nodes), T, mode, nb, len(segments)))
    return {'bands': nb, 'ranges': ranges, 'segments': len(segments), 'nodes': len(g.nodes), 'bytes': len(data)}


def main(argv):
    if len(argv) < 5:
        print(__doc__)
        return 2
    opts = {a.split('=')[0]: (a.split('=')[1] if '=' in a else True) for a in argv[5:]}
    mode = 'int8' if '--int8' in opts else ('fp16' if '--fp16' in opts else 'fp32')
    build(argv[1], argv[2], argv[3], int(argv[4]), mode=mode,
          group_bands=int(opts.get('--group-bands', 2)), group_frames=int(opts.get('--group-frames', 27)),
          min_in=int(opts.get('--min-in', 256)))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
