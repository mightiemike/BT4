No vulnerability found for this question.

The reported CVE describes an infinite loop specific to PCRE regex negation matching zero-width patterns in Suricata's rule engine. Protobuf-go contains no regex/PCRE matching logic, so there is no equivalent "negated pattern that never advances" construct.

I reviewed the analogous bug class — loops that fail to advance a read/parse cursor — across protobuf-go's decode paths:
- Binary unmarshaling loops in `internal/impl/decode.go` and `internal/impl/codec_gen.go` all advance `b = b[n:]` and bail out via `errDecode` whenever `protowire.ConsumeVarint`/`ConsumeBytes`/`ConsumeFieldValue` return `n < 0` [1](#0-0) [2](#0-1) .
- The map/list unmarshal loops similarly always consume bytes or return an error [3](#0-2) .
- The JSON decoder's `skipJSONValue` explicitly documents and guards against infinite loops by treating unexpected `json.EOF` as an error [4](#0-3) .
- The text-format decoder's tokenizer (`parseNext`, `parseIdent`, `parseStringValue`) always consumes at least one byte or returns an error/EOF, with no negation-based lookahead that could stall [5](#0-4) [6](#0-5) .

None of these loops involve a "negated match" concept analogous to PCRE's negation-with-zero-width-match issue, and all bounded-progress invariants are enforced by requiring forward buffer consumption or explicit error returns. This is a rejection based on the report's bug class (regex engine infinite loop) not mapping onto any reachable protobuf-go parsing construct.

### Citations

**File:** internal/impl/decode.go (L136-152)
```go
	for len(b) > 0 {
		// Parse the tag (field number and wire type).
		var tag uint64
		if b[0] < 0x80 {
			tag = uint64(b[0])
			b = b[1:]
		} else if len(b) >= 2 && b[1] < 128 {
			tag = uint64(b[0]&0x7f) + uint64(b[1])<<7
			b = b[2:]
		} else {
			var n int
			tag, n = protowire.ConsumeVarint(b)
			if n < 0 {
				return out, errDecode
			}
			b = b[n:]
		}
```

**File:** internal/impl/codec_gen.go (L339-356)
```go
		for len(b) > 0 {
			var v uint64
			var n int
			if len(b) >= 1 && b[0] < 0x80 {
				v = uint64(b[0])
				n = 1
			} else if len(b) >= 2 && b[1] < 128 {
				v = uint64(b[0]&0x7f) + uint64(b[1])<<7
				n = 2
			} else {
				v, n = protowire.ConsumeVarint(b)
			}
			if n < 0 {
				return protoreflect.Value{}, out, errDecode
			}
			list.Append(protoreflect.ValueOfBool(protowire.DecodeBool(v)))
			b = b[n:]
		}
```

**File:** internal/impl/codec_map.go (L130-168)
```go
	for len(b) > 0 {
		num, wtyp, n := protowire.ConsumeTag(b)
		if n < 0 {
			return out, errDecode
		}
		if num > protowire.MaxValidNumber {
			return out, errDecode
		}
		b = b[n:]
		err := errUnknown
		switch num {
		case genid.MapEntry_Key_field_number:
			var v protoreflect.Value
			var o unmarshalOutput
			v, o, err = mapi.keyFuncs.unmarshal(b, key, num, wtyp, opts)
			if err != nil {
				break
			}
			key = v
			n = o.n
		case genid.MapEntry_Value_field_number:
			var v protoreflect.Value
			var o unmarshalOutput
			v, o, err = mapi.valFuncs.unmarshal(b, val, num, wtyp, opts)
			if err != nil {
				break
			}
			val = v
			n = o.n
		}
		if err == errUnknown {
			n = protowire.ConsumeFieldValue(num, wtyp, b)
			if n < 0 {
				return out, errDecode
			}
		} else if err != nil {
			return out, err
		}
		b = b[n:]
```

**File:** encoding/protojson/well_known_types.go (L310-336)
```go
// skipJSONValue parses a JSON value (null, boolean, string, number, object and
// array) in order to advance the read to the next JSON value. It relies on
// the decoder returning an error if the types are not in valid sequence.
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
```

**File:** internal/encoding/text/decode.go (L96-106)
```go
// parseNext parses the next Token based on given last kind.
func (d *Decoder) parseNext(lastKind Kind) (Token, error) {
	// Trim leading spaces.
	d.consume(0)
	isEOF := false
	if len(d.in) == 0 {
		isEOF = true
	}

	switch lastKind {
	case EOF:
```

**File:** internal/encoding/text/decode_string.go (L24-47)
```go
func (d *Decoder) parseStringValue() (Token, error) {
	// Note that the ending quote is sufficient to unambiguously mark the end
	// of a string. Thus, the text grammar does not require intervening
	// whitespace or control characters in-between strings.
	// Thus, the following is valid:
	//	`"foo"'bar'"baz"` => "foobarbaz"
	in0 := d.in
	var ss []string
	for len(d.in) > 0 && (d.in[0] == '"' || d.in[0] == '\'') {
		s, err := d.parseString()
		if err != nil {
			return Token{}, err
		}
		ss = append(ss, s)
	}
	// d.in already points to the end of the value at this point.
	return Token{
		kind:  Scalar,
		attrs: stringValue,
		pos:   len(d.orig) - len(in0),
		raw:   in0[:len(in0)-len(d.in)],
		str:   strings.Join(ss, ""),
	}, nil
}
```
