### Title
`RecursionLimit` configured via `proto.UnmarshalOptions` is bypassed for unknown/unregistered group fields, which are skipped using a fixed, independent recursion counter - ([File: encoding/protowire/wire.go])

### Summary
`proto.UnmarshalOptions.RecursionLimit` is documented as limiting "how deeply messages may be nested" [1](#0-0) , and every recursive call into `unmarshalPointer`/`unmarshalMessage`/`unmarshalMap` decrements the caller-supplied `opts.depth`/`o.RecursionLimit` and rejects further recursion once it goes negative [2](#0-1) [3](#0-2) . However, when an unknown or unregistered field is encountered with `StartGroupType` during eager or lazy unmarshaling, it is skipped via `protowire.ConsumeFieldValue(num, wtyp, b)` [4](#0-3) [5](#0-4) . That function hard-codes its own recursion depth to the package constant `DefaultRecursionLimit` (10000), completely independent of the caller's configured `RecursionLimit`: [6](#0-5) [7](#0-6) 

This is directly analogous to the Rack `QueryParser` bug: the enforcement path (`opts.depth` decrement) counts recursion for one code path (known/registered fields), while the actual consuming/skipping path (unknown group fields) uses a completely different, uncoupled counting mechanism, silently permitting nesting far beyond the caller's configured limit.

### Finding Description
`proto.Unmarshal`/`UnmarshalOptions.Unmarshal` initializes `o.RecursionLimit` (defaulting to `protowire.DefaultRecursionLimit` if zero) and threads it through every message/map/list unmarshal call as `opts.depth`, decrementing on each recursive descent [8](#0-7) [9](#0-8) .

When the decoder in `unmarshalPointerEager` (and identically in `unmarshalPointerLazy`) encounters a field number that is not present in `denseCoderFields`/`coderFields` and is not resolvable as a registered extension, it falls into the `errUnknown` branch and calls `protowire.ConsumeFieldValue(num, wtyp, b)` purely to skip over the bytes [10](#0-9) . If `wtyp` is `StartGroupType`, `ConsumeFieldValue` recurses into `consumeFieldValueD`, which tracks its *own* depth counter seeded from the constant `DefaultRecursionLimit = 10000`, decrementing once per nested group level [11](#0-10) . This depth value is never derived from, or synchronized with, `opts.depth`/the caller's `UnmarshalOptions.RecursionLimit`.

Consequently, a caller that explicitly lowers `RecursionLimit` (e.g., to `1`, `5`, or `100`) specifically to bound the cost of processing nested/recursive protobuf structures on an unprivileged request path (a common defensive pattern, exactly mirrored in the package's own tests, e.g. `RecursionLimit: 1` triggering `errRecursionDepth` for *known* fields [12](#0-11) ) gets no such protection for group-typed fields that are unknown to the schema (unregistered field numbers, or extension numbers the configured `Resolver` does not recognize). Those fields can be nested up to the hard-coded `10000` levels regardless of the caller's configured limit.

### Impact Explanation
This lets a remote, unprivileged client bypass an operator-configured DoS mitigation (`RecursionLimit`) by encoding the excess nesting purely as unknown/unregistered group fields, achieving up to ~10000 levels of nested group parsing (tag scan + `ConsumeTag`/recursive call overhead per level) instead of the intended small bound. This matches CWE-400/CWE-770 resource-consumption bypass, the same bug class as the Rack advisory, where a configured protective limit is silently not enforced on an alternate parsing path. The severity is bounded by the fixed ceiling of 10000 (Go's growable goroutine stack will not overflow at this depth), so the practical effect is CPU/stack-frame amplification proportional to the gap between the operator's intended limit and 10000, not unbounded resource exhaustion.

### Likelihood Explanation
Reachable on any binary-protobuf unmarshal path (`proto.Unmarshal`, `UnmarshalOptions.Unmarshal`, `UnmarshalState`) that processes attacker-controlled bytes against a trusted schema, using ordinary unregistered field numbers (or an extension resolver that doesn't recognize the number) with `StartGroupType` wiring — no malicious peer/custom resolver/attacker-supplied descriptor is required, and no privileged caller is needed. It requires only that the deployment sets a custom (lower) `RecursionLimit` for defensive purposes, which is a documented, encouraged configuration knob.

### Recommendation
Thread the caller's remaining `opts.depth` (or an equivalent value derived from `UnmarshalOptions.RecursionLimit`) into `protowire.ConsumeFieldValue`/`consumeFieldValueD` when it is invoked to skip unknown group fields inside `unmarshalPointerEager`/`unmarshalPointerLazy`, instead of relying on the unrelated package-level `DefaultRecursionLimit` constant, so the configured limit is honored uniformly for both known and unknown fields.

### Proof of Concept
```go
package main

import (
	"fmt"

	"google.golang.org/protobuf/encoding/protowire"
	"google.golang.org/protobuf/proto"
	testpb "google.golang.org/protobuf/internal/testprotos/test3"
)

// buildNestedUnknownGroup builds `depth` levels of nested groups under an
// unregistered field number (999), which will be treated as "unknown" by
// the target message and skipped via protowire.ConsumeFieldValue.
func buildNestedUnknownGroup(depth int) []byte {
	const unknownFieldNum = 999
	b := protowire.AppendTag(nil, unknownFieldNum, protowire.StartGroupType)
	for i := 0; i < depth; i++ {
		b = protowire.AppendTag(b, unknownFieldNum, protowire.StartGroupType)
	}
	for i := 0; i < depth; i++ {
		b = protowire.AppendTag(b, unknownFieldNum, protowire.EndGroupType)
	}
	b = protowire.AppendTag(b, unknownFieldNum, protowire.EndGroupType)
	return b
}

func main() {
	// Operator configures a strict RecursionLimit of 2 to bound resource
	// usage on untrusted input.
	opts := proto.UnmarshalOptions{RecursionLimit: 2}

	m := &testpb.TestAllTypes{}
	data := buildNestedUnknownGroup(5000) // far exceeds RecursionLimit=2

	err := opts.Unmarshal(data, m)
	// Expected if limit were enforced consistently: "exceeded maximum recursion depth".
	// Actual: err == nil, because the unknown group field is skipped via
	// protowire.ConsumeFieldValue, which uses its own fixed depth of 10000,
	// bypassing the caller's RecursionLimit entirely.
	fmt.Println("err:", err)
}
```

### Citations

**File:** proto/decode.go (L46-48)
```go
	// RecursionLimit limits how deeply messages may be nested.
	// If zero, a default limit is applied.
	RecursionLimit int
```

**File:** proto/decode.go (L61-74)
```go
func Unmarshal(b []byte, m Message) error {
	_, err := UnmarshalOptions{RecursionLimit: protowire.DefaultRecursionLimit}.unmarshal(b, m.ProtoReflect())
	return err
}

// Unmarshal parses the wire-format message in b and places the result in m.
// The provided message must be mutable (e.g., a non-nil pointer to a message).
func (o UnmarshalOptions) Unmarshal(b []byte, m Message) error {
	if o.RecursionLimit == 0 {
		o.RecursionLimit = protowire.DefaultRecursionLimit
	}
	_, err := o.unmarshal(b, m.ProtoReflect())
	return err
}
```

**File:** proto/decode.go (L221-224)
```go
func (o UnmarshalOptions) unmarshalMap(b []byte, wtyp protowire.Type, mapv protoreflect.Map, fd protoreflect.FieldDescriptor) (n int, err error) {
	if o.RecursionLimit--; o.RecursionLimit < 0 {
		return 0, errRecursionDepth
	}
```

**File:** internal/impl/decode.go (L103-119)
```go
func (mi *MessageInfo) unmarshalPointer(b []byte, p pointer, groupTag protowire.Number, opts unmarshalOptions) (out unmarshalOutput, err error) {
	mi.init()
	if opts.depth--; opts.depth < 0 {
		return out, errRecursionDepth
	}
	if flags.ProtoLegacy && mi.isMessageSet {
		return unmarshalMessageSet(mi, b, p, opts)
	}

	lazyDecoding := LazyEnabled() // default
	if opts.NoLazyDecoding() {
		lazyDecoding = false // explicitly disabled
	}
	if mi.lazyOffset.IsValid() && lazyDecoding {
		return mi.unmarshalPointerLazy(b, p, groupTag, opts)
	}
	return mi.unmarshalPointerEager(b, p, groupTag, opts)
```

**File:** internal/impl/decode.go (L197-230)
```go
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
			if !opts.DiscardUnknown() && mi.unknownOffset.IsValid() {
				u := mi.mutableUnknownBytes(p)
				*u = protowire.AppendTag(*u, num, wtyp)
				*u = append(*u, b[:n]...)
			}
```

**File:** internal/impl/lazy.go (L357-369)
```go
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
```

**File:** encoding/protowire/wire.go (L23-29)
```go
const (
	MinValidNumber        Number = 1
	FirstReservedNumber   Number = 19000
	LastReservedNumber    Number = 19999
	MaxValidNumber        Number = 1<<29 - 1
	DefaultRecursionLimit        = 10000
)
```

**File:** encoding/protowire/wire.go (L106-114)
```go
// ConsumeFieldValue parses a field value and returns its length.
// This assumes that the field [Number] and wire [Type] have already been parsed.
// This returns a negative length upon an error (see [ParseError]).
//
// When parsing a group, the length includes the end group marker and
// the end group is verified to match the starting field number.
func ConsumeFieldValue(num Number, typ Type, b []byte) (n int) {
	return consumeFieldValueD(num, typ, b, DefaultRecursionLimit)
}
```

**File:** encoding/protowire/wire.go (L116-153)
```go
func consumeFieldValueD(num Number, typ Type, b []byte, depth int) (n int) {
	switch typ {
	case VarintType:
		_, n = ConsumeVarint(b)
		return n
	case Fixed32Type:
		_, n = ConsumeFixed32(b)
		return n
	case Fixed64Type:
		_, n = ConsumeFixed64(b)
		return n
	case BytesType:
		_, n = ConsumeBytes(b)
		return n
	case StartGroupType:
		if depth < 0 {
			return errCodeRecursionDepth
		}
		n0 := len(b)
		for {
			num2, typ2, n := ConsumeTag(b)
			if n < 0 {
				return n // forward error code
			}
			b = b[n:]
			if typ2 == EndGroupType {
				if num != num2 {
					return errCodeEndGroup
				}
				return n0 - len(b)
			}

			n = consumeFieldValueD(num2, typ2, b, depth-1)
			if n < 0 {
				return n // forward error code
			}
			b = b[n:]
		}
```

**File:** proto/testmessages_test.go (L2342-2363)
```go
	{
		desc: "exceed recursion limit: maps",
		unmarshalOptions: proto.UnmarshalOptions{
			// Maps are syntactic sugar for repeated fields of a synthetic
			// message type (with key as field 1, value as field 2).
			RecursionLimit: 1,
		},
		decodeTo: []proto.Message{
			(*testpb.TestAllTypes)(nil),
			(*testeditionspb.TestAllTypes)(nil),
		},
		wire: protopack.Message{
			protopack.Tag{56, protopack.BytesType}, protopack.LengthPrefix(protopack.Message{
				protopack.Tag{1, protopack.VarintType}, protopack.Varint(1056),
				protopack.Tag{2, protopack.VarintType}, protopack.Varint(1156),
			}),
			protopack.Tag{56, protopack.BytesType}, protopack.LengthPrefix(protopack.Message{
				protopack.Tag{1, protopack.VarintType}, protopack.Varint(2056),
				protopack.Tag{2, protopack.VarintType}, protopack.Varint(2156),
			}),
		}.Marshal(),
	},
```
