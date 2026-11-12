#!/usr/bin/env python3
"""Vocal separation for RapReaper: takes a finished song and gives back the voice on its own (and the music
on its own), with the same networks the Vocal Remover uses.

Two ways, tried in this order:

1. A network run here through ONNX Runtime (pip package `onnxruntime`), on a model file found on this
   computer: in the Vocal Remover's `models` folder inside the RapReaper folder, in RapReaper's own
   `kit/models` folder, or (Windows) where the Vocal Remover's download pieces usually land. In order of
   preference:
     - the Mel-Band Roformer "Kim FT2" (MelRoformer-KimFT2-int8.onnx): the Vocal Remover's default since 2.0,
       the most accurate model measured for this project;
     - the MDX-Net "Kim Vocal 2" (Kim_Vocal_2.onnx), from earlier versions: quicker, less accurate.
   The arithmetic around the network (short-time Fourier transform, windows, overlap, gain) is the one the
   Vocal Remover program uses, so both give the same result.
2. The Vocal Remover program itself (VocalRemover.exe on Windows, VocalRemover on a Mac), started from the
   command line with --in / --out / --start / --end. ONLY when the program has been allowed by name: its path
   and SHA-256 are recorded in kit/state/settings.json (the installer does this for the copy it ships, or
   "Allow Vocal Remover" for another one), and the file is checked against that checksum before every start.
   RapReaper never runs a program it merely found on the computer: a file called VocalRemover.exe in Downloads
   could be anything.

Every model file is checked against its published SHA-256 before ONNX Runtime loads it, for the same reason, and
the bytes that were checked are the bytes that are loaded (no second read of the file by path).

ONNX Runtime's own telemetry is switched off (ORT_DISABLE_TELEMETRY=1, set before every import of it and passed
to the Vocal Remover program): nothing about a separation leaves the computer.

The graphics processor: when kit/state/settings.json says "gpu": true, way 1 asks ONNX Runtime for the
DirectML provider (Windows; needs the `onnxruntime-directml` package, which the installer's --gpu option
installs) or the CoreML provider (Mac; part of the ordinary package), and way 2 passes --gpu on to the
Vocal Remover (which uses its own tested setting). Either falls back to the processor by itself.

Either way takes minutes for a whole song on an ordinary computer, so the work runs as a background job
(start_job / job_status). Every job works in its own fresh folder (kit/state/sep/<job>/); only when it has
finished are its two files moved to their final names, which never replace a file that is already there
(" (2)", " (3)" ... are added instead). A small record of each finished pair (kit/state/separations/) says
which song file (path, size, age), which stretch and which way made it, so a pair is only ever reported as
the result of exactly that request. Nothing here touches REAPER.
"""
import hashlib
import json
import os

os.environ["ORT_DISABLE_TELEMETRY"] = "1"       # before anything can import onnxruntime: no telemetry, ever

import subprocess   # noqa: E402
import sys          # noqa: E402
import time         # noqa: E402

import numpy as np  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rapreaper_platform as PF     # noqa: E402

KIM_VOCAL_2 = {
    "key": "kim_vocal_2", "name": "Kim Vocal 2 (MDX-Net)", "kind": "mdx",
    "file": "Kim_Vocal_2.onnx",
    "sha256": "ce74ef3b6a6024ce44211a07be9cf8bc6d87728cc852a68ab34eb8e58cde9c8b",
    "n_fft": 7680, "hop": 1024, "dim_f": 3072, "dim_t": 256, "sr": 44100,
    "compensate": 1.009, "vocals": True, "bytes": 66759214, "max_bytes": 200 * 1024 * 1024,
}
MEL_ROFORMER = {
    "key": "mel_roformer_kim_ft2", "name": "Mel-Roformer Kim FT2", "kind": "roformer",
    "file": "MelRoformer-KimFT2-int8.onnx",
    "sha256": "f81d9265cf57b66808db38d72ae136f3d74f3888f642e085478288f774b5a0d4",
    "n_fft": 2048, "hop": 441, "sr": 44100, "vocals": True, "bytes": 236771629, "max_bytes": 300 * 1024 * 1024,
}
MODELS = [MEL_ROFORMER, KIM_VOCAL_2]
INPUT_PEAK = 0.9        # the level the Roformer networks see, as in the Vocal Remover


# ---------------------------------------------------------------------------
# finding what this computer has
# ---------------------------------------------------------------------------

def vocal_remover_dir(home):
    """The Vocal Remover's folder inside the RapReaper folder (kit's parent)."""
    return os.path.join(os.path.dirname(os.path.abspath(home)), "VocalRemover")


def _candidates_dirs(home):
    """Where a model file may be: the Vocal Remover's models folder in the RapReaper folder, RapReaper's own models
    folder and, on Windows, the usual download places and other Vocal Remover folders. On a Mac only the two
    folders inside RapReaper are looked at: reading Downloads, Desktop or Documents from a program an AI app started
    would make macOS ask the person for permission, in that app's name."""
    out = [os.path.join(vocal_remover_dir(home), "models"), os.path.join(home, "models")]
    if not PF.IS_WINDOWS:
        return [d for d in out if os.path.isdir(d)]
    up = PF.home_dir()
    for base in (os.path.join(up, "Downloads"), os.path.join(up, "Desktop"), os.path.join(up, "Documents"),
                 os.environ.get("LOCALAPPDATA") or "", os.environ.get("PROGRAMFILES") or ""):
        if not base or not os.path.isdir(base):
            continue
        out.append(base)
        try:
            for name in sorted(os.listdir(base)):
                p = os.path.join(base, name)
                if os.path.isdir(p) and "vocal" in name.lower():
                    out.append(p)
                    out.append(os.path.join(p, "models"))
        except OSError:
            pass
    return [d for d in out if os.path.isdir(d)]


MAX_PIECES = 16                 # the published models come in at most 8 pieces; more than this is not a model download
_FAILED_JOINS = set()           # sets of pieces (names, sizes, ages) that were joined once and did not give the model


def _pieces(folder, spec):
    """The complete set of 'Name.onnx.partNofM' pieces in a folder, in numeric order (part2 before part10), or None.
    A set counts only when every piece 1..M is there once, M is at most MAX_PIECES and the sizes add up to exactly the
    published file's size, so stray or foreign pieces are never even read."""
    pre = spec["file"] + ".part"
    found, total = {}, None
    for name in os.listdir(folder):
        if not name.startswith(pre):
            continue
        num, sep, of = name[len(pre):].partition("of")
        if not sep or not (num.isascii() and num.isdigit()) or not (of.isascii() and of.isdigit()):
            continue                                 # 'x.onnx.part1of3 (1)' and the like: not a piece of this set
        if os.path.islink(os.path.join(folder, name)) or not os.path.isfile(os.path.join(folder, name)):
            continue                                 # a link, pipe or device is never read
        if total is not None and int(of) != total:
            return None
        total = int(of)
        if int(num) in found:
            return None
        found[int(num)] = name
    if not total or total > MAX_PIECES or sorted(found) != list(range(1, total + 1)):
        return None
    pieces = [found[k] for k in range(1, total + 1)]
    try:
        sizes = [os.path.getsize(os.path.join(folder, n)) for n in pieces]
    except OSError:
        return None
    if sum(sizes) != spec["bytes"]:
        return None
    return pieces


