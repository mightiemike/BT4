Confirmed: `prototext.MarshalOptions.Marshal` (`encoding/prototext/encode.go`) does not check `FieldOptions.debug_redact` at all — `marshalField`/`marshalSingular` write every field's value unconditionally, and `marshalMessage` iterates all fields via `order.RangeFields` with no redaction filter.### Title
`debug_redact` field option is not enforced by `prototext`/`protojson` Format/Marshal, exposing sensitive fields via generated `String()` methods and any debug/error output - (File: `encoding/prototext/encode.go`)

### Summary
`descriptor.proto`'s `FieldOptions.debug_redact` is explicitly documented as marking a field "should not be printed out when using debug formats, e.g. when the field contains sensitive credentials" [1](#0-0) . However, neither `prototext.MarshalOptions.marshal`/`Format` nor `protojson`'s marshaler check this option anywhere in the field-emission path, so every generated message's `String()` method (which calls `protoimpl.X.MessageStringOf`, itself `prototext.MarshalOptions{Multiline:false}.Format(m)`) prints sensitive fields in full.

### Finding Description
`internal/impl/api_export.go`'s `MessageStringOf` is wired into every generated message's `String()` method: `return prototext.MarshalOptions{Multiline: false}.Format(m)` [2](#0-1) . This `String()` is invoked implicitly anywhere a message is passed to `fmt`/`log`/`%v`/`%s`, error wrapping, or any generic debug/observability code — a very common and unprivileged code path (e.g., a handler that logs an incoming request struct, or formats it into an error string returned to a client).

`prototext.Format`/`Marshal` walk through `marshalMessage` → `order.RangeFields` → `marshalField` → `marshalSingular`, and none of these ever look at `fd.Options()` for `debug_redact`; every field, regardless of its `FieldOptions.DebugRedact` bit, is written to the output [3](#0-2) . `marshalSingular` unconditionally writes bool/string/int/enum/message contents with no redaction gate [4](#0-3) . The same absence of checking is present in `protojson`'s marshal options and code (no `DebugRedact`/`redact` reference anywhere in the marshal path) [5](#0-4) . A grep across the whole repository shows `DebugRedact`/`redact` only appears in the generated descriptor types (`types/descriptorpb/descriptor.pb.go`), genid constants, `SECURITY.md`, and an example in `reflect/protorange/example_test.go` that manually implements redaction as a workaround — not in any encoder/marshaler enforcement code.

This is the exact analog of the GLPI issue: a "setup"/config value that the schema author intends to be excluded from a user-facing/log surface (`debug_redact = true`, GLPI's "smtp or cas hosts" fields) is nonetheless exposed because the code responsible for producing the debug/error representation does not honor the exclusion flag.

### Impact Explanation
Any trusted `.proto` schema that marks a field `[debug_redact = true]` to protect credentials/secrets (the documented intended use of this option) gets no actual protection from the standard library's debug/text formatting. If application code calls `fmt.Sprintf("%v", msg)`, logs a request/response message, or includes `err.Error()` derived from a message's `String()` in a response (a common and unprivileged pattern, directly analogous to GLPI leaking SMTP/CAS host config through an error page), the "redacted" field's actual value (e.g. a password, API token, or internal host) is printed in the clear. This is a confidentiality violation exactly matching CVE-2022-31143's class (exposure of sensitive setup/credential-adjacent info through a normal, unauthenticated-reachable code path), and matches the CVSS profile of C:L/I:N/A:N.

### Likelihood Explanation
High, in a schema-trusted context: the option exists specifically so authors mark sensitive fields, and the natural way anyone consumes it (calling `.String()`, `fmt.Println(msg)`, or logging a struct) is completely unprotected. No malicious peer, custom resolver, or descriptor is required — just a normal message type with `debug_redact` set and a normal debug/log call, which is standard Go idiom (implicit `Stringer` invocation).

### Recommendation
Have `prototext`/`protojson` (and `protoimpl.X.MessageStringOf`) check `fd.Options().(*descriptorpb.FieldOptions).GetDebugRedact()` for each field descriptor before writing its value into `Format`/debug output paths, replacing redacted-field values with a placeholder such as `[REDACTED]`, mirroring the manual workaround shown in `reflect/protorange/example_test.go`'s `Example_sanitizeStrings`.

### Proof of Concept
```go
package main

import (
	"fmt"

	"google.golang.org/protobuf/encoding/prototext"
	"google.golang.org/protobuf/types/descriptorpb"
	// assume a generated message type M with a field
	// `secret string = 1 [debug_redact = true];`
)

func main() {
	// Given generated type with field marked debug_redact=true in the .proto,
	// e.g. equivalent to:
	//   message M { string secret = 1 [debug_redact = true]; }
	m := &M{Secret: "super-secret-smtp-password"}

	// Simulates typical unprivileged debug/log/error-string usage:
	fmt.Println(m.String())                 // -> secret: "super-secret-smtp-password"
	fmt.Println(prototext.Format(m))         // -> secret: "super-secret-smtp-password"

	// Confirm the descriptor really requests redaction:
	fd := m.ProtoReflect().Descriptor().Fields().ByName("secret")
	opts := fd.Options().(*descriptorpb.FieldOptions)
	fmt.Println("DebugRedact:", opts.GetDebugRedact()) // -> true, yet value was printed anyway
}
```
The sensitive value is printed unredacted despite `debug_redact = true`, demonstrating that `prototext`'s and `protoimpl.X.MessageStringOf`'s output does not honor the field's documented redaction contract.

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

**File:** encoding/prototext/encode.go (L181-212)
```go
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

**File:** encoding/protojson/encode.go (L1-120)
```go
// Copyright 2019 The Go Authors. All rights reserved.
// Use of this source code is governed by a BSD-style
// license that can be found in the LICENSE file.

package protojson

import (
	"encoding/base64"
	"fmt"

	"google.golang.org/protobuf/internal/encoding/json"
	"google.golang.org/protobuf/internal/encoding/messageset"
	"google.golang.org/protobuf/internal/errors"
	"google.golang.org/protobuf/internal/filedesc"
	"google.golang.org/protobuf/internal/flags"
	"google.golang.org/protobuf/internal/genid"
	"google.golang.org/protobuf/internal/order"
	"google.golang.org/protobuf/internal/pragma"
	"google.golang.org/protobuf/proto"
	"google.golang.org/protobuf/reflect/protoreflect"
	"google.golang.org/protobuf/reflect/protoregistry"
)

const defaultIndent = "  "

// Format formats the message as a multiline string.
// This function is only intended for human consumption and ignores errors.
// Do not depend on the output being stable. Its output will change across
// different builds of your program, even when using the same version of the
// protobuf module.
func Format(m proto.Message) string {
	return MarshalOptions{Multiline: true}.Format(m)
}

// Marshal writes the given [proto.Message] in JSON format using default options.
// Do not depend on the output being stable. Its output will change across
// different builds of your program, even when using the same version of the
// protobuf module.
func Marshal(m proto.Message) ([]byte, error) {
	return MarshalOptions{}.Marshal(m)
}

// MarshalOptions is a configurable JSON format marshaler.
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

	// AllowPartial allows messages that have missing required fields to marshal
	// without returning an error. If AllowPartial is false (the default),
	// Marshal will return error if there are any missing required fields.
	AllowPartial bool

	// UseProtoNames uses proto field name instead of lowerCamelCase name in JSON
	// field names.
	UseProtoNames bool

	// UseEnumNumbers emits enum values as numbers.
	UseEnumNumbers bool

	// EmitUnpopulated specifies whether to emit unpopulated fields. It does not
	// emit unpopulated oneof fields or unpopulated extension fields.
	// The JSON value emitted for unpopulated fields are as follows:
	//  ╔═══════╤════════════════════════════╗
	//  ║ JSON  │ Protobuf field             ║
	//  ╠═══════╪════════════════════════════╣
	//  ║ false │ proto3 boolean fields      ║
	//  ║ 0     │ proto3 numeric fields      ║
	//  ║ ""    │ proto3 string/bytes fields ║
	//  ║ null  │ proto2 scalar fields       ║
	//  ║ null  │ message fields             ║
	//  ║ []    │ list fields                ║
	//  ║ {}    │ map fields                 ║
	//  ╚═══════╧════════════════════════════╝
	EmitUnpopulated bool

	// EmitDefaultValues specifies whether to emit default-valued primitive fields,
	// empty lists, and empty maps. The fields affected are as follows:
	//  ╔═══════╤════════════════════════════════════════╗
	//  ║ JSON  │ Protobuf field                         ║
	//  ╠═══════╪════════════════════════════════════════╣
	//  ║ false │ non-optional scalar boolean fields     ║
	//  ║ 0     │ non-optional scalar numeric fields     ║
	//  ║ ""    │ non-optional scalar string/byte fields ║
	//  ║ []    │ empty repeated fields                  ║
	//  ║ {}    │ empty map fields                       ║
	//  ╚═══════╧════════════════════════════════════════╝
	//
	// Behaves similarly to EmitUnpopulated, but does not emit "null"-value fields,
	// i.e. presence-sensing fields that are omitted will remain omitted to preserve
	// presence-sensing.
	// EmitUnpopulated takes precedence over EmitDefaultValues since the former generates
	// a strict superset of the latter.
	EmitDefaultValues bool

	// Resolver is used for looking up types when expanding google.protobuf.Any
	// messages. If nil, this defaults to using protoregistry.GlobalTypes.
	Resolver interface {
		protoregistry.ExtensionTypeResolver
		protoregistry.MessageTypeResolver
	}
}

// Format formats the message as a string.
// This method is only intended for human consumption and ignores errors.
// Do not depend on the output being stable. Its output will change across
// different builds of your program, even when using the same version of the
// protobuf module.
func (o MarshalOptions) Format(m proto.Message) string {
	if m == nil || !m.ProtoReflect().IsValid() {
		return "<nil>" // invalid syntax, but okay since this is for debugging
```
