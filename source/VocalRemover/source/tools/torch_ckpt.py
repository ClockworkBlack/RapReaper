"""Read a PyTorch checkpoint (the zip form torch.save writes) into NumPy arrays without PyTorch.

Only what a state_dict needs: tensors become NumPy arrays (float32/float16/bfloat16/int64/bool...), dicts stay
dicts. Anything else (optimizer state objects, Python classes) comes back as a placeholder, which is fine: the
model builders only read the weights.

    from torch_ckpt import load_state_dict
    sd = load_state_dict('model.ckpt')      # {name: np.ndarray}
"""
import pickle
import zipfile

import numpy as np

_DTYPES = {
    "FloatStorage": np.float32, "DoubleStorage": np.float64, "HalfStorage": np.float16,
    "LongStorage": np.int64, "IntStorage": np.int32, "ShortStorage": np.int16, "CharStorage": np.int8,
    "ByteStorage": np.uint8, "BoolStorage": np.bool_, "BFloat16Storage": "bfloat16",
}


class _Placeholder:
    def __init__(self, *a, **k):
        self.args = a

    def __call__(self, *a, **k):
        return _Placeholder(*a)

    def __setstate__(self, state):
        self.state = state


class _Storage:
    def __init__(self, dtype, key, nbytes):
        self.dtype, self.key, self.nbytes = dtype, key, nbytes


def _rebuild_tensor(storage, offset, size, stride, *rest):
    return ("tensor", storage, int(offset), tuple(int(s) for s in size), tuple(int(s) for s in stride))


def _rebuild_parameter(data, requires_grad=False, backward_hooks=None, *rest):
    return data


class _Unpickler(pickle.Unpickler):
    def __init__(self, f, loader):
        super().__init__(f)
        self.loader = loader

    def find_class(self, module, name):
        if module == "torch._utils" and name in ("_rebuild_tensor_v2", "_rebuild_tensor_v3"):
            return _rebuild_tensor
        if module == "torch._utils" and name == "_rebuild_parameter":
            return _rebuild_parameter
        if module == "torch" and name in _DTYPES:
            return _DTYPES[name]
        if module == "collections" and name == "OrderedDict":
            import collections
            return collections.OrderedDict
        if module == "torch" and name in ("float32", "float16", "bfloat16", "float64", "int64"):
            return name
        return _Placeholder

    def persistent_load(self, pid):
        # ('storage', dtype, key, location, numel)
        if isinstance(pid, tuple) and pid and pid[0] == "storage":
            dtype, key, _loc, numel = pid[1], pid[2], pid[3], pid[4]
            return _Storage(dtype, str(key), int(numel))
        raise pickle.UnpicklingError("unknown persistent id %r" % (pid,))


def _materialize(obj, zf, prefix, cache):
    if isinstance(obj, tuple) and obj and obj[0] == "tensor":
        _, storage, offset, size, stride = obj
        if storage.key not in cache:
            with zf.open(prefix + "data/" + storage.key) as f:
                raw = f.read()
            dt = storage.dtype
            if dt == "bfloat16":
                u = np.frombuffer(raw, dtype=np.uint16).astype(np.uint32) << 16
                arr = u.view(np.float32)
            else:
                arr = np.frombuffer(raw, dtype=dt)
            cache[storage.key] = arr
        base = cache[storage.key]
        if len(size) == 0:
            return np.array(base[offset], dtype=base.dtype)
        itemsize = base.dtype.itemsize
        return np.lib.stride_tricks.as_strided(base[offset:], shape=size, strides=tuple(s * itemsize for s in stride)).copy()
    if isinstance(obj, dict):
        return {k: _materialize(v, zf, prefix, cache) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)) and not (isinstance(obj, tuple) and obj and obj[0] == "tensor"):
        return [_materialize(v, zf, prefix, cache) for v in obj]
    return obj


def load(path):
    """Everything in the checkpoint, tensors as NumPy arrays."""
    zf = zipfile.ZipFile(path)
    pkl = [n for n in zf.namelist() if n.endswith("/data.pkl") or n == "data.pkl"]
    if not pkl:
        raise ValueError("%s is not a PyTorch zip checkpoint" % path)
    prefix = pkl[0][: -len("data.pkl")]
    with zf.open(pkl[0]) as f:
        obj = _Unpickler(f, None).load()
    return _materialize(obj, zf, prefix, {})


def load_state_dict(path):
    """The model weights: {name: np.ndarray}. Accepts a bare state_dict or a training checkpoint with 'state_dict' /
    'model' / 'state' inside; strips a 'module.' or 'model.' prefix if every key carries it."""
    obj = load(path)
    sd = obj
    for key in ("state_dict", "model_state_dict", "model", "state"):
        if isinstance(sd, dict) and key in sd and isinstance(sd[key], dict) and sd[key] and all(
                isinstance(v, np.ndarray) for v in list(sd[key].values())[:5]):
            sd = sd[key]
            break
    if not isinstance(sd, dict):
        raise ValueError("no state_dict in %s" % path)
    sd = {k: v for k, v in sd.items() if isinstance(v, np.ndarray)}
    for pre in ("module.", "model."):
        if sd and all(k.startswith(pre) for k in sd):
            sd = {k[len(pre):]: v for k, v in sd.items()}
    return sd


if __name__ == "__main__":
    import sys
    sd = load_state_dict(sys.argv[1])
    total = 0
    for k, v in sd.items():
        total += v.size
        if len(sys.argv) > 2:
            print(k, v.shape, v.dtype)
    print("%d tensors, %.1f M values" % (len(sd), total / 1e6))
