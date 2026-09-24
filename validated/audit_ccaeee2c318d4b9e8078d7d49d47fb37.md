Confirmed: `MarshalOptions` in `encoding/prototext/encode.go` has no field for redaction, and `FieldOptions.DebugRedact` / `EnumValueOptions.DebugRedact` are never referenced anywhere under `encoding/` — only in the generated descriptor code, `internal/genid`, and test/doc files.

### Title
Debug-format serializers (`prototext`/`String()`) ignore `debug_redact` field option, leaking sensitive fields into logs - ([File: encoding/prototext/encode.go])

### Summary
`descriptor.proto` defines `FieldOptions.debug_redact` (and `EnumValueOptions.debug_redact`) specifically so that schema authors can mark fields (e.g., credentials, tokens, PII) as "must not be printed in debug output" [1](#0-0) . Despite this documented contract, the generated `String()` method that every protobuf-go message exposes — used implicitly by `fmt.Printf("%v", msg)`, `log.Printf`, and virtually every Go logging/error-wrapping call — routes through `protoimpl.X.MessageStringOf`, which calls `prototext.MarshalOptions{Multiline: false}.Format(m)` with no redaction logic at all [2](#0-1) . The `prototext` encoder's `MarshalOptions` struct has no field or code path that consults `FieldOptions.GetDebugRedact()` before emitting a field's value [3](#0-2) .

### Finding Description
- Source: a message whose `.proto` schema marks a field with `[debug_redact = true]` (trusted-schema precondition, e.g. an internal `Credentials` or `AuthToken` message).
- Parser/sink path: any code calling `msg.String()` (auto-invoked by `%v`/`%s` formatting verbs, `errors.New(fmt.Sprintf(...))`, structured loggers that stringify arguments) or explicitly calling `prototext.Format(msg)` / `prototext.MarshalOptions{}.Format(msg)`.
- Failing check: `Export.MessageStringOf` unconditionally builds compact text output via `prototext.MarshalOptions{Multiline: false}.Format(m)` [4](#0-3) , and the `Format`/`Marshal` implementation in `encoding/prototext/encode.go` has no logic referencing `debug_redact` anywhere in the marshal path [5](#0-4) . A grep across `encoding/` confirms zero references to `DebugRedact`/`Redact`.
- The field descriptor comment itself documents the intended contract — "Indicate that the field value should not be printed out when using debug formats, e.g. when the field contains sensitive credentials" [6](#0-5)  — but the library never enforces it for either `String()` or `prototext`.

### Impact Explanation
Any service that logs protobuf messages (via `%v`, error wrapping, or debug/text-format serialization) will emit the full plaintext value of fields the schema author explicitly flagged as sensitive, directly matching the CWE-532 bug class from the NiFi report (sensitive property values printed into logs despite being intended to be protected). This is a confidentiality issue reachable via completely ordinary, unprivileged application code paths (any `log.Printf("%v", req)` on an incoming request message containing a `debug_redact`-marked field).

### Likelihood Explanation
High likelihood of occurrence in practice: `String()` is the default `Stringer` implementation for every generated message, and Go idioms (`%v` verb, error formatting, `zap`/`logrus` reflection-based encoders) routinely invoke it without the developer realizing text-format serialization is happening. Any schema using `debug_redact` (a widely known pattern for marking secrets) but relying on protobuf-go's advertised protection would silently leak the marked fields.

### Recommendation
Implement `debug_redact` enforcement in `encoding/prototext` (and ideally `encoding/protojson`'s debug helpers) by checking `fd.Options().(*descriptorpb.FieldOptions).GetDebugRedact()` (and the enum-value equivalent) during field emission in the marshal encoder, replacing redacted values with a placeholder (e.g. `"[REDACTED]"`) instead of the literal value, matching the behavior of other protobuf implementations that do honor this option for debug-only output paths (`String()`/`Format()`).

### Proof of Concept
```go
package main

import (
	"fmt"

	"google.golang.org/protobuf/types/known/wrapperspb"
	// hypothetical generated message with a field
	// annotated: string secret = 1 [debug_redact = true];
	secretpb "example.com/secretpb"
)

func main() {
	m := &secretpb.Credentials{
		Username: "alice",
		Secret:   "super-secret-api-key", // marked debug_redact=true in .proto
	}
	// Ordinary logging call — invokes generated String() -> prototext.Format
	fmt.Printf("handling request: %v\n", m)
	// Output includes: secret:"super-secret-api-key"
	// despite the field being marked debug_redact = true in the schema.
}
```
This demonstrates that `debug_redact` provides no actual protection in protobuf-go: the plaintext secret is written wherever `%v`/`String()`/`prototext.Format` is used, which is exactly the kind of implicit debug-logging sink flagged in the NiFi CVE-2020-9486 report.

### Citations

**File:** types/descriptorpb/descriptor.pb.go (L2868-2871)
```go
	// Indicate that the field value should not be printed out when using debug
	// formats, e.g. when the field contains sensitive credentials.
	DebugRedact     *bool                           `protobuf:"varint,16,opt,name=debug_redact,json=debugRedact,def=0" json:"debug_redact,omitempty"`
	Retention       *FieldOptions_OptionRetention   `protobuf:"varint,17,opt,name=retention,enum=google.protobuf.FieldOptions_OptionRetention" json:"retention,omitempty"`
```

**File:** internal/impl/api_export.go (L173-177)
```go
// MessageStringOf returns the message value as a string,
// which is the message serialized in the protobuf text format.
func (Export) MessageStringOf(m protoreflect.ProtoMessage) string {
	return prototext.MarshalOptions{Multiline: false}.Format(m)
}
```

**File:** encoding/prototext/encode.go (L46-85)
```go
type MarshalOptions struct {
	pragma.NoUnkeyedLiterals

	// Multiline specifies whether the marshaler should format the output in
	// indented-form with every textual element on a new line.
	// If Indent is an empty string, then an arbitrary indent is chosen.
	Multiline bool

	// Indent specifies the set of indentation characters to use in a multiline
	// formatted output such that every entry is preceded by Indent and
	// terminated by a newline. If non-empty, then Multiline is treated as true.
	// Indent can only be composed of space or tab characters.
	Indent string

	// EmitASCII specifies whether to format strings and bytes as ASCII only
	// as opposed to using UTF-8 encoding when possible.
	EmitASCII bool

	// allowInvalidUTF8 specifies whether to permit the encoding of strings
	// with invalid UTF-8. This is unexported as it is intended to only
	// be specified by the Format method.
	allowInvalidUTF8 bool

	// AllowPartial allows messages that have missing required fields to marshal
	// without returning an error. If AllowPartial is false (the default),
	// Marshal will return error if there are any missing required fields.
	AllowPartial bool

	// EmitUnknown specifies whether to emit unknown fields in the output.
	// If specified, the unmarshaler may be unable to parse the output.
	// The default is to exclude unknown fields.
	EmitUnknown bool

	// Resolver is used for looking up types when expanding google.protobuf.Any
	// messages. If nil, this defaults to using protoregistry.GlobalTypes.
	Resolver interface {
		protoregistry.ExtensionTypeResolver
		protoregistry.MessageTypeResolver
	}
}
```

**File:** encoding/prototext/encode.go (L87-109)
```go
// Format formats the message as a string.
// This method is only intended for human consumption and ignores errors.
// Do not depend on the output being stable. Its output will change across
// different builds of your program, even when using the same version of the
// protobuf module.
func (o MarshalOptions) Format(m proto.Message) string {
	if m == nil || !m.ProtoReflect().IsValid() {
		return "<nil>" // invalid syntax, but okay since this is for debugging
	}
	o.allowInvalidUTF8 = true
	o.AllowPartial = true
	o.EmitUnknown = true
	b, _ := o.Marshal(m)
	return string(b)
}

// Marshal writes the given [proto.Message] in textproto format using options in
// MarshalOptions object. Do not depend on the output being stable. Its output
// will change across different builds of your program, even when using the
// same version of the protobuf module.
func (o MarshalOptions) Marshal(m proto.Message) ([]byte, error) {
	return o.marshal(nil, m)
}
```
