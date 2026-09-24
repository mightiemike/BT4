No vulnerability found for this question.

The GeoWebCache issue is about unchecked JNDI lookups that let a configuration value (or an admin-supplied string in GeoServer) request an arbitrary remote resource, enabling class deserialization and RCE. The closest analog in protobuf-go is type-URL resolution for `google.protobuf.Any`, which goes through `protoregistry.MessageTypeResolver.FindMessageByURL` in the JSON, text, and range/transform paths [1](#0-0) [2](#0-1) [3](#0-2) .

Unlike JNDI, this resolution is not a remote/dynamic lookup mechanism: `FindMessageByURL` truncates the URL to a bare type name and does a simple map lookup against `Types.typesByName`, a registry populated only by compile-time-registered, trusted Go types (or, for `dynamicpb`, from an already-trusted `protoregistry.Files` set) [4](#0-3) [5](#0-4) . There is no capability to fetch, load, or deserialize an arbitrary class/resource from attacker-controlled data — an unregistered or unresolvable type simply returns `NotFound`, which every caller (`protojson`, `prototext`, `protorange`, `protocmp`, `anypb.UnmarshalNew`) treats as a hard error and aborts [6](#0-5) .

Per the rules, this scenario requires a trusted schema/resolver and rejects analogs relying on attacker-supplied resolvers or descriptors, which is exactly what would be needed to make an "unchecked lookup" meaningful here. Since the resolver is a closed, locally-registered lookup table rather than an open-ended remote/dynamic resource fetch, there is no equivalent broken invariant to the JNDI CVE in this codebase.

### Citations

**File:** encoding/protojson/well_known_types.go (L205-209)
```go
	typeURL := tok.ParsedString()
	emt, err := d.opts.Resolver.FindMessageByURL(typeURL)
	if err != nil {
		return d.newError(tok.Pos(), "unable to resolve %v: %q", tok.RawString(), err)
	}
```

**File:** encoding/prototext/decode.go (L716-720)
```go
func (d decoder) unmarshalExpandedAny(typeURL string, pos int) ([]byte, error) {
	mt, err := d.opts.Resolver.FindMessageByURL(typeURL)
	if err != nil {
		return nil, d.newError(pos, "unable to resolve message [%v]: %v", typeURL, err)
	}
```

**File:** types/known/anypb/any.pb.go (L247-258)
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
```

**File:** reflect/protoregistry/registry.go (L431-447)
```go
// A compliant implementation must deterministically return the same type
// if no error is encountered.
//
// The [Types] type implements this interface.
type MessageTypeResolver interface {
	// FindMessageByName looks up a message by its full name.
	// E.g., "google.protobuf.Any"
	//
	// This return (nil, NotFound) if not found.
	FindMessageByName(message protoreflect.FullName) (protoreflect.MessageType, error)

	// FindMessageByURL looks up a message by a URL identifier.
	// See documentation on google.protobuf.Any.type_url for the URL format.
	//
	// This returns (nil, NotFound) if not found.
	FindMessageByURL(url string) (protoreflect.MessageType, error)
}
```

**File:** reflect/protoregistry/registry.go (L632-657)
```go
// FindMessageByURL looks up a message by a URL identifier.
// See documentation on google.protobuf.Any.type_url for the URL format.
//
// This returns (nil, [NotFound]) if not found.
func (r *Types) FindMessageByURL(url string) (protoreflect.MessageType, error) {
	// This function is similar to FindMessageByName but
	// truncates anything before and including '/' in the URL.
	if r == nil {
		return nil, NotFound
	}
	if r == GlobalTypes {
		globalMutex.RLock()
		defer globalMutex.RUnlock()
	}
	message := protoreflect.FullName(url)
	if i := strings.LastIndexByte(url, '/'); i >= 0 {
		message = message[i+len("/"):]
	}

	if v := r.typesByName[message]; v != nil {
		if mt, _ := v.(protoreflect.MessageType); mt != nil {
			return mt, nil
		}
		return nil, errors.New("found wrong type: got %v, want message", typeName(v))
	}
	return nil, NotFound
```

**File:** types/dynamicpb/types.go (L115-126)
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
```
