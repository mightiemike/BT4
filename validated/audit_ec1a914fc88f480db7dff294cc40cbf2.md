No vulnerability found for this question.

The QEMU CVE describes an out-of-bounds read when parsing a length/option field without validating it against the remaining buffer size. I searched protobuf-go's wire-format decoding paths for an analogous unchecked-length read and found the opposite: every length-prefixed read in the codebase validates the parsed length against the remaining buffer before slicing.

- `protowire.ConsumeVarint` checks `len(b)` at every byte before indexing [1](#0-0) .
- `protowire.ConsumeBytes` explicitly rejects a length that exceeds the remaining buffer (`m > uint64(len(b[n:]))`) before slicing [2](#0-1) .
- The eager unmarshal loop in `internal/impl/decode.go` bounds-checks the tag and forwards error codes from `ConsumeFieldValue`/`ConsumeVarint` rather than reading past the buffer [3](#0-2) .
- The lazy-decoding validator in `internal/impl/validate.go` also checks `size > uint64(len(b))` before taking the sub-slice for a bytes-type field [4](#0-3) .
- `internal/protolazy/bufferreader.go`'s `Skip`, `SkipBytes`, `DecodeVarint`/`DecodeVarintSlow` all check position against buffer length and return `io.ErrUnexpectedEOF`/`errOverflow` rather than reading out of bounds [5](#0-4) .
- The project's own test suite explicitly exercises these exact malformed-length scenarios ("bytes field overruns message", "bytes field lacks size", "varint length overrun", "varint overflow") and expects decode errors, confirming these are treated as security-relevant invariants already enforced [6](#0-5) .

Since the bug class in CVE-2017-11434 (missing length validation before an out-of-bounds buffer read) is not present anywhere in the scoped decode paths — bounds checks are applied consistently before every slice operation — there is no valid analog to report.

### Citations

**File:** encoding/protowire/wire.go (L267-280)
```go
func ConsumeVarint(b []byte) (v uint64, n int) {
	var y uint64
	if len(b) <= 0 {
		return 0, errCodeTruncated
	}
	v = uint64(b[0])
	if v < 0x80 {
		return v, 1
	}
	v -= 0x80

	if len(b) <= 1 {
		return 0, errCodeTruncated
	}
```

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

**File:** internal/impl/decode.go (L136-156)
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

**File:** internal/protolazy/bufferreader.go (L326-354)
```go
}

// skip skips the specified number of bytes
func (b *BufferReader) Skip(n int) (err error) {
	if len(b.Buf) < b.Pos+n {
		return io.ErrUnexpectedEOF
	}
	b.Pos += n
	return
}

// skipFixed64 skips a fixed64
func (b *BufferReader) SkipFixed64() (err error) {
	return b.Skip(8)
}

// skipFixed32 skips a fixed32
func (b *BufferReader) SkipFixed32() (err error) {
	return b.Skip(4)
}

// skipBytes skips a set of bytes
func (b *BufferReader) SkipBytes() (err error) {
	n, err := b.DecodeVarint32()
	if err != nil {
		return err
	}
	return b.Skip(int(n))
}
```

**File:** proto/testmessages_test.go (L2264-2330)
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
	{
		desc: "varint overflow",
		decodeTo: []proto.Message{
			(*testpb.TestAllTypes)(nil),
			(*testeditionspb.TestAllTypes)(nil),
			(*testpb.TestAllExtensions)(nil),
			(*testeditionspb.TestAllExtensions)(nil),
		},
		wire: protopack.Message{
			protopack.Tag{1, protopack.VarintType},
			protopack.Raw("\xff\xff\xff\xff\xff\xff\xff\xff\xff\x02"),
		}.Marshal(),
	},
	{
		desc: "varint length overrun",
		decodeTo: []proto.Message{
			(*testpb.TestAllTypes)(nil),
			(*testeditionspb.TestAllTypes)(nil),
			(*testpb.TestAllExtensions)(nil),
			(*testeditionspb.TestAllExtensions)(nil),
		},
		wire: protopack.Message{
			protopack.Tag{1, protopack.VarintType},
			protopack.Raw("\xff\xff\xff\xff\xff\xff\xff\xff\xff"),
		}.Marshal(),
	},
```
