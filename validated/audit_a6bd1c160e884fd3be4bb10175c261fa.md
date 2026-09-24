No vulnerability found for this question.

CVE-2017-12952 is a NULL pointer dereference in libgig's C++ `LoadString` function when parsing crafted binary `.gig` files—a memory-unsafe language issue in an unrelated audio-sample library. There is no analogous broken invariant in protobuf-go's string-decoding paths.

Across all string decode paths I examined—binary wire decoding (`consumeString`, `consumeStringPtr` in [1](#0-0) , [2](#0-1) ), ProtoJSON (`unmarshalScalar` in [3](#0-2) ), and ProtoText (`parseString` in [4](#0-3) )—every length/bounds check is validated before use (e.g. `protowire.ConsumeBytes` returning `n < 0` on truncation, checked immediately by callers) [5](#0-4) . Pointer fields are defensively nil-checked and lazily allocated before dereference (`if *vp == nil { *vp = new(string) }`) rather than dereferenced unconditionally [6](#0-5) . Go's memory-safety guarantees also mean an unset/absent field simply yields a zero-value `string`, not a NULL/uninitialized-pointer crash as in the C++ analog. No reachable unprivileged request path in trusted-schema binary/JSON decoding exhibits the "missing null/length check before use" pattern the CVE describes.

### Citations

**File:** internal/impl/codec_gen.go (L5020-5032)
```go
// consumeString wire decodes a string pointer as a String.
func consumeString(b []byte, p pointer, wtyp protowire.Type, f *coderFieldInfo, opts unmarshalOptions) (out unmarshalOutput, err error) {
	if wtyp != protowire.BytesType {
		return out, errUnknown
	}
	v, n := protowire.ConsumeBytes(b)
	if n < 0 {
		return out, errDecode
	}
	*p.String() = string(v)
	out.n = n
	return out, nil
}
```

**File:** internal/impl/codec_gen.go (L5143-5159)
```go
// consumeStringPtr wire decodes a *string pointer as a String.
func consumeStringPtr(b []byte, p pointer, wtyp protowire.Type, f *coderFieldInfo, opts unmarshalOptions) (out unmarshalOutput, err error) {
	if wtyp != protowire.BytesType {
		return out, errUnknown
	}
	v, n := protowire.ConsumeBytes(b)
	if n < 0 {
		return out, errDecode
	}
	vp := p.StringPtr()
	if *vp == nil {
		*vp = new(string)
	}
	**vp = string(v)
	out.n = n
	return out, nil
}
```

**File:** encoding/protojson/decode.go (L330-333)
```go
	case protoreflect.StringKind:
		if tok.Kind() == json.String {
			return protoreflect.ValueOfString(tok.ParsedString()), nil
		}
```

**File:** internal/encoding/text/decode_string.go (L50-68)
```go
func (d *Decoder) parseString() (string, error) {
	in := d.in
	if len(in) == 0 {
		return "", ErrUnexpectedEOF
	}
	quote := in[0]
	in = in[1:]
	i := indexNeedEscapeInBytes(in)
	in, out := in[i:], in[:i:i] // set cap to prevent mutations
	for len(in) > 0 {
		switch r, n := utf8.DecodeRune(in); {
		case r == utf8.RuneError && n == 1:
			return "", d.newSyntaxError("invalid UTF-8 detected")
		case r == 0 || r == '\n':
			return "", d.newSyntaxError("invalid character %q in string", r)
		case r == rune(quote):
			in = in[1:]
			d.consume(len(d.in) - len(in))
			return string(out), nil
```

**File:** encoding/protowire/wire.go (L460-469)
```go
func ConsumeBytes(b []byte) (v []byte, n int) {
	m, n := ConsumeVarint(b)
	if n < 0 {
		return nil, n // forward error code
	}
	if m > uint64(len(b[n:])) {
		return nil, errCodeTruncated
	}
	return b[n:][:m], n + int(m)
}
```
