No vulnerability found for this question.

The reported issue is a JavaScript-specific prototype-pollution bug in `set-getter`, where an attacker-controlled property path passed to a generic object setter can walk up to `Object.prototype` and pollute it globally. Go has no prototype-chain object model, so there is no equivalent "shared global prototype" that can be corrupted by a field/key name.

I reviewed protobuf-go's analogous "generic setter by key" paths — `protoreflect.Message.Set`/`Mutable` [1](#0-0) , the map/extension setters in `internal/impl/message_reflect_gen.go` and `internal/impl/message_reflect.go` [2](#0-1) [3](#0-2) , `dynamicpb`'s map setter [4](#0-3) , and the wire-format decode/merge paths that dispatch field values into a message by field descriptor [5](#0-4) [6](#0-5) . All of these:

- Key strictly off the trusted, compiled `FieldDescriptor`/field number from the message's own schema (`md.Fields().ByNumber(num)` or a resolver lookup restricted to declared extension ranges), not an arbitrary attacker-supplied string path.
- Operate on per-instance Go struct fields, map entries or extension maps allocated per message instance — there is no shared prototype/global object that unrelated instances or types share.
- Reject unknown numbers by routing them into the message's own `unknown fields` byte buffer rather than writing into any object structure by name [7](#0-6) .

Because there is no reachable code path where unprivileged, schema-trusted input (default binary or ProtoJSON parsing) can use a controlled key/path to write into a shared/global object analogous to `Object.prototype`, this bug class does not have a valid analog in protobuf-go.

### Citations

**File:** reflect/protoreflect/value.go (L93-103)
```go
	// Set stores the value for a field.
	//
	// For a field belonging to a oneof, it implicitly clears any other field
	// that may be currently set within the same oneof.
	// For extension fields, it implicitly stores the provided ExtensionType.
	// When setting a composite type, it is unspecified whether the stored value
	// aliases the source's memory in any way. If the composite value is an
	// empty, read-only value, then it panics.
	//
	// Set is a mutating operation and unsafe for concurrent use.
	Set(FieldDescriptor, Value)
```

**File:** internal/impl/message_reflect_gen.go (L224-232)
```go
func (m *messageReflectWrapper) Set(fd protoreflect.FieldDescriptor, v protoreflect.Value) {
	mi := m.messageInfo()
	mi.init()
	if fi, xd := mi.checkField(fd); fi != nil {
		fi.set(m.pointer(), v)
	} else {
		mi.extensionMap(m.pointer()).Set(xd, v)
	}
}
```

**File:** internal/impl/message_reflect.go (L281-304)
```go
func (m *extensionMap) Set(xd protoreflect.ExtensionTypeDescriptor, v protoreflect.Value) {
	xt := xd.Type()
	isValid := true
	switch {
	case !xt.IsValidValue(v):
		isValid = false
	case xd.IsList():
		isValid = v.List().IsValid()
	case xd.IsMap():
		isValid = v.Map().IsValid()
	case xd.Message() != nil:
		isValid = v.Message().IsValid()
	}
	if !isValid {
		panic(fmt.Sprintf("%v: assigning invalid value", xd.FullName()))
	}

	if *m == nil {
		*m = make(map[int32]ExtensionField)
	}
	var x ExtensionField
	x.Set(xt, v)
	(*m)[int32(xd.Number())] = x
}
```

**File:** types/dynamicpb/dynamic.go (L456-461)
```go
func (x *dynamicMap) Get(k protoreflect.MapKey) protoreflect.Value { return x.mapv[k.Interface()] }
func (x *dynamicMap) Set(k protoreflect.MapKey, v protoreflect.Value) {
	typecheckSingular(x.desc.MapKey(), k.Value())
	typecheckSingular(x.desc.MapValue(), v)
	x.mapv[k.Interface()] = v
}
```

**File:** proto/decode.go (L159-185)
```go
		// Find the field descriptor for this field number.
		fd := fields.ByNumber(num)
		if fd == nil && md.ExtensionRanges().Has(num) {
			extType, err := o.Resolver.FindExtensionByNumber(md.FullName(), num)
			if err != nil && err != protoregistry.NotFound {
				return errors.New("%v: unable to resolve extension %v: %v", md.FullName(), num, err)
			}
			if extType != nil {
				fd = extType.TypeDescriptor()
			}
		}
		var err error
		if fd == nil {
			err = errUnknown
		}

		// Parse the field value.
		var valLen int
		switch {
		case err != nil:
		case fd.IsList():
			valLen, err = o.unmarshalList(b[tagLen:], wtyp, m.Mutable(fd).List(), fd)
		case fd.IsMap():
			valLen, err = o.unmarshalMap(b[tagLen:], wtyp, m.Mutable(fd).Map(), fd)
		default:
			valLen, err = o.unmarshalSingular(b[tagLen:], wtyp, m, fd)
		}
```

**File:** proto/decode.go (L186-197)
```go
		if err != nil {
			if err != errUnknown {
				return err
			}
			valLen = protowire.ConsumeFieldValue(num, wtyp, b[tagLen:])
			if valLen < 0 {
				return errDecode
			}
			if !o.DiscardUnknown {
				m.SetUnknown(append(m.GetUnknown(), b[:tagLen+valLen]...))
			}
		}
```

**File:** proto/merge.go (L89-107)
```go
	src.Range(func(fd protoreflect.FieldDescriptor, v protoreflect.Value) bool {
		switch {
		case fd.IsList():
			o.mergeList(dst.Mutable(fd).List(), v.List(), fd)
		case fd.IsMap():
			o.mergeMap(dst.Mutable(fd).Map(), v.Map(), fd.MapValue())
		case fd.Message() != nil:
			o.mergeMessage(dst.Mutable(fd).Message(), v.Message())
		case fd.Kind() == protoreflect.BytesKind:
			dst.Set(fd, o.cloneBytes(v))
		default:
			dst.Set(fd, v)
		}
		return true
	})

	if len(src.GetUnknown()) > 0 {
		dst.SetUnknown(append(dst.GetUnknown(), src.GetUnknown()...))
	}
```
