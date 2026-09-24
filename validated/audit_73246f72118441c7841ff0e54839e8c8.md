No vulnerability found for this question.

Based on my analysis of the protobuf-go decode paths, every location that reads a length/count field before consuming payload bytes performs an explicit bounds check against the remaining buffer before slicing:

- `protowire.ConsumeBytes` validates `m > uint64(len(b[n:]))` before returning the sliced payload [1](#0-0) .
- The fast-path validator in `internal/impl/validate.go` checks `size > uint64(len(b))` before slicing the bytes-typed field value [2](#0-1) .
- All generated packed-slice decoders (`consumeUint64Slice`, `consumeFixed32Slice`, `consumeInt32Slice`, etc.) first call `protowire.ConsumeBytes` to obtain a length-validated sub-slice, then only ever iterate `for len(b) > 0` within that already-bounded slice, checking `n < 0` on every element decode [3](#0-2) .
- `internal/protolazy/bufferreader.go`'s `Skip` method also checks `len(b.Buf) < b.Pos+n` before advancing past a count-derived offset [4](#0-3) .

This is the exact invariant broken in the kernel CVE (a count field used to size a payload read without checking it against the buffer's remaining space), but in protobuf-go's default binary/ProtoJSON decode paths this check is consistently and explicitly present at every payload-consuming site, including the generated per-type codec functions and the generic fast-path validator. There is no reachable unprivileged decode path where an attacker-controlled count/length field is trusted to exceed the actual buffer bounds.

### Citations

**File:** encoding/protowire/wire.go (L460-469)
```go
func ConsumeBytes(b []byte) (v []byte, n int) {
	m, n := ConsumeVarint(b)
	if n < 0 {
		return nil, n // forward error code
	}
	if m > uint64(len(b[n:])) {
		return nil, errCodeTruncated
	}
	return b[n:][:m], n + int(m)
}
```

**File:** internal/impl/validate.go (L422-442)
```go
			case protowire.BytesType:
				var size uint64
				if len(b) >= 1 && b[0] < 0x80 {
					size = uint64(b[0])
					b = b[1:]
				} else if len(b) >= 2 && b[1] < 128 {
					size = uint64(b[0]&0x7f) + uint64(b[1])<<7
					b = b[2:]
				} else {
					var n int
					size, n = protowire.ConsumeVarint(b)
					if n < 0 {
						return out, ValidationInvalid
					}
					b = b[n:]
				}
				if size > uint64(len(b)) {
					return out, ValidationInvalid
				}
				v := b[:size]
				b = b[size:]
```

**File:** internal/impl/codec_gen.go (L2815-2853)
```go
// consumeUint64Slice wire decodes a []uint64 pointer as a repeated Uint64.
func consumeUint64Slice(b []byte, p pointer, wtyp protowire.Type, f *coderFieldInfo, opts unmarshalOptions) (out unmarshalOutput, err error) {
	sp := p.Uint64Slice()
	if wtyp == protowire.BytesType {
		b, n := protowire.ConsumeBytes(b)
		if n < 0 {
			return out, errDecode
		}
		count := 0
		for _, v := range b {
			if v < 0x80 {
				count++
			}
		}
		if count > 0 {
			p.growUint64Slice(count)
		}
		s := *sp
		for len(b) > 0 {
			var v uint64
			var n int
			if len(b) >= 1 && b[0] < 0x80 {
				v = uint64(b[0])
				n = 1
			} else if len(b) >= 2 && b[1] < 128 {
				v = uint64(b[0]&0x7f) + uint64(b[1])<<7
				n = 2
			} else {
				v, n = protowire.ConsumeVarint(b)
			}
			if n < 0 {
				return out, errDecode
			}
			s = append(s, v)
			b = b[n:]
		}
		*sp = s
		out.n = n
		return out, nil
```

**File:** internal/protolazy/bufferreader.go (L329-335)
```go
func (b *BufferReader) Skip(n int) (err error) {
	if len(b.Buf) < b.Pos+n {
		return io.ErrUnexpectedEOF
	}
	b.Pos += n
	return
}
```
