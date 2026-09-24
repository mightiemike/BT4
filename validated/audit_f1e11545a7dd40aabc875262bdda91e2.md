No vulnerability found for this question.

The Bandit bug is about a late size check in a *streaming* frame parser: the parser buffers an entire declared-length frame body from a network connection before checking it against `max_frame_size`, letting an attacker trickle bytes past the negotiated cap.

protobuf-go's only analogous streaming, length-prefixed parser is `encoding/protodelim.UnmarshalOptions.UnmarshalFrom`, and it does the opposite of the Bandit bug — it checks `size` against `MaxSize` *before* touching the underlying reader for the body, and only then calls `br.Peek(int(size))` or `make([]byte, size)` + `io.ReadFull`: [1](#0-0) 

This is the exact fix pattern the Bandit advisory recommends (reject on the length field alone before buffering the body), so this path is not vulnerable to the same bug class.

The other candidate location, `protowire.ConsumeBytes`, only operates on an already-fully-in-memory byte slice (`proto.Unmarshal` and friends are not streaming APIs — the caller supplies the complete `[]byte`), so there is no network-trickling scenario where a "late check" would let unbounded memory accumulate; the bounds check `m > uint64(len(b[n:]))` is against data already resident in memory, not a growing network buffer: [2](#0-1) 

`internal/impl/validate.go`'s streaming validator likewise only walks an in-memory buffer that was already fully received by the caller (e.g., via gRPC, which enforces its own max-message-size before invoking protobuf-go) — there's no protobuf-go-owned "read from socket incrementally" step with a deferred size check analogous to Bandit's HTTP/2 frame header vs. body: [3](#0-2) 

Since protobuf-go itself doesn't own the network transport/framing layer (that's delegated to callers like gRPC or `protodelim`, both of which validate size before buffering), there is no reachable, unprivileged path in this repo exhibiting the "trust length header, buffer full body, then check" pattern described in the report.

### Citations

**File:** encoding/protodelim/protodelim.go (L112-146)
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

**File:** encoding/protowire/wire.go (L458-469)
```go
// ConsumeBytes parses b as a length-prefixed bytes value, reporting its length.
// This returns a negative length upon an error (see [ParseError]).
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
