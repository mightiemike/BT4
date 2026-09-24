No analogous vulnerability found. The reported CVE describes an integer‑overflow-driven heap buffer overflow in `tiftoimage`, where an attacker-controlled size value overflows and is used to allocate/index a buffer without adequate bounds checking.

In protobuf-go, the equivalent code paths that turn attacker-controlled length fields into buffer operations are consistently guarded:

- `protowire.ConsumeVarint` explicitly detects and rejects varints that would overflow 64 bits, returning `errCodeOverflow` rather than wrapping. [1](#0-0) 
- `protowire.ConsumeBytes` checks the decoded length `m` against the actual remaining buffer length before slicing, so an oversized or malicious length can never cause an out-of-bounds read/slice. [2](#0-1) 
- `protodelim.UnmarshalFrom`, the one place that allocates a buffer (`make([]byte, size)`) sized directly from an attacker-supplied varint, explicitly validates `size` against `math.MaxInt` and against the configured `MaxSize` before allocation, specifically to prevent the `make([]byte, size)` panic/overflow scenario.
<invoke name="codebase_search">
<parameter name="query">placeholder</parameter>
</invoke>

### Citations

**File:** encoding/protowire/wire.go (L358-367)
```go
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
