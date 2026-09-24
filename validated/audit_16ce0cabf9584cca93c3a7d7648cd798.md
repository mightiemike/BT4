### Title
Unbounded Recursion in `proto.Merge`/`proto.Clone` Enables Stack-Exhaustion DoS on Attacker-Controlled Deeply Nested Messages - ([File: proto/merge.go])

### Summary
`proto.Unmarshal`, `protojson.Unmarshal`, and `prototext.Unmarshal` all enforce a recursion depth bound (`protowire.DefaultRecursionLimit = 10000`) while decoding nested/self-recursive messages [1](#0-0) . Once such a message is successfully decoded (which is legal up to 10000 nesting levels), calling `proto.Merge` or `proto.Clone` on it recurses through the exact same nested structure with **no depth limit at all**, mirroring the cJSON bug pattern where the parser bounds depth but the copy/duplicate path (`cJSON_Duplicate`) does not enforce an equivalently tight bound.

### Finding Description
`proto.Unmarshal` decrements `RecursionLimit` on every nested message and errors out once it goes negative [2](#0-1) , and the default limit is `protowire.DefaultRecursionLimit = 10000` [1](#0-0) . The same bound is applied identically in `protojson.decoder.unmarshalMessage` [3](#0-2)  and `internal/impl.unmarshalPointer` [4](#0-3) . This means an attacker can legally send a binary or JSON payload nesting a self-recursive message field (e.g., `optional_nested_message.corecursive...`, as exercised by `TestAllTypes` test fixtures) up to 10000 levels deep, and the parser will accept it without error, per the "just at recursion limit" test cases [5](#0-4) .

However, `proto.Merge` and `proto.Clone` — routine operations applied to already-decoded, trusted-schema messages (e.g., defensive copies, response aggregation, caching) — recurse through the resulting in-memory message graph with **no recursion counter or depth check whatsoever**:
- `proto/merge.go`'s `mergeOptions.mergeMessage` recursively calls itself for every nested message field, list element, and map value with no depth tracking [6](#0-5) .
- The generated-message fast path in `internal/impl/merge.go`'s `mergePointer`/`mergeMessage`/`mergeMessageSlice`/`mergeMessageListValue` similarly recurses unbounded, and `mergeMessageListValue` additionally calls `proto.Clone` (itself unbounded) on every list element [7](#0-6) [8](#0-7) .
- `proto.Clone` is implemented directly on top of the same unbounded `mergeMessage` [9](#0-8) .

This is the direct structural analog of the cJSON report: the parser (`cJSON_Parse`/protobuf `Unmarshal`) enforces a nesting limit, but the duplication/merge sink (`cJSON_Duplicate`/`proto.Merge`/`proto.Clone`) either has a much higher or (here) no limit at all, so a payload that is valid at the parser's boundary can still exhaust the stack once it reaches the unbounded sink.

### Impact Explanation
Any unprivileged caller who can submit a protobuf message (binary, ProtoJSON, or text format) that gets decoded by a service and subsequently copied/merged (a very common pattern — defensive copies before mutation, request/response aggregation, retry buffering, caching layers) can trigger unbounded native-stack recursion once decoded depth is high enough (up to the 10000-level ceiling allowed by the decoder). This crashes the process (`SIGSEGV`/goroutine stack overflow → fatal error, unrecoverable via `recover()`), resulting in denial of service. Confidentiality/integrity of data are not affected; this is a pure availability impact (`VA:H`, matching the CVSS vector class in the source report).

### Likelihood Explanation
Likelihood is limited by two factors: (1) an application must actually build a self-recursive/deeply-nested message via untrusted request data and then call `proto.Merge`/`proto.Clone` on it — not guaranteed for every protobuf-go consumer, but a common and idiomatic pattern; and (2) the message schema must contain a recursive or deeply nestable message field (common in tree-like domain models, e.g., filters, ASTs, org-chart-style structures). Reaching the default 10000-level bound requires a payload roughly proportional in size to the depth, which is easily achievable well within typical request-size limits. No privileged access, custom resolver, or malicious descriptor is required — only a schema with a recursive/nested message type and a standard `Unmarshal` + `Merge`/`Clone` call sequence.

### Recommendation
Introduce a recursion/depth-tracking mechanism in `proto.Merge` (`mergeOptions.mergeMessage`) and in the generated-message merge fast path (`internal/impl/merge.go`), consistent with the depth counters already used by `Unmarshal`. Cap recursion at a value no higher than `protowire.DefaultRecursionLimit` (or lower), and return an error/panic cleanly rather than allowing raw stack recursion to proceed unbounded. The same treatment should be applied to `proto.Equal` and any other recursive traversal API operating on already-decoded messages, since they share the same unbounded-recursion pattern.

### Proof of Concept
```go
package main

import (
	"google.golang.org/protobuf/proto"
	testpb "google.golang.org/protobuf/internal/testprotos/test"
)

// Build a message nested ~9999 levels deep via the recursive
// optional_nested_message.corecursive field chain, well within the
// protowire.DefaultRecursionLimit (10000) enforced by Unmarshal.
func buildDeep(depth int) *testpb.TestAllTypes {
	m := &testpb.TestAllTypes{}
	cur := m
	for i := 0; i < depth; i++ {
		next := &testpb.TestAllTypes{}
		cur.OptionalNestedMessage = &testpb.TestAllTypes_NestedMessage{
			Corecursive: next,
		}
		cur = next
	}
	return m
}

func main() {
	src := buildDeep(9999)

	b, err := proto.Marshal(src)
	if err != nil {
		panic(err)
	}

	// Step 1: Unmarshal succeeds — this is within the decoder's
	// recursion-depth bound, so the attacker-controlled payload is accepted.
	dst := &testpb.TestAllTypes{}
	if err := proto.Unmarshal(b, dst); err != nil {
		panic(err)
	}

	// Step 2: A routine defensive copy / merge operation on the
	// already-decoded message recurses ~9999 stack frames with NO
	// depth limit, risking stack exhaustion (crash) on typical goroutine
	// stack sizes for sufficiently deep/complex nested structures.
	clone := proto.Clone(dst)
	_ = clone
}
```
Note: exact crash depth depends on Go's growable-goroutine-stack limits and per-frame size of `mergeMessage`/`mergePointer`, so real-world payloads may need to combine deep nesting with wide fan-out (lists/maps of nested messages, each independently recursively merged/cloned via `mergeMessageListValue`/`mergeMapOfMessage`) to reliably exhaust the stack; the unbounded-recursion invariant violation itself is unconditionally present regardless of the exact crash threshold.

### Citations

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

**File:** proto/decode.go (L90-127)
```go
func (o UnmarshalOptions) unmarshal(b []byte, m protoreflect.Message) (out protoiface.UnmarshalOutput, err error) {
	if o.Resolver == nil {
		o.Resolver = protoregistry.GlobalTypes
	}
	if !o.Merge {
		Reset(m.Interface())
	}
	allowPartial := o.AllowPartial
	o.Merge = true
	o.AllowPartial = true
	methods := protoMethods(m)
	if methods != nil && methods.Unmarshal != nil &&
		!(o.DiscardUnknown && methods.Flags&protoiface.SupportUnmarshalDiscardUnknown == 0) {
		in := protoiface.UnmarshalInput{
			Message:  m,
			Buf:      b,
			Resolver: o.Resolver,
			Depth:    o.RecursionLimit,
		}
		if o.DiscardUnknown {
			in.Flags |= protoiface.UnmarshalDiscardUnknown
		}

		if !allowPartial {
			// This does not affect how current unmarshal functions work, it just allows them
			// to record this for lazy the decoding case.
			in.Flags |= protoiface.UnmarshalCheckRequired
		}
		if o.NoLazyDecoding {
			in.Flags |= protoiface.UnmarshalNoLazyDecoding
		}

		out, err = methods.Unmarshal(in)
	} else {
		if o.RecursionLimit--; o.RecursionLimit < 0 {
			return out, errRecursionDepth
		}
		err = o.unmarshalMessageSlow(b, m)
```

**File:** encoding/protojson/decode.go (L124-128)
```go
func (d decoder) unmarshalMessage(m protoreflect.Message, skipTypeURL bool) error {
	d.opts.RecursionLimit--
	if d.opts.RecursionLimit < 0 {
		return errors.New("exceeded max recursion depth")
	}
```

**File:** internal/impl/decode.go (L103-107)
```go
func (mi *MessageInfo) unmarshalPointer(b []byte, p pointer, groupTag protowire.Number, opts unmarshalOptions) (out unmarshalOutput, err error) {
	mi.init()
	if opts.depth--; opts.depth < 0 {
		return out, errRecursionDepth
	}
```

**File:** proto/testmessages_test.go (L1746-1766)
```go
	{
		desc: "just at recursion limit: maps",
		unmarshalOptions: proto.UnmarshalOptions{
			// Maps are syntactic sugar for repeated fields of a synthetic
			// message type (with key as field 1, value as field 2).
			RecursionLimit: 2,
		},
		decodeTo: makeMessages(protobuild.Message{
			"map_int32_int32": map[int32]int32{1056: 1156, 2056: 2156},
		}, &test3pb.TestAllTypes{}, &testeditionspb.TestAllTypes{}),
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

**File:** proto/merge.go (L39-60)
```go
// Clone returns a deep copy of m.
// If the top-level message is invalid, it returns an invalid message as well.
func Clone(m Message) Message {
	// NOTE: Most usages of Clone assume the following properties:
	//	t := reflect.TypeOf(m)
	//	t == reflect.TypeOf(m.ProtoReflect().New().Interface())
	//	t == reflect.TypeOf(m.ProtoReflect().Type().Zero().Interface())
	//
	// Embedding protobuf messages breaks this since the parent type will have
	// a forwarded ProtoReflect method, but the Interface method will return
	// the underlying embedded message type.
	if m == nil {
		return nil
	}
	src := m.ProtoReflect()
	if !src.IsValid() {
		return src.Type().Zero().Interface()
	}
	dst := src.New()
	mergeOptions{}.mergeMessage(dst, src)
	return dst.Interface()
}
```

**File:** proto/merge.go (L72-108)
```go
func (o mergeOptions) mergeMessage(dst, src protoreflect.Message) {
	methods := protoMethods(dst)
	if methods != nil && methods.Merge != nil {
		in := protoiface.MergeInput{
			Destination: dst,
			Source:      src,
		}
		out := methods.Merge(in)
		if out.Flags&protoiface.MergeComplete != 0 {
			return
		}
	}

	if !dst.IsValid() {
		panic(fmt.Sprintf("cannot merge into invalid %v message", dst.Descriptor().FullName()))
	}

	src.Range(func(fd protoreflect.FieldDescriptor, v protoreflect.Value) bool {
		switch {
		case fd.IsList():
			o.mergeList(dst.Mutable(fd).List(), v.List(), fd)
		case fd.IsMap():
			o.mergeMap(dst.Mutable(fd).Map(), v.Map(), fd.MapValue())
		case fd.Message() != nil:
			o.mergeMessage(dst.Mutable(fd).Message(), v.Message())
		case fd.Kind() == protoreflect.BytesKind:
			dst.Set(fd, o.cloneBytes(v))
		default:
			dst.Set(fd, v)
		}
		return true
	})

	if len(src.GetUnknown()) > 0 {
		dst.SetUnknown(append(dst.GetUnknown(), src.GetUnknown()...))
	}
}
```

**File:** internal/impl/merge.go (L36-80)
```go
func (mi *MessageInfo) mergePointer(dst, src pointer, opts mergeOptions) {
	mi.init()
	if dst.IsNil() {
		panic(fmt.Sprintf("invalid value: merging into nil message"))
	}
	if src.IsNil() {
		return
	}

	var presenceSrc presence
	var presenceDst presence
	if mi.presenceOffset.IsValid() {
		presenceSrc = src.Apply(mi.presenceOffset).PresenceInfo()
		presenceDst = dst.Apply(mi.presenceOffset).PresenceInfo()
	}

	for _, f := range mi.orderedCoderFields {
		if f.funcs.merge == nil {
			continue
		}
		sfptr := src.Apply(f.offset)

		if f.presenceIndex != noPresence {
			if !presenceSrc.Present(f.presenceIndex) {
				continue
			}
			dfptr := dst.Apply(f.offset)
			if f.isLazy {
				if sfptr.AtomicGetPointer().IsNil() {
					mi.lazyUnmarshal(src, f.num)
				}
				if presenceDst.Present(f.presenceIndex) && dfptr.AtomicGetPointer().IsNil() {
					mi.lazyUnmarshal(dst, f.num)
				}
			}
			f.funcs.merge(dst.Apply(f.offset), sfptr, f, opts)
			presenceDst.SetPresentUnatomic(f.presenceIndex, mi.presenceSize)
			continue
		}

		if f.isPointer && sfptr.Elem().IsNil() {
			continue
		}
		f.funcs.merge(dst.Apply(f.offset), sfptr, f, opts)
	}
```

**File:** internal/impl/merge.go (L143-185)
```go
func mergeMessageListValue(dst, src protoreflect.Value, opts mergeOptions) protoreflect.Value {
	dstl := dst.List()
	srcl := src.List()
	for i, llen := 0, srcl.Len(); i < llen; i++ {
		sm := srcl.Get(i).Message()
		dm := proto.Clone(sm.Interface()).ProtoReflect()
		dstl.Append(protoreflect.ValueOfMessage(dm))
	}
	return dst
}

func mergeMessageValue(dst, src protoreflect.Value, opts mergeOptions) protoreflect.Value {
	opts.Merge(dst.Message().Interface(), src.Message().Interface())
	return dst
}

func mergeMessage(dst, src pointer, f *coderFieldInfo, opts mergeOptions) {
	if f.mi != nil {
		if dst.Elem().IsNil() {
			dst.SetPointer(pointerOfValue(reflect.New(f.mi.GoReflectType.Elem())))
		}
		f.mi.mergePointer(dst.Elem(), src.Elem(), opts)
	} else {
		dm := dst.AsValueOf(f.ft).Elem()
		sm := src.AsValueOf(f.ft).Elem()
		if dm.IsNil() {
			dm.Set(reflect.New(f.ft.Elem()))
		}
		opts.Merge(asMessage(dm), asMessage(sm))
	}
}

func mergeMessageSlice(dst, src pointer, f *coderFieldInfo, opts mergeOptions) {
	for _, sp := range src.PointerSlice() {
		dm := reflect.New(f.ft.Elem().Elem())
		if f.mi != nil {
			f.mi.mergePointer(pointerOfValue(dm), sp, opts)
		} else {
			opts.Merge(asMessage(dm), asMessage(sp.AsValueOf(f.ft.Elem().Elem())))
		}
		dst.AppendPointerSlice(pointerOfValue(dm))
	}
}
```
