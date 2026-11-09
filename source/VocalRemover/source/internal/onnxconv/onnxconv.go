// Package onnxconv turns the processor model file (int8 weights, dynamic activation quantisation) into
// the graphics-processor model file (float16 weights and matrix products, everything else float32).
//
// The two files hold the same network: for every quantised linear layer the processor file carries the
// int8 weights and one scale per output column; the graphics file carries those weights multiplied back
// out, stored as float16. Graphics processors (DirectML, CoreML) run float16 matrix products natively and
// do not run the dynamic-quantisation operators, which is why the second file exists. It is generated on
// this computer from the first (nothing is downloaded), deterministically, so its checksum is known in
// advance and checked like every other model file.
//
// Only the handful of protobuf messages involved are interpreted (ModelProto, GraphProto, NodeProto,
// TensorProto); everything else is copied through byte for byte.
package onnxconv

import (
	"encoding/binary"
	"errors"
	"fmt"
	"math"
)

// ---- protobuf wire format -------------------------------------------------------------------------

type field struct {
	num   int
	wtype int
	val   uint64 // varint / fixed values
	data  []byte // length-delimited payload
}

func readVarint(b []byte) (uint64, int, error) {
	var v uint64
	for i := 0; i < len(b) && i < 10; i++ {
		v |= uint64(b[i]&0x7F) << (7 * uint(i))
		if b[i] < 0x80 {
			return v, i + 1, nil
		}
	}
	return 0, 0, errors.New("bad varint")
}

func splitFields(b []byte) ([]field, error) {
	var out []field
	for len(b) > 0 {
		key, n, err := readVarint(b)
		if err != nil {
			return nil, err
		}
		b = b[n:]
		f := field{num: int(key >> 3), wtype: int(key & 7)}
		switch f.wtype {
		case 0:
			v, n, err := readVarint(b)
			if err != nil {
				return nil, err
			}
			f.val, b = v, b[n:]
		case 1:
			if len(b) < 8 {
				return nil, errors.New("truncated fixed64")
			}
			f.val, b = binary.LittleEndian.Uint64(b), b[8:]
		case 2:
			l, n, err := readVarint(b)
			if err != nil {
				return nil, err
			}
			b = b[n:]
			if uint64(len(b)) < l {
				return nil, errors.New("truncated field")
			}
			f.data, b = b[:l], b[l:]
		case 5:
			if len(b) < 4 {
				return nil, errors.New("truncated fixed32")
			}
			f.val, b = uint64(binary.LittleEndian.Uint32(b)), b[4:]
		default:
			return nil, fmt.Errorf("unsupported wire type %d", f.wtype)
		}
		out = append(out, f)
	}
	return out, nil
}

func putVarint(dst []byte, v uint64) []byte {
	for v >= 0x80 {
		dst = append(dst, byte(v)|0x80)
		v >>= 7
	}
	return append(dst, byte(v))
}

func putField(dst []byte, f field) []byte {
	dst = putVarint(dst, uint64(f.num)<<3|uint64(f.wtype))
	switch f.wtype {
	case 0:
		dst = putVarint(dst, f.val)
	case 1:
		dst = binary.LittleEndian.AppendUint64(dst, f.val)
	case 2:
		dst = putVarint(dst, uint64(len(f.data)))
		dst = append(dst, f.data...)
	case 5:
		dst = binary.LittleEndian.AppendUint32(dst, uint32(f.val))
	}
	return dst
}

func bytesField(num int, data []byte) field { return field{num: num, wtype: 2, data: data} }
func varintField(num int, v uint64) field   { return field{num: num, wtype: 0, val: v} }

// ---- the messages we read ------------------------------------------------------------------------

type node struct {
	raw     []byte
	inputs  []string
	outputs []string
	opType  string
}

func parseNode(raw []byte) (node, error) {
	fs, err := splitFields(raw)
	if err != nil {
		return node{}, err
	}
	n := node{raw: raw}
	for _, f := range fs {
		switch f.num {
		case 1:
			n.inputs = append(n.inputs, string(f.data))
		case 2:
			n.outputs = append(n.outputs, string(f.data))
		case 4:
			n.opType = string(f.data)
		}
	}
	return n, nil
}

