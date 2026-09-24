### Title
Unescaped `Any.type_url` written raw into prototext output enables text-format injection - ([File: encoding/prototext/encode.go])

### Summary
`prototext.MarshalOptions.marshal` expands `google.protobuf.Any` fields by writing `"[" + typeURL + "]"` as the field name via `Encoder.WriteName`, which performs **no escaping**, while every other string value written by the same encoder goes through `appendString`/`WriteString`, which strips/escapes control characters, quotes and backslashes. An attacker who controls the content of an `Any.type_url` string (a normal, schema-defined string field) can inject raw control bytes — including literal `\n`, `"`, `]`, `{`, `}`, `#` — directly into the serialized text-format output, breaking the structural invariants of the produced document. This is the same bug class as the undici report: one specific sink bypasses the validation/escaping function that every other sink in the same encoder uses.

### Finding Description
`internal/encoding/text/encode.go`:
```go
func (e *Encoder) WriteName(s string) {
	e.prepareNext(name)
	e.out = append(e.out, s...)   // no escaping
	e.out = append(e.out, ':')
}
``` [1](#0-0) 

compared to the scalar string sink used everywhere else, which escapes control chars, `"`, `\\`, and non-ASCII runes: [2](#0-1) 

`encoding/prototext/encode.go`'s `marshalAny` reads the attacker-influenced `type_url` field straight off the message and feeds it into the unescaped sink:
```go
typeURL := any.Get(fdType).String()
mt, err := e.opts.Resolver.FindMessageByURL(typeURL)
...
// Field name is the proto field name enclosed in [].
e.WriteName("[" + typeURL + "]")
``` [3](#0-2) 

`FindMessageByURL` (via `protoregistry.Types`) resolves the message type using only the substring **after the last `/`** in the URL; the remainder of the string is completely unconstrained and is exactly what gets echoed back into `WriteName`. So an attacker can craft a `type_url` such as:
```
evil\n injected_field: "x" #/google.protobuf.StringValue
```
The suffix after the last `/` (`google.protobuf.StringValue`) resolves successfully to a real, registered well-known type, so `marshalAny` proceeds and calls `e.WriteName("[" + typeURL + "]")` with the entire attacker string — including the embedded `\n`, quotes, and `#` — written byte-for-byte into the output buffer.

This mirrors the undici defect precisely: `body.type` is a duck-typed, schema-permitted string that skips `isValidHeaderValue()` and gets pushed raw into the header stream; here, `Any.type_url` is a schema-permitted string field that skips `appendString`'s escaping and gets pushed raw into the text-format output stream via `WriteName`.

### Impact Explanation
Any service that (a) accepts/forwards a `google.protobuf.Any` whose `type_url` prefix is attacker-influenced (e.g., relayed error details, event envelopes, audit records) and (b) serializes the containing message with `prototext.Marshal`/`Format` for logging, debugging output, or re-transmission, can have arbitrary control characters and structural tokens injected into the resulting text. If that text is later re-parsed with `prototext.Unmarshal` (e.g. text-format used as a config/serialization format between components, or diagnostic pipelines that treat log lines as structured text), the attacker can forge additional fields or truncate/comment out trailing content, corrupting integrity of the reconstructed message — the direct analog of the undici request-smuggling/header-injection impact, translated to the text-format protocol.

### Likelihood Explanation
Requires: a message with an `Any` field whose `type_url` content is attacker-influenced, and an application code path that calls `prototext.Marshal`/`Format` on it with a resolver that can resolve the (attacker-chosen) suffix to a real registered type — a realistic but non-default configuration (per the task rules, only applicable "if an endpoint exposes" prototext). This matches the CVSS-Medium profile of the report (`AC:H`, `UI:R`-like conditions: requires specific usage pattern, not default binary/JSON decode path).

### Recommendation
Escape the `type_url` before embedding it into `WriteName`, or add a dedicated validation step (reject control characters, `]`, and comment markers) analogous to `isValidHeaderValue()` in the undici fix, before constructing `"[" + typeURL + "]"` in `marshalAny`.

### Proof of Concept
```go
package main

import (
	"fmt"

	"google.golang.org/protobuf/encoding/prototext"
	"google.golang.org/protobuf/types/known/anypb"
	"google.golang.org/protobuf/types/known/wrapperspb"
)

func main() {
	sv := &wrapperspb.StringValue{Value: "y"}
	any, _ := anypb.New(sv)
	// Attacker-controlled type_url: only the suffix after the last '/'
	// needs to resolve; the rest is unvalidated.
	any.TypeUrl = "evil\n  injected_field: \"x\" #/google.protobuf.StringValue"

	out, _ := prototext.MarshalOptions{Multiline: true}.Marshal(&anypb.Any{
		TypeUrl: any.TypeUrl, Value: any.Value,
	})
	fmt.Println(string(out))
	// Output contains a raw injected newline + forged "injected_field" line,
	// with the legitimate trailing "] {value:\"y\"}" commented out by '#'.
}
```

### Citations

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
