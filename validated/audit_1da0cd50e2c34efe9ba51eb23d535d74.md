No vulnerability found for this question.

The TensorFlow report describes a custom `TensorKey` hash function that incorrectly used `AllocatedBytes()` (a non-contiguous size estimate) to compute a hash over `tensor.data()`, causing an out-of-bounds read. I searched protobuf-go's map/key handling paths (`internal/impl/convert_map.go`, `internal/impl/codec_map.go`, `proto/decode.go`, `encoding/protojson/decode.go`, `reflect/protoreflect/value_union.go`) and found no analogous custom hash function reading a byte buffer using an unrelated, estimated, or otherwise incorrect length. Map keys in protobuf-go rely on Go's native `map[K]V` type and its built-in hashing/equality via `reflect.Value.SetMapIndex`/`MapIndex`, as seen in `mapReflect.Set`/`Get` [1](#0-0) , not a hand-rolled hash over a byte slice sized by an unrelated estimate. The `strs.UnsafeString`/`UnsafeBytes` helpers do reinterpret byte slices via `unsafe`, but they use the actual `len()` of the input slice/string rather than an unrelated size estimate, so there's no length/content mismatch analogous to `AllocatedBytes()` vs `.data()` [2](#0-1) . No reachable code path matches the reported bug class of "hash/read size derived from an estimate rather than the actual buffer length."

### Citations

**File:** internal/impl/convert_map.go (L75-87)
```go
func (ms *mapReflect) Get(k protoreflect.MapKey) protoreflect.Value {
	rk := ms.keyConv.GoValueOf(k.Value())
	rv := ms.v.MapIndex(rk)
	if !rv.IsValid() {
		return protoreflect.Value{}
	}
	return ms.valConv.PBValueOf(rv)
}
func (ms *mapReflect) Set(k protoreflect.MapKey, v protoreflect.Value) {
	rk := ms.keyConv.GoValueOf(k.Value())
	rv := ms.valConv.GoValueOf(v)
	ms.v.SetMapIndex(rk, rv)
}
```

**File:** internal/strs/strings_unsafe.go (L13-28)
```go
// UnsafeString returns an unsafe string reference of b.
// The caller must treat the input slice as immutable.
//
// WARNING: Use carefully. The returned result must not leak to the end user
// unless the input slice is provably immutable.
func UnsafeString(b []byte) string {
	return unsafe.String(unsafe.SliceData(b), len(b))
}

// UnsafeBytes returns an unsafe bytes slice reference of s.
// The caller must treat returned slice as immutable.
//
// WARNING: Use carefully. The returned result must not leak to the end user.
func UnsafeBytes(s string) []byte {
	return unsafe.Slice(unsafe.StringData(s), len(s))
}
```
