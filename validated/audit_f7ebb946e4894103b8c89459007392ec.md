No vulnerability found for this question.

The CVE-2014-8140 report describes a C-language heap-based buffer overflow in Info-ZIP's `test_compr_eb` function, caused by an unchecked "extra field" length taken from a crafted ZIP header being used directly in a memory copy. This bug class requires memory-unsafe pointer/length arithmetic that is only exploitable in languages like C.

`protobuf-go` is written in Go, which enforces slice-bounds checking at the runtime level; any out-of-bounds access would surface only as a safe panic, not an exploitable memory-corruption primitive. I checked the length-prefixed-field decode paths that are the closest analog to the ZIP "extra field length" pattern:

- `protowire.ConsumeBytes` validates the declared length against the remaining buffer before slicing: `if m > uint64(len(b[n:])) { return nil, errCodeTruncated }` [1](#0-0) .
- `protolazy.BufferReader.Skip`, used when skipping bytes/groups during lazy-index building, explicitly checks `len(b.Buf) < b.Pos+n` before advancing the position [2](#0-1) .
- The core generic message decode loop (`unmarshalPointerEager`/`unmarshalPointerLazy`) and generated codec functions (e.g. `consumeBytesNoZero`, `consumeString`) all route length-prefixed data through `protowire.ConsumeBytes`/`ConsumeFieldValue`, which reject truncated/oversized lengths before any copy occurs [3](#0-2) [4](#0-3) .

No reachable parser path (binary `Unmarshal`, ProtoJSON, lazy extension/message decoding) performs an unchecked length-to-copy operation analogous to the C `unzip` flaw, so this report does not map to an equivalent vulnerability in scoped production `protobuf-go` code.

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

**File:** internal/protolazy/bufferreader.go (L328-335)
```go
// skip skips the specified number of bytes
func (b *BufferReader) Skip(n int) (err error) {
	if len(b.Buf) < b.Pos+n {
		return io.ErrUnexpectedEOF
	}
	b.Pos += n
	return
}
```

**File:** internal/impl/decode.go (L218-230)
```go
		if err != nil {
			if err != errUnknown {
				return out, err
			}
			n = protowire.ConsumeFieldValue(num, wtyp, b)
			if n < 0 {
				return out, errDecode
			}
			if !opts.DiscardUnknown() && mi.unknownOffset.IsValid() {
				u := mi.mutableUnknownBytes(p)
				*u = protowire.AppendTag(*u, num, wtyp)
				*u = append(*u, b[:n]...)
			}
```

**File:** internal/impl/codec_gen.go (L5487-5500)
```go
// consumeBytesNoZero wire decodes a []byte pointer as a Bytes.
// The zero value is not decoded.
func consumeBytesNoZero(b []byte, p pointer, wtyp protowire.Type, f *coderFieldInfo, opts unmarshalOptions) (out unmarshalOutput, err error) {
	if wtyp != protowire.BytesType {
		return out, errUnknown
	}
	v, n := protowire.ConsumeBytes(b)
	if n < 0 {
		return out, errDecode
	}
	*p.Bytes() = append(([]byte)(nil), v...)
	out.n = n
	return out, nil
}
```
