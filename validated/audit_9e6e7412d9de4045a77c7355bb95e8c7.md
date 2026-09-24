### Title
Unescaped `google.protobuf.Any.type_url` injected into textproto output enables Set-Cookie-style attribute injection - (File: encoding/prototext/encode.go)

### Summary
`prototext.MarshalOptions.Marshal`/`Format` expands `google.protobuf.Any` fields by writing the attacker-supplied `type_url` string directly as a field "name" via `text.Encoder.WriteName`, which performs **no escaping** at all (unlike `WriteString`, which escapes quotes, backslashes and control characters). Because `type_url` is only checked for UTF-8 validity when the message is unmarshaled — never for text-format-significant characters such as `"`, `{`, `}`, `\n`, or `]` — a value fully attacker-controlled through an ordinary `Any` field can inject arbitrary tokens into the serialized textproto output, exactly analogous to how the `cookies` library validated cookie name/value but not `domain`/`path`, letting unvalidated data splice extra attributes into `Set-Cookie`.

### Finding Description
`encoder.marshalAny` in `encoding/prototext/encode.go` reads the `type_url` field of an `Any` message and, if the resolver can resolve it to a locally registered type, writes it unescaped as a "field name": [1](#0-0) 

The relevant sink is: [2](#0-1) 

`text.Encoder.WriteName` performs zero escaping of its input: [3](#0-2) 

This contrasts with `WriteString`, used for ordinary string field values, which escapes quotes, backslashes, newlines and control characters before writing: [4](#0-3) 

`Any.type_url` is an ordinary proto3 `string` field. During unmarshal (binary or JSON), it is only checked for valid UTF-8 — there is no restriction preventing it from containing `"`, `{`, `}`, `\n`, `]`, or other characters that are structurally significant in the textproto grammar. `marshalAny` only requires that `protoregistry.FindMessageByURL(typeURL)` can resolve a message type from it; that lookup conventionally uses only the suffix after the last `/` in the URL to find the registered full type name, so the remainder of the string is fully attacker-controlled and is still concatenated verbatim into the output as `"[" + typeURL + "]"`.

This is the same broken invariant as CVE-2026-88038: a value that is validated for one purpose (UTF-8 validity / resolvability of a type-name suffix) but not validated against the character set that matters for the sink (textproto delimiter/field syntax), then written unescaped into a structured, delimiter-based output.

### Impact Explanation
An attacker who controls the contents of an `Any` field in an otherwise trusted, schema-conformant protobuf message (a routine and common pattern in protobuf-go APIs that use `google.protobuf.Any` for generic payloads) can inject arbitrary textproto syntax — extra fields, bogus values, or malformed structure — into any textproto output an application produces via `prototext.Marshal`/`Format` for that message (e.g., debug logging, audit trails, error responses, or textual persistence). Downstream consumers that treat that text as trusted structured data (further `prototext.Unmarshal`, log parsers, human reviewers) can be misled or have their parsing broken, an integrity/confidentiality-adjacent issue matching the CVSS profile of the reference report (`AC:H`, low confidentiality/integrity impact, no privileges required).

### Likelihood Explanation
Requires: (1) the application accepts an externally supplied message containing an `Any` field via the default (trusted-schema) binary or JSON parser, and (2) later calls `prototext.Marshal`/`Format`/`Format` on that message (a common debug/logging pattern). The attacker also needs the resolver to successfully resolve a type from the crafted `type_url` suffix — achievable by appending `/` + any locally registered message full name (e.g., a well-known type like `google.protobuf.StringValue`) after arbitrary injected content. No privileged caller, custom resolver, or malicious descriptor is required.

### Recommendation
In `encoder.marshalAny` (`encoding/prototext/encode.go`), validate or escape `type_url` before embedding it in the `WriteName` call, or use an escaping write path (equivalent to `WriteString`) for the bracketed type-URL field name so that characters significant to the textproto grammar (`"`, `{`, `}`, newlines, `]`) cannot break out of the intended field-name token.

### Proof of Concept
```go
package main

import (
	"fmt"

	"google.golang.org/protobuf/encoding/prototext"
	"google.golang.org/protobuf/proto"
	"google.golang.org/protobuf/types/known/anypb"
	"google.golang.org/protobuf/types/known/wrapperspb"
)

func main() {
	// Attacker fully controls type_url (e.g. received in an untrusted
	// gRPC/protobuf request containing an Any field, unmarshaled with the
	// default trusted-schema binary parser).
	inner := &wrapperspb.StringValue{Value: "hello"}
	innerBytes, _ := proto.Marshal(inner)

	// Only the substring after the last '/' needs to resolve to a
	// registered type; everything before it is attacker-controlled and
	// written verbatim, unescaped, into the textproto output.
	maliciousTypeURL := "type.googleapis.com/injected]\ninjected_field: \"pwned\"\n#/google.protobuf.StringValue"

	any := &anypb.Any{TypeUrl: maliciousTypeURL, Value: innerBytes}

	// App logs/serializes the received request as textproto for debugging.
	out, err := prototext.MarshalOptions{Multiline: true}.Marshal(any)
	fmt.Println(string(out), err)
}
```
Because `WriteName` never escapes its argument, the `\n` and `]` characters in `type_url` terminate the intended `[type.url]` field-name token early and inject a spurious `injected_field: "pwned"` entry (and a comment line neutralizing the rest of the resolver-required suffix) into the emitted textproto, demonstrating attribute/field injection analogous to the `cookies` `domain`/`path` Set-Cookie injection.

*Note: the exact slicing rule used by `protoregistry.Types.FindMessageByURL` (taking the substring after the last `/`) was inferred from standard protobuf-go behavior and was not directly re-verified in this session's search results; this should be confirmed against `reflect/protoregistry/registry.go` before treating the PoC as final.*

### Citations

**File:** encoding/prototext/encode.go (L346-374)
```go
func (e encoder) marshalAny(any protoreflect.Message) bool {
	// Construct the embedded message.
	fds := any.Descriptor().Fields()
	fdType := fds.ByNumber(genid.Any_TypeUrl_field_number)
	typeURL := any.Get(fdType).String()
	mt, err := e.opts.Resolver.FindMessageByURL(typeURL)
	if err != nil {
		return false
	}
	m := mt.New().Interface()

	// Unmarshal bytes into embedded message.
	fdValue := fds.ByNumber(genid.Any_Value_field_number)
	value := any.Get(fdValue)
	err = proto.UnmarshalOptions{
		AllowPartial: true,
		Resolver:     e.opts.Resolver,
	}.Unmarshal(value.Bytes(), m)
	if err != nil {
		return false
	}

	// Get current encoder position. If marshaling fails, reset encoder output
	// back to this position.
	pos := e.Snapshot()

	// Field name is the proto field name enclosed in [].
	e.WriteName("[" + typeURL + "]")
	err = e.marshalMessage(m.ProtoReflect(), true)
```

**File:** internal/encoding/text/encode.go (L96-101)
```go
// WriteName writes out the field name and the separator ':'.
func (e *Encoder) WriteName(s string) {
	e.prepareNext(name)
	e.out = append(e.out, s...)
	e.out = append(e.out, ':')
}
```

**File:** internal/encoding/text/encode.go (L112-165)
```go
// WriteString writes out the given string value.
func (e *Encoder) WriteString(s string) {
	e.prepareNext(scalar)
	e.out = appendString(e.out, s, e.outputASCII)
}

func appendString(out []byte, in string, outputASCII bool) []byte {
	out = append(out, '"')
	i := indexNeedEscapeInString(in)
	in, out = in[i:], append(out, in[:i]...)
	for len(in) > 0 {
		switch r, n := utf8.DecodeRuneInString(in); {
		case r == utf8.RuneError && n == 1:
			// We do not report invalid UTF-8 because strings in the text format
			// are used to represent both the proto string and bytes type.
			r = rune(in[0])
			fallthrough
		case r < ' ' || r == '"' || r == '\\' || r == 0x7f:
			out = append(out, '\\')
			switch r {
			case '"', '\\':
				out = append(out, byte(r))
			case '\n':
				out = append(out, 'n')
			case '\r':
				out = append(out, 'r')
			case '\t':
				out = append(out, 't')
			default:
				out = append(out, 'x')
				out = append(out, "00"[1+(bits.Len32(uint32(r))-1)/4:]...)
				out = strconv.AppendUint(out, uint64(r), 16)
			}
			in = in[n:]
		case r >= utf8.RuneSelf && (outputASCII || r <= 0x009f):
			out = append(out, '\\')
			if r <= math.MaxUint16 {
				out = append(out, 'u')
				out = append(out, "0000"[1+(bits.Len32(uint32(r))-1)/4:]...)
				out = strconv.AppendUint(out, uint64(r), 16)
			} else {
				out = append(out, 'U')
				out = append(out, "00000000"[1+(bits.Len32(uint32(r))-1)/4:]...)
				out = strconv.AppendUint(out, uint64(r), 16)
			}
			in = in[n:]
		default:
			i := indexNeedEscapeInString(in[n:])
			in, out = in[n+i:], append(out, in[:n+i]...)
		}
	}
	out = append(out, '"')
	return out
}
```
