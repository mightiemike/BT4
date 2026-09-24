No vulnerability found for this question.

The CVE-2021-21347 bug class is XStream's polymorphic-deserialization RCE, where an attacker-controlled type name lets XStream instantiate and unsafely construct arbitrary Java classes (gadget chains) purely from untrusted input, absent a strict type whitelist.

protobuf-go's closest analog is `google.protobuf.Any` type-URL resolution, used in `protojson`'s `decoder.unmarshalAny` and `anypb.UnmarshalNew`. In both cases, the type name embedded in the input (`@type` field or `TypeUrl`) is passed to a `MessageTypeResolver.FindMessageByURL`, which is backed by `protoregistry.GlobalTypes` — a registry populated only by message types statically linked into the binary at compile time via generated-package imports. `mt.New()` merely allocates a zero-value instance of an already-known, trusted Go struct type; there is no dynamic class loading, no arbitrary constructor execution, and no gadget-chain-style side effects analogous to Java deserialization. [1](#0-0) [2](#0-1) [3](#0-2) 

Per the analysis rules, this is a trusted-schema, no-attacker-supplied-descriptor scenario: unresolved type URLs simply return `protoregistry.NotFound`/an error, and no unprivileged request path can cause the library to construct or execute code for a type that wasn't already compiled into the binary. This is not the same broken invariant as XStream's blacklist-bypass RCE, so no valid analog exists.

### Citations

**File:** types/known/anypb/any.pb.go (L247-266)
```go
func UnmarshalNew(src *Any, opts proto.UnmarshalOptions) (dst proto.Message, err error) {
	if src.GetTypeUrl() == "" {
		return nil, protoimpl.X.NewError("invalid empty type URL")
	}
	if opts.Resolver == nil {
		opts.Resolver = protoregistry.GlobalTypes
	}
	r, ok := opts.Resolver.(protoregistry.MessageTypeResolver)
	if !ok {
		return nil, protoregistry.NotFound
	}
	mt, err := r.FindMessageByURL(src.GetTypeUrl())
	if err != nil {
		if err == protoregistry.NotFound {
			return nil, err
		}
		return nil, protoimpl.X.NewError("could not resolve %q: %v", src.GetTypeUrl(), err)
	}
	dst = mt.New().Interface()
	return dst, opts.Unmarshal(src.GetValue(), dst)
```

**File:** encoding/protojson/well_known_types.go (L205-212)
```go
	typeURL := tok.ParsedString()
	emt, err := d.opts.Resolver.FindMessageByURL(typeURL)
	if err != nil {
		return d.newError(tok.Pos(), "unable to resolve %v: %q", tok.RawString(), err)
	}

	// Create new message for the embedded message type and unmarshal into it.
	em := emt.New()
```

**File:** types/dynamicpb/types.go (L119-127)
```go
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
