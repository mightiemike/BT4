### Title
`debug_redact` FieldOptions annotation is never honored by prototext/`String()` debug formatting, leaking fields the schema marks as sensitive - ([File: encoding/prototext/encode.go])

### Summary
Every generated protobuf-go message's `String()` method, and `prototext.Format`/`prototext.Marshal`, serialize field values without ever checking the `google.protobuf.FieldOptions.debug_redact` (and `EnumValueOptions.debug_redact`) annotation. A trusted `.proto` schema author who marks a credential/secret field with `[debug_redact = true]` — explicitly to prevent that value from appearing "when using debug formats, e.g. when the field contains sensitive credentials" — gets no protection at all: the value is printed in cleartext by `fmt.Println(msg)`, `%v`/`%s` formatting, `log.Printf("%v", msg)`, or explicit `proto.Message.String()` calls, which are extremely common on unprivileged request/response and error-logging paths.

### Finding Description
`descriptor.proto` defines `FieldOptions.debug_redact` with the documented contract: "Indicate that the field value should not be printed out when using debug formats, e.g. when the field contains sensitive credentials." [1](#0-0) 

Generated messages implement `String()` via `protoimpl.X.MessageStringOf`, which routes straight to `prototext.MarshalOptions{Multiline: false}.Format(m)`: [2](#0-1) 

The actual field-value encoder, `marshalSingular` in `encoding/prototext/encode.go`, switches purely on `fd.Kind()` and never consults `fd.Options()`/`debug_redact` before writing string/bytes/enum values: [3](#0-2) 

`marshalMessage`/`marshalField` likewise iterate all populated fields via `order.RangeFields` with no redaction filter: [4](#0-3) 

A repo-wide search confirms `DebugRedact`/`debug_redact` only exists as a generated *accessor* (`GetDebugRedact()`) on the `descriptorpb` option messages themselves and in reflection metadata (`internal/genid`, `reflect/protoreflect/source_gen.go`); it is not read anywhere in `encoding/prototext`, `internal/msgfmt` (used by `testing/protocmp.Message.String()`), or `encoding/protojson`. The field is defined but structurally inert — the exact "flag exists, but is never honored on the failing/printing path" pattern from the reference CVE (Ansible ignoring `no_log` for failed tasks and printing sensitive data anyway).

### Impact Explanation
Any Go service using protobuf-go that (a) defines a field with `[debug_redact = true]` (e.g. `password`, `api_key`, `session_token`) per the schema's own stated intent, and (b) logs or prints the containing message for debugging/error reporting (a default, idiomatic Go pattern: `log.Printf("bad request: %v", req)`, `fmt.Errorf("invalid message %v", msg)`, error wrapping, `zap`/`logrus` `%v` formatting, panics that print `%+v`), will unconditionally leak the "redacted" field's plaintext value into logs, stderr, crash dumps, or error responses. This is a confidentiality violation on values the schema explicitly flagged as sensitive, reachable from any unprivileged/authenticated request path where the received message is logged — no malicious peer or attacker-controlled data is required; the vulnerability is triggered purely by normal operation with a trusted schema.

### Likelihood Explanation
High likelihood of the annotation being present and relied upon: `debug_redact` is a standard, documented protobuf field option specifically designed for this use case, and other protobuf implementations (C++, Java) honor it in their debug-string paths, so schema authors reasonably expect protobuf-go to do the same. Logging a request/response proto via `%v`/`String()` on error or for tracing is extremely common Go idiom, so the "sink" (accidental logging) is easily reached without any special attacker action — the analog to Ansible's failed-task log path is the ubiquitous Go pattern of logging on error/failure.

### Recommendation
In `encoding/prototext/encode.go` (`marshalSingular`/`marshalField`) and in `internal/msgfmt/format.go` (used by `protocmp.Message.String()`), check `fd.Options().(*descriptorpb.FieldOptions).GetDebugRedact()` (and the enum-value equivalent for enum fields) before emitting the field's value, and substitute a redaction marker (e.g. `"[REDACTED]"`) instead of the raw value when true. Apply consistently to `String()`, `Format()`, and any other "debug format" surfaces (including `testing/protocmp`) so the guarantee documented in `descriptor.proto` is actually enforced by the runtime.

### Proof of Concept
```go
// schema (trusted, defines the intent):
// message Login {
//   string username = 1;
//   string password = 2 [debug_redact = true];
// }

package main

import (
	"fmt"
	loginpb "example.com/loginpb"
)

func main() {
	m := &loginpb.Login{
		Username: "alice",
		Password: "S3cr3t-Cred",
	}

	// Common idiomatic Go logging on a failed/invalid request:
	fmt.Printf("rejecting request: %v\n", m)
	// Expected (per debug_redact contract): password should not appear.
	// Actual output: username:"alice" password:"S3cr3t-Cred"
}
```
Because `encoding/prototext.MarshalOptions.Format` (invoked by `String()`) never inspects `debug_redact`, the password appears in cleartext in the printed/logged output despite the schema explicitly marking it for redaction.

### Citations

**File:** types/descriptorpb/descriptor.pb.go (L2868-2870)
```go
	// Indicate that the field value should not be printed out when using debug
	// formats, e.g. when the field contains sensitive credentials.
	DebugRedact     *bool                           `protobuf:"varint,16,opt,name=debug_redact,json=debugRedact,def=0" json:"debug_redact,omitempty"`
```

**File:** internal/impl/api_export.go (L173-177)
```go
// MessageStringOf returns the message value as a string,
// which is the message serialized in the protobuf text format.
func (Export) MessageStringOf(m protoreflect.ProtoMessage) string {
	return prototext.MarshalOptions{Multiline: false}.Format(m)
}
```

**File:** encoding/prototext/encode.go (L161-212)
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
	if err != nil {
		return err
	}

	// Marshal unknown fields.
	if e.opts.EmitUnknown {
		e.marshalUnknown(m.GetUnknown())
	}

	return nil
}

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

**File:** encoding/prototext/encode.go (L216-265)
```go
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
