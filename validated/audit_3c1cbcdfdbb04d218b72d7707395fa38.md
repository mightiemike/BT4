No vulnerability found for this question.

This CVE describes a C-language heap-based buffer overflow in the Linux kernel's ksmbd SMB server (`set_ntacl_dacl`), caused by unchecked buffer sizing after a malformed `SMB2_SET_INFO_HE`/`SMB2_QUERY_INFO_HE` sequence. That bug class — raw pointer arithmetic writing past a heap allocation — does not map onto protobuf-go's decode paths, which are written in memory-safe Go and consistently bounds-check before slicing.

I reviewed the core decode paths for the equivalent invariant (length-prefixed/size field validation before use):
- `internal/impl/decode.go`'s `unmarshalPointerEager` validates tag/field-number bounds and rejects negative `ConsumeVarint`/`ConsumeFieldValue` results before advancing `b = b[n:]`. [1](#0-0) 
- `internal/impl/validate.go`'s bytes-type handling explicitly checks `size > uint64(len(b))` before slicing, rejecting oversized length prefixes as `ValidationInvalid`. [2](#0-1) 
- The generated repeated-field decoders (e.g. `consumeUint32Slice`, `consumeFixed64Slice` in `internal/impl/codec_gen.go`) compute a pre-sized `count` from the packed buffer length, call `p.growXSlice(count)` to preallocate exactly that capacity, and then `append` while checking `n < 0` on each `Consume*` call — Go's slice/append semantics prevent any out-of-bounds heap write even if `count` were miscalculated. [3](#0-2) 
- `internal/protolazy/bufferreader.go`'s `SkipValue`/`SkipGroup` similarly decode a length via `DecodeVarint32` and call `Skip(int(n))`, which is bounds-checked internally. [4](#0-3) 

There is no analogous "malformed SET_INFO followed by unchecked ACL buffer write" pattern in protobuf-go: there's no ACL/DACL concept, no split query/set-info state machine, and no manual buffer allocation sized from one field then written using a size from another untrusted field. All slice growth in the codebase goes through Go's safe `append`/`make`, and every length field is checked against the remaining buffer length before use, so the described heap-overflow primitive has no reachable equivalent here.

### Citations

**File:** internal/impl/decode.go (L136-164)
```go
	for len(b) > 0 {
		// Parse the tag (field number and wire type).
		var tag uint64
		if b[0] < 0x80 {
			tag = uint64(b[0])
			b = b[1:]
		} else if len(b) >= 2 && b[1] < 128 {
			tag = uint64(b[0]&0x7f) + uint64(b[1])<<7
			b = b[2:]
		} else {
			var n int
			tag, n = protowire.ConsumeVarint(b)
			if n < 0 {
				return out, errDecode
			}
			b = b[n:]
		}
		var num protowire.Number
		if n := tag >> 3; n < uint64(protowire.MinValidNumber) || n > uint64(protowire.MaxValidNumber) {
			return out, errDecode
		} else {
			num = protowire.Number(n)
		}
		wtyp := protowire.Type(tag & 7)

		if wtyp == protowire.EndGroupType {
			if num != groupTag {
				return out, errDecode
			}
```

**File:** internal/impl/validate.go (L422-440)
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
```

**File:** internal/impl/codec_gen.go (L4488-4510)
```go
func consumeFixed64Slice(b []byte, p pointer, wtyp protowire.Type, f *coderFieldInfo, opts unmarshalOptions) (out unmarshalOutput, err error) {
	sp := p.Uint64Slice()
	if wtyp == protowire.BytesType {
		b, n := protowire.ConsumeBytes(b)
		if n < 0 {
			return out, errDecode
		}
		count := len(b) / protowire.SizeFixed64()
		if count > 0 {
			p.growUint64Slice(count)
		}
		s := *sp
		for len(b) > 0 {
			v, n := protowire.ConsumeFixed64(b)
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

**File:** internal/protolazy/bufferreader.go (L198-220)
```go
// skipValue skips a value in the protobuf, based on the specified tag
func (b *BufferReader) SkipValue(tag uint32) (err error) {
	wireType := tag & 0x7
	switch protowire.Type(wireType) {
	case protowire.VarintType:
		err = b.SkipVarint()
	case protowire.Fixed64Type:
		err = b.SkipFixed64()
	case protowire.BytesType:
		var n uint32
		n, err = b.DecodeVarint32()
		if err == nil {
			err = b.Skip(int(n))
		}
	case protowire.StartGroupType:
		err = b.SkipGroup(tag)
	case protowire.Fixed32Type:
		err = b.SkipFixed32()
	default:
		err = fmt.Errorf("Unexpected wire type (%d)", wireType)
	}
	return
}
```
