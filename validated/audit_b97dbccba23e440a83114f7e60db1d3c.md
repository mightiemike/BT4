No vulnerability found for this question.

The CVE describes a stack-based buffer overflow in a C library (`mxml`) caused by writing a double-precision float into a fixed-size stack buffer during XML serialization. This bug class fundamentally depends on C-style fixed-size stack buffers with unchecked bounds.

In protobuf-go, all analogous float/double serialization paths use Go's dynamically-growing `[]byte` slices via `append` and `strconv.AppendFloat`, which never operate on fixed-size stack buffers and cannot overflow in this manner:

- `internal/encoding/text/encode.go` `appendFloat` uses `strconv.AppendFloat(out, n, 'g', -1, bitSize)` on a growable slice. [1](#0-0) 
- `internal/encoding/json/encode.go` `appendFloat` similarly uses `strconv.AppendFloat` on a growable `[]byte`. [2](#0-1) 
- The binary wire encoders for double/fixed64 fields use `protowire.AppendFixed64`, which appends exactly 8 bytes to a growable slice rather than writing into a fixed-size buffer. [3](#0-2) 
- Generated codec paths for double fields (`appendDoubleValue`, `appendDoubleSliceValue`, `appendDoublePackedSlice`) all route through `protowire.AppendFixed64` on growable output buffers. [4](#0-3) 

Go's slice-append model does not admit the "write past a stack-allocated fixed array" class of bug that CVE-2018-20004 describes; any out-of-bounds write here would require memory corruption in the Go runtime itself, not application-level logic. There is no reachable code path in protobuf-go where a double value's textual/binary representation is written into a fixed-size stack buffer without bounds-checked growth. This report does not map to any exploitable analog in this codebase.

### Citations

**File:** internal/encoding/text/encode.go (L184-195)
```go
func appendFloat(out []byte, n float64, bitSize int) []byte {
	switch {
	case math.IsNaN(n):
		return append(out, "nan"...)
	case math.IsInf(n, +1):
		return append(out, "inf"...)
	case math.IsInf(n, -1):
		return append(out, "-inf"...)
	default:
		return strconv.AppendFloat(out, n, 'g', -1, bitSize)
	}
}
```

**File:** internal/encoding/json/encode.go (L148-176)
```go
func appendFloat(out []byte, n float64, bitSize int) []byte {
	switch {
	case math.IsNaN(n):
		return append(out, `"NaN"`...)
	case math.IsInf(n, +1):
		return append(out, `"Infinity"`...)
	case math.IsInf(n, -1):
		return append(out, `"-Infinity"`...)
	}

	// JSON number formatting logic based on encoding/json.
	// See floatEncoder.encode for reference.
	fmt := byte('f')
	if abs := math.Abs(n); abs != 0 {
		if bitSize == 64 && (abs < 1e-6 || abs >= 1e21) ||
			bitSize == 32 && (float32(abs) < 1e-6 || float32(abs) >= 1e21) {
			fmt = 'e'
		}
	}
	out = strconv.AppendFloat(out, n, fmt, -1, bitSize)
	if fmt == 'e' {
		n := len(out)
		if n >= 4 && out[n-4] == 'e' && out[n-3] == '-' && out[n-2] == '0' {
			out[n-2] = out[n-1]
			out = out[:n-1]
		}
	}
	return out
}
```

**File:** encoding/protowire/wire.go (L425-436)
```go
// AppendFixed64 appends v to b as a little-endian uint64.
func AppendFixed64(b []byte, v uint64) []byte {
	return append(b,
		byte(v>>0),
		byte(v>>8),
		byte(v>>16),
		byte(v>>24),
		byte(v>>32),
		byte(v>>40),
		byte(v>>48),
		byte(v>>56))
}
```

**File:** internal/impl/codec_gen.go (L4922-4931)
```go
// appendDoubleSliceValue encodes a []float64 value as a repeated Double.
func appendDoubleSliceValue(b []byte, listv protoreflect.Value, wiretag uint64, opts marshalOptions) ([]byte, error) {
	list := listv.List()
	for i, llen := 0, list.Len(); i < llen; i++ {
		v := list.Get(i)
		b = protowire.AppendVarint(b, wiretag)
		b = protowire.AppendFixed64(b, math.Float64bits(v.Float()))
	}
	return b, nil
}
```
