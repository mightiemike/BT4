No vulnerability found for this question.

The Lighthouse report describes an application-level race condition where blob data fetched asynchronously (`getBlobs`) could be dropped before persistence — a logic bug specific to Ethereum's beacon-chain block-import pipeline. This does not map to any parser/decode invariant in protobuf-go.

I examined protobuf-go's closest analogous mechanism — lazy message decoding, which has similar "populate on first read, shared across concurrent access" semantics — and found it is deliberately hardened against exactly this class of bug:

- Lazy field pointers are only ever set via atomic compare-and-swap (`AtomicSetPointerIfNil`) and read via `AtomicGetPointer`/`AtomicLoadPointer`, so a concurrent read that races with the lazy-unmarshal write cannot observe a partially-populated or lost value [1](#0-0) .
- `lazyUnmarshal` explicitly documents the race concern and avoids writing the shared pointer until the sub-message is fully decoded into a fresh object, only then publishing it atomically if the slot is still nil [2](#0-1) .
- Extension lazy decoding uses a mutex plus an atomic "once" flag to guarantee single-decode-and-publish semantics under concurrent access [3](#0-2) [4](#0-3) .
- Generated getters for lazy fields explicitly check-and-trigger unmarshal atomically before reading, ensuring the value is always fully materialized before use [5](#0-4) .
- The library ships dedicated race-detector regression tests (`internal/race_test/lazy/lazy_race_test.go`, e.g. `TestMarshalMessageSetLazyRace`, `TestParallellMarshalMixed`) specifically to catch "not persisting"/lost-update races on lazily decoded data [6](#0-5) [7](#0-6) .

No reachable unprivileged request path (default binary/ProtoJSON parse under trusted schema) exhibits a broken publish/persist invariant analogous to the Lighthouse `getBlobs` bug — the lazy-decode subsystem is specifically engineered and tested against this bug class.

### Citations

**File:** internal/impl/pointer_unsafe_opaque.go (L12-29)
```go
func (p pointer) AtomicGetPointer() pointer {
	return pointer{p: atomic.LoadPointer((*unsafe.Pointer)(p.p))}
}

func (p pointer) AtomicSetPointer(v pointer) {
	atomic.StorePointer((*unsafe.Pointer)(p.p), v.p)
}

func (p pointer) AtomicSetNilPointer() {
	atomic.StorePointer((*unsafe.Pointer)(p.p), unsafe.Pointer(nil))
}

func (p pointer) AtomicSetPointerIfNil(v pointer) pointer {
	if atomic.CompareAndSwapPointer((*unsafe.Pointer)(p.p), unsafe.Pointer(nil), v.p) {
		return v
	}
	return pointer{p: atomic.LoadPointer((*unsafe.Pointer)(p.p))}
}
```

**File:** internal/impl/lazy.go (L66-82)
```go
	lazy := *p.Apply(mi.lazyOffset).LazyInfoPtr()
	start, end, found, _, multipleEntries := lazy.FindFieldInProto(uint32(num))
	if !found && multipleEntries == nil {
		panic(fmt.Sprintf("lazyUnmarshal: can't find field data for %v.%v", mi.Desc.FullName(), num))
	}
	// The actual pointer in the message can not be set until the whole struct is filled in, otherwise we will have races.
	// Create another pointer and set it atomically, if we won the race and the pointer in the original message is still nil.
	fp := pointerOfValue(reflect.New(f.ft))
	if multipleEntries != nil {
		for _, entry := range multipleEntries {
			mi.unmarshalField(lazy.Buffer()[entry.Start:entry.End], fp, f, lazy, lazy.UnmarshalFlags())
		}
	} else {
		mi.unmarshalField(lazy.Buffer()[start:end], fp, f, lazy, lazy.UnmarshalFlags())
	}
	p.Apply(f.offset).AtomicSetPointerIfNil(fp.Elem())
}
```

**File:** internal/impl/codec_extension.go (L104-121)
```go
func (f *ExtensionField) isUnexpandedLazy() bool {
	return f.lazy != nil && atomic.LoadUint32(&f.lazy.atomicOnce) == 0
}

// lazyBuffer retrieves the buffer for a lazy extension if it's not yet expanded.
//
// The returned buffer has to be kept over whatever operation we're planning,
// as re-retrieving it will fail after the message is lazily decoded.
func (f *ExtensionField) lazyBuffer() []byte {
	// This function might be in the critical path, so check the atomic without
	// taking a look first, then only take the lock if needed.
	if !f.isUnexpandedLazy() {
		return nil
	}
	f.lazy.mu.Lock()
	defer f.lazy.mu.Unlock()
	return f.lazy.b
}
```

**File:** internal/impl/codec_extension.go (L123-165)
```go
func (f *ExtensionField) lazyInit() {
	f.lazy.mu.Lock()
	defer f.lazy.mu.Unlock()
	if atomic.LoadUint32(&f.lazy.atomicOnce) == 1 {
		return
	}
	if f.lazy.xi != nil {
		b := f.lazy.b
		val := f.typ.New()
		for len(b) > 0 {
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
					panic(errors.New("bad tag in lazy extension decoding"))
				}
				b = b[n:]
			}
			num := protowire.Number(tag >> 3)
			wtyp := protowire.Type(tag & 7)
			var out unmarshalOutput
			var err error
			val, out, err = f.lazy.xi.funcs.unmarshal(b, val, num, wtyp, lazyUnmarshalOptions)
			if err != nil {
				panic(errors.New("decode failure in lazy extension decoding: %v", err))
			}
			b = b[out.n:]
		}
		f.lazy.value = val
	} else {
		panic("No support for lazy fns for ExtensionField")
	}
	f.lazy.xi = nil
	f.lazy.b = nil
	atomic.StoreUint32(&f.lazy.atomicOnce, 1)
}
```

**File:** internal/testprotos/mixed/mixed.pb.go (L641-653)
```go
func (x *OpaqueLazy) GetOpen() *OpenLazy {
	if x != nil {
		if protoimpl.X.Present(&(x.XXX_presence[0]), 0) {
			if protoimpl.X.AtomicCheckPointerIsNil(&x.xxx_hidden_Open) {
				protoimpl.X.UnmarshalField(x, 1)
			}
			var rv *OpenLazy
			protoimpl.X.AtomicLoadPointer(protoimpl.Pointer(&x.xxx_hidden_Open), protoimpl.Pointer(&rv))
			return rv
		}
	}
	return nil
}
```

**File:** internal/impl/lazy_test.go (L525-583)
```go
func TestMarshalMessageSetLazyRace(t *testing.T) {
	if !flags.LazyUnmarshalExtensions {
		t.Skip("lazy extension unmarshaling disabled; not built with the protolegacy tag")
	}

	h := &lazytestpb.Holder{Data: &messagesetpb.MessageSet{}}

	ext := &lazytestpb.Rabbit{Name: proto.String("Judy")}
	proto.SetExtension(h.GetData(), lazytestpb.E_Rabbit_MessageSetExtension, ext)

	b, err := proto.Marshal(h)
	if err != nil {
		t.Fatalf("Could not marshal message: %v", err)
	}
	if err := proto.Unmarshal(b, h); err != nil {
		t.Fatalf("Could not unmarshal message: %v", err)
	}
	// after Unmarshal, the extension is in undecoded form.
	// GetExtension will decode it lazily. Make sure this does
	// not race against Marshal.

	// The following pattern is similar to x/sync/errgroup,
	// but we want to avoid adding that dependencies just for a test.
	var (
		wg       sync.WaitGroup
		errOnce  sync.Once
		groupErr error
	)
	for n := 30; n > 0; n-- {
		wg.Add(2)
		go func() {
			defer wg.Done()
			if err := func() error {
				b, err := proto.Marshal(h)
				if err == nil {
					return proto.Unmarshal(b, &lazytestpb.Rabbit{})
				}
				return err
			}(); err != nil {
				errOnce.Do(func() { groupErr = err })
			}
		}()
		go func() {
			defer wg.Done()
			if err := func() error {
				mm := proto.GetExtension(h.GetData(), lazytestpb.E_Rabbit_MessageSetExtension).(*lazytestpb.Rabbit)
				if mm == nil {
					return errors.New("proto: missing extension")
				}
				return nil
			}(); err != nil {
				errOnce.Do(func() { groupErr = err })
			}
		}()
	}
	wg.Wait()
	if groupErr != nil {
		t.Fatal(groupErr)
	}
```

**File:** internal/race_test/lazy/lazy_race_test.go (L440-498)
```go
func TestParallellMarshalMixed(t *testing.T) {
	m := fillMixedOpaqueLazy()
	b, err := proto.Marshal(m)
	if err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 10000; i++ {
		ml := &mixedpb.OpaqueLazy{}
		d := make(chan bool)
		if err := proto.Unmarshal(b, ml); err != nil {
			t.Fatalf("Error while unmarshaling: %v", err)
		}

		go func() {
			b2, err := proto.Marshal(ml)
			if err != nil {
				t.Errorf("Marshal error: %v", err)
				d <- false
				return
			}
			m := &mixedpb.OpaqueLazy{}
			if err := proto.Unmarshal(b2, m); err != nil {
				t.Errorf("Unmarshal error: %v", err)
				d <- false
				return
			}
			if !proto.Equal(ml, m) { // This is what expands all fields of ml
				t.Errorf("Unmarshal roundtrip - protos not equal")
				d <- false
				return
			}
			d <- true
		}()
		go func() {
			b2, err := proto.Marshal(ml)
			if err != nil {
				t.Errorf("Marshal error: %v", err)
				d <- false
				return
			}
			m := &mixedpb.OpaqueLazy{}
			if err := proto.Unmarshal(b2, m); err != nil {
				t.Errorf("Unmarshal error: %v", err)
				d <- false
				return
			}
			if !proto.Equal(ml, m) { // This is what expands all fields of ml
				t.Errorf("Unmarshal roundtrip - protos not equal")
				d <- false
				return
			}
			d <- true
		}()
		x := <-d
		y := <-d
		if !x || !y {
			t.Fatalf("Worker reported error")
		}
	}
```
