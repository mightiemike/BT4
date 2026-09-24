### Title
Integer overflow / signed truncation of attacker-controlled length in `BufferReader.Skip` on 32-bit platforms leads to out-of-bounds cursor and crash - ([File: internal/protolazy/bufferreader.go])

### Summary
`protolazy.BufferReader.DecodeVarint32` returns an attacker-controlled `uint32` length taken directly from the wire, and every caller (`SkipValue`, `SkipBytes`, `SkipGroup`, and the inlined `BytesType` handling in `buildIndex`) converts it with `int(n)` before calling `Skip(n int)`. On 32-bit `GOARCH` builds (`386`, `arm`, `mips`, etc.), Go's `int` is 32 bits wide, so any wire-supplied length value greater than `math.MaxInt32` (up to `math.MaxUint32`, fully reachable from `DecodeVarint32`) truncates to a negative `int`. The bounds check in `Skip` then fails to catch the negative value, and `BufferReader.Pos` is advanced by a negative amount, producing an out-of-range cursor position that leads to an out-of-bounds slice/index panic later in decoding — mirroring the CVE's "no integer-overflow protection on 32-bit platforms" pattern that leads to a crash on attacker-supplied data. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

### Finding Description
`DecodeVarint32` decodes up to 5 varint bytes into a `uint32`, so it can return any value in `[0, 4294967295]` directly from attacker-supplied wire bytes with no upper bound check [5](#0-4) .

That value is then passed as the length to skip:
- `SkipValue`'s `BytesType` case: `n, err = b.DecodeVarint32(); ... err = b.Skip(int(n))` [2](#0-1) 
- `SkipBytes`: same pattern [6](#0-5) 
- `SkipGroup`'s `BytesType` case, same pattern [7](#0-6) 
- The inlined varint/length-skip logic in `buildIndex` (`internal/protolazy/lazy.go`), used to build a lazy-field index while scanning the raw message bytes [4](#0-3) 

`Skip` itself performs the only guard:
```go
func (b *BufferReader) Skip(n int) (err error) {
	if len(b.Buf) < b.Pos+n {
		return io.ErrUnexpectedEOF
	}
	b.Pos += n
	return
}
``` [3](#0-2) 

On 64-bit platforms `int` is 64 bits, so `int(n)` for `n uint32` is always non-negative and this check is safe. On 32-bit platforms, if the wire length `n` exceeds `math.MaxInt32`, `int(n)` becomes negative. `len(b.Buf) < b.Pos+n` then computes `b.Pos + (a negative number)`, which is smaller than `b.Pos`, so the check almost always passes (no error returned), and `b.Pos += n` moves the read cursor backward past the start of the buffer (or to an inconsistent negative position). Subsequent reads through `BufferReader` (e.g. `b.Buf[i]` in `DecodeVarint`/`DecodeVarint32`, or later slicing in `buildIndex`) then index with an invalid `Pos`, causing an out-of-range panic (crash) — the same broken invariant class as the reported CVE (missing integer-overflow guard on 32-bit platforms causing a crash on attacker input), just in Go's lazy-message-decoding path instead of C's `readelf.c`.

### Impact Explanation
This is reachable during normal `proto.Unmarshal` of any message that has at least one field annotated as a lazily-decoded field (the `protolazy`/lazy-unmarshal feature), which is a legitimate, non-malicious schema configuration, not an attacker-controlled or exotic input. Once such a schema exists, an attacker who can supply the serialized bytes controls the crafted length varint that drives this bug. On 32-bit builds of a service, this can cause an application crash (denial of service) via a corrupted read cursor and subsequent out-of-bounds panic. It does not, on the evidence gathered, provide a path to memory disclosure or memory corruption beyond a panic/crash, since Go's runtime bounds-checks slice/array accesses (unlike C, where OOB access can lead to further undefined behavior).

### Likelihood Explanation
Likelihood is constrained by two preconditions: (1) the target must be running on a 32-bit `GOARCH` (386/arm/mips/etc.) — a real but comparatively less-common production configuration, and (2) the schema must include at least one field using the `protolazy` lazy-decode option, which is an internal/legacy feature. Given both conditions hold, exploitation only requires a single attacker-controlled message with a crafted 5-byte varint length near `math.MaxUint32`, no special privileges, and no custom resolver.

### Recommendation
In `internal/protolazy/bufferreader.go`, validate the length before conversion so that `n` is never treated as negative on 32-bit platforms — e.g., reject/return an error if `n > uint32(math.MaxInt32)` (or otherwise widen the comparison to unsigned arithmetic) before calling `Skip`, and make `Skip`'s bounds check overflow-safe (e.g., compare using `uint64` or check `n < 0` explicitly) rather than relying on signed pointer arithmetic that can wrap on 32-bit `int`.

### Proof of Concept
On a 32-bit build (`GOARCH=386` or `arm`), construct a message containing a field marked lazy in its `.proto` schema. Craft the wire bytes for that field so its length varint decodes (via `DecodeVarint32`) to a value greater than `2147483647` (`math.MaxInt32`), e.g. the 5-byte varint encoding of `0xFFFFFFFF`:
```
bytes = []byte{0xFF, 0xFF, 0xFF, 0xFF, 0x0F}
```
Feed a message whose lazy field's length-delimited value begins with this varint into `proto.Unmarshal`. During lazy-field index building, `buildIndex`/`SkipValue`/`SkipBytes` calls `DecodeVarint32()` (returns `4294967295`), then `Skip(int(4294967295))`, which on a 32-bit build truncates to `Skip(-1)`. The bounds check `len(b.Buf) < b.Pos + (-1)` passes, `b.Pos` is decremented, and the next `BufferReader` read indexes the buffer at an invalid position, panicking with an index-out-of-range error and crashing the process.

### Citations

**File:** internal/protolazy/bufferreader.go (L144-196)
```go
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

**File:** internal/protolazy/bufferreader.go (L206-211)
```go
	case protowire.BytesType:
		var n uint32
		n, err = b.DecodeVarint32()
		if err == nil {
			err = b.Skip(int(n))
		}
```

**File:** internal/protolazy/bufferreader.go (L236-241)
```go
			err = b.Skip(8)
		case protowire.BytesType:
			n, err = b.DecodeVarint32()
			if err == nil {
				err = b.Skip(int(n))
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

**File:** internal/protolazy/bufferreader.go (L347-354)
```go
// skipBytes skips a set of bytes
func (b *BufferReader) SkipBytes() (err error) {
	n, err := b.DecodeVarint32()
	if err != nil {
		return err
	}
	return b.Skip(int(n))
}
```

**File:** internal/protolazy/lazy.go (L226-233)
```go
		case protowire.Fixed64Type:
			err = r.SkipFixed64()
		case protowire.BytesType:
			var n uint32
			n, err = r.DecodeVarint32()
			if err == nil {
				err = r.Skip(int(n))
			}
```
