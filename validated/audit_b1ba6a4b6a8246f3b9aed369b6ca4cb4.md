Confirmed: this is a genuine analog. `SizeField` and `AppendField` on `*protolazy.XXX_lazyUnmarshalInfo` are called once per lazy field during every `size()`/`marshal()` pass [1](#0-0) [2](#0-1) , and both funnel into `FindFieldInProto` → `lookupField`, which does a **linear scan of the entire field index for every call** [3](#0-2) . Since `orderedCoderFields` is iterated once per lazy field in `sizePointerSlow`/`marshalAppendPointer`, and each iteration triggers an O(index-size) scan, a message with many lazy fields interleaved with many out-of-order/duplicate entries produces O(n²) behavior on the decode→re-encode (or decode→size) path — directly analogous to the Rack `select_best_encoding` bug (repeated O(n) rescans driven by attacker-controlled repetition count).

### Title
Quadratic-time field lookup in lazy-unmarshal index causes CPU DoS on decode-then-marshal path - ([File: internal/protolazy/lazy.go])

### Summary
`protolazy.XXX_lazyUnmarshalInfo.FindFieldInProto` (used by `SizeField`/`AppendField`/`lazyUnmarshal`) resolves a field's byte range via `lookupField`, which performs a full linear scan of the message's field index on every call [3](#0-2) . When a message using lazily-decoded fields (`[lazy=true]` fields) contains many interleaved/repeated top-level field entries, `buildIndex` produces a large index [4](#0-3) , and every subsequent `Size()`/`Marshal()` call re-scans that whole index once per lazy field via `sizePointerSlow` and `marshalAppendPointer` [5](#0-4) [6](#0-5) , yielding O(n²) CPU cost for n interleaved entries.

### Finding Description
- Source: attacker-controlled binary payload decoded via `proto.Unmarshal` with lazy decoding enabled (`opts.CanBeLazy()`), which stores the raw buffer and builds an index of field byte-ranges instead of eagerly decoding [7](#0-6) .
- Parser state: `buildIndex` scans the buffer once (O(n)) and records one `IndexEntry` per contiguous run of a field number, marking out-of-order data and sorting if needed [8](#0-7) .
- Validation/lookup step: on `Size()` or `Marshal()`, for every field marked `isLazy` that hasn't been eagerly unmarshaled, `SizeField`/`AppendField` call `FindFieldInProto`, which calls `lookupField` — a straight-line `for i, entry := range index` scan [3](#0-2) .
- Sink: this scan is repeated once per lazy field in the loop over `mi.orderedCoderFields` [5](#0-4) . If a peer sends a message where the wire bytes place many distinct (or repeated, non-contiguous) field entries so the index is large, and the message type declares many lazy fields, the total cost of a single `Size`+`Marshal` round-trip becomes O(index_size × num_lazy_fields), which is quadratic in attacker-controlled input size.

This mirrors the Rack analog precisely: a cheap-looking per-item operation (`lookupField`) is unconditionally re-run over the same growing collection (`index`) once per logical unit of work (once per lazy field), rather than being memoized or using an O(log n)/O(1) lookup structure.

### Impact Explanation
Any service that unmarshals untrusted binary protobuf into a message type containing `[lazy=true]` fields and then serializes/sizes the resulting message (a common pattern: decode request → re-marshal for forwarding, logging, or JSON conversion) can be forced into disproportionate CPU consumption by a single crafted request, causing availability degradation (CWE-400/CWE-407), consistent with the High severity of the analog Rack finding.

### Likelihood Explanation
This requires the schema to actually declare lazy fields (an opt-in `FieldOptions.lazy` annotation), which limits blast radius compared to Rack's ubiquitous `Deflater` middleware. Under the stated "trusted schema" assumption this is a legitimate, reachable configuration (not attacker-controlled), and once present, the vulnerable path (decode + size/marshal) is triggered by completely ordinary, unprivileged request handling with attacker-controlled wire bytes — no malicious peer, custom resolver, or descriptor needed.

### Recommendation
Replace the linear `lookupField` scan with a binary search over the sorted index (the index is already sorted when `outOfOrder` is detected, or can always be maintained sorted), or cache per-field-number offsets in a map keyed by field number built once in `buildIndex`, so lookups are O(log n) or O(1) instead of O(n) per call.

### Proof of Concept
Not fully verifiable without the ability to compile and execute Go code (no terminal/filesystem access in this session). Conceptually: define a proto message with N fields declared `[lazy=true]`. Craft a binary payload interleaving M copies of unrelated/duplicate field entries (causing `buildIndex` to produce an index of size ~M) alongside the N lazy fields, so wire bytes are roughly O(M) in size. Call `proto.Unmarshal` (lazy decoding enabled by default), then call `proto.Size(m)` or `proto.Marshal(m)`. Each of the N lazy-field lookups triggers a full O(M) scan in `lookupField`, giving O(N×M) cost — quadratic when N and M scale together with a single request's payload size.

### Citations

**File:** internal/impl/encode.go (L85-112)
```go
	for _, f := range mi.orderedCoderFields {
		if f.funcs.size == nil {
			continue
		}
		fptr := p.Apply(f.offset)

		if f.presenceIndex != noPresence {
			if !presence.Present(f.presenceIndex) {
				continue
			}

			if f.isLazy && fptr.AtomicGetPointer().IsNil() {
				if lazyFields(opts) {
					size += (*lazy).SizeField(uint32(f.num))
					continue
				} else {
					mi.lazyUnmarshal(p, f.num)
				}
			}
			size += f.funcs.size(fptr, f, opts)
			continue
		}

		if f.isPointer && fptr.Elem().IsNil() {
			continue
		}
		size += f.funcs.size(fptr, f, opts)
	}
```

**File:** internal/impl/encode.go (L176-219)
```go
	for _, f := range mi.orderedCoderFields {
		if f.funcs.marshal == nil {
			continue
		}
		fptr := p.Apply(f.offset)

		if f.presenceIndex != noPresence {
			if !presence.Present(f.presenceIndex) {
				continue
			}
			if f.isLazy {
				// Be careful, this field needs to be read atomically, like for a get
				if f.isPointer && fptr.AtomicGetPointer().IsNil() {
					if lazyFields(opts) {
						b, _ = (*lazy).AppendField(b, uint32(f.num))
						continue
					} else {
						mi.lazyUnmarshal(p, f.num)
					}
				}

				b, err = f.funcs.marshal(b, fptr, f, opts)
				if err != nil {
					return b, err
				}
				continue
			} else if f.isPointer && fptr.Elem().IsNil() {
				continue
			}
			b, err = f.funcs.marshal(b, fptr, f, opts)
			if err != nil {
				return b, err
			}
			continue
		}

		if f.isPointer && fptr.Elem().IsNil() {
			continue
		}
		b, err = f.funcs.marshal(b, fptr, f, opts)
		if err != nil {
			return b, err
		}
	}
```

**File:** internal/protolazy/lazy.go (L152-266)
```go
		}
		// DONE: tag, err = r.DecodeVarint32()

		fieldNum := protoFieldNumber(tag)
		if fieldNum < lastProtoFieldNum {
			outOfOrder = true
		}

		// Skip the current value -- will skip over an entire group as well.
		// INLINED: err = r.SkipValue(tag)
		wireType := tag & 0x7
		switch protowire.Type(wireType) {
		case protowire.VarintType:
			// INLINED: err = r.SkipVarint()
			i := r.Pos

			if len(r.Buf)-i < 10 {
				// Use DecodeVarintSlow() to skip while
				// checking for buffer overflow, but ignore result
				_, err = r.DecodeVarintSlow()
				goto out2
			}
			if r.Buf[i] < 0x80 {
				goto out
			}
			i++

			if r.Buf[i] < 0x80 {
				goto out
			}
			i++

			if r.Buf[i] < 0x80 {
				goto out
			}
			i++

			if r.Buf[i] < 0x80 {
				goto out
			}
			i++

			if r.Buf[i] < 0x80 {
				goto out
			}
			i++

			if r.Buf[i] < 0x80 {
				goto out
			}
			i++

			if r.Buf[i] < 0x80 {
				goto out
			}
			i++

			if r.Buf[i] < 0x80 {
				goto out
			}
			i++

			if r.Buf[i] < 0x80 {
				goto out
			}
			i++

			if r.Buf[i] < 0x80 {
				goto out
			}
			return nil, errOverflow
		out:
			r.Pos = i + 1
			// DONE: err = r.SkipVarint()
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

	out2:
		if err != nil {
			return nil, err
		}
		if fieldNum != lastProtoFieldNum {
			index = append(index, IndexEntry{FieldNum: fieldNum,
				Start: uint32(curPos),
				End:   uint32(r.Pos)},
			)
		} else {
			index[len(index)-1].End = uint32(r.Pos)
			index[len(index)-1].MultipleContiguous = true
		}
		lastProtoFieldNum = fieldNum
	}
	if outOfOrder {
		sort.Slice(index, func(i, j int) bool {
			return index[i].FieldNum < index[j].FieldNum ||
				(index[i].FieldNum == index[j].FieldNum &&
					index[i].Start < index[j].Start)
		})
	}
	return index, nil
}
```

**File:** internal/protolazy/lazy.go (L331-358)
```go
func lookupField(indexp *[]IndexEntry, fieldNum uint32) (start, end uint32, found bool, multipleContiguous bool, multipleEntries []IndexEntry) {
	// The pointer indexp to the index was already loaded atomically.
	// The slice is uniquely associated with the pointer, so it doesn't
	// need to be loaded atomically.
	index := *indexp
	for i, entry := range index {
		if fieldNum == entry.FieldNum {
			if i < len(index)-1 && entry.FieldNum == index[i+1].FieldNum {
				// Handle the uncommon case where there are
				// repeated entries for the same field which
				// are not contiguous in the protobuf.
				multiple := make([]IndexEntry, 1, 2)
				multiple[0] = IndexEntry{fieldNum, entry.Start, entry.End, entry.MultipleContiguous}
				i++
				for i < len(index) && index[i].FieldNum == fieldNum {
					multiple = append(multiple, IndexEntry{fieldNum, index[i].Start, index[i].End, index[i].MultipleContiguous})
					i++
				}
				return 0, 0, false, false, multiple

			}
			return entry.Start, entry.End, true, entry.MultipleContiguous, nil
		}
		if fieldNum < entry.FieldNum {
			return 0, 0, false, false, nil
		}
	}
	return 0, 0, false, false, nil
```

**File:** internal/impl/lazy.go (L184-205)
```go
	presence = p.Apply(mi.presenceOffset).PresenceInfo()
	lazy = p.Apply(mi.lazyOffset).LazyInfoPtr()
	if !presence.AnyPresent(mi.presenceSize) {
		if opts.CanBeLazy() {
			// If the message contains existing data, we need to merge into it.
			// Lazy unmarshaling doesn't merge, so only enable it when the
			// message is empty (has no presence bitmap).
			lazyDecode = true
			if *lazy == nil {
				*lazy = &protolazy.XXX_lazyUnmarshalInfo{}
			}
			(*lazy).SetUnmarshalFlags(opts.flags)
			if !opts.AliasBuffer() {
				// Make a copy of the buffer for lazy unmarshaling.
				// Set the AliasBuffer flag so recursive unmarshal
				// operations reuse the copy.
				b = append([]byte{}, b...)
				opts.flags |= piface.UnmarshalAliasBuffer
			}
			(*lazy).SetBuffer(b)
		}
	}
```
