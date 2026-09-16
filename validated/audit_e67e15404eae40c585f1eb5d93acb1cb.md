### Title
Data race between concurrent `FileHandler::get()` reads and `FileHandler::append()` writes in the mmap-backed storage layer - ([File: crates/apollo_storage/src/mmap_file/mod.rs])

### Summary
The Apollo storage layer's memory-mapped file implementation protects writer mutations (`append`, `grow`, `flush`) with a `Mutex<MMapFile<V>>`, but the reader path (`FileHandler::get`) bypasses this mutex entirely and reads directly through a raw, unsynchronized pointer into the same shared mmap region. This is structurally the same bug class as the referenced Optimism `MemoryStateDB` finding: a shared mutable data structure guarded by a lock for writers while a reader path accesses the same memory without acquiring (or otherwise synchronizing with) that lock.

### Finding Description
`open_file` creates a single `MMapFile<V>` wrapped in `Arc<Mutex<MMapFile<V>>>` and hands out both a `FileHandler<V, RW>` (writer) and `FileHandler<V, RO>` (reader), both holding a raw `memory_ptr: *const u8` into the mmap: [1](#0-0) 

The writer path correctly locks the shared `Mutex` before mutating the mmap and advancing the append offset: [2](#0-1) 

However, `Reader::get()` — implemented for both `RW` and `RO` modes — dereferences `self.memory_ptr` directly via `std::slice::from_raw_parts`, without ever acquiring `self.mmap_file.lock()`: [3](#0-2) 

`unsafe impl Send/Sync` are manually asserted for `FileHandler`, so this type is explicitly designed to be shared/cloned across threads: [4](#0-3) 

Because the read path establishes no happens-before relationship (no lock, no atomic, no fence) with the writer's mutation of the same mapped memory, this is a data race under the Rust memory model even when reader and writer target logically disjoint byte ranges — the write is not "published" to other threads through any synchronizing operation. `FileHandlers<RW>` (used for classes, CASM, thin state diffs, transactions, transaction outputs) is exactly this pattern, and `StorageReader`/`StorageWriter` clones of these file handlers are used concurrently: block/state-diff/class writes happen on the single storage writer thread while state reads (used by the gateway, mempool re-validation, blockifier class/CASM lookups, and RPC) happen concurrently on other threads via `StorageReader::begin_ro_txn()`.

### Impact Explanation
A torn or otherwise inconsistent read of class bytecode (Sierra/CASM), state diffs, or transaction data served through this race can lead to:
- Non-deterministic or corrupted deserialization results for a declared class or CASM fetched during transaction execution (`get_compiled_class`/`get_class_definition_at`), which can produce a different computed class hash / compiled-class hash or different execution result on different nodes for the same input — i.e., honest-node divergence, and downstream wrong committed state root / block hash.
- Because the storage layer underlies both block building and Starknet OS re-execution/verification, such divergence can propagate into disagreement about the correct state commitment.

This qualifies as Medium severity per the scope rules (honest-node divergence / wrong committed root), even though it requires a race window rather than being deterministically triggerable.

### Likelihood Explanation
This is reachable purely by normal, unprivileged sequencer operation: any transaction, declared class, or L1 message that causes a storage read (e.g., a `declare` transaction or contract call needing a not-yet-cached class/CASM) can race with the ongoing storage writer thread appending new blocks/classes to the same mmap file. No malicious operator, prover, or peer behavior is required — it is an intrinsic concurrency bug in the storage layer that is exercised on every node under ordinary throughput. The actual probability of an observably corrupted read depends on precise timing/compiler-reordering behavior, which is why this is rated Medium rather than High/Critical, matching the severity assigned to the analogous upstream Optimism finding.

### Recommendation
Ensure `FileHandler::get()` (the `Reader<V>` impl) synchronizes with the writer, e.g., by acquiring `self.mmap_file.lock()` for the duration of the raw-pointer read (or by re-deriving the read pointer from the locked `MMapFile` guard, as done for `append`/`flush`/`stats`), or by switching the raw pointer read to synchronized/volatile accesses. At minimum, add an explicit acquire/release synchronization point between `append`'s mutable write and `get`'s read of the same memory to eliminate the data race, rather than relying on `unsafe impl Send + Sync` to paper over unsynchronized shared mutable access.

### Proof of Concept
1. Open a mmap file via `open_file`, obtain a `FileHandler<V, RW>` (`writer`) and a cloned `FileHandler<V, RO>` (`reader`), as done in the existing test `concurrent_reads_single_write`: [5](#0-4) 
2. Spawn one thread that repeatedly calls `writer.append(&data)` (which locks the mutex and mutates the mmap and moves the offset), and multiple reader threads that repeatedly call `reader.get(location)` at a location whose write is concurrently in flight or freshly returned, without any lock/barrier on the read side.
3. Under ThreadSanitizer/Miri, or under adversarial scheduling/compiler optimizations, the read is a data race with the writer's mutation because `get()` never takes `self.mmap_file.lock()`; this can be demonstrated by running the existing test suite for this module under a race detector (e.g., `RUSTFLAGS="-Z sanitizer=thread" cargo +nightly test -p apollo_storage mmap_file`), which should flag the concurrent unsynchronized access between `append`'s locked write and `get`'s unlocked raw-pointer read.

### Citations

**File:** crates/apollo_storage/src/mmap_file/mod.rs (L187-200)
```rust
    let shared_mmap_file = Arc::new(Mutex::new(mmap_file));

    let mut write_file_handler: FileHandler<V, RW> = FileHandler {
        memory_ptr: mmap_ptr,
        mmap_file: shared_mmap_file.clone(),
        _mode: PhantomData,
    };
    write_file_handler.grow_file_if_needed(0);

    let read_file_handler: FileHandler<V, RO> =
        FileHandler { memory_ptr: mmap_ptr, mmap_file: shared_mmap_file, _mode: PhantomData };

    Ok((write_file_handler, read_file_handler))
}
```

**File:** crates/apollo_storage/src/mmap_file/mod.rs (L211-218)
```rust
impl<V: ValueSerde, Mode: TransactionKind> Clone for FileHandler<V, Mode> {
    fn clone(&self) -> Self {
        Self { memory_ptr: self.memory_ptr, mmap_file: self.mmap_file.clone(), _mode: PhantomData }
    }
}

unsafe impl<V: ValueSerde, Mode: TransactionKind> Send for FileHandler<V, Mode> {}
unsafe impl<V: ValueSerde, Mode: TransactionKind> Sync for FileHandler<V, Mode> {}
```

**File:** crates/apollo_storage/src/mmap_file/mod.rs (L233-263)
```rust
impl<V: ValueSerde + Debug> Writer<V> for FileHandler<V, RW> {
    fn append(&mut self, val: &V::Value) -> LocationInFile {
        trace!("Inserting object: {:?}", val);
        let serialized = V::serialize(val).expect("Should be able to serialize");
        let len = serialized.len();
        let offset;
        {
            let mut mmap_file = self.mmap_file.lock().expect("Lock should not be poisoned");
            offset = mmap_file.offset;
            trace!("Inserting object at offset: {}", offset);
            let mmap_slice = &mut mmap_file.mmap[offset..];
            mmap_slice[..len].copy_from_slice(&serialized);
            mmap_file
                .mmap
                .flush_async_range(offset, len)
                .expect("Failed to asynchronously flush the mmap after inserting");
            mmap_file.offset += len;
            mmap_file.should_flush = true;
        }
        let location = LocationInFile { offset, len };
        self.grow_file_if_needed(location.next_offset());
        location
    }

    fn flush(&self) {
        let mut mmap_file = self.mmap_file.lock().expect("Lock should not be poisoned");
        if mmap_file.should_flush {
            mmap_file.flush();
        }
    }
}
```

**File:** crates/apollo_storage/src/mmap_file/mod.rs (L265-278)
```rust
impl<V: ValueSerde, Mode: TransactionKind> Reader<V> for FileHandler<V, Mode> {
    /// Returns an object from the file.
    fn get(&self, location: LocationInFile) -> MmapFileResult<Option<V::Value>> {
        trace!("Reading object at location: {:?}", location);
        let mut bytes = unsafe {
            std::slice::from_raw_parts(
                self.memory_ptr.offset(location.offset.try_into()?),
                location.len,
            )
        };
        trace!("Deserializing object: {:?}", bytes);
        Ok(V::deserialize(&mut bytes))
    }
}
```

**File:** crates/apollo_storage/src/mmap_file/mmap_file_test.rs (L80-124)
```rust
#[test]
fn concurrent_reads_single_write() {
    let dir = tempdir().unwrap();
    let offset = 0;
    let (mut writer, reader) = open_file::<NoVersionValueWrapper<Vec<u8>>>(
        get_mmap_file_test_config(),
        dir.path().to_path_buf().join("test_concurrent_reads_single_write"),
        offset,
    )
    .unwrap();
    let first_data = vec![1, 2, 3];
    let second_data = vec![3, 2, 1];
    let first_location = writer.append(&first_data);
    writer.flush();
    let second_location =
        LocationInFile { offset: first_location.next_offset(), len: first_location.len };

    let n = 10;
    let barrier = Arc::new(std::sync::Barrier::new(n + 1));
    let mut handles = Vec::with_capacity(n);

    for _ in 0..n {
        let reader = reader.clone();
        let reader_barrier = barrier.clone();
        let first_data = first_data.clone();
        handles.push(std::thread::spawn(move || {
            assert_eq!(reader.get(first_location).unwrap().unwrap(), first_data);
            reader_barrier.wait();
            // readers wait for the writer to write the value.
            reader_barrier.wait();
            reader.get(second_location).unwrap()
        }));
    }
    // Writer waits for all readers to read the first value.
    barrier.wait();
    writer.append(&second_data);
    writer.flush();
    // Allow readers to proceed reading the second value.
    barrier.wait();

    for handle in handles {
        let res = handle.join().unwrap().unwrap();
        assert_eq!(res, second_data);
    }
}
```
