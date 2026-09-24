No vulnerability found for this question.

The reported CVE describes a native memory-allocator flaw in glibc's `malloc` where sizes near `SIZE_MAX` could cause an under-sized allocation, leading to heap corruption — a C-level unsafe-memory bug. Protobuf-go is a pure Go library with no equivalent unchecked-size-to-malloc path.

Searching protobuf-go's decode paths for the analogous bug class (allocation sized from an attacker-controlled length prefix without overflow/bounds validation) shows the opposite: every reachable site validates the size before using it.

- `protowire.ConsumeBytes` explicitly checks `m > uint64(len(b[n:]))` before slicing, rejecting any length that would exceed the remaining buffer [1](#0-0) .
- `protodelim.UnmarshalOptions.UnmarshalFrom`, the one place that does `make([]byte, size)` from an untrusted varint-encoded length, explicitly guards against overflow before allocating: it checks `size > math.MaxInt` (or against a configured `MaxSize`) and returns `SizeTooLargeError` rather than calling `make` with an unsafe value [2](#0-1) .
- Generated-message and generic decode paths (`internal/impl/codec_field.go`'s `consumeMessage`, `internal/impl/validate.go`'s varint/length validation) rely on `protowire.ConsumeVarint`/`ConsumeBytes`, which already reject truncated or overflowing lengths (`errCodeTruncated`, `errCodeOverflow`) before any slice or copy occurs [3](#0-2) [4](#0-3) .

There is no reachable path in scoped production code (trusted schema, default binary/ProtoJSON parser, no malicious peer or custom resolver) where an attacker-controlled length is used to allocate or index a buffer without a size/bounds check equivalent to the fix that would be required for the glibc issue. The "varint length overrun" test case in the test suite confirms this is explicitly handled and tested, not a live gap [5](#0-4) .

### Citations

**File:** encoding/protowire/wire.go (L267-367)
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
	y = uint64(b[1])
	v += y << 7
	if y < 0x80 {
		return v, 2
	}
	v -= 0x80 << 7

	if len(b) <= 2 {
		return 0, errCodeTruncated
	}
	y = uint64(b[2])
	v += y << 14
	if y < 0x80 {
		return v, 3
	}
	v -= 0x80 << 14

	if len(b) <= 3 {
		return 0, errCodeTruncated
	}
	y = uint64(b[3])
	v += y << 21
	if y < 0x80 {
		return v, 4
	}
	v -= 0x80 << 21

	if len(b) <= 4 {
		return 0, errCodeTruncated
	}
	y = uint64(b[4])
	v += y << 28
	if y < 0x80 {
		return v, 5
	}
	v -= 0x80 << 28

	if len(b) <= 5 {
		return 0, errCodeTruncated
	}
	y = uint64(b[5])
	v += y << 35
	if y < 0x80 {
		return v, 6
	}
	v -= 0x80 << 35

	if len(b) <= 6 {
		return 0, errCodeTruncated
	}
	y = uint64(b[6])
	v += y << 42
	if y < 0x80 {
		return v, 7
	}
	v -= 0x80 << 42

	if len(b) <= 7 {
		return 0, errCodeTruncated
	}
	y = uint64(b[7])
	v += y << 49
	if y < 0x80 {
		return v, 8
	}
	v -= 0x80 << 49

	if len(b) <= 8 {
		return 0, errCodeTruncated
	}
	y = uint64(b[8])
	v += y << 56
	if y < 0x80 {
		return v, 9
	}
	v -= 0x80 << 56

	if len(b) <= 9 {
		return 0, errCodeTruncated
	}
	y = uint64(b[9])
	v += y << 63
	if y < 2 {
		return v, 10
	}
	return 0, errCodeOverflow
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

**File:** encoding/protodelim/protodelim.go (L117-146)
```go
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

	var b []byte
	var err error
	if br, ok := r.(*bufio.Reader); ok {
		// Use the []byte from the bufio.Reader instead of having to allocate one.
		// This reduces CPU usage and allocated bytes.
		b, err = br.Peek(int(size))
		if err == nil {
			defer br.Discard(int(size))
		} else {
			b = nil
		}
	}
	if b == nil {
		b = make([]byte, size)
		_, err = io.ReadFull(r, b)
	}
```

**File:** internal/impl/codec_field.go (L230-248)
```go
func consumeMessage(b []byte, m proto.Message, wtyp protowire.Type, opts unmarshalOptions) (out unmarshalOutput, err error) {
	if wtyp != protowire.BytesType {
		return out, errUnknown
	}
	v, n := protowire.ConsumeBytes(b)
	if n < 0 {
		return out, errDecode
	}
	o, err := opts.Options().UnmarshalState(protoiface.UnmarshalInput{
		Buf:     v,
		Message: m.ProtoReflect(),
	})
	if err != nil {
		return out, err
	}
	out.n = n
	out.initialized = o.Flags&protoiface.UnmarshalInitialized != 0
	return out, nil
}
```

**File:** proto/testmessages_test.go (L2317-2330)
```go
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
