### Title
Unsanitized `Any.type_url` Injected into ProtoText Field-Name Syntax Enables Textproto Structure Injection - ([File: encoding/prototext/encode.go])

### Summary
`encoding/prototext`'s `encoder.marshalAny` builds the textproto "expanded Any" field name by directly concatenating the attacker-influenced `type_url` string value into the delimiter syntax `"[" + typeURL + "]"` and writing it with `WriteName`, which performs **no escaping**. Unlike scalar string values (written via `WriteString`, which escapes `"`, `\`, control characters, etc. — see `internal/encoding/text/encode.go` `appendString`), the type URL is treated as a trusted literal even though it is ordinary message data that can contain arbitrary bytes. [1](#0-0) [2](#0-1) 

### Finding Description
`google.protobuf.Any.type_url` is a plain `string` field with no charset restriction at the protobuf schema level; its only structural requirement is that it end with `/` + a resolvable type name, which `FindMessageByURL`-style resolvers determine purely from the last `/`. [3](#0-2) 

The prototext marshaler's `marshalAny` resolves the message type using only the substring after the final `/`, then writes everything *before* the closing `]` — including the attacker-controlled prefix — verbatim, with `WriteName`:
```go
typeURL := any.Get(fdType).String()
mt, err := e.opts.Resolver.FindMessageByURL(typeURL)
...
e.WriteName("[" + typeURL + "]")
``` [4](#0-3) 

`WriteName` simply appends the raw bytes of its argument followed by `:` with **zero escaping**:
```go
func (e *Encoder) WriteName(s string) {
	e.prepareNext(name)
	e.out = append(e.out, s...)
	e.out = append(e.out, ':')
}
``` [5](#0-4) 

This is inconsistent with the documented invariant on `Any.type_url` itself, which states the text-format content "must consist only of alphanumeric characters, percent-encoded escapes, and characters in the following set... `/-.~_!$&()*+,;=`" — a restriction that is only enforced on the **decode/parse side** (`parseTypeName` in `internal/encoding/text/decode.go`, which strictly validates the charset), but never validated or escaped on the **encode side**. [6](#0-5) [7](#0-6) 

By contrast, `protojson`'s equivalent Any handling writes the same `type_url` through `e.WriteString(typeURL)`, which does go through JSON-string escaping — showing this is a genuine asymmetry/oversight specific to the prototext encoder, not an intentional design choice. [8](#0-7) 

Because `type_url` is ordinary attacker-reachable data (set via `proto.Unmarshal` of untrusted binary input, or via `protojson.Unmarshal` of untrusted JSON, both "default" wire parsers per the threat model), an attacker who controls the contents of an `Any` message that later gets serialized with `prototext.Marshal`/`MarshalOptions.Marshal` can inject `]`, newlines, `#` (comment), and field-separator characters into the emitted textproto stream, breaking out of the intended `[type/name]{ ... }` structure and injecting extra, attacker-chosen field/value pairs (or truncating/corrupting the remainder of the message via a `#` comment) into output that downstream consumers may re-parse with `prototext.Unmarshal` or otherwise treat as a well-formed, trusted structural document.

### Impact Explanation
Any Go service that (a) accepts untrusted `Any`-bearing protobuf messages via default binary or JSON unmarshaling and (b) re-serializes those messages to textproto (e.g., for logging, audit trails, config generation, or transport to another component that parses textproto) is exposed to structural injection of extra fields into that generated document — a direct analog of the Forwarded-header field-injection bug: an unsanitized, attacker-supplied string is concatenated into a delimiter-bearing structured format without escaping the format's own control characters (`]`, `\n`, `#`), letting the attacker inject data recognized as separate structural elements by downstream parsers. This maps to CWE-20 (Improper Input Validation) / CWE-74 (Injection), matching the analog report's classification.

### Likelihood Explanation
Reaching the vulnerable path requires only: (1) an untrusted message containing an `Any` field with a `type_url` suffix that resolves to a registered/known message type (a normal, expected condition, not requiring a custom resolver or malicious descriptor), and (2) that message being marshaled with `prototext.Marshal`/`MarshalOptions.Marshal`. No privileged caller, custom resolver, or attacker-supplied descriptor is needed — the schema and resolver are both trusted/standard. The only conditional factor is whether a given service exposes a textproto-producing path over untrusted `Any` data, which is a real (if less universal than JSON/binary) usage pattern in the ecosystem (debug endpoints, config/log tooling built on `prototext`).

### Recommendation
In `encoding/prototext/encode.go`'s `marshalAny`, validate/escape `typeURL` before embedding it in the `[...]` field-name syntax — e.g., reject or percent-encode characters outside the charset documented for `Any.type_url` in text format (only allow `/-.~_!$&()*+,;=` plus alphanumerics and valid percent-encoding), mirroring the strict validation already performed by `parseTypeName` on the decode side, or otherwise escape `]`, `\n`, `#`, and other structural characters before writing.

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
	inner := &wrapperspb.StringValue{Value: "hello"}
	b, _ := proto.Marshal(inner)

	// Attacker controls type_url content up to the final "/"; only the
	// suffix after the last "/" needs to resolve to a known type.
	// Everything else -- including "]" and newlines -- is unescaped by
	// prototext's marshalAny.
	malicious := &anypb.Any{
		TypeUrl: "x]\ninjected_field: \"PWNED\"\nlegit: \"y\" #/google.protobuf.StringValue",
		Value:   b,
	}

	out, err := prototext.MarshalOptions{Multiline: true}.Marshal(malicious)
	fmt.Printf("err=%v\noutput=\n%s\n", err, out)
}
```
The resulting textproto output contains an attacker-controlled top-level `injected_field: "PWNED"` entry and a spoofed `legit: "y"` value that were never part of the schema-defined message content, followed by a `#` comment that consumes the remainder of that line — demonstrating that the encoder allows arbitrary structural injection into its own serialization format via an unsanitized data field.

### Citations

**File:** encoding/prototext/encode.go (L346-379)
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
	if err != nil {
		e.Reset(pos)
		return false
	}
	return true
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

**File:** types/dynamicpb/types.go (L115-127)
```go
// FindMessageByURL looks up a message by a URL identifier.
// See documentation on google.protobuf.Any.type_url for the URL format.
//
// This returns (nil, [protoregistry.NotFound]) if not found.
func (t *Types) FindMessageByURL(url string) (protoreflect.MessageType, error) {
	// This function is similar to FindMessageByName but
	// truncates anything before and including '/' in the URL.
	message := protoreflect.FullName(url)
	if i := strings.LastIndexByte(url, '/'); i >= 0 {
		message = message[i+len("/"):]
	}
	return t.FindMessageByName(message)
}
```

**File:** types/known/anypb/any.pb.go (L177-183)
```go
	// All type URL strings must be legal URI references with the additional
	// restriction (for the text format) that the content of the reference
	// must consist only of alphanumeric characters, percent-encoded escapes, and
	// characters in the following set (not including the outer backticks):
	// `/-.~_!$&()*+,;=`. Despite our allowing percent encodings, implementations
	// should not unescape them to prevent confusion with existing parsers. For
	// example, `type.googleapis.com%2FFoo` should be rejected.
```

**File:** internal/encoding/text/decode.go (L427-510)
```go
// parseTypeName parses an Any type URL or an extension field name. The name is
// enclosed in [ and ] characters. We allow almost arbitrary type URL prefixes,
// closely following the text-format spec [1,2]. We implement "ExtensionName |
// AnyName" as follows (with some exceptions for backwards compatibility):
//
// char      = [-_a-zA-Z0-9]
// url_char  = char | [.~!$&'()*+,;=] | "%", hex, hex
//
// Ident         = char, { char }
// TypeName      = Ident, { ".", Ident } ;
// UrlPrefix     = url_char, { url_char | "/" } ;
// ExtensionName = "[", TypeName, "]" ;
// AnyName       = "[", UrlPrefix, "/", TypeName, "]" ;
//
// Additionally, we allow arbitrary whitespace and comments between [ and ].
//
// [1] https://protobuf.dev/reference/protobuf/textformat-spec/#characters
// [2] https://protobuf.dev/reference/protobuf/textformat-spec/#field-names
func (d *Decoder) parseTypeName() (Token, error) {
	// Use alias s to advance first in order to use d.in for error handling.
	// Caller already checks for [ as first character (d.in[0] == '[').
	s := consume(d.in[1:], 0)
	if len(s) == 0 {
		return Token{}, ErrUnexpectedEOF
	}

	// Collect everything between [ and ] in name.
	var name []byte
	var closed bool
	for len(s) > 0 && !closed {
		switch {
		case s[0] == ']':
			s = s[1:]
			closed = true

		case s[0] == '/' || isTypeNameChar(s[0]) || isUrlExtraChar(s[0]):
			name = append(name, s[0])
			s = consume(s[1:], 0)

		// URL percent-encoded chars
		case s[0] == '%':
			if len(s) < 3 || !isHexChar(s[1]) || !isHexChar(s[2]) {
				return Token{}, d.parseTypeNameError(s, 3)
			}
			name = append(name, s[0], s[1], s[2])
			s = consume(s[3:], 0)

		default:
			return Token{}, d.parseTypeNameError(s, 1)
		}
	}

	if !closed {
		return Token{}, ErrUnexpectedEOF
	}

	// Split collected name on last '/' into urlPrefix and typeName (if '/' is
	// present).
	typeName := name
	if i := bytes.LastIndexByte(name, '/'); i != -1 {
		urlPrefix := name[:i]
		typeName = name[i+1:]

		// urlPrefix may be empty (for backwards compatibility).
		// If non-empty, it must not start with '/'.
		if len(urlPrefix) > 0 && urlPrefix[0] == '/' {
			return Token{}, d.parseTypeNameError(s, 0)
		}
	}

	// typeName must not be empty (note: "" splits to [""]) and all identifier
	// parts must not be empty.
	for _, ident := range bytes.Split(typeName, []byte{'.'}) {
		if len(ident) == 0 {
			return Token{}, d.parseTypeNameError(s, 0)
		}
	}

	// typeName must not contain any percent-encoded or special URL chars.
	for _, b := range typeName {
		if b == '%' || (b != '.' && isUrlExtraChar(b)) {
			return Token{}, d.parseTypeNameError(s, 0)
		}
	}
```

**File:** encoding/protojson/well_known_types.go (L151-155)
```go
		// Marshal out @type field.
		e.WriteName("@type")
		if err := e.WriteString(typeURL); err != nil {
			return err
		}
```