func makeNode(op string, inputs, outputs []string, attrs ...field) []byte {
	var b []byte
	for _, in := range inputs {
		b = putField(b, bytesField(1, []byte(in)))
	}
	for _, out := range outputs {
		b = putField(b, bytesField(2, []byte(out)))
	}
	b = putField(b, bytesField(4, []byte(op)))
	for _, a := range attrs {
		b = putField(b, bytesField(5, a.data))
	}
	return b
}

// attrInt encodes AttributeProto{name, i, type=INT}.
func attrInt(name string, v int64) field {
	var b []byte
	b = putField(b, bytesField(1, []byte(name)))
	b = putField(b, varintField(3, uint64(v)))
	b = putField(b, varintField(20, 2))
	return bytesField(5, b)
}

type tensor struct {
	raw      []byte
	name     string
	dataType int
	dims     []int64
	rawData  []byte
	floats   []byte // float_data (field 4) when the writer used it instead of raw_data
}

func parseTensor(raw []byte) (tensor, error) {
	fs, err := splitFields(raw)
	if err != nil {
		return tensor{}, err
	}
	t := tensor{raw: raw}
	for _, f := range fs {
		switch f.num {
		case 1:
			if f.wtype == 0 {
				t.dims = append(t.dims, int64(f.val))
			} else { // packed
				b := f.data
				for len(b) > 0 {
					v, n, err := readVarint(b)
					if err != nil {
						return tensor{}, err
					}
					t.dims = append(t.dims, int64(v))
					b = b[n:]
				}
			}
		case 2:
			t.dataType = int(f.val)
		case 8:
			t.name = string(f.data)
		case 9:
			t.rawData = f.data
		case 4:
			if f.wtype == 2 {
				t.floats = f.data
			}
		}
	}
	return t, nil
}

func makeTensor(name string, dataType int, dims []int64, raw []byte) []byte {
	var b []byte
	for _, d := range dims {
		b = putField(b, varintField(1, uint64(d)))
	}
	b = putField(b, varintField(2, uint64(dataType)))
	b = putField(b, bytesField(8, []byte(name)))
	b = putField(b, bytesField(9, raw))
	return b
}

const (
	dtFloat   = 1
	dtInt8    = 3
	dtFloat16 = 10
)

// Float16 converts a float32 to IEEE half precision with round-to-nearest-even (the result is the
// same on every computer, so the converted file's checksum can be checked).
func Float16(f float32) uint16 {
	bits := math.Float32bits(f)
	sign := uint16(bits>>16) & 0x8000
	exp := int((bits >> 23) & 0xFF)
	mant := bits & 0x7FFFFF
	switch {
	case exp == 0xFF: // inf or nan
		if mant != 0 {
			return sign | 0x7E00
		}
		return sign | 0x7C00
	case exp > 112+30: // too large: infinity
		return sign | 0x7C00
	case exp >= 113: // normal half
		e := uint16(exp - 112)
		m := mant >> 13
		round := mant & 0x1FFF
		if round > 0x1000 || (round == 0x1000 && m&1 == 1) {
			m++
			if m == 0x400 { // mantissa overflow carries into the exponent
				m = 0
				e++
				if e >= 31 {
					return sign | 0x7C00
				}
			}
		}
		return sign | e<<10 | uint16(m)
	case exp >= 103: // subnormal half
		shift := uint(126 - exp) // 14..23
		full := mant | 0x800000
		m := full >> shift
		rem := full & (1<<shift - 1)
		halfway := uint32(1) << (shift - 1)
		if rem > halfway || (rem == halfway && m&1 == 1) {
			m++
		}
		return sign | uint16(m)
	default:
		return sign
	}
}

