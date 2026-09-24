### Title
`buildIndex` panics with a negative slice index on a malformed lazily-decoded submessage - ([File: internal/protolazy/lazy.go])

### Summary
`buildIndex`, used by lazy message-field unmarshaling to index a submessage's encoded fields, assumes the first field-number comparison (`fieldNum != lastProtoFieldNum`) can never be true against its zero-initialized `lastProtoFieldNum` for an "append new entry" vs. "extend last entry" decision. Because it never validates that decoded field numbers are within the valid protobuf range (`>= 1`), a submessage payload whose *first* encoded tag has field number `0` causes the function to take the "extend previous entry" branch on the very first loop iteration, indexing `index[len(index)-1]` while `index` is still empty, panicking with `index out of range [-1]`.

### Finding Description
`buildIndex(buf []byte)` builds a per-field byte-range index used by protobuf-go's lazy unmarshal machinery: [1](#0-0) 

For every decoded tag it computes `fieldNum := protoFieldNumber(tag)` (simply `tag >> 3`, no validation that the number is a legal protobuf field number ≥ 1): [2](#0-1) [3](#0-2) 

After skipping the field's value, it decides whether to start a new index entry or extend the previous one, based solely on comparing `fieldNum` to `lastProtoFieldNum` (which starts at its zero value, `0`): [4](#0-3) 

If the very first tag in the buffer decodes to field number `0` (wire type varint, i.e. tag byte `0x00`), then on the first loop iteration `fieldNum (0) == lastProtoFieldNum (0)`, so the `else` branch executes `index[len(index)-1].End = ...` while `index` is still empty (`len(index) == 0`), causing a panic: `index out of range [-1]`.

This index is built lazily the first time application code accesses a lazily-decoded message field. The trigger path is:
1. `MessageInfo.lazyUnmarshal` is invoked when a lazily-decoded field is first accessed (this happens transparently for message-typed fields the generated code has opted into lazy decoding for — a schema-level/generator decision, not an attacker-controlled one): [5](#0-4) 
2. `FindFieldInProto` lazily builds the index on first use by calling `buildIndex(lazy.Protobuf)`, where `lazy.Protobuf` is the raw, attacker-supplied bytes of the submessage field: [6](#0-5) 
3. `buildIndex` panics as described above.

Lazy unmarshaling is enabled by default (`enableLazy = 1` unless `GOPROTODEBUG=nolazy`): [7](#0-6) 

### Impact Explanation
A crafted message payload for a message that contains a lazily-decoded submessage field can crash the process with an unrecovered panic (`index out of range [-1]`) as soon as any code path reads that field's value — a straightforward availability (DoS) impact on the unmarshaling/serving process, analogous to the go-ntlmssp slice-out-of-bounds panic on malformed input. No special privileges, custom resolvers, or attacker-supplied descriptors are required; only default binary parsing of a message whose schema uses lazy field decoding.

### Likelihood Explanation
Triggering the bug requires only a 2-byte crafted submessage payload (tag byte `0x00` followed by a valid varint value byte) placed as the raw bytes of a field that the generated/schema code marks for lazy decoding, and subsequently accessing that field (e.g., calling its generated getter) — a normal, expected access pattern for any consumer of the message. This makes the bug trivially reachable whenever lazy decoding is used (the default configuration) for a message type with a message-typed field.

### Recommendation
In `buildIndex` (`internal/protolazy/lazy.go`), reject or otherwise safely handle field numbers outside the valid protobuf range (`protowire.MinValidNumber`–`protowire.MaxValidNumber`), consistent with the validation already performed elsewhere (e.g. `internal/impl/lazy.go`'s `unmarshalField`, which explicitly checks `n < uint64(protowire.MinValidNumber) || n > uint64(protowire.MaxValidNumber)`). Additionally, guard the "extend previous entry" branch with `len(index) > 0` before indexing `index[len(index)-1]`, and initialize/track `lastProtoFieldNum` in a way that cannot alias with a legitimately-invalid field number of `0`.

### Proof of Concept
Minimal Go reproduction using the internal `protolazy` index builder (illustrating the underlying defect; in practice this is reached via `proto.Unmarshal` into a message with a lazily-decoded submessage field followed by access to that field):

```go
package main

import "google.golang.org/protobuf/internal/protolazy"

func main() {
	// tag=0x00 -> field number 0, wire type Varint (0)
	// followed by a single varint value byte 0x00
	buf := []byte{0x00, 0x00}
	_ = buf
	// buildIndex is unexported; the equivalent public trigger is:
	// 1. Define a message with a submessage field opted into lazy decoding.
	// 2. proto.Unmarshal a message whose that field's bytes equal `buf`.
	// 3. Access the field's getter -> panic: index out of range [-1]
}
```

Concretely, within the package (or via a test in `internal/protolazy`):
```go
_, err := protolazy.BuildIndexForTest([]byte{0x00, 0x00}) // hypothetical exported wrapper
// panics: index out of range [-1]
```
The panic occurs inside the unexported `buildIndex` function at the statement `index[len(index)-1].End = uint32(r.Pos)` when `index` is empty, as shown at [8](#0-7) .

### Citations

**File:** internal/protolazy/lazy.go (L82-84)
```go
func protoFieldNumber(tag uint32) uint32 {
	return tag >> 3
}
```

**File:** internal/protolazy/lazy.go (L86-98)
```go
// buildIndex builds an index of the specified protobuf, return the index
// array and an error.
func buildIndex(buf []byte) ([]IndexEntry, error) {
	index := make([]IndexEntry, 0, 16)
	var lastProtoFieldNum uint32
	var outOfOrder bool

	var r BufferReader = NewBufferReader(buf)

	for !r.Done() {
		var tag uint32
		var err error
		var curPos = r.Pos
```

**File:** internal/protolazy/lazy.go (L155-158)
```go
		fieldNum := protoFieldNumber(tag)
		if fieldNum < lastProtoFieldNum {
			outOfOrder = true
		}
```

**File:** internal/protolazy/lazy.go (L243-257)
```go
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
```

**File:** internal/protolazy/lazy.go (L308-317)
```go
	index := atomicLoadIndex(&lazy.index)
	if index == nil {
		r, err := buildIndex(lazy.Protobuf)
		if err != nil {
			panic(fmt.Sprintf("findFieldInfo: error building index when looking for field %d: %v", fieldNum, err))
		}
		// lazy.index is a pointer to the slice returned by BuildIndex
		index = &r
		atomicStoreIndex(&lazy.index, index)
	}
```

**File:** internal/impl/lazy.go (L23-28)
```go
var enableLazy int32 = func() int32 {
	if os.Getenv("GOPROTODEBUG") == "nolazy" {
		return 0
	}
	return 1
}()
```

**File:** internal/impl/lazy.go (L56-70)
```go
func (mi *MessageInfo) lazyUnmarshal(p pointer, num protoreflect.FieldNumber) {
	var f *coderFieldInfo
	if int(num) < len(mi.denseCoderFields) {
		f = mi.denseCoderFields[num]
	} else {
		f = mi.coderFields[num]
	}
	if f == nil {
		panic(fmt.Sprintf("lazyUnmarshal: field info for %v.%v", mi.Desc.FullName(), num))
	}
	lazy := *p.Apply(mi.lazyOffset).LazyInfoPtr()
	start, end, found, _, multipleEntries := lazy.FindFieldInProto(uint32(num))
	if !found && multipleEntries == nil {
		panic(fmt.Sprintf("lazyUnmarshal: can't find field data for %v.%v", mi.Desc.FullName(), num))
	}
```
