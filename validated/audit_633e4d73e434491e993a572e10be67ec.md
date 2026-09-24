### Title
Integer overflow in `protolazy.BufferReader.Skip`/`SkipBytes` causes decoder desync/panic on 32-bit platforms - ([File: internal/protolazy/bufferreader.go])

### Summary
`BufferReader.DecodeVarint32` returns an attacker-controlled length as `uint32` (values up to `0xFFFFFFFF` are valid, non-overflowing varints), which callers immediately convert with `int(n)` before passing it to `Skip`. On 32-bit builds of Go (`GOARCH=386`, `arm`, `mips`, `wasm`, etc.), `int` is 32 bits, so any length value ≥ `0x80000000` truncates to a negative `int`. `Skip` then fails to detect the out-of-range condition and moves `BufferReader.Pos` backward (or negative), desynchronizing the parser exactly as described in CVE-2016-7944 ("a length value of INT_MAX... triggers the client to stop reading data and get out of sync").

### Finding Description
`DecodeVarint32` in [1](#0-0)  decodes up to a full 32-bit unsigned value from attacker-supplied bytes and only rejects values that need a 5th continuation byte beyond bit 28 — i.e. any value up to `0xFFFFFFFF` is accepted as a valid length.

This length is used, uncasted for range, in several skip helpers: [2](#0-1) [3](#0-2) [4](#0-3) 

and the underlying bounds check itself: [5](#0-4) 

`Skip(n int)` checks `len(b.Buf) < b.Pos+n`. On a 32-bit platform, if the attacker supplies a bytes-length varint of e.g. `0x80000000`, `int(n)` becomes `-2147483648`. The addition `b.Pos+n` becomes deeply negative, the truncation check `len(b.Buf) < b.Pos+n` evaluates false (no error), and `b.Pos += n` drives the reader position negative or far out of range — the read cursor is now desynchronized from the actual buffer, mirroring the libXfixes bug where a bogus `INT_MAX`-class length caused the client to "stop reading data and get out of sync."

This code path is reachable from parsing untrusted input: fields annotated `[lazy = true]` in a trusted `.proto` schema are decoded via `MessageInfo.unmarshalPointerLazy` ( [6](#0-5) ), which stores the raw bytes and lazily builds a field index on first access via `buildIndex`, which uses this exact `BufferReader` skip logic to walk attacker-controlled bytes ( [7](#0-6) ). Lazy decoding is enabled by default ( [8](#0-7) ), so no special caller opt-in beyond the schema's `lazy=true` annotation is required.

### Impact Explanation
Once `Pos` becomes negative or wildly incorrect, subsequent buffer accesses (`b.Buf[i]` in `DecodeVarint`/`DecodeVarint32`/`SkipVarint`) index with a corrupted, potentially negative offset. Go's runtime bounds checking prevents true out-of-bounds memory reads, but a negative slice index triggers an unrecoverable `panic: runtime error: index out of range` — an unhandled panic during unmarshaling of untrusted data, causing a remote, unauthenticated denial of service for any Go service compiled for a 32-bit architecture (`GOARCH=386`, `arm`, `mips`, `wasm`, etc.) that unmarshals messages with a `lazy=true` field.

### Likelihood Explanation
The precondition — running on a 32-bit `GOARCH` — significantly narrows applicability; most production Go network services run on 64-bit architectures, where `int` is 64-bit and this length can never truncate negative. On 64-bit builds the bug does not manifest. On qualifying 32-bit builds, the trigger requires only a single crafted length-prefixed field of the right size in an otherwise ordinary protobuf message with a lazily-decoded field, making it trivially reachable and reproducible for anyone controlling the wire input.

### Recommendation
Validate the decoded 32-bit length against `math.MaxInt` (or explicitly against `int32(math.MaxInt32)` semantics) before converting to `int` in `SkipValue`, `SkipGroup`, and `SkipBytes`, and make `Skip`'s bounds check overflow-safe (e.g. compare `n > len(b.Buf)-b.Pos` using unsigned/`int64` arithmetic, and reject negative `n`) so a corrupted or maliciously large length always results in an explicit `io.ErrUnexpectedEOF`/error rather than a position underflow.

### Proof of Concept
On a 32-bit build (`GOARCH=386` or `GOARCH=arm`):
```go
package main

import "google.golang.org/protobuf/internal/protolazy"

func main() {
    // varint encoding of 0x80000000 (2147483648), a valid 32-bit length
    // followed by arbitrary trailing bytes representing a second field.
    buf := []byte{0x80, 0x80, 0x80, 0x80, 0x08 /* continues bit pattern for 0x80000000 */}
    r := protolazy.NewBufferReader(buf)
    // SkipBytes decodes the length via DecodeVarint32 and calls Skip(int(n)).
    // int(0x80000000) truncates to a negative int on 32-bit builds,
    // causing r.Pos to underflow instead of returning io.ErrUnexpectedEOF.
    _ = r.SkipBytes()
}
```
When wired through `unmarshalPointerLazy`/`buildIndex` on a message containing a `lazy=true` field, an attacker-supplied message with this crafted length desynchronizes `BufferReader.Pos`, and later index-building on the same buffer accesses a negative offset, panicking the process.

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

**File:** internal/protolazy/bufferreader.go (L206-211)
```go
	case protowire.BytesType:
		var n uint32
		n, err = b.DecodeVarint32()
		if err == nil {
			err = b.Skip(int(n))
		}
```

**File:** internal/protolazy/bufferreader.go (L237-241)
```go
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

**File:** internal/impl/lazy.go (L262-386)
```go
	Field:
		switch {
		case f != nil:
			if f.funcs.unmarshal == nil {
				break
			}
			if f.isLazy && lazyDecode {
				switch {
				case lazyFields == nil || lazyFields[f] == lazyValidateOnly:
					// Attempt to validate this field and leave it for later lazy unmarshaling.
					o, valid := mi.skipField(b, f, wtyp, opts)
					switch valid {
					case ValidationValid:
						// Skip over the valid field and continue.
						err = nil
						presence.SetPresentUnatomic(f.presenceIndex, mi.presenceSize)
						requiredMask |= f.validation.requiredBit
						if !o.initialized {
							initialized = false
						}
						n = o.n
						break Field
					case ValidationInvalid:
						return out, errors.New("invalid proto wire format")
					case ValidationWrongWireType:
						break Field
					case ValidationUnknown:
						if lazyFields == nil {
							lazyFields = make(map[*coderFieldInfo]lazyAction)
						}
						if presence.Present(f.presenceIndex) {
							// We were unable to determine if the field is valid or not,
							// and we've already skipped over at least one instance of this
							// field. Clear the presence bit (so if we stop decoding early,
							// we don't leave a partially-initialized field around) and flag
							// the field for unmarshaling before we return.
							presence.ClearPresent(f.presenceIndex)
							lazyFields[f] = lazyUnmarshalLater
							discardUnknown = true
							break Field
						} else {
							// We were unable to determine if the field is valid or not,
							// but this is the first time we've seen it. Flag it as needing
							// eager unmarshaling and fall through to the eager unmarshal case below.
							lazyFields[f] = lazyUnmarshalNow
						}
					}
				case lazyFields[f] == lazyUnmarshalLater:
					// This field will be unmarshaled in a separate pass below.
					// Skip over it here.
					discardUnknown = true
					break Field
				default:
					// Eagerly unmarshal the field.
				}
			}
			if f.isLazy && !lazyDecode && presence.Present(f.presenceIndex) {
				if p.Apply(f.offset).AtomicGetPointer().IsNil() {
					mi.lazyUnmarshal(p, f.num)
				}
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
			if !discardUnknown && !opts.DiscardUnknown() && mi.unknownOffset.IsValid() {
				u := mi.mutableUnknownBytes(p)
				*u = protowire.AppendTag(*u, num, wtyp)
				*u = append(*u, b[:n]...)
			}
		}
		b = b[n:]
		end := start - len(b)
		if lazyDecode && f != nil && f.isLazy {
			if num != lastNum {
				lazyIndex = append(lazyIndex, protolazy.IndexEntry{
					FieldNum: uint32(num),
					Start:    uint32(pos),
					End:      uint32(end),
				})
			} else {
				i := len(lazyIndex) - 1
				lazyIndex[i].End = uint32(end)
				lazyIndex[i].MultipleContiguous = true
			}
		}
		if num < lastNum {
```

**File:** internal/protolazy/lazy.go (L226-241)
```go
		case protowire.Fixed64Type:
			err = r.SkipFixed64()
		case protowire.BytesType:
			var n uint32
			n, err = r.DecodeVarint32()
			if err == nil {
				err = r.Skip(int(n))
			}
		case protowire.StartGroupType:
			err = r.SkipGroup(tag)
		case protowire.Fixed32Type:
			err = r.SkipFixed32()
		default:
			err = fmt.Errorf("Unexpected wire type (%d)", wireType)
		}
		// DONE: err = r.SkipValue(tag)
```

**File:** runtime/protolazy/protolazy.go (L7-12)
```go
// The following logic determines whether lazy decoding is enabled:
//  1. Lazy decoding is enabled by default, unless the environment variable
//     GOPROTODEBUG=nolazy is set.
//  2. If still on, calling protolazy.Disable() turns off lazy decoding.
//  3. If still on, proto.UnmarshalOptions's NoLazyDecoding turns off
//     lazy decoding for this Unmarshal operation only.
```
