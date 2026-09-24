No Vulnerability found for this question.

The Hadoop CVE describes classic unsafe object deserialization (Java `ObjectInputStream`-style gadget-chain deserialization) of data pulled from ZooKeeper, leading to arbitrary command execution. I looked for an analogous pattern in protobuf-go — places where untrusted bytes are deserialized and used to construct/execute code based on attacker-controlled type information without validation, such as the `Any` type-resolution paths in `encoding/protojson/well_known_types.go` (`unmarshalAny`), `encoding/prototext/decode.go` (`unmarshalAny`), `types/known/anypb/any.pb.go` (`UnmarshalNew`), and the lazy-decoding paths in `internal/impl/lazy.go` and `internal/impl/decode.go`.

None of these present an analogous broken invariant:
- `Any.UnmarshalNew`/`FindMessageByURL` only resolves types already linked/registered in the binary via `protoregistry.GlobalTypes` — it cannot instantiate or execute arbitrary attacker-supplied types or code, unlike Java's polymorphic deserialization gadget chains. [1](#0-0) 
- Lazy decoding in `internal_impl/lazy.go` only defers parsing of already-schema-validated submessage bytes; it still goes through the same wire-format validation (`skipField`, `unmarshalField`) as eager decoding, with no unsafe deserialization of arbitrary object graphs. [2](#0-1) 
- The eager/text/JSON `Any` decoders reject unresolvable type URLs with an error rather than deserializing blindly. [3](#0-2) 

Since protobuf-go's message construction is restricted to compile-time-linked, schema-known message types (no dynamic class loading or reflection-based arbitrary object instantiation from untrusted input), there is no equivalent "deserialize untrusted data without validation leading to arbitrary code execution" sink in this codebase under the stated trusted-schema/default-parser assumptions.

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

**File:** internal/impl/lazy.go (L175-210)
```go
func (mi *MessageInfo) unmarshalPointerLazy(b []byte, p pointer, groupTag protowire.Number, opts unmarshalOptions) (out unmarshalOutput, err error) {
	initialized := true
	var requiredMask uint64
	var lazy **protolazy.XXX_lazyUnmarshalInfo
	var presence presence
	var lazyIndex []protolazy.IndexEntry
	var lastNum protowire.Number
	outOfOrder := false
	lazyDecode := false
	presence = p.Apply(mi.presenceOffset).PresenceInfo()
	lazy = p.Apply(mi.lazyOffset).LazyInfoPtr()
	if !presence.AnyPresent(mi.presenceSize) {
		if opts.CanBeLazy() {
			// If the message contains existing data, we need to merge into it.
			// Lazy unmarshaling doesn't merge, so only enable it when the
			// message is empty (has no presence bitmap).
			lazyDecode = true
			if *lazy == nil {
				*lazy = &protolazy.XXX_lazyUnmarshalInfo{}
			}
			(*lazy).SetUnmarshalFlags(opts.flags)
			if !opts.AliasBuffer() {
				// Make a copy of the buffer for lazy unmarshaling.
				// Set the AliasBuffer flag so recursive unmarshal
				// operations reuse the copy.
				b = append([]byte{}, b...)
				opts.flags |= piface.UnmarshalAliasBuffer
			}
			(*lazy).SetBuffer(b)
		}
	}
	// Track special handling of lazy fields.
	//
	// In the common case, all fields are lazyValidateOnly (and lazyFields remains nil).
	// In the event that validation for a field fails, this map tracks handling of the field.
	type lazyAction uint8
```

**File:** encoding/protojson/well_known_types.go (L205-209)
```go
	typeURL := tok.ParsedString()
	emt, err := d.opts.Resolver.FindMessageByURL(typeURL)
	if err != nil {
		return d.newError(tok.Pos(), "unable to resolve %v: %q", tok.RawString(), err)
	}
```
