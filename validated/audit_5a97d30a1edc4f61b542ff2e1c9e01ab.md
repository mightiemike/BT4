No vulnerability found for this question.

The CVE describes a Firefox/Thunderbird graphics-driver heap buffer overflow caused by unconstrained "blit" values from GPU compositing code — a domain (video driver blit operations) that has no structural analog in protobuf-go.

Searching protobuf-go's decode paths for the closest analogous bug class (user-controlled size/length values driving unchecked buffer operations) shows the relevant invariant is consistently enforced:

- `protowire.ConsumeBytes` validates that the length varint does not exceed the remaining buffer before slicing, returning `errCodeTruncated` otherwise [1](#0-0) .
- Generated-message decode paths (`consumeFixed32Slice`, `consumeFixed64Slice`, `consumeSfixed32Slice`, etc.) first consume length-delimited bytes via `protowire.ConsumeBytes`, check `n < 0`, and only then compute element counts and grow slices with `p.growXxxSlice`, which itself does a safe `make`+`copy` rather than raw pointer arithmetic [2](#0-1) [3](#0-2) .
- The generic message unmarshal loop (`MessageInfo.unmarshalPointerEager`) validates tag/field numbers and always checks the returned length `n < 0` before advancing the buffer slice `b = b[n:]` [4](#0-3) .
- The lazy/unknown-field path (`protolazy.BufferReader.SkipValue`) similarly decodes the length varint and calls `b.Skip(int(n))`, which is bounds-checked internally rather than performing raw unconstrained blits [5](#0-4) .
- Existing test cases (`bytes field overruns message`, `bytes field lacks size`) confirm this exact bug class (attacker-controlled length overruns buffer) is already covered and rejected by the decoder [6](#0-5) .

No reachable, unprivileged decode path in protobuf-go (binary or ProtoJSON, trusted schema) allows an attacker-supplied length/size value to drive a buffer read/write past its bounds — every length-prefixed consumption is validated against the remaining buffer length before use. This is not a valid analog of CVE-2020-26971.

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

**File:** internal/impl/codec_gen.go (L3527-3551)
```go
// consumeFixed32Slice wire decodes a []uint32 pointer as a repeated Fixed32.
func consumeFixed32Slice(b []byte, p pointer, wtyp protowire.Type, f *coderFieldInfo, opts unmarshalOptions) (out unmarshalOutput, err error) {
	sp := p.Uint32Slice()
	if wtyp == protowire.BytesType {
		b, n := protowire.ConsumeBytes(b)
		if n < 0 {
			return out, errDecode
		}
		count := len(b) / protowire.SizeFixed32()
		if count > 0 {
			p.growUint32Slice(count)
		}
		s := *sp
		for len(b) > 0 {
			v, n := protowire.ConsumeFixed32(b)
			if n < 0 {
				return out, errDecode
			}
			s = append(s, v)
			b = b[n:]
		}
		*sp = s
		out.n = n
		return out, nil
	}
```

**File:** internal/impl/pointer_unsafe.go (L163-169)
```go
func (p pointer) growInt32Slice(addCap int) {
	sp := p.Int32Slice()
	s := make([]int32, 0, addCap+len(*sp))
	s = s[:len(*sp)]
	copy(s, *sp)
	*sp = s
}
```

**File:** internal/impl/decode.go (L136-232)
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
			groupTag = 0
			break
		}

		var f *coderFieldInfo
		if int(num) < len(mi.denseCoderFields) {
			f = mi.denseCoderFields[num]
		} else {
			f = mi.coderFields[num]
		}
		var n int
		err := errUnknown
		switch {
		case f != nil:
			if f.funcs.unmarshal == nil {
				break
			}
			var o unmarshalOutput
			o, err = f.funcs.unmarshal(b, p.Apply(f.offset), wtyp, f, opts)
			n = o.n
			if err != nil {
				break
			}
			requiredMask |= f.validation.requiredBit
			if f.funcs.isInit != nil && !o.initialized {
				initialized = false
			}

			if f.presenceIndex != noPresence {
				presence.SetPresentUnatomic(f.presenceIndex, mi.presenceSize)
			}

		default:
			// Possible extension.
			if exts == nil && mi.extensionOffset.IsValid() {
				exts = p.Apply(mi.extensionOffset).Extensions()
				if *exts == nil {
					*exts = make(map[int32]ExtensionField)
				}
			}
			if exts == nil {
				break
			}
			var o unmarshalOutput
			o, err = mi.unmarshalExtension(b, num, wtyp, *exts, opts)
			if err != nil {
				break
			}
			n = o.n
			if !o.initialized {
				initialized = false
			}
		}
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
		}
		b = b[n:]
```

**File:** internal/protolazy/bufferreader.go (L206-211)
```go
	case protowire.BytesType:
		var n uint32
		n, err = b.DecodeVarint32()
		if err == nil {
			err = b.Skip(int(n))
		}
```

**File:** proto/testmessages_test.go (L2264-2304)
```go
	{
		desc: "bytes field overruns message",
		decodeTo: []proto.Message{
			(*testpb.TestAllTypes)(nil),
			(*testeditionspb.TestAllTypes)(nil),
			(*testpb.TestAllExtensions)(nil),
			(*testeditionspb.TestAllExtensions)(nil),
		},
		wire: protopack.Message{
			protopack.Tag{18, protopack.BytesType}, protopack.LengthPrefix{protopack.Message{
				protopack.Tag{2, protopack.BytesType}, protopack.LengthPrefix{protopack.Message{
					protopack.Tag{15, protopack.BytesType}, protopack.Varint(2),
				}},
				protopack.Tag{1, protopack.VarintType}, protopack.Varint(0),
			}},
		}.Marshal(),
	},
	{
		desc: "varint field overruns message",
		decodeTo: []proto.Message{
			(*testpb.TestAllTypes)(nil),
			(*testeditionspb.TestAllTypes)(nil),
			(*testeditionspb.TestAllExtensions)(nil),
			(*testpb.TestAllExtensions)(nil),
		},
		wire: protopack.Message{
			protopack.Tag{1, protopack.VarintType},
		}.Marshal(),
	},
	{
		desc: "bytes field lacks size",
		decodeTo: []proto.Message{
			(*testpb.TestAllTypes)(nil),
			(*testeditionspb.TestAllTypes)(nil),
			(*testpb.TestAllExtensions)(nil),
			(*testeditionspb.TestAllExtensions)(nil),
		},
		wire: protopack.Message{
			protopack.Tag{18, protopack.BytesType},
		}.Marshal(),
	},
```