// Convert rewrites an int8 processor model into the float16 graphics model. It returns the new file
// and how many linear layers were converted.
func Convert(model []byte) ([]byte, int, error) {
	top, err := splitFields(model)
	if err != nil {
		return nil, 0, fmt.Errorf("not an ONNX file: %v", err)
	}
	var graphIdx = -1
	for i, f := range top {
		if f.num == 7 && f.wtype == 2 {
			if graphIdx >= 0 {
				return nil, 0, errors.New("more than one graph")
			}
			graphIdx = i
		}
	}
	if graphIdx < 0 {
		return nil, 0, errors.New("no graph in the model")
	}
	gfs, err := splitFields(top[graphIdx].data)
	if err != nil {
		return nil, 0, err
	}
	var nodes []node
	inits := map[string]tensor{}
	var initOrder []string
	for _, f := range gfs {
		switch {
		case f.num == 1 && f.wtype == 2:
			n, err := parseNode(f.data)
			if err != nil {
				return nil, 0, err
			}
			nodes = append(nodes, n)
		case f.num == 5 && f.wtype == 2:
			t, err := parseTensor(f.data)
			if err != nil {
				return nil, 0, err
			}
			inits[t.name] = t
			initOrder = append(initOrder, t.name)
		}
	}
	// index nodes by the tensor they produce
	producer := map[string]int{}
	for i, n := range nodes {
		for _, o := range n.outputs {
			producer[o] = i
		}
	}
	consumers := map[string][]int{}
	for i, n := range nodes {
		for _, in := range n.inputs {
			consumers[in] = append(consumers[in], i)
		}
	}
	// find the pattern DynamicQuantizeLinear -> MatMulInteger -> Cast -> Mul(., Mul(scale_x, scale_w))
	replaced := map[int]bool{}
	newNodes := map[int][][]byte{} // index of the DynamicQuantizeLinear node -> replacement nodes
	halfWeights := map[string]string{}
	var newInits [][]byte
	converted := 0
	for i, dq := range nodes {
		if dq.opType != "DynamicQuantizeLinear" || len(dq.outputs) != 3 {
			continue
		}
		xq, xs, xzp := dq.outputs[0], dq.outputs[1], dq.outputs[2]
		mmIdx := soleConsumer(consumers, xq)
		if mmIdx < 0 || nodes[mmIdx].opType != "MatMulInteger" || len(nodes[mmIdx].inputs) < 3 {
			continue
		}
		mm := nodes[mmIdx]
		if mm.inputs[0] != xq || mm.inputs[2] != xzp {
			continue
		}
		wqName := mm.inputs[1]
		castIdx := soleConsumer(consumers, mm.outputs[0])
		if castIdx < 0 || nodes[castIdx].opType != "Cast" {
			continue
		}
		scaleIdx := soleConsumer(consumers, xs)
		if scaleIdx < 0 || nodes[scaleIdx].opType != "Mul" || len(nodes[scaleIdx].inputs) != 2 {
			continue
		}
		sc := nodes[scaleIdx]
		wsName := sc.inputs[1]
		if sc.inputs[0] != xs {
			wsName = sc.inputs[0]
		}
		outIdx := soleConsumer(consumers, nodes[castIdx].outputs[0])
		if outIdx < 0 || nodes[outIdx].opType != "Mul" {
			continue
		}
		mul := nodes[outIdx]
		if !(mul.inputs[0] == nodes[castIdx].outputs[0] && mul.inputs[1] == sc.outputs[0]) &&
			!(mul.inputs[1] == nodes[castIdx].outputs[0] && mul.inputs[0] == sc.outputs[0]) {
			continue
		}
		wq, ok := inits[wqName]
		ws, ok2 := inits[wsName]
		if !ok || !ok2 || wq.dataType != dtInt8 || ws.dataType != dtFloat || len(wq.dims) != 2 {
			continue
		}
		half, done := halfWeights[wqName]
		if !done {
			data, err := dequantizeHalf(wq, ws)
			if err != nil {
				return nil, 0, fmt.Errorf("weights %s: %v", wqName, err)
			}
			half = wqName + ".h"
			halfWeights[wqName] = half
			newInits = append(newInits, makeTensor(half, dtFloat16, wq.dims, data))
		}
		x := dq.inputs[0]
		y := mul.outputs[0]
		xh, yh := y+".x16", y+".y16"
		newNodes[i] = [][]byte{
			makeNode("Cast", []string{x}, []string{xh}, attrInt("to", dtFloat16)),
			makeNode("MatMul", []string{xh, half}, []string{yh}),
			makeNode("Cast", []string{yh}, []string{y}, attrInt("to", dtFloat)),
		}
		for _, k := range []int{mmIdx, castIdx, scaleIdx, outIdx} {
			replaced[k] = true
		}
		converted++
	}
	if converted == 0 {
		return nil, 0, errors.New("no quantised layers found; this is not the processor model file")
	}
	// which initializers are still used
	used := map[string]bool{}
	for i, n := range nodes {
		if replaced[i] || newNodes[i] != nil {
			continue
		}
		for _, in := range n.inputs {
			used[in] = true
		}
	}
	for _, reps := range newNodes {
		for _, raw := range reps {
			n, _ := parseNode(raw)
			for _, in := range n.inputs {
				used[in] = true
			}
		}
	}
	// rebuild the graph: nodes in order (with replacements in place), initializers minus the dropped
	// ones, in the field order our writer uses: nodes (1), name (2), initializers (5), then everything
	// else as it was (inputs 11, outputs 12, ...).
	var g []byte
	for i, n := range nodes {
		if replaced[i] {
			continue
		}
		if reps, ok := newNodes[i]; ok {
			for _, raw := range reps {
				g = putField(g, bytesField(1, raw))
			}
			continue
		}
		g = putField(g, bytesField(1, n.raw))
	}
	for _, f := range gfs {
		if f.num == 2 {
			g = putField(g, f)
		}
	}
	for _, name := range initOrder {
		if !used[name] {
			continue
		}
		g = putField(g, bytesField(5, inits[name].raw))
	}
	for _, raw := range newInits {
		g = putField(g, bytesField(5, raw))
	}
	for _, f := range gfs {
		if f.num == 1 || f.num == 2 || f.num == 5 {
			continue
		}
		g = putField(g, f)
	}
	var out []byte
	for i, f := range top {
		if i == graphIdx {
			out = putField(out, bytesField(7, g))
			continue
		}
		if f.num == 2 && f.wtype == 2 { // producer_name
			out = putField(out, bytesField(2, []byte(string(f.data)+" (float16 graphics variant)")))
			continue
		}
		out = putField(out, f)
	}
	return out, converted, nil
}

