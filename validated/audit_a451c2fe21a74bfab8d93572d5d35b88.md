Based on my research, protobuf-go's decoding paths have explicit, well-tested overflow/bounds guards that address exactly the class of bug described in the Redis report (unchecked large length values leading to heap corruption):

- `protowire.ConsumeVarint` bounds-checks every byte and returns an explicit overflow error code (`errCodeOverflow`) if the 10th byte's high bits are set, rather than silently wrapping the value. [1](#0-0) 
- `protowire.ConsumeBytes` validates the decoded length against the actual remaining buffer size before slicing (`m > uint64(len(b[n:]))`), preventing any out-of-bounds read/allocation from an oversized length field. [2](#0-1) 
- `protodelim.UnmarshalOptions.UnmarshalFrom`, the one place that turns an attacker-controlled length into a `make([]byte, size)` allocation, explicitly checks `size > math.MaxInt` (and against a configurable `MaxSize`, defaulting to 4 MiB) before allocating, specifically to avoid the panic/overflow this class of bug would cause.
<invoke name="codebase_search">
<parameter name="query">placeholder</parameter>
</invoke>

### Citations

**File:** encoding/protowire/wire.go (L358-366)
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
