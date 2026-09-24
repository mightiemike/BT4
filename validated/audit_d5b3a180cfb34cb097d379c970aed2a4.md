### Title
`debug_redact` FieldOptions annotation is never honored by `prototext`/`protojson`/`fmt.Stringer` output, exposing fields marked sensitive in logs and debug output - ([File: encoding/prototext/encode.go])

### Summary
`google.protobuf.FieldOptions.debug_redact` (and the analogous `EnumValueOptions.debug_redact`) exist specifically so that a schema author can flag a field as containing "sensitive credentials" that "should not be printed out when using debug formats" [1](#0-0) . This is the protobuf-go analog of Ansible's `no_log` task flag from CVE-2018-10855: a declared "do not log this" marker that downstream logging/debug code paths are expected to honor. In protobuf-go, every generated message's `String()` method, and by extension `fmt.Println`, `%v`/`%s` formatting, and generic error/log statements that stringify a proto message, route through `protoimpl.X.MessageStringOf`, which calls `prototext.MarshalOptions{Multiline: false}.Format(m)` [2](#0-1) .

### Finding Description
The `debug_redact` option is defined and exposed via `FieldOptions.GetDebugRedact()` [3](#0-2) , and via `EnumValueOptions.GetDebugRedact()` [4](#0-3) , so schema authors can mark a field carrying secrets (tokens, passwords, PII) for redaction in debug output — exactly analogous to Ansible's `no_log: true`.

However, tracing the actual sink — `prototext.MarshalOptions.Format` → `marshal` → `marshalMessage` → `marshalField` → `marshalSingular` [5](#0-4) [6](#0-5) [7](#0-6)  — shows that the field's value is written out unconditionally for every kind (`WriteString`, `WriteBool`, `WriteInt`, etc.), with no check anywhere against `fd.Options().(*descriptorpb.FieldOptions).GetDebugRedact()`. A repo-wide search confirms `debug_redact`/`GetDebugRedact` is referenced only in `descriptor.pb.go`, `internal/genid`, and unrelated `protorange` example code — it is never consulted by `encoding/prototext`, `encoding/protojson`, or `internal/impl` (the generated `String()` sink). Just like Ansible silently ignoring `no_log` on the failure/exception logging path, protobuf-go silently ignores `debug_redact` on every message-to-text conversion path, so any component that logs `err`, calls `fmt.Sprintf("%v", msg)`, or otherwise stringifies a message containing a field marked `debug_redact = true` will emit the "sensitive" value in full.

### Impact Explanation
Any service using generated protobuf messages that carry fields annotated `debug_redact = true` (the schema's explicit signal that the field holds credentials/secrets) will leak those values into logs, panic traces, or terminal output whenever the message (or a struct embedding it) is formatted via `%v`/`%s`, `fmt.Println`, `log.Printf`, or `prototext.Format`/`MarshalOptions.Format`. This is confidentiality-impacting disclosure of data the schema owner explicitly tried to protect, matching the CVSS vector of the analog report (`C:H/I:N/A:N`).

### Likelihood Explanation
This is trivially reachable: it requires no attacker interaction beyond the normal operation of logging error values or debug-formatting protobuf messages that a developer has already annotated `debug_redact = true` for exactly this reason. Since this is a silent, universal gap in the marshaler (not a corner case), any team using `debug_redact` believing it provides protection is unconditionally exposed the first time such a message is logged.

### Recommendation
Have `prototext`'s `marshalSingular`/`marshalField` (and the analogous `protojson` encoder, if it shares the same gap) check `fd.Options().(*descriptorpb.FieldOptions).GetDebugRedact()` (and `EnumValueOptions.GetDebugRedact()` for enum values) before emitting a field's value in `Format`/debug paths, substituting a placeholder such as `"[REDACTED]"` when the flag is set — mirroring how Ansible was fixed to honor `no_log` even on failure paths.

### Proof of Concept
```go
package main

import (
	"fmt"

	"google.golang.org/protobuf/encoding/prototext"
	"google.golang.org/protobuf/proto"
	// generated message MyMsg with a field `password` annotated:
	//   string password = 1 [debug_redact = true];
)

func main() {
	m := &MyMsg{Password: "s3cr3t-token-12345"}
	fmt.Println(m)                                   // -> password:"s3cr3t-token-12345"
	fmt.Println(prototext.Format(m))                 // -> password:"s3cr3t-token-12345"
	fmt.Printf("request failed: %v\n", m)            // typical error-log call site
}
```
Despite `password` being declared `debug_redact = true` in the `.proto` schema specifically to prevent this, the plaintext secret is printed in all three cases because no code path in `encoding/prototext` (or the `String()`/`MessageStringOf` sink it backs) inspects `GetDebugRedact()`.

**Note on verification limits:** I was unable to fully inspect `encoding/protojson/encode.go`'s field-marshaling loop within the available iterations to confirm whether it independently checks `debug_redact` (it appeared in the same grep-negative set, but I did not read its full body). The prototext path and the `String()`/`MessageStringOf` sink used by every generated message, however, are confirmed to never check the flag.

### Citations

**File:** types/descriptorpb/descriptor.pb.go (L2868-2870)
```go
	// Indicate that the field value should not be printed out when using debug
	// formats, e.g. when the field contains sensitive credentials.
	DebugRedact     *bool                           `protobuf:"varint,16,opt,name=debug_redact,json=debugRedact,def=0" json:"debug_redact,omitempty"`
```

**File:** types/descriptorpb/descriptor.pb.go (L2978-2983)
```go
func (x *FieldOptions) GetDebugRedact() bool {
	if x != nil && x.DebugRedact != nil {
		return *x.DebugRedact
	}
	return Default_FieldOptions_DebugRedact
}
```

**File:** types/descriptorpb/descriptor.pb.go (L3262-3267)
```go
func (x *EnumValueOptions) GetDebugRedact() bool {
	if x != nil && x.DebugRedact != nil {
		return *x.DebugRedact
	}
	return Default_EnumValueOptions_DebugRedact
}
```

**File:** internal/impl/api_export.go (L173-177)
```go
// MessageStringOf returns the message value as a string,
// which is the message serialized in the protobuf text format.
func (Export) MessageStringOf(m protoreflect.ProtoMessage) string {
	return prototext.MarshalOptions{Multiline: false}.Format(m)
}
```

**File:** encoding/prototext/encode.go (L161-188)
```go
// marshalMessage marshals the given protoreflect.Message.
func (e encoder) marshalMessage(m protoreflect.Message, inclDelims bool) error {
	messageDesc := m.Descriptor()
	if !flags.ProtoLegacy && messageset.IsMessageSet(messageDesc) {
		return errors.New("no support for proto1 MessageSets")
	}

	if inclDelims {
		e.StartMessage()
		defer e.EndMessage()
	}

	// Handle Any expansion.
	if messageDesc.FullName() == genid.Any_message_fullname {
		if e.marshalAny(m) {
			return nil
		}
		// If unable to expand, continue on to marshal Any as a regular message.
	}

	// Marshal fields.
	var err error
	order.RangeFields(m, order.IndexNameFieldOrder, func(fd protoreflect.FieldDescriptor, v protoreflect.Value) bool {
		if err = e.marshalField(fd.TextName(), v, fd); err != nil {
			return false
		}
		return true
	})
```

**File:** encoding/prototext/encode.go (L200-212)
```go

// marshalField marshals the given field with protoreflect.Value.
func (e encoder) marshalField(name string, val protoreflect.Value, fd protoreflect.FieldDescriptor) error {
	switch {
	case fd.IsList():
		return e.marshalList(name, val.List(), fd)
	case fd.IsMap():
		return e.marshalMap(name, val.Map(), fd)
	default:
		e.WriteName(name)
		return e.marshalSingular(val, fd)
	}
}
```

**File:** encoding/prototext/encode.go (L214-265)
```go
// marshalSingular marshals the given non-repeated field value. This includes
// all scalar types, enums, messages, and groups.
func (e encoder) marshalSingular(val protoreflect.Value, fd protoreflect.FieldDescriptor) error {
	kind := fd.Kind()
	switch kind {
	case protoreflect.BoolKind:
		e.WriteBool(val.Bool())

	case protoreflect.StringKind:
		s := val.String()
		if !e.opts.allowInvalidUTF8 && strs.EnforceUTF8(fd) && !utf8.ValidString(s) {
			return errors.InvalidUTF8(string(fd.FullName()))
		}
		e.WriteString(s)

	case protoreflect.Int32Kind, protoreflect.Int64Kind,
		protoreflect.Sint32Kind, protoreflect.Sint64Kind,
		protoreflect.Sfixed32Kind, protoreflect.Sfixed64Kind:
		e.WriteInt(val.Int())

	case protoreflect.Uint32Kind, protoreflect.Uint64Kind,
		protoreflect.Fixed32Kind, protoreflect.Fixed64Kind:
		e.WriteUint(val.Uint())

	case protoreflect.FloatKind:
		// Encoder.WriteFloat handles the special numbers NaN and infinites.
		e.WriteFloat(val.Float(), 32)

	case protoreflect.DoubleKind:
		// Encoder.WriteFloat handles the special numbers NaN and infinites.
		e.WriteFloat(val.Float(), 64)

	case protoreflect.BytesKind:
		e.WriteString(string(val.Bytes()))

	case protoreflect.EnumKind:
		num := val.Enum()
		if desc := fd.Enum().Values().ByNumber(num); desc != nil {
			e.WriteLiteral(string(desc.Name()))
		} else {
			// Use numeric value if there is no enum description.
			e.WriteInt(int64(num))
		}

	case protoreflect.MessageKind, protoreflect.GroupKind:
		return e.marshalMessage(val.Message(), true)

	default:
		panic(fmt.Sprintf("%v has unknown kind: %v", fd.FullName(), kind))
	}
	return nil
}
```
