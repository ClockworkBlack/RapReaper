"""Minimal ONNX protobuf writer (no onnx package needed).

Implements just enough of onnx.proto (ModelProto / GraphProto / NodeProto / TensorProto /
AttributeProto / ValueInfoProto) to serialise a graph that onnxruntime will load.
Field numbers follow onnx/onnx.proto (IR version 8).
"""
import struct
import numpy as np

# ---- protobuf wire helpers ---------------------------------------------------

def _varint(v):
    out = bytearray()
    v &= (1 << 64) - 1
    while True:
        b = v & 0x7F
        v >>= 7
        if v:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _key(field, wtype):
    return _varint((field << 3) | wtype)


def f_varint(field, v):
    return _key(field, 0) + _varint(v)


def f_bytes(field, b):
    return _key(field, 2) + _varint(len(b)) + b


def f_str(field, s):
    return f_bytes(field, s.encode('utf-8'))


def f_float(field, v):
    return _key(field, 5) + struct.pack('<f', v)


# ---- ONNX messages -------------------------------------------------------------

DT = {np.dtype('float32'): 1, np.dtype('uint8'): 2, np.dtype('int8'): 3, np.dtype('int32'): 6,
      np.dtype('int64'): 7, np.dtype('bool'): 9, np.dtype('float16'): 10}


def tensor_proto(name, arr):
    arr = np.ascontiguousarray(arr)
    b = b''
    for d in arr.shape:
        b += f_varint(1, d)
    b += f_varint(2, DT[arr.dtype])
    b += f_str(8, name)
    b += f_bytes(9, arr.tobytes())
    return b


def attr_proto(name, value):
    b = f_str(1, name)
    if isinstance(value, bool):
        b += f_varint(3, int(value)) + f_varint(20, 2)
    elif isinstance(value, int):
        b += f_varint(3, value) + f_varint(20, 2)
    elif isinstance(value, float):
        b += f_float(2, value) + f_varint(20, 1)
    elif isinstance(value, str):
        b += f_bytes(4, value.encode()) + f_varint(20, 3)
    elif isinstance(value, np.ndarray):
        b += f_bytes(5, tensor_proto('', value)) + f_varint(20, 4)
    elif isinstance(value, (list, tuple)):
        if all(isinstance(x, int) for x in value):
            for x in value:
                b += f_varint(8, x)
            b += f_varint(20, 7)
        elif all(isinstance(x, float) for x in value):
            for x in value:
                b += f_float(7, x)
            b += f_varint(20, 6)
        else:
            raise TypeError(value)
    else:
        raise TypeError(value)
    return b


def node_proto(op, inputs, outputs, name='', domain='', **attrs):
    b = b''
    for i in inputs:
        b += f_str(1, i)
    for o in outputs:
        b += f_str(2, o)
    if name:
        b += f_str(3, name)
    b += f_str(4, op)
    for k, v in attrs.items():
        b += f_bytes(5, attr_proto(k, v))
    if domain:
        b += f_str(7, domain)
    return b


def value_info(name, elem_type, shape):
    dims = b''
    for d in shape:
        if isinstance(d, str):
            dims += f_bytes(1, f_str(2, d))
        else:
            dims += f_bytes(1, f_varint(1, d))
    tt = f_varint(1, elem_type) + f_bytes(2, dims)
    tp = f_bytes(1, tt)
    return f_str(1, name) + f_bytes(2, tp)


def graph_proto(name, nodes, initializers, inputs, outputs):
    b = b''
    for n in nodes:
        b += f_bytes(1, n)
    b += f_str(2, name)
    for t in initializers:
        b += f_bytes(5, t)
    for i in inputs:
        b += f_bytes(11, i)
    for o in outputs:
        b += f_bytes(12, o)
    return b


def model_proto(graph, opset=17, producer='vocalremover-onnxwriter', extra_opsets=()):
    b = f_varint(1, 8)                       # ir_version 8
    b += f_str(2, producer)
    b += f_bytes(7, graph)
    b += f_bytes(8, f_str(1, '') + f_varint(2, opset))
    for dom, ver in extra_opsets:
        b += f_bytes(8, f_str(1, dom) + f_varint(2, ver))
    return b


# ---- graph builder -------------------------------------------------------------

class Graph:
    def __init__(self):
        self.nodes = []
        self.inits = []
        self.inputs = []
        self.outputs = []
        self._n = 0
        self._consts = {}

    def name(self, hint='t'):
        self._n += 1
        return f'{hint}_{self._n}'

    def const(self, arr, hint='c'):
        arr = np.ascontiguousarray(arr)
        key = None
        if arr.size <= 16:
            key = (hint, arr.dtype.str, arr.shape, arr.tobytes())
            if key in self._consts:
                return self._consts[key]
        n = self.name(hint)
        self.inits.append(tensor_proto(n, arr))
        if key is not None:
            self._consts[key] = n
        return n

    def i64(self, vals, hint='shape'):
        return self.const(np.asarray(vals, dtype=np.int64), hint)

    def op(self, op_type, inputs, n_out=1, hint=None, domain='', out_names=None, **attrs):
        outs = out_names or [self.name(hint or op_type.lower()) for _ in range(n_out)]
        self.nodes.append(node_proto(op_type, list(inputs), outs, domain=domain, **attrs))
        return outs[0] if n_out == 1 else outs

    def input(self, name, shape, elem_type=1):
        self.inputs.append(value_info(name, elem_type, shape))
        return name

    def output(self, name, shape, elem_type=1):
        self.outputs.append(value_info(name, elem_type, shape))

    # convenience ------------------------------------------------------------
    def reshape(self, x, shape):
        return self.op('Reshape', [x, self.i64(shape)])

    def transpose(self, x, perm):
        return self.op('Transpose', [x], perm=list(perm))

    def matmul(self, x, w):
        return self.op('MatMul', [x, w])

    def add(self, a, b):
        return self.op('Add', [a, b])

    def mul(self, a, b):
        return self.op('Mul', [a, b])

    def sub(self, a, b):
        return self.op('Sub', [a, b])

    def div(self, a, b):
        return self.op('Div', [a, b])

    def concat(self, xs, axis):
        return self.op('Concat', xs, axis=axis)

    def split(self, x, sizes, axis):
        return self.op('Split', [x, self.i64(sizes, 'split')], n_out=len(sizes), axis=axis)

    def unsqueeze(self, x, axes):
        return self.op('Unsqueeze', [x, self.i64(axes, 'axes')])

    def squeeze(self, x, axes):
        return self.op('Squeeze', [x, self.i64(axes, 'axes')])

    def serialize(self, name='g', opset=17, extra_opsets=()):
        g = graph_proto(name, self.nodes, self.inits, self.inputs, self.outputs)
        return model_proto(g, opset=opset, extra_opsets=extra_opsets)
