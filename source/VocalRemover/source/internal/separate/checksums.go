package separate

// SHA-256 of BS-Roformer-1297-int8.onnx as built by tools/build_roformer_onnx.py (801-frame windows,
// attention and feed-forward applied in groups of 2 bands / 27 frames, int8 weights).
const bsRoformerSHA256 = "a31b1fed94ed5fcf3c8ef41f9cf55a31ba6da2f1028d91d296907ced99b23cdf"

// SHA-256 of MelRoformer-KimFT2-int8.onnx (same builder, same window and groups, int8 weights) and of
// MelRoformer-KimFT2-fp16.onnx, which the program generates from it (internal/onnxconv) and which must
// therefore come out byte for byte the same on every computer.
const (
	melRoformerSHA256    = "f81d9265cf57b66808db38d72ae136f3d74f3888f642e085478288f774b5a0d4"
	melRoformerGPUSHA256 = "8c5aa71978d478d5a20b657ec9ae1b411b74c548d2fd81b2239cbf2a829bcc91"
)
