No vulnerability found for this question.

The reviewed decode paths in `protowire.ConsumeVarint`/`ConsumeBytes`, `internal/protolazy/bufferreader.go`'s `DecodeVarint`/`DecodeVarint32`/`SkipVarint`, and `internal/impl/decode.go`'s field-parsing loop all bound-check lengths against the remaining buffer and return explicit error sentinels (`errOverflow`, `io.ErrUnexpectedEOF`, `errDecode`) rather than saturating an unbounded length into a pointer/index used for a raw memory copy, which is the specific bug class in CVE-2026-86142 (xmlXPtrEval computing a saturated/overflowed length that is then used unchecked in `xmlXPtrEvalXPtrPart`). [1](#0-0) [2](#0-1) 

There is no analogous "length saturation" defect on the trusted-schema, default binary/ProtoJSON parsing path: every length-prefixed read (`ConsumeBytes`, `SkipValue`'s `BytesType` case, map/list/message unmarshal) checks the decoded length against the actual slice bounds before slicing, and Go's slice bounds checking prevents an out-of-bounds read/write even if a length were computed incorrectly. [3](#0-2) [4](#0-3)  The existing test suite even explicitly exercises "bytes field overruns message" and "varint length overrun" cases and expects them to error out safely, confirming this class of input is already handled. [5](#0-4) 

The `protodelim` size-delimited reader also explicitly bounds the decoded size against `MaxSize`/`math.MaxInt` before allocating, avoiding any saturation-into-unsafe-allocation issue analogous to the libxml2 bug. [6](#0-5) 

Since protobuf-go's decode paths rely on Go's memory-safe slice semantics with explicit bounds checks (not raw pointer arithmetic like libxml2's C code), there is no reachable unprivileged path where a length-saturation bug leads to a heap buffer overflow.

### Citations

**File:** internal/protolazy/bufferreader.go (L143-196)
```go
// decodeVarint32 decodes a varint32 at the current position
func (b *BufferReader) DecodeVarint32() (x uint32, err error) {
	i := b.Pos
	buf := b.Buf

	if i >= len(buf) {
		return 0, io.ErrUnexpectedEOF
	} else if buf[i] < 0x80 {
		b.Pos++
		return uint32(buf[i]), nil
	} else if len(buf)-i < 5 {
		v, err := b.DecodeVarintSlow()
		return uint32(v), err
	}

	var v uint32
	// we already checked the first byte
	x = uint32(buf[i]) & 127
	i++

	v = uint32(buf[i])
	i++
	x |= (v & 127) << 7
	if v < 128 {
		goto done
	}

	v = uint32(buf[i])
	i++
	x |= (v & 127) << 14
	if v < 128 {
		goto done
	}

	v = uint32(buf[i])
	i++
	x |= (v & 127) << 21
	if v < 128 {
		goto done
	}

	v = uint32(buf[i])
	i++
	x |= (v & 127) << 28
	if v < 128 {
		goto done
	}

	return 0, errOverflow

done:
	b.Pos = i
	return
}
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

**File:** internal/impl/decode.go (L136-159)
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
```

**File:** proto/decode.go (L221-253)
```go
func (o UnmarshalOptions) unmarshalMap(b []byte, wtyp protowire.Type, mapv protoreflect.Map, fd protoreflect.FieldDescriptor) (n int, err error) {
	if o.RecursionLimit--; o.RecursionLimit < 0 {
		return 0, errRecursionDepth
	}
	if wtyp != protowire.BytesType {
		return 0, errUnknown
	}
	b, n = protowire.ConsumeBytes(b)
	if n < 0 {
		return 0, errDecode
	}
	var (
		keyField = fd.MapKey()
		valField = fd.MapValue()
		key      protoreflect.Value
		val      protoreflect.Value
		haveKey  bool
		haveVal  bool
	)
	switch valField.Kind() {
	case protoreflect.GroupKind, protoreflect.MessageKind:
		val = mapv.NewValue()
	}
	// Map entries are represented as a two-element message with fields
	// containing the key and value.
	for len(b) > 0 {
		num, wtyp, n := protowire.ConsumeTag(b)
		if n < 0 {
			return 0, errDecode
		}
		if num > protowire.MaxValidNumber {
			return 0, errDecode
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

**File:** encoding/protodelim/protodelim.go (L112-129)
```go
	size, n := protowire.ConsumeVarint(sizeBuf)
	if n < 0 {
		return protowire.ParseError(n)
	}

	maxSize := o.MaxSize
	if maxSize == 0 {
		maxSize = defaultMaxSize
	}
	if maxSize == -1 {
		// No limit specified: Just check that size fits into an integer,
		// otherwise the make([]byte, size) call below will panic.
		if size > math.MaxInt {
			return errors.Wrap(&SizeTooLargeError{Size: size, MaxSize: math.MaxInt}, "")
		}
	} else if size > uint64(maxSize) {
		return errors.Wrap(&SizeTooLargeError{Size: size, MaxSize: uint64(maxSize)}, "")
	}
```
