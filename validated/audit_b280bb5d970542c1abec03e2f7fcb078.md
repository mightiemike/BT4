### Title
Unescaped `Any.type_url` written raw into ProtoText output enables terminal/control-character injection - ([File: encoding/prototext/encode.go])

### Summary
`prototext.MarshalOptions.Marshal`/`Format` expand `google.protobuf.Any` fields by writing the field name as `"[" + typeURL + "]"` via `Encoder.WriteName`, which appends the string to the output buffer with **no escaping**, unlike every other string-valued field in the same encoder, which goes through `WriteString`/`appendString` and is properly escaped (control characters, quotes, backslashes, and non-ASCII code points are escaped). Since `type_url` is attacker-influenced application data (not fixed schema data like a field name), this is an analog of the Helm CVE-2021-21303 pattern: a value that is expected to look like a well-formed identifier (there, a SemVer version; here, a `type.googleapis.com/full.type.Name` URL) is passed to a text/terminal-consuming sink without validation or escaping, letting a crafted value carry unsanitized control bytes (including ANSI escape sequences) straight into the rendered output.

### Finding Description
In `encoding/prototext/encode.go`, `marshalAny` handles Any expansion: [1](#0-0) 

The `typeURL` is read directly out of the message with `any.Get(fdType).String()` (no validation of its contents), then used to resolve a message type with `e.opts.Resolver.FindMessageByURL(typeURL)`. If resolution succeeds, the encoder calls:

```go
e.WriteName("[" + typeURL + "]")
```

`Encoder.WriteName` in `internal/encoding/text/encode.go` performs no escaping at all: [2](#0-1) 

Compare this to the normal string-field path, `Encoder.WriteString` → `appendString`, which escapes control characters (`r < ' '`), quotes, backslashes, DEL (`0x7f`), and non-ASCII runes as `\xNN`/`\uNNNN`/`\UNNNNNNNN`: [3](#0-2) 

Similarly, `marshalSingular` treats an ordinary `string` field through `WriteString`, applying the same escaping/UTF-8 checks: [4](#0-3) 

`type_url` is a normal `string` field of the well-known `Any` message; if it were marshaled as a plain field (resolution failure path) it would be escaped like any other string. But when resolution succeeds, the encoder bypasses that safe path and writes the raw value via `WriteName`.

Resolution via `FindMessageByURL` only requires the substring **after the last `/`** to exactly match a registered full message name (e.g., `google.protobuf.Empty`); everything before that last `/` is attacker-controlled and unconstrained. This lets an attacker embed arbitrary bytes — including ASCII control characters and full ANSI/VT100 escape sequences — in the prefix while still satisfying resolution, e.g.:

```
type_url = "https://type.googleapis.com/\x1b[2J\x1b[3;1HFAKE-LOG-LINE/google.protobuf.Empty"
```

This full string is written verbatim (inside `[...]`) into the ProtoText output whenever the surrounding message is formatted with `prototext.Marshal`/`prototext.Format` — the routine Devins/services use for debug/log/error output of arbitrary proto messages, exactly analogous to how Helm printed chart version strings.

### Impact Explanation
Any code path that renders an attacker-influenced protobuf message containing an `Any` field via `prototext.Format`/`Marshal` (common for debug logging, CLI/tool output, error/status detail dumps) will emit the raw `type_url` bytes unescaped. This allows an attacker who controls message content (e.g., request payload later logged, or a `google.protobuf.Any`-typed error detail reflected back to an operator's terminal) to inject terminal control sequences — clearing/scrolling the screen, repositioning the cursor, or forging fake log lines — to spoof or obscure information shown to an operator. This mirrors the confidentiality/integrity impact rated in the Helm advisory (CWE-74, terminal output spoofing), not memory safety or parsing correctness.

### Likelihood Explanation
Requires: (1) a message containing an `Any` field whose `type_url`/`value` are attacker-influenced, (2) the `value` bytes must at least be parseable as `AllowPartial` for the resolved type (trivially satisfied — even an empty byte string round-trips against many message types), (3) the crafted `type_url` prefix must still end in `/<full.type.name>` that is registered in the resolver in use, and (4) the message must reach a `prototext.Marshal`/`Format` call whose output is displayed on a terminal or otherwise interpreted for control sequences. Each precondition is plausible in services that log/format arbitrary or partially attacker-controlled proto messages (common for gRPC status details, generic message dumps), but it depends on the resolver actually holding some registered type and on an operator-facing sink; this makes it a real but situational Medium-severity issue, consistent with the Helm advisory's own severity rating.

### Recommendation
In `marshalAny` (encoding/prototext/encode.go), escape `typeURL` the same way ordinary string fields are escaped before embedding it in the field-name token, e.g. build the bracketed name through the same `appendString`/`WriteString` escaping logic used for scalar strings instead of raw concatenation passed to `WriteName`. At minimum, reject or escape control characters (`< 0x20`, `0x7f`) and non-printable bytes in `type_url` prior to writing it to any text-based sink.

### Proof of Concept
```go
package main

import (
	"fmt"

	"google.golang.org/protobuf/encoding/prototext"
	"google.golang.org/protobuf/proto"
	"google.golang.org/protobuf/types/known/anypb"
	"google.golang.org/protobuf/types/known/emptypb"
)

func main() {
	// value bytes for an empty message parse fine against emptypb.Empty
	a := &anypb.Any{
		TypeUrl: "https://type.googleapis.com/\x1b[2J\x1b[H<INJECTED>/google.protobuf.Empty",
		Value:   []byte{}, // empty payload
	}
	b, err := prototext.Marshal(a)
	if err != nil {
		panic(err)
	}
	fmt.Printf("%q\n", string(b)) // raw ESC bytes appear unescaped inside the "[...]" name
	_ = proto.Message(a)
	_ = emptypb.Empty{}
}
```
Because `FindMessageByURL` only checks the suffix after the last `/` (`google.protobuf.Empty`), resolution succeeds, `marshalAny` takes the raw-write branch, and the output contains literal `\x1b[2J\x1b[H` (clear-screen/cursor-home) bytes inside the field name — bytes that would be escaped (`\x1b`) had this gone through the normal `WriteString` path used for every other string field.

### Citations

**File:** encoding/prototext/encode.go (L222-227)
```go
	case protoreflect.StringKind:
		s := val.String()
		if !e.opts.allowInvalidUTF8 && strs.EnforceUTF8(fd) && !utf8.ValidString(s) {
			return errors.InvalidUTF8(string(fd.FullName()))
		}
		e.WriteString(s)
```

**File:** encoding/prototext/encode.go (L344-379)
```go
// marshalAny marshals the given google.protobuf.Any message in expanded form.
// It returns true if it was able to marshal, else false.
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