def _assemble_pieces(folder, spec, into):
    """The models come as 'Name.onnx.part1of3' pieces (download size limits). Put them together into `into` (RapReaper's
    kit/models), through a temporary file that only takes the model's name once its SHA-256 is the published one.
    Returns the joined path, or None (no complete set, or a set that did not give the model; such a set is not tried
    again in this process)."""
    pieces = _pieces(folder, spec)
    if not pieces:
        return None
    try:
        sig = (os.path.abspath(folder), tuple((n, os.stat(os.path.join(folder, n)).st_size, os.stat(os.path.join(folder, n)).st_mtime_ns)
                                             for n in pieces))
    except OSError:
        return None
    if sig in _FAILED_JOINS:
        return None
    os.makedirs(into, exist_ok=True)
    dest = os.path.join(into, spec["file"])
    tmp = os.path.join(into, ".%s.joining-%d" % (spec["file"], os.getpid()))
    h = hashlib.sha256()
    try:
        with open(tmp, "wb") as out:
            for n in pieces:
                with open(os.path.join(folder, n), "rb") as f:
                    while True:
                        b = f.read(1024 * 1024)
                        if not b:
                            break
                        h.update(b)
                        out.write(b)
        if h.hexdigest() != spec["sha256"]:
            _FAILED_JOINS.add(sig)
            REJECTED["models"].append(os.path.join(folder, pieces[0]))
            return None
        os.replace(tmp, dest)                         # only the published bytes ever carry the model's name
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)                             # the joined file that did not match (or a write that failed)
    return dest


def sha256_of(path, chunk=1024 * 1024):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _verified_model(path, spec):
    """True when the file is the published model, byte for byte. A model file is parsed by native code inside ONNX
    Runtime, so a file that merely has the right name is not trusted."""
    try:
        return os.path.getsize(path) == spec["bytes"] and sha256_of(path) == spec["sha256"]
    except OSError:
        return False


def verified_model_bytes(path, spec=None):
    """The model file's bytes, read once and checked against the published SHA-256: the exact bytes ONNX Runtime is then
    given (never the path, which could be changed between the check and the load). Raises ValueError otherwise."""
    spec = spec or model_spec_for(path)
    with open(path, "rb") as f:
        data = f.read(spec["max_bytes"] + 1)
    if len(data) != spec["bytes"] or hashlib.sha256(data).hexdigest() != spec["sha256"]:
        raise ValueError("the model file %s is not the published %s (its checksum differs); it was not loaded"
                         % (os.path.basename(path), spec["file"]))
    return data


REJECTED = {"models": [], "exe": None}      # what was found but not trusted, for the answer's benefit
_VERIFIED = {}      # path -> the file's identity (size, modification and change times, inode) when it was hashed in THIS
#                     process; a file written to since (even with its old size and modification time put back) has a new
#                     change time and is hashed again. The worker hashes the bytes it loads in any case.


def _identity(p):
    st = os.stat(p)
    return (st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_ino, st.st_dev)


def find_model(home, spec=None):
    """Path of the (verified) model file on this computer, or None. Without spec: the best model present, in the
    order of MODELS (the Roformer first)."""
    if spec is None:
        for s in MODELS:
            p = find_model(home, s)
            if p:
                return p
        return None
    REJECTED["models"] = [r for r in REJECTED["models"] if spec["file"] not in os.path.basename(r)]
    for d in _candidates_dirs(home):
        p = os.path.join(d, spec["file"])
        if os.path.isfile(p):
            try:
                sig = _identity(p)
            except OSError:
                continue
            if _VERIFIED.get(p) == sig:
                return p
            if _verified_model(p, spec):
                try:
                    if _identity(p) == sig:
                        _VERIFIED[p] = sig
                        return p
                except OSError:
                    pass
            _VERIFIED.pop(p, None)
            REJECTED["models"].append(p)
    for d in _candidates_dirs(home):
        try:
            got = _assemble_pieces(d, spec, os.path.join(home, "models"))
        except OSError:
            got = None
        if got:
            try:
                _VERIFIED[got] = _identity(got)
            except OSError:
                continue
            return got
    return None


def model_spec_for(path):
    """The spec a (verified) model path belongs to."""
    for s in MODELS:
        if os.path.basename(path) == s["file"]:
            return s
    return KIM_VOCAL_2


def program_name():
    return "VocalRemover" if PF.IS_MAC else "VocalRemover.exe"


def find_exe(home):
    """The Vocal Remover program, only when it has been allowed (path + checksum in settings.json) and the file on
    disk still has that checksum. Never a program merely found on the computer."""
    REJECTED["exe"] = None
    s = _settings(home)
    path, want = s.get("vocal_remover_exe"), str(s.get("vocal_remover_sha256") or "").lower()
    if not path or not os.path.isfile(path):
        return None
    if len(want) != 64:
        REJECTED["exe"] = "named in settings.json but not allowed with its checksum (run 'Allow Vocal Remover%s')" % PF.LAUNCHER_EXT
        return None
    try:
        if sha256_of(path).lower() != want:
            REJECTED["exe"] = ("the program file has changed since it was allowed (run the installer, or 'Allow Vocal Remover%s' "
                               "again if that was you)" % PF.LAUNCHER_EXT)
            return None
    except OSError:
        return None
    return path


def allow_exe(home, path):
    """Record the permission to run this program: its path and checksum (called by the installer, never by a tool)."""
    path = os.path.abspath(path)
    base = os.path.basename(path).lower()
    if not os.path.isfile(path) or not (base.endswith(".exe") or base == "vocalremover"):
        raise ValueError("that is not the Vocal Remover program (VocalRemover.exe, or VocalRemover on a Mac): %s" % path)
    digest = sha256_of(path)
    s = _settings(home)
    s["vocal_remover_exe"], s["vocal_remover_sha256"] = path, digest
    os.makedirs(os.path.join(home, "state"), exist_ok=True)
    _write(os.path.join(home, "state", "settings.json"), s)
    return digest


def _settings(home):
    try:
        with open(os.path.join(home, "state", "settings.json"), encoding="utf-8-sig") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def gpu_wanted(home):
    """True when settings.json asks for the graphics processor ("gpu": true)."""
    return bool(_settings(home).get("gpu"))


def _ort():
    """ONNX Runtime, imported with its telemetry switched off (the variable is set again here in case something in this
    process changed it, and the switch the package itself offers is used as well)."""
    os.environ["ORT_DISABLE_TELEMETRY"] = "1"
    import onnxruntime as ort
    try:
        ort.disable_telemetry_events()
    except Exception:
        pass
    return ort


