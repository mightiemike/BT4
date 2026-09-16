### Title
Unbounded object write into memory-mapped storage file can exceed reserved backing size, causing a SIGBUS write-access violation - (File: `crates/apollo_storage/src/mmap_file/mod.rs`)

### Summary
`FileHandler::append` in `apollo_storage`'s mmap-file backend writes a serialized value directly into an `mmap`ped region at the current offset, but the code only pre-grows the backing file to cover `max_object_size` bytes past the *previous* offset — it never checks that the actual serialized object (`len`) is smaller than `max_object_size` before performing the write. This mirrors the CVE-2017-14259 pattern in Bento4's `AP4_StscAtom`: a write happens into a buffer sized according to an assumed/derived bound rather than the actual data size, producing a write memory-access violation.

### Finding Description
`append` computes `len = serialized.len()` for the value being stored and immediately writes it: [1](#0-0) 

```rust
impl<V: ValueSerde + Debug> Writer<V> for FileHandler<V, RW> {
    fn append(&mut self, val: &V::Value) -> LocationInFile {
        ...
        let serialized = V::serialize(val).expect("Should be able to serialize");
        let len = serialized.len();
        ...
        let mmap_slice = &mut mmap_file.mmap[offset..];
        mmap_slice[..len].copy_from_slice(&serialized);
        ...
        mmap_file.offset += len;
        ...
        let location = LocationInFile { offset, len };
        self.grow_file_if_needed(location.next_offset());
        location
    }
```

The mmap itself is created up front for the full configured `max_size` (e.g. 1 TB) via `MmapOptions::new().len(config.max_size).map_mut(&file)`, but the **backing file on disk** is only grown lazily to `size = size + growth_step` via `grow()`, and only in increments that assume every appended object is `<= max_object_size`: [2](#0-1) 

```rust
impl<V: ValueSerde> FileHandler<V, RW> {
    fn grow_file_if_needed(&mut self, offset: usize) {
        let mut mmap_file = self.mmap_file.lock().expect("Lock should not be poisoned");
        if mmap_file.size < offset + mmap_file.config.max_object_size {
            ...
            mmap_file.grow();
        }
    }
}
```

The module doc explicitly states this as a caller-enforced invariant rather than something the code itself checks: [3](#0-2) 

```rust
//! Interface for handling append only data that is backed up by mmap file directly.
//! Data is serialized directly into the mmap file.
//! The caller **must** ensure that:
//! * The serialized data is not larger than the maximum object size.
```

Because the virtual mapping (`mmap`) spans up to `max_size` regardless of the file's real length, writing `len` bytes at `offset` when `len > max_object_size` (the only quantity the grow logic accounts for) can write past the region of the file that was actually extended on disk. Writing into mmap pages beyond the file's true length triggers a `SIGBUS`/write access violation at the OS level — the same underlying bug class as the Bento4 `AP4_StscAtom` "Write Memory Access Violation": a write sized/located based on an assumed bound rather than a verified one.

### Impact Explanation
If any storage value type routed through this `Writer<V>` implementation can be serialized to a size exceeding its configured `max_object_size` (each stored table — transactions, classes, state diffs, headers, etc. — has its own `MmapFileConfig`), a single crafted/oversized object triggers a SIGBUS crash in the storage-writing process on every honest node that processes and persists that same block/transaction/class. Since all honest sequencer/full nodes execute the same deterministic storage path when committing a block, this is a network-wide, deterministically reproducible crash — i.e., a network liveness failure (nodes unable to confirm/persist new blocks), not merely a single-node fault.

### Likelihood Explanation
Exploitability depends entirely on whether any upstream validation (transaction size limits, calldata/bytecode size limits, bouncer weights, blob/DA chunk limits) already guarantees every value serialized into a given mmap-backed table stays under that table's `max_object_size` (default 256 MB) before this code path executes. The code base does not encode or assert this bound at the point of writing — it only documents it as a precondition — so the guarantee, if it exists, lives entirely outside this module in caller code that was not located within this investigation. I could not confirm within the available context which concrete tables/value types (transactions, classes, state diffs, etc.) are wired to this mmap backend and whether their respective upstream size checks are strictly tighter than the corresponding `max_object_size` in all deployed configs. This uncertainty limits confidence in real-world reachability by an unprivileged transaction sender.

### Recommendation
Add an explicit bounds check in `append` (or in `grow_file_if_needed`) that rejects/errors when `len > self.config.max_object_size`, and/or grow the file based on the actual `len` of the object being written rather than only the fixed `max_object_size`, before performing `copy_from_slice`. This converts the current "caller must ensure" trust assumption into an enforced invariant, eliminating the possibility of writing past the mmap's actually-backed file region regardless of what upstream size validation does or doesn't guarantee.

### Proof of Concept
Not independently verified end-to-end (would require confirming a concrete caller path from an unprivileged transaction/declare/L1-message input to a `V::Value` whose `StorageSerde::serialize_into` output exceeds the target table's configured `max_object_size` without being rejected earlier by gateway/mempool/bouncer validation). Conceptually: submit/declare data whose stored representation (e.g., a state-diff or class blob keyed to a table with a small `max_object_size`) exceeds that table's `max_object_size`; on block commit, `append` writes `len` bytes at `offset` into the mmap, potentially past the file's true grown length, causing a SIGBUS on every node performing the write.

---
**Note on confidence**: I was not able to fully trace, within the available index, which storage tables (`crates/apollo_storage/src/lib.rs` open-file call sites) use this mmap-backed `Writer` and what `MmapFileConfig` values (and corresponding upstream size validation) apply to each — this is necessary to confirm that an attacker-controlled value can actually exceed `max_object_size` end-to-end. This finding should be treated as a plausible analog requiring further verification of that specific reachability chain rather than a fully proven exploit.

### Citations

**File:** crates/apollo_storage/src/mmap_file/mod.rs (L1-5)
```rust
//! Interface for handling append only data that is backed up by mmap file directly.
//! Data is serialized directly into the mmap file.
//! The caller **must** ensure that:
//! * The serialized data is not larger than the maximum object size.
//! * New data is appended to the file (i.e, at the offset returned by the previous write).
```

**File:** crates/apollo_storage/src/mmap_file/mod.rs (L220-230)
```rust
impl<V: ValueSerde> FileHandler<V, RW> {
    fn grow_file_if_needed(&mut self, offset: usize) {
        let mut mmap_file = self.mmap_file.lock().expect("Lock should not be poisoned");
        if mmap_file.size < offset + mmap_file.config.max_object_size {
            debug!(
                "Attempting to grow file. File size: {}, offset: {}, max_object_size: {}",
                mmap_file.size, offset, mmap_file.config.max_object_size
            );
            mmap_file.grow();
        }
    }
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
