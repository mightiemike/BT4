No vulnerability found for this question.

The Apache Cayenne CVE describes Hessian deserialization where an attacker-controlled payload can trigger arbitrary class instantiation/side-effect methods (Java's classic deserialization gadget-chain problem). This bug class does not map onto protobuf-go's design:

- `anypb.UnmarshalNew` / `Any.UnmarshalTo` only resolve message types that are already statically linked into the binary via `protoregistry.GlobalTypes`, and construction is via `mt.New()` which just allocates a zero-value generated struct — no arbitrary constructors, `init()`-style side effects, or gadget chains are invoked based on attacker-supplied type names. [1](#0-0) 
- The core wire decoder (`internal/impl/decode.go`) and lazy-extension decoder (`internal/impl/lazy.go`, `internal/impl/codec_extension.go`) only ever populate fields of pre-registered message types using reflection-based setters; unknown/extension fields are either stored as raw bytes or resolved strictly through `protoregistry.ExtensionTypeResolver.FindExtensionByNumber`, which also requires compile-time registration. [2](#0-1) 
- ProtoJSON and prototext `Any` handling behave the same way — `FindMessageByURL` resolution followed by a plain `Marshal`/`Unmarshal` round-trip, with no dynamic class loading or arbitrary method invocation. [3](#0-2) 

Since protobuf-go under the stated trusted-schema/default-parser assumptions never instantiates or invokes arbitrary code based on untrusted type identifiers (unlike Java's Hessian/Object deserialization), there is no analogous broken invariant to report.

### Citations

**File:** types/known/anypb/any.pb.go (L247-267)
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
}
```

**File:** internal/impl/decode.go (L247-263)
```go
func (mi *MessageInfo) unmarshalExtension(b []byte, num protowire.Number, wtyp protowire.Type, exts map[int32]ExtensionField, opts unmarshalOptions) (out unmarshalOutput, err error) {
	x := exts[int32(num)]
	xt := x.Type()
	if xt == nil {
		var err error
		xt, err = opts.resolver.FindExtensionByNumber(mi.Desc.FullName(), num)
		if err != nil {
			if err == protoregistry.NotFound {
				return out, errUnknown
			}
			return out, errors.New("%v: unable to resolve extension %v: %v", mi.Desc.FullName(), num, err)
		}
	}
	xi := getExtensionFieldInfo(xt)
	if xi.funcs.unmarshal == nil {
		return out, errUnknown
	}
```

**File:** encoding/protojson/well_known_types.go (L205-224)
```go
	typeURL := tok.ParsedString()
	emt, err := d.opts.Resolver.FindMessageByURL(typeURL)
	if err != nil {
		return d.newError(tok.Pos(), "unable to resolve %v: %q", tok.RawString(), err)
	}

	// Create new message for the embedded message type and unmarshal into it.
	em := emt.New()
	if unmarshal := wellKnownTypeUnmarshaler(emt.Descriptor().FullName()); unmarshal != nil {
		// If embedded message is a custom type,
		// unmarshal the JSON "value" field into it.
		if err := d.unmarshalAnyValue(unmarshal, em); err != nil {
			return err
		}
	} else {
		// Else unmarshal the current JSON object into it.
		if err := d.unmarshalMessage(em, true); err != nil {
			return err
		}
	}
```