def have_onnxruntime():
    try:
        _ort()
        return True
    except Exception:
        return False


def gpu_providers():
    """The ONNX Runtime providers this computer's graphics processor would need, when they are installed:
    [] when none is."""
    try:
        have = set(_ort().get_available_providers())
    except Exception:
        return []
    if PF.IS_WINDOWS and "DmlExecutionProvider" in have:
        return ["DmlExecutionProvider"]
    if PF.IS_MAC and "CoreMLExecutionProvider" in have:
        return ["CoreMLExecutionProvider"]
    return []


def ways(home):
    """What this computer can separate with: {"onnxruntime": bool, "onnx": model path or None, "model": its name,
    "exe": program path or None, "gpu": wanted?, "gpu_providers": [...]}."""
    onnx = find_model(home)
    return {"onnxruntime": have_onnxruntime(), "onnx": onnx, "model": model_spec_for(onnx)["name"] if onnx else None,
            "exe": find_exe(home), "gpu": gpu_wanted(home), "gpu_providers": gpu_providers()}


# ---------------------------------------------------------------------------
# the networks through ONNX Runtime
# ---------------------------------------------------------------------------

def _session(model_path, threads=None, gpu=False, model_bytes=None):
    """An ONNX Runtime session on the graphics processor when asked and possible, else on the processor.
    Returns (session, where) with where 'DirectML' / 'CoreML' / 'processor'. The session is made from the model's
    bytes after they were checked against the published checksum (model_bytes, or read and checked here)."""
    ort = _ort()
    model = model_bytes if model_bytes is not None else verified_model_bytes(model_path)
    opts = ort.SessionOptions()
    if threads:
        opts.intra_op_num_threads = int(threads)
    if gpu:
        for prov in gpu_providers():
            try:
                o2 = ort.SessionOptions()
                if threads:
                    o2.intra_op_num_threads = int(threads)
                if prov == "DmlExecutionProvider":
                    o2.enable_mem_pattern = False
                    o2.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
                    sess = ort.InferenceSession(model, o2, providers=[prov, "CPUExecutionProvider"])
                else:
                    sess = ort.InferenceSession(model, o2, providers=[(prov, {"ModelFormat": "MLProgram", "MLComputeUnits": "ALL"}),
                                                                      "CPUExecutionProvider"])
                return sess, ("DirectML" if prov.startswith("Dml") else "CoreML")
            except Exception:
                continue
    return ort.InferenceSession(model, opts, providers=["CPUExecutionProvider"]), "processor"