func soleConsumer(consumers map[string][]int, name string) int {
	c := consumers[name]
	if len(c) != 1 {
		return -1
	}
	return c[0]
}

// dequantizeHalf multiplies int8 weights (in, out) by their per-column scales and stores float16.
func dequantizeHalf(wq, ws tensor) ([]byte, error) {
	in, out := int(wq.dims[0]), int(wq.dims[1])
	if len(wq.rawData) != in*out {
		return nil, fmt.Errorf("int8 data has %d bytes, expected %d", len(wq.rawData), in*out)
	}
	scales, err := floats(ws)
	if err != nil {
		return nil, err
	}
	if len(scales) != out {
		return nil, fmt.Errorf("%d scales for %d columns", len(scales), out)
	}
	buf := make([]byte, 2*in*out)
	for r := 0; r < in; r++ {
		row := wq.rawData[r*out : (r+1)*out]
		for c, q := range row {
			v := float32(int8(q)) * scales[c]
			binary.LittleEndian.PutUint16(buf[2*(r*out+c):], Float16(v))
		}
	}
	return buf, nil
}

func floats(t tensor) ([]float32, error) {
	if t.rawData != nil {
		if len(t.rawData)%4 != 0 {
			return nil, errors.New("odd float data")
		}
		out := make([]float32, len(t.rawData)/4)
		for i := range out {
			out[i] = math.Float32frombits(binary.LittleEndian.Uint32(t.rawData[4*i:]))
		}
		return out, nil
	}
	if t.floats != nil { // packed float_data
		if len(t.floats)%4 != 0 {
			return nil, errors.New("odd float data")
		}
		out := make([]float32, len(t.floats)/4)
		for i := range out {
			out[i] = math.Float32frombits(binary.LittleEndian.Uint32(t.floats[4*i:]))
		}
		return out, nil
	}
	return nil, errors.New("no float data")
}
