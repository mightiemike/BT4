### Title
Data race between unsynchronized raw-pointer reads and mutex-guarded mmap writes/growth in `FileHandler` — ([File: crates/apollo_storage/src/mmap_file/mod.rs])

### Summary
`FileHandler<V, Mode>` unsoundly asserts `Send`/`Sync` and reads storage data through a raw pointer (`memory_ptr`) outside of the `Mutex` that otherwise protects all mutations of the underlying `MMapFile`. This is structurally the same bug class as the actix-web advisory (unsound `Send`/`Sync` on non-thread-safe state, and unsynchronized aliasing of memory that another thread can mutate), applied to the sequencer's append-only mmap storage engine that backs blocks, headers, state diffs, and classes.

### Finding Description
`FileHandler` is defined as:
```rust
pub(crate) struct FileHandler<V: ValueSerde, Mode: TransactionKind> {
    memory_ptr: *const u8,
    mmap_file: Arc<Mutex<MMapFile<V>>>,
    _mode: PhantomData<Mode>,
}

unsafe impl<V: ValueSerde, Mode: TransactionKind> Send for FileHandler<V, Mode> {}
unsafe impl<V: ValueSerde, Mode: TransactionKind> Sync for FileHandler<V, Mode> {}
``` [1](#0-0) 

The `Writer::append` path takes the `Mutex` lock, writes into `mmap_file.mmap`, advances `offset`, and (outside the lock) may trigger `grow_file_if_needed`, which locks again and calls `MMapFile::grow`, extending the backing file's length: [2](#0-1) [3](#0-2) 

In contrast, `Reader::get` dereferences `self.memory_ptr` directly via `std::slice::from_raw_parts`, **without ever acquiring `mmap_file`'s `Mutex`**:
```rust
impl<V: ValueSerde, Mode: TransactionKind> Reader<V> for FileHandler<V, Mode> {
    fn get(&self, location: LocationInFile) -> MmapFileResult<Option<V::Value>> {
        let mut bytes = unsafe {
            std::slice::from_raw_parts(
                self.memory_ptr.offset(location.offset.try_into()?),
                location.len,
            )
        };
        Ok(V::deserialize(&mut bytes))
    }
}
``` [4](#0-3) 

Because the region beyond the current file length is mapped virtual memory that is not yet backed by real file pages until `MMapFile::grow` extends `file.set_len(..)`, a reader can race a concurrent writer that is in the middle of copying serialized bytes into the mmap (`mmap_slice[..len].copy_from_slice(&serialized)`) or is in the middle of `grow()` (which calls `flush()` then `file.set_len()`), all without any synchronization with the reader thread. This mirrors exactly the reported bug class: unsound `Send`/`Sync` markers on shared mutable state, and unsynchronized aliasing (a reader observing a mutable buffer that a writer is concurrently mutating, i.e., a data race under Rust's memory model, and under the OS a potential access to not-yet-backed mapped pages).

### Impact Explanation
- A reader thread can observe a **torn read** (partially-written serialized bytes) for an object whose write is concurrently in progress, causing `V::deserialize` to either fail or, worse, silently succeed on garbage bytes, since the deserializer only sees a byte slice with no validity check tied to the concurrent write.
- Because `apollo_storage`'s mmap-backed tables are read by multiple components (e.g., serving RPC / sync data, feeding the block-building and state-commitment pipeline) while the writer path (block commit) is concurrently appending, this can lead to different nodes/threads observing different (corrupted) values for the same logical record depending on timing — a form of **honest-node divergence** in derived state, or a node crash (SIGBUS on pages not yet extended by `grow()`), which can prevent that node from confirming further transactions.
- This is a native memory-safety/data-race issue (CWE-362), the same class flagged in the actix-web advisory, now present in the storage layer that ultimately underlies committed chain state.

### Likelihood Explanation
Reachability requires ordinary concurrent read/write access to the same mmap-backed table, which occurs naturally as the node processes new blocks (writer path) while simultaneously serving reads for other logic (reader path) — no special privilege is needed, and the race window is a function of write volume, which an unprivileged party can influence simply by submitting transactions that get included in blocks, increasing write frequency to widen the race window. Precise, reliable exploitation (e.g., guaranteeing a specific corrupted value at a specific offset) is timing-dependent and non-deterministic, which lowers the likelihood of deterministic exploitation but does not eliminate the correctness/availability risk.

### Recommendation
- Remove the manual `unsafe impl Send`/`Sync` for `FileHandler` and instead guard all reads through the same `Mutex<MMapFile<V>>` used for writes, or use an atomic/fenced published length (e.g., an `AtomicUsize` for the current committed offset, updated with `Release` ordering after the write, and read with `Acquire` ordering before any `Reader::get` call) so reads only ever observe fully-written regions.
- Ensure `grow()`'s `file.set_len` happens-before any reader can access the newly extended region, and audit `LocationInFile` offsets passed to `get` to guarantee they never exceed the last fully-flushed/committed offset.

### Proof of Concept
Conceptual PoC (race, not deterministic single-shot exploit):
1. Thread A (writer) calls `FileHandler::append` with a large object near the current `growth_step` boundary, causing `grow_file_if_needed` → `MMapFile::grow` to run (`file.set_len` extends the file while another thread may already be mid-copy).
2. Thread B (reader), holding a `LocationInFile` for an offset near or past the pre-grow boundary, concurrently calls `Reader::get`, dereferencing `memory_ptr` without taking the `Mutex`.
3. Under a scheduler that interleaves B's raw read with A's `copy_from_slice` (or with A's `file.set_len` call before the OS has finished backing the page), B observes torn/partial bytes or faults, since `Reader::get` never synchronizes with the writer's lock at [4](#0-3) .

### Citations

**File:** crates/apollo_storage/src/mmap_file/mod.rs (L148-157)
```rust
impl<V: ValueSerde> MMapFile<V> {
    /// Grows the file by the growth step.
    fn grow(&mut self) {
        self.flush();
        let new_size = self.size + self.config.growth_step;
        let new_size_u64 = u64::try_from(new_size).expect("usize should fit in u64");
        debug!("Growing file to size: {}", new_size);
        self.file.set_len(new_size_u64).expect("Failed to set the file size");
        self.size = new_size;
    }
```

**File:** crates/apollo_storage/src/mmap_file/mod.rs (L204-218)
```rust
pub(crate) struct FileHandler<V: ValueSerde, Mode: TransactionKind> {
    memory_ptr: *const u8,
    mmap_file: Arc<Mutex<MMapFile<V>>>,
    _mode: PhantomData<Mode>,
}

// Manual impl so that V: Clone is not required.
impl<V: ValueSerde, Mode: TransactionKind> Clone for FileHandler<V, Mode> {
    fn clone(&self) -> Self {
        Self { memory_ptr: self.memory_ptr, mmap_file: self.mmap_file.clone(), _mode: PhantomData }
    }
}

unsafe impl<V: ValueSerde, Mode: TransactionKind> Send for FileHandler<V, Mode> {}
unsafe impl<V: ValueSerde, Mode: TransactionKind> Sync for FileHandler<V, Mode> {}
```

**File:** crates/apollo_storage/src/mmap_file/mod.rs (L233-255)
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
```

**File:** crates/apollo_storage/src/mmap_file/mod.rs (L265-277)
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
```