def _stft(x, n_fft, hop):
    """x: (2, n) -> complex (2, n_fft//2+1, frames), centred frames with reflect padding (as torch.stft)."""
    pad = n_fft // 2
    xp = np.pad(x, ((0, 0), (pad, pad)), mode="reflect")
    win = np.hanning(n_fft + 1)[:-1].astype(np.float32)          # periodic Hann, as torch.hann_window
    frames = 1 + (xp.shape[1] - n_fft) // hop
    idx = np.arange(n_fft)[None, :] + hop * np.arange(frames)[:, None]
    out = np.empty((2, n_fft // 2 + 1, frames), dtype=np.complex64)
    for c in range(2):
        out[c] = np.fft.rfft(xp[c][idx] * win, axis=1).T
    return out


def _istft(spec, n_fft, hop, length):
    """inverse of _stft with the same window (window-sum normalised overlap-add)."""
    win = np.hanning(n_fft + 1)[:-1].astype(np.float32)
    frames = spec.shape[2]
    total = n_fft + hop * (frames - 1)
    out = np.zeros((2, total), dtype=np.float64)
    norm = np.zeros(total, dtype=np.float64)
    for c in range(2):
        fr = np.fft.irfft(spec[c].T, n=n_fft, axis=1) * win
        for i in range(frames):
            out[c, i * hop:i * hop + n_fft] += fr[i]
    for i in range(frames):
        norm[i * hop:i * hop + n_fft] += win * win
    out /= np.maximum(norm, 1e-8)
    pad = n_fft // 2
    return out[:, pad:pad + length].astype(np.float32)


def _stereo(mix):
    x = np.asarray(mix, dtype=np.float32)
    if x.ndim == 1:
        x = np.stack([x, x], axis=1)
    if x.shape[1] == 1:
        x = np.repeat(x, 2, axis=1)
    return x[:, :2]


def _to_model_rate(mix, sr, msr):
    from scipy import signal as sg
    x = _stereo(mix)
    n_in = len(x)
    if sr != msr:
        x = sg.resample_poly(x, msr, sr, axis=0).astype(np.float32)
    return x.T, n_in


def _from_model_rate(voc, mix, sr, msr, n_in):
    """The vocal estimate (2, n) at the model's rate -> (vocals, instrumental) at the song's rate, (n_in, 2) each. The
    instrumental is the song minus the vocal at the song's own rate, so the two always add up to the song exactly (as the
    Vocal Remover program does), also when the song had to be resampled for the network."""
    from scipy import signal as sg
    voc = voc.T
    if sr != msr:
        voc = sg.resample_poly(voc, sr, msr, axis=0)
    if len(voc) < n_in:
        voc = np.pad(voc, ((0, n_in - len(voc)), (0, 0)))
    voc = voc[:n_in].astype(np.float32)
    inst = (_stereo(mix)[:n_in] - voc).astype(np.float32)
    return voc, inst


def separate_mdx(mix, sr, model_path, spec=KIM_VOCAL_2, denoise=True, progress=None, threads=None, gpu=False, model_bytes=None):
    """MDX-Net. mix: (n, 2) float at any rate. Returns (vocals (n, 2), instrumental (n, 2), where_it_ran)."""
    msr = spec["sr"]
    x, n_in = _to_model_rate(mix, sr, msr)
    n_fft, hop, dim_f, dim_t = spec["n_fft"], spec["hop"], spec["dim_f"], spec["dim_t"]
    seg = hop * (dim_t - 1)                                       # samples per network window
    step = seg // 2                                               # 50 % overlap; the halves are cross-faded
    n = x.shape[1]
    pad_end = (-(n - seg)) % step if n > seg else seg - n
    xp = np.pad(x, ((0, 0), (0, pad_end)))
    sess, where = _session(model_path, threads, gpu, model_bytes)
    name = sess.get_inputs()[0].name
    out = np.zeros_like(xp)
    weight = np.zeros(xp.shape[1], dtype=np.float32)
    starts = list(range(0, xp.shape[1] - seg + 1, step))
    fade = np.ones(seg, dtype=np.float32)
    ramp = np.linspace(0, 1, step, dtype=np.float32)
    fade[:step] = ramp
    fade[-step:] = ramp[::-1]
    for k, s0 in enumerate(starts):
        piece = xp[:, s0:s0 + seg]
        S = _stft(piece, n_fft, hop)[:, :dim_f, :dim_t]           # (2, dim_f, dim_t)
        inp = np.concatenate([S.real, S.imag], axis=0)[None]      # (1, 4, dim_f, dim_t): [L.re, R.re, L.im, R.im]
        y = sess.run(None, {name: inp.astype(np.float32)})[0]
        if denoise:
            y2 = sess.run(None, {name: (-inp).astype(np.float32)})[0]
            y = 0.5 * (y - y2)
        y = y[0]
        Y = np.zeros((2, n_fft // 2 + 1, dim_t), dtype=np.complex64)
        Y[:, :dim_f, :] = y[0:2] + 1j * y[2:4]
        est = _istft(Y, n_fft, hop, seg) * spec["compensate"]
        w = fade.copy()
        if k == 0:
            w[:step] = 1.0
        if k == len(starts) - 1:
            w[-step:] = 1.0
        out[:, s0:s0 + seg] += est * w
        weight[s0:s0 + seg] += w
        if progress:
            progress((k + 1) / len(starts))
    out /= np.maximum(weight, 1e-6)
    out = out[:, :n]
    voc = out if spec["vocals"] else x[:, :n] - out
    voc, inst = _from_model_rate(voc, mix, sr, msr, n_in)
    return voc, inst, where


def _stft_full(chunk, T, n_fft, hop):
    """chunk (2, L) -> (1, 2, bins, T, 2) float32, frame t centred at t*hop with reflect padding (the Roformer layout)."""
    L = chunk.shape[1]
    half = n_fft // 2
    win = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(n_fft) / n_fft)).astype(np.float64)
    idx = np.arange(T)[:, None] * hop - half + np.arange(n_fft)[None, :]
    idx = np.where(idx < 0, -idx, idx)
    idx = np.where(idx >= L, 2 * (L - 1) - idx, idx)
    out = np.empty((1, 2, n_fft // 2 + 1, T, 2), dtype=np.float32)
    for c in range(2):
        sp = np.fft.rfft(chunk[c][idx] * win, axis=1).T
        out[0, c, :, :, 0] = sp.real
        out[0, c, :, :, 1] = sp.imag
    return out


def _istft_full(spec, L, n_fft, hop):
    T = spec.shape[3]
    half = n_fft // 2
    win = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(n_fft) / n_fft)).astype(np.float64)
    total = n_fft + hop * (T - 1)
    env = np.zeros(total)
    for t in range(T):
        env[t * hop:t * hop + n_fft] += win * win
    out = np.zeros((2, L), dtype=np.float32)
    for c in range(2):
        sp = spec[0, c, :, :, 0].T + 1j * spec[0, c, :, :, 1].T
        fr = np.fft.irfft(sp, n=n_fft, axis=1) * win
        acc = np.zeros(total)
        for t in range(T):
            acc[t * hop:t * hop + n_fft] += fr[t]
        out[c] = (acc[half:half + L] / np.maximum(env[half:half + L], 1e-11)).astype(np.float32)
    return out


def separate_roformer(mix, sr, model_path, spec=MEL_ROFORMER, progress=None, threads=None, gpu=False, model_bytes=None):
    """Roformer family (the Vocal Remover's 'fast' plan: 8 s windows, one second of crossfade). mix: (n, 2) float at
    any rate. Returns (vocals (n, 2), instrumental (n, 2), where_it_ran)."""
    msr, n_fft, hop = spec["sr"], spec["n_fft"], spec["hop"]
    x, n_in = _to_model_rate(mix, sr, msr)
    n = x.shape[1]
    sess, where = _session(model_path, threads, gpu, model_bytes)
    T = int(sess.get_inputs()[0].shape[3])
    chunk = hop * (T - 1)
    peak = float(np.abs(x).max()) if n else 0.0
    if n == 0 or peak == 0:
        voc, inst = _from_model_rate(np.zeros_like(x), mix, sr, msr, n_in)
        return voc, inst, where
    gain = INPUT_PEAK / peak
    fade = msr
    step = chunk - fade
    starts, off = [], 0
    while off < n:
        if off + chunk >= n:
            tail = max(0, n - chunk)
            if not starts or starts[-1] != tail:
                starts.append(tail)
            break
        starts.append(off)
        off += step

    def window_for(k):
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
            y = sess.run(None, {"spec": _stft_full(part * gain, T, n_fft, hop)})[0]
            est = _istft_full(y, chunk, n_fft, hop)
        else:
            est = np.zeros((2, chunk), dtype=np.float32)
        w = window_for(k)[:size]
        result[:, s:s + size] += est[:, :size] * w
        weight[s:s + size] += w
        if progress:
            progress((k + 1) / len(starts))
    result /= np.maximum(weight, 1e-8)[None, :]
    voc = (result / gain).astype(np.float32)
    voc, inst = _from_model_rate(voc, mix, sr, msr, n_in)
    return voc, inst, where


def separate(mix, sr, model_path, progress=None, threads=None, gpu=False, model_bytes=None):
    """Whichever network the model file is: (vocals, instrumental, where_it_ran). model_bytes: the file's checked bytes
    (read and checked here when not given)."""
    spec = model_spec_for(model_path)
    if spec["kind"] == "roformer":
        return separate_roformer(mix, sr, model_path, spec, progress=progress, threads=threads, gpu=gpu, model_bytes=model_bytes)
    return separate_mdx(mix, sr, model_path, spec, progress=progress, threads=threads, gpu=gpu, model_bytes=model_bytes)


# ---------------------------------------------------------------------------
# background job: separate a stretch of a song file
# ---------------------------------------------------------------------------

JOB_MAX_SECONDS = 60 * 60       # a separation is stopped after max(this, JOB_SECONDS_PER_SECOND x the seconds of song it works on)
JOB_SECONDS_PER_SECOND = 15     # a slow laptop on the processor needs about ten times the song's length
UNKNOWN_SONG_SECONDS = 600.0    # the length assumed when this Python cannot read the song (the program reads more formats)
LONGEST_SONG_SECONDS = 6 * 3600.0


def job_file(home):
    return os.path.join(home, "state", "separate_job.json")


def jobs_dir(home):
    """Where each job gets its own fresh, empty working folder (kit/state/sep/<job>)."""
    return os.path.join(home, "state", "sep")


def records_dir(home):
    """Where the record of every finished pair is kept (kit/state/separations): outside the music folders, so listings of
    imports and captures stay music only."""
    return os.path.join(home, "state", "separations")


def _write(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def _seconds_words(x):
    return ("%.1f s" % x) if abs(x) < 1e6 else ("%g s" % x)


def song_seconds(song):
    """The song's length in seconds, or None when this Python cannot read the file (the Vocal Remover reads more kinds)."""
    try:
        import rapreaper_audio as A
        _d, _sr, info = A.read_audio(song, duration=0.05, max_seconds=None)
        return float(info["file_seconds"])
    except Exception:
        return None


def clamp_stretch(song, start=0.0, seconds=None):
    """(start, seconds, material_seconds) of a request, kept inside the song (seconds None: to the end). Raises ValueError,
    in plain words, when the start lies at or beyond the end, so an absurd number never reaches the program or a file name."""
    try:
        start = float(start or 0.0)
        seconds = None if seconds is None else float(seconds)
    except (TypeError, ValueError):
        raise ValueError("start_seconds and seconds must be numbers.")
    if start != start or (seconds is not None and seconds != seconds) or start in (float("inf"), float("-inf")):
        raise ValueError("start_seconds and seconds must be ordinary numbers.")
    start = max(0.0, start)
    length = song_seconds(song)
    if length is None:
        if start >= LONGEST_SONG_SECONDS:
            raise ValueError("start_seconds (%s) is beyond the end of the song." % _seconds_words(start))
        if seconds is not None:
            seconds = min(seconds, LONGEST_SONG_SECONDS)
        material = seconds if seconds is not None else UNKNOWN_SONG_SECONDS
    else:
        if start >= length - 0.05:
            raise ValueError("start_seconds (%s) is at or beyond the end of the song, which is %s long. Give a start inside the song."
                             % (_seconds_words(start), _seconds_words(length)))
        if seconds is not None and start + seconds >= length:
            seconds = length - start
        material = seconds if seconds is not None else length - start
    start = round(start, 3)
    seconds = None if seconds is None else round(seconds, 3)
    if seconds is not None and seconds <= 0:
        raise ValueError("seconds must be more than 0.")
    return start, seconds, max(0.0, float(material))


def source_of(song):
    """What identifies the song file a job worked on: its real path, size and modification time."""
    p = os.path.realpath(song)
    st = os.stat(p)
    return {"path": os.path.normcase(p), "size": st.st_size, "mtime_ns": st.st_mtime_ns}


def _same_stretch(d, start, seconds):
    try:
        if abs(float(d.get("start") or 0.0) - float(start or 0.0)) > 0.01:
            return False
        if (d.get("seconds") is None) != (seconds is None):
            return False
        return seconds is None or abs(float(d["seconds"]) - float(seconds)) <= 0.01
    except (TypeError, ValueError):
        return False


def _same_path(a, b):
    return bool(a) and bool(b) and os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def instrumental_name(out_wav):
    """The instrumental's file name beside a vocal's: '<x> (Vocals).wav' -> '<x> (Instrumental).wav', otherwise appended."""
    stem = os.path.splitext(out_wav)[0]
    if stem.endswith(" (Vocals)"):
        return stem[: -len(" (Vocals)")] + " (Instrumental).wav"
    return stem + " (Instrumental).wav"


def _numbered(path, k):
    if k <= 1:
        return path
    stem, ext = os.path.splitext(path)
    return "%s (%d)%s" % (stem, k, ext)


def fresh_pair(vocal, instrumental=None, first=1):
    """(k, vocal, instrumental): the wanted names, or with ' (2)', ' (3)' ... added, from numbering `first` on, at which
    NEITHER file exists yet. Nothing is ever written over."""
    instrumental = instrumental or instrumental_name(vocal)
    for k in range(max(1, int(first)), 10000):
        v, i = _numbered(vocal, k), _numbered(instrumental, k)
        if not os.path.lexists(v) and not os.path.lexists(i):
            return k, v, i
    raise RuntimeError("There are too many separated files called %s already." % os.path.basename(vocal))


def _copy_new(src, dst):
    """Copy src to a NEW file dst ('x': refused when dst exists)."""
    made = False
    try:
        with open(src, "rb") as f, open(dst, "xb") as out:
            made = True
            while True:
                b = f.read(1024 * 1024)
                if not b:
                    break
                out.write(b)
    except FileExistsError:
        raise
    except OSError:
        if made and os.path.exists(dst):
            os.remove(dst)                          # the half-written copy this function itself made
        raise


def _move_new(src, dst):
    """Move src to dst, never replacing a file that is at dst (FileExistsError then): a hard link (which the system refuses
    when the name is taken) and the old name removed; where links are not possible, a rename that cannot replace (Windows)
    or an exclusive copy."""
    if os.path.lexists(dst):
        raise FileExistsError(dst)
    try:
        os.link(src, dst)
    except FileExistsError:
        raise
    except (OSError, AttributeError, NotImplementedError):
        if os.name == "nt":
            try:
                os.rename(src, dst)                 # on Windows a rename never replaces an existing file
                return
            except FileExistsError:
                raise
            except OSError:
                pass
        _copy_new(src, dst)
    os.remove(src)                                  # the job's own working copy, now that the final name holds it


def _place_pair(tv, ti, wanted_v, wanted_i, first=1):
    """Move a job's two files from its working folder to the first free pair of names (from numbering `first` on).
    Returns (vocal, instrumental)."""
    k = max(1, int(first or 1))
    while True:
        k, v, i = fresh_pair(wanted_v, wanted_i, k)
        try:
            _move_new(tv, v)
        except FileExistsError:
            k += 1
            continue
        try:
            _move_new(ti, i)
        except FileExistsError:
            _move_new(v, tv)                        # taken in the meantime: take the vocal back and use the next pair
            k += 1
            continue
        return v, i


def _clear_dir(folder):
    """Remove a job's working folder: the files RapReaper's job wrote into it, then the folder itself."""
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for n in names:
        p = os.path.join(folder, n)
        if os.path.isfile(p) and not os.path.islink(p):
            try:
                os.remove(p)                        # the job's log, progress and leftover working files
            except OSError:
                pass
    try:
        os.rmdir(folder)
    except OSError:
        pass


def _prune_jobs(home, keep=None):
    """The working folders of earlier jobs (finished or failed: only one job runs at a time)."""
    root = jobs_dir(home)
    try:
        names = os.listdir(root)
    except OSError:
        return
    for n in names:
        p = os.path.join(root, n)
        if n != keep and os.path.isdir(p) and not os.path.islink(p):
            _clear_dir(p)


def _proc_start(pid):
    """When process `pid` was started, as the system records it (a token to compare, not a clock time): Linux
    /proc/<pid>/stat field 22, Windows GetProcessTimes, macOS `ps -o lstart=`. None when it cannot be read."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if pid <= 0:
        return None
    if os.name == "nt":
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            k32.OpenProcess.restype = ctypes.c_void_p
            h = k32.OpenProcess(0x1000, False, pid)         # PROCESS_QUERY_LIMITED_INFORMATION
            if not h:
                return None
            try:
                c, e, kt, ut = ctypes.c_ulonglong(), ctypes.c_ulonglong(), ctypes.c_ulonglong(), ctypes.c_ulonglong()
                ok = k32.GetProcessTimes(ctypes.c_void_p(h), ctypes.byref(c), ctypes.byref(e), ctypes.byref(kt), ctypes.byref(ut))
                return str(c.value) if ok else None
            finally:
                k32.CloseHandle(ctypes.c_void_p(h))
        except Exception:
            return None
    try:
        with open("/proc/%d/stat" % pid) as f:
            return f.read().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        if os.path.isdir("/proc/self"):
            return None                                     # Linux: no such process
    try:
        r = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           text=True, timeout=10)
        return r.stdout.strip() or None
    except Exception:
        return None


def _stop(pid):
    """Stop the job's own process (only ever called after _ours said it is that very process). Its process alone: never a
    tree of processes by number."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(int(pid)), "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
        else:
            os.kill(int(pid), 15)
    except Exception:
        pass


def way_words(rec):
    """Plain words for how a job separates."""
    if rec.get("way") == "exe":
        return "the Vocal Remover program" + (" (graphics processor allowed)" if rec.get("gpu") else "")
    return "the %s network through ONNX Runtime%s" % (rec.get("model") or "Kim Vocal 2", " (graphics processor asked for)" if rec.get("gpu") else "")


def start_job(home, song, out_wav, start=0.0, seconds=None, way=None, log=None, instrumental=None, again=False):
    """Start separating `song` [start, start+seconds] in a separate process. `out_wav` is the vocal's wanted name and
    `instrumental` the instrumental's (default: beside it); the job takes those names, or the first ' (2)', ' (3)' ... pair
    at which neither file exists, and never writes over a file. Returns the job record. `way`: 'onnx' or 'exe' (default:
    whichever is there, the program first). One job at a time: while one runs, another start is refused (so a chat
    cannot pile up workers)."""
    cur = job_status(home)
    if cur.get("state") == "running":
        raise RuntimeError("A separation is already running (%s, %d%% done, %s so far). Wait for it, or ask for the same "
                           "song and stretch again to follow its progress; a second one would only slow both down."
                           % (os.path.basename(str(cur.get("song") or "")), int(100 * float(cur.get("progress") or 0)),
                              "%d s" % int(time.time() - float(cur.get("started") or time.time()))))
    try:
        start, seconds, material = clamp_stretch(song, start, seconds)
    except ValueError as e:
        raise RuntimeError(str(e))
    w = ways(home)
    if way is None:
        # the Vocal Remover program first (it carries the graphics-processor support and its own tested setting),
        # else the same network through this Python's ONNX Runtime
        way = "exe" if w["exe"] else ("onnx" if (w["onnxruntime"] and w["onnx"]) else None)
    if way == "onnx" and not (w["onnxruntime"] and w["onnx"]):
        way = None
    if way == "exe" and not w["exe"]:
        way = None
    if way is None:
        why = []
        if REJECTED["models"]:
            why.append("a model file was found but it is not the published model (its checksum differs), so it was not "
                       "used: %s" % ", ".join(sorted(set(os.path.basename(p) for p in REJECTED["models"]))))
        if REJECTED["exe"]:
            why.append("the Vocal Remover program is %s" % REJECTED["exe"])
        how = ("Run 'Install RapReaper%s' again: it adds the small Python package ONNX Runtime, joins the Vocal "
               "Remover's model pieces (the files whose names contain '.onnx.part', from the RapReaper download) into "
               "VocalRemover%smodels, and allows the Vocal Remover program it ships (another copy: 'Allow Vocal Remover%s'). "
               "Or separate the song yourself with the Vocal Remover and give its '(Vocals).wav' to learn_song_effects."
               % (PF.LAUNCHER_EXT, os.sep, PF.LAUNCHER_EXT))
        raise RuntimeError("This %s has no way to separate the vocal yet%s. %s"
                           % (PF.MACHINE, (": " + "; ".join(why)) if why else "", how))
    gpu = w["gpu"]
    wanted_i = instrumental or instrumental_name(out_wav)
    os.makedirs(os.path.dirname(os.path.abspath(out_wav)), exist_ok=True)
    os.makedirs(os.path.dirname(os.path.abspath(wanted_i)), exist_ok=True)
    k, vocal, inst = fresh_pair(out_wav, wanted_i)
    # side files an earlier run (or an earlier version) left under these names: RapReaper's own markers, never music
    for old in (out_wav + ".done", out_wav + ".log", vocal + ".done", vocal + ".log",
                os.path.join(os.path.dirname(os.path.abspath(out_wav)), ".separate_progress")):
        try:
            if os.path.isfile(old) and not os.path.islink(old):
                os.remove(old)                              # a stale marker that would otherwise say "done" too early
        except OSError:
            pass
    jid = "%d-%s" % (int(time.time()), os.urandom(4).hex())
    _prune_jobs(home)
    jd = os.path.join(jobs_dir(home), jid)
    os.makedirs(jd)                                         # fresh and empty: the job's files, and only its files, land here
    rec = {"id": jid, "song": song, "source": source_of(song), "wanted": out_wav, "wanted_instrumental": wanted_i, "number": k,
           "out": vocal, "instrumental": inst, "start": start, "seconds": seconds, "material_seconds": round(material, 1),
           "way": way, "dir": jd, "started": time.time(), "state": "running", "progress": 0.0, "pid": None, "pid_start": None,
           "error": None, "gpu": gpu, "again": bool(again),
           "model": w["model"] if way == "onnx" else "the Vocal Remover's own choice"}
    flags = {"creationflags": 0x00000008 | 0x00000200} if os.name == "nt" else {"start_new_session": True}
    env = dict(os.environ, ORT_DISABLE_TELEMETRY="1")      # the program uses ONNX Runtime too: no telemetry there either
    logf = open(os.path.join(jd, "log.txt"), "w")
    try:
        if way == "onnx":
            cmd = [sys.executable, os.path.abspath(__file__), "--worker", home, song, jd, repr(float(start)),
                   repr(float(seconds)) if seconds is not None else "-1", w["onnx"], "gpu" if gpu else "cpu", out_wav, wanted_i, str(k)]
            p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=logf, cwd=jd, env=env, **flags)
        else:
            cmd = [w["exe"], "--in", song, "--out", jd, "--format", "32f"]
            if start > 0:
                cmd += ["--start", "%.2f" % start]
            if seconds:
                cmd += ["--end", "%.2f" % (start + seconds)]
            if gpu:
                cmd += ["--gpu", "on"]
            p = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT, cwd=jd, env=env, **flags)
    finally:
        logf.close()
    rec["pid"] = p.pid
    rec["pid_start"] = _proc_start(p.pid)
    _write(job_file(home), rec)
    if log:
        log("separation started (%s): %s" % (way, " ".join(cmd)))
    return rec


def retry_with_network(home, failed):
    """After the Vocal Remover program failed on a job: start the same job again with the network here, once.
    Returns the new record, or None when there is no network here or this job was a retry already."""
    if failed.get("way") != "exe" or failed.get("retried") or not failed.get("wanted"):
        return None
    w = ways(home)
    if not (w["onnxruntime"] and w["onnx"]):
        return None
    rec = start_job(home, failed["song"], failed["wanted"], start=float(failed.get("start") or 0.0), seconds=failed.get("seconds"),
                    way="onnx", instrumental=failed.get("wanted_instrumental"), again=failed.get("again"))
    rec["retried"] = True
    rec["program_failed"] = str(failed.get("error") or "")[:300]
    _write(job_file(home), rec)
    return rec


def job_matches(rec, song, start, seconds, wanted):
    """True when the job record is the job for exactly this request: the same song file (real path, size and modification
    time), the same stretch and the same wanted vocal name. A failure that was already reported does not count (the next
    request starts afresh)."""
    if not isinstance(rec, dict) or rec.get("state") in (None, "none") or not rec.get("source"):
        return False
    if rec.get("state") == "failed" and rec.get("reported"):
        return False
    try:
        here = source_of(song)
    except (OSError, ValueError):
        return False
    return rec["source"] == here and _same_path(rec.get("wanted"), wanted) and _same_stretch(rec, start, seconds)


def mark_reported(home, rec):
    """Note that a failed job's error was told: the next request for it starts a new job."""
    cur = _read_json(job_file(home))
    if cur and cur.get("id") == rec.get("id") and cur.get("state") == "failed":
        cur["reported"] = True
        _write(job_file(home), cur)


def _record_path(home, vocal):
    base = os.path.basename(vocal)
    safe = "".join(c if (c.isalnum() or c in "._-") else "_" for c in base)[:60]
    key = hashlib.sha256(os.path.normcase(os.path.abspath(vocal)).encode("utf-8", "surrogatepass")).hexdigest()[:12]
    return os.path.join(records_dir(home), "%s-%s.json" % (safe, key))


def _write_record(home, rec):
    """The record of a finished pair: which song file (real path, size, age), which stretch, which way, which job."""
    d = {"vocal": rec["out"], "instrumental": rec["instrumental"], "vocal_bytes": os.path.getsize(rec["out"]),
         "instrumental_bytes": os.path.getsize(rec["instrumental"]), "source": rec["source"], "start": rec["start"],
         "seconds": rec["seconds"], "wanted": rec["wanted"], "way": rec["way"], "model": rec.get("model"),
         "ran_on": rec.get("ran_on"), "job": rec["id"], "made": time.time()}
    os.makedirs(records_dir(home), exist_ok=True)
    _write(_record_path(home, rec["out"]), d)


def finished_pair(home, song, start, seconds, wanted):
    """The newest finished pair made from exactly this song file (same real path, size and modification time) and this
    stretch, for this wanted name: {"vocal", "instrumental", "ran_on", "job", ...}, or None. Never the files of another
    song (one with the same name in another folder included), never a pair whose job has not finished, never a pair whose
    files were removed or changed since."""
    try:
        src = source_of(song)
    except OSError:
        return None
    job = _read_json(job_file(home)) or {}
    cands = []
    try:
        names = os.listdir(records_dir(home))
    except OSError:
        names = []
    for n in names:
        if n.endswith(".json"):
            d = _read_json(os.path.join(records_dir(home), n))
            if d:
                cands.append(d)
    if job.get("state") == "done" and job.get("source"):
        cands.append({"vocal": job.get("out"), "instrumental": job.get("instrumental"), "source": job["source"], "start": job.get("start"),
                      "seconds": job.get("seconds"), "wanted": job.get("wanted"), "way": job.get("way"), "model": job.get("model"),
                      "ran_on": job.get("ran_on"), "job": job.get("id"), "made": job.get("finished") or job.get("started") or 0})
    best = None
    for d in cands:
        if d.get("source") != src or not _same_path(d.get("wanted"), wanted) or not _same_stretch(d, start, seconds):
            continue
        if job.get("id") and d.get("job") == job.get("id") and job.get("state") != "done":
            continue                                        # that job has not finished (or failed): its files are not a result
        v, i = d.get("vocal"), d.get("instrumental")
        try:
            if not (v and i and os.path.isfile(v) and os.path.isfile(i)):
                continue
            if d.get("vocal_bytes") is not None and (os.path.getsize(v) != d["vocal_bytes"] or os.path.getsize(i) != d.get("instrumental_bytes")):
                continue                                    # changed since: not the separation any more
        except OSError:
            continue
        if best is None or float(d.get("made") or 0) > float(best.get("made") or 0):
            best = d
    return best


def _alive(pid):
    if not pid:
        return False
    if os.name == "nt":
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.OpenProcess.restype = ctypes.c_void_p             # a handle is pointer-sized on 64-bit Windows
        h = k32.OpenProcess(0x1000, False, int(pid))
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k32.GetExitCodeProcess(ctypes.c_void_p(h), ctypes.byref(code))
        k32.CloseHandle(ctypes.c_void_p(h))
        return bool(ok) and code.value == 259
    try:
        # a child of this process that has exited stays a zombie (and os.kill would still count it as alive) until it is
        # reaped: reap it here
        done, _status = os.waitpid(int(pid), os.WNOHANG)
        if done:
            return False
    except ChildProcessError:
        pass        # started by another process (an earlier server), so only its existence can be asked
    except OSError:
        return False
    try:
        os.kill(int(pid), 0)
    except OSError:
        return False
    try:    # Linux: a zombie left by another parent is listed with the state Z
        with open("/proc/%d/stat" % int(pid)) as f:
            if f.read().rsplit(")", 1)[1].split()[0] == "Z":
                return False
    except (OSError, IndexError, ValueError):
        pass
    return True


def _ours(rec):
    """Is the job's process still running? True: yes, and it is the very process the job started (its start time, read
    when it was started, still matches). False: it has ended, or the number now belongs to another program. None: a
    process with that number runs but whether it is the job's cannot be told (it is then never stopped)."""
    pid = rec.get("pid")
    if not _alive(pid):
        return False
    want = rec.get("pid_start")
    got = _proc_start(pid)
    if want is None or got is None:
        return None
    return got == want


def job_limit(rec):
    """How long a job may run: an hour, or 15 times the seconds of song it works on when that is longer."""
    try:
        material = float(rec.get("material_seconds") or UNKNOWN_SONG_SECONDS)
    except (TypeError, ValueError):
        material = UNKNOWN_SONG_SECONDS
    return max(JOB_MAX_SECONDS, JOB_SECONDS_PER_SECOND * material)


def _wav_complete(path):
    """A WAV file written to its end: a RIFF header whose size agrees with the file, and a data chunk that fits (a program
    that stopped half-way leaves a header that promises more than the file holds)."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            head = f.read(12)
            if len(head) < 12 or head[:4] not in (b"RIFF", b"RF64") or head[8:12] != b"WAVE":
                return False
            if head[:4] == b"RIFF" and int.from_bytes(head[4:8], "little") + 8 > size:
                return False
            pos = 12
            for _n in range(64):
                f.seek(pos)
                ch = f.read(8)
                if len(ch) < 8:
                    return False
                n = int.from_bytes(ch[4:8], "little")
                if ch[:4] == b"data":
                    return n > 0 and (n == 0xFFFFFFFF or pos + 8 + n <= size)
                pos += 8 + n + (n & 1)
    except OSError:
        return False
    return False


def _program_result(rec, ended):
    """The two files the Vocal Remover program wrote into the job's own folder, taken only from the exact 'Instrumental:'
    and 'Vocals:' lines it prints once both are written, and only when both files are complete WAVs. A program that
    stopped without those lines has no result, whatever files it left (they may be half-written)."""
    jd = rec["dir"]
    real_jd = os.path.normcase(os.path.realpath(jd))
    text = _tail(os.path.join(jd, "log.txt"), 20000)
    got = {}
    for line in text.replace("\r", "\n").splitlines():
        for label in ("Instrumental", "Vocals"):
            if line.startswith(label + ":"):
                got[label] = line.split(":", 1)[1].strip()

    def inside(p):
        return p and os.path.normcase(os.path.realpath(os.path.dirname(os.path.abspath(p)))) == real_jd and os.path.isfile(p) \
            and not os.path.islink(p) and _wav_complete(p)
    ti, tv = got.get("Instrumental"), got.get("Vocals")
    if inside(ti) and inside(tv) and not _same_path(ti, tv):
        return tv, ti
    return None


def _finish(home, rec, ended=False):
    """When the job has its result: move it to its final names (never over a file), keep the record of the pair, mark the
    job done and clear its working folder. Returns True then."""
    jd = rec["dir"]
    if rec.get("way") == "exe":
        found = _program_result(rec, ended)
        if not found:
            return False
        v, i = _place_pair(found[0], found[1], rec["wanted"], rec["wanted_instrumental"], rec.get("number") or 1)
        ran_on = _ran_on_from_log(os.path.join(jd, "log.txt"))
    else:
        d = _read_json(os.path.join(jd, "done.json"))
        if not d or not d.get("vocal") or not d.get("instrumental"):
            return False
        v, i, ran_on = d["vocal"], d["instrumental"], str(d.get("where") or "processor")[:200]
        if not (os.path.isfile(v) and os.path.isfile(i)):
            return False
    rec.update({"state": "done", "progress": 1.0, "out": v, "instrumental": i, "ran_on": ran_on, "finished": time.time()})
    _write(job_file(home), rec)
    _write_record(home, rec)
    _clear_dir(jd)
    return True


def _program_progress(path):
    """The last 'NN%' the Vocal Remover program printed, as 0..1 (None when there is none yet)."""
    pct = None
    for chunk in _tail(path, 600).replace("\n", "\r").split("\r"):
        words = chunk.strip()[:-1].split() if chunk.strip().endswith("%") else []
        if words and words[-1].isdigit():
            pct = min(100, int(words[-1]))
    return None if pct is None else pct / 100.0


def job_status(home):
    """The current job record with 'state' running / done / failed / none, progress 0..1 and, when done, 'out'
    (the vocal), 'instrumental' and 'ran_on'. A finished job is recognised first (whatever its age); only a job whose
    process is still RapReaper's own (same number AND same start time) is ever stopped, after max(an hour, 15 x the
    seconds of song)."""
    rec = _read_json(job_file(home))
    if not rec:
        return {"state": "none"}
    if rec.get("state") != "running":
        return rec
    if not rec.get("dir"):
        # a job started by an earlier version of RapReaper: never stopped from here, never reported as finished
        if _alive(rec.get("pid")):
            return rec
        rec["state"], rec["error"] = "failed", "it was started by an earlier version of RapReaper; start it again"
        _write(job_file(home), rec)
        return rec
    jd = rec["dir"]
    try:
        if _finish(home, rec):
            return rec
    except OSError as e:
        rec["state"], rec["error"] = "failed", "its result could not be put in place: %s" % e
        _write(job_file(home), rec)
        return rec
    try:
        if rec.get("way") == "exe":
            p = _program_progress(os.path.join(jd, "log.txt"))
            if p is not None:
                rec["progress"] = p
        else:
            with open(os.path.join(jd, "progress")) as f:
                rec["progress"] = float(f.read().strip() or 0)
    except (OSError, ValueError):
        pass
    ours = _ours(rec)
    if ours is not False and time.time() - float(rec.get("started") or time.time()) > job_limit(rec):
        if ours:
            _stop(rec.get("pid"))
        rec["state"] = "failed"
        rec["error"] = "it took longer than %d minutes and was stopped" % (job_limit(rec) // 60) if ours else \
            "it took longer than %d minutes; its process could not be confirmed as RapReaper's own, so it was left alone" % (job_limit(rec) // 60)
        _write(job_file(home), rec)
        return rec
    if ours is False:
        try:
            if _finish(home, rec, ended=True):              # it may have finished between the first look and now
                return rec
        except OSError as e:
            rec["error"] = "its result could not be put in place: %s" % e
        rec["state"] = "failed"
        rec["error"] = rec.get("error") or _tail(os.path.join(jd, "log.txt")) or (
            "the Vocal Remover program stopped without writing a (Vocals).wav" if rec.get("way") == "exe" else "the separation process stopped early")
        _write(job_file(home), rec)
    return rec


def _ran_on_from_log(path):
    text = _tail(path, 4000)
    for line in text.replace("\r", "\n").splitlines():
        if "on the " in line and "Done in" in line:
            return line.split("on the ", 1)[1].strip()
    return "processor"


def _tail(path, n=600):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()[-n:].strip()
    except OSError:
        return ""


def _worker(home, song, jd, start, seconds, model_path, gpu, wanted_v, wanted_i, first):
    """The separation process: reads the stretch, checks the model's bytes and loads exactly those, writes the two files into
    the job's own folder, then moves them to the first free pair of final names and leaves done.json there."""
    os.environ["ORT_DISABLE_TELEMETRY"] = "1"
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import rapreaper_audio as A
    prog = os.path.join(jd, "progress")

    def note(p):
        try:
            with open(prog, "w") as f:
                f.write("%.3f" % p)
        except OSError:
            pass

    model_bytes = verified_model_bytes(model_path)
    data, sr, _ = A.read_audio(song, start=start, duration=(seconds if seconds and seconds > 0 else None), max_seconds=None)
    voc, inst, where = separate(data, sr, model_path, progress=note, gpu=gpu, model_bytes=model_bytes)
    del model_bytes
    # 32-bit float, like the Vocal Remover's "32f" setting: the two files add up to the song exactly, and a part
    # that peaks above full scale (common with loud songs) is kept as it is instead of being clipped
    tv, ti = os.path.join(jd, "vocals.wav"), os.path.join(jd, "instrumental.wav")
    A.write_wav(tv, voc, sr, 32)
    A.write_wav(ti, inst, sr, 32)
    v, i = _place_pair(tv, ti, wanted_v, wanted_i, first)
    _write(os.path.join(jd, "done.json"), {"where": where, "vocal": v, "instrumental": i})


if __name__ == "__main__":
    if len(sys.argv) >= 12 and sys.argv[1] == "--worker":
        _home, _song, _dir, _start, _secs, _model, _gpu, _wv, _wi, _k = sys.argv[2:12]
        _worker(_home, _song, _dir, float(_start), float(_secs), _model, _gpu == "gpu", _wv, _wi, int(_k))
    else:
        print(__doc__)
