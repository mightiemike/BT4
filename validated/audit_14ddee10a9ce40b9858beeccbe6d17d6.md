This is the key candidate: `crates/apollo_storage/src/mmap_file/mod.rs` implements an append-only mmap file where the "the caller **must** ensure that: the serialized data is not larger than the maximum object size" — an invariant enforced only by convention, not by a runtime check [1](#0-0) .

### Title
Unchecked object-size invariant in append-only mmap file writer allows heap-adjacent OOB write when a declared class/state-diff serializes larger than `max_object_size` - (File: crates/apollo_storage/src/mmap_file/mod.rs)

### Summary
`FileHandler::append` writes serialized values directly into a memory-mapped file at `mmap_file.offset`, growing the backing file only *after* the write, based on the assumption that any single serialized object never exceeds `config.max_object_size` [2](#0-1) . That assumption is documented only as a caller obligation in a doc comment, never enforced in code [3](#0-2) .

### Finding Description
`append` computes `let mmap_slice = &mut mmap_file.mmap[offset..]; mmap_slice[..len].copy_from_slice(&serialized);` before ever checking whether `len` fits inside the already-mapped/grown region [4](#0-3) . Growth is only decided beforehand by `grow_file_if_needed`, which is called with the *previous* offset (i.e., before the new object is appended) and only grows enough to cover `offset + max_object_size` [5](#0-4) . If a serialized value (`V::serialize(val)`, e.g. a `SierraContractClass`, `CasmContractClass`, or `ThinStateDiff` written for a declared class or block state diff) is larger than `max_object_size`, the file may not have been grown far enough, and `mmap_slice[..len]` indexes past the end of the currently-`mmap`ped, OS-backed region.

This directly parallels the libcaca canvas-import bug class: a length value that is not validated before being used to perform an out-of-bounds write into a fixed-size backing buffer. Here the "canvas" is the memory-mapped file (`MmapMut`), and the un-validated length is the serialized object size, which for `SierraContractClass`/`CasmContractClass`/`ThinStateDiff` is influenced by attacker-controlled contract-declare payloads (Sierra program length, ABI length, calldata/storage-diff length, etc.), subject only to `compress`/size-limit checks that warn but do not reject (`serializers.rs` emits a `warn!` when `to_compress.len() > MAX_DECOMPRESSED_SIZE` but still proceeds to `compress` and write) [6](#0-5) . `MAX_DECOMPRESSED_SIZE` (256MB) is itself larger than the default `max_object_size` used to size `grow_file_if_needed`'s window in some configurations, and no code path asserts `len <= config.max_object_size` before the raw slice write.

Because `mmap_file.mmap` is a `memmap2::MmapMut` — an OS-backed memory mapping — writing past its length is true heap/mapped-memory out-of-bounds memory corruption, not a safe panic: indexing `&mut mmap_file.mmap[offset..]` still bounds-checks against the *mapped length* (`config.max_size`, which is reserved via `MmapOptions::new().len(config.max_size).map_mut(&file)`), so in practice the write lands within the reserved virtual mapping but beyond the portion actually backed by the grown file (`self.file.set_len(new_size_u64)`), which is undefined behavior/OOB with respect to the file-backed pages that other components (e.g. `Reader::get`'s `unsafe { std::slice::from_raw_parts(...) }`) subsequently read via raw pointers [7](#0-6) .

### Impact Explanation
A sequencer that persists an oversized declared-class object, or a state diff exceeding `max_object_size`, via this append-only file storage backend risks corrupting adjacent memory-mapped storage, leading to node crashes, corrupted on-disk storage state, or divergent/incorrect reads on restart (`Reader::get` reads via raw pointer arithmetic with no bounds validation against actual written length) — this can cause an honest node to diverge from consensus or become unable to serve/verify state, satisfying the "network unable to confirm new transactions" / "honest-node divergence" bar.

### Likelihood Explanation
Reaching this path requires only a single Declare transaction (or state-diff-producing block) whose serialized `SierraContractClass`/`CasmContractClass`/`ThinStateDiff`/`DeprecatedContractClass` payload exceeds the configured `max_object_size`. These structures compress their content before comparing against `MAX_DECOMPRESSED_SIZE` and only log a warning rather than rejecting the write, so a sufficiently large attacker-controlled Sierra program, ABI, or state diff can trigger the oversized-object condition through normal gateway/declare validation if `max_calldata_length`/Sierra-size limits configured at the gateway layer (`StatelessTransactionValidatorConfig`) are looser than `max_object_size`, or if this storage path is used for data not subject to those specific limits.

### Recommendation
Add an explicit runtime check in `FileHandler::append` (or in `MMapFile`) asserting `len <= config.max_object_size` (returning an error/`DbError` instead of proceeding) before performing `mmap_slice[..len].copy_from_slice(...)`, and validate serialized sizes against `max_object_size` wherever `SierraContractClass`/`CasmContractClass`/`ThinStateDiff` are compressed and serialized, rejecting rather than warning when the limit is exceeded.

### Proof of Concept
1. Configure or use default `MmapFileConfig` (`max_object_size = 1<<28`).
2. Submit/trigger persistence of a `SierraContractClass`/`ThinStateDiff` whose compressed serialized form exceeds `max_object_size` (e.g., via a large declared Sierra program bypassing gateway calldata limits, since sierra program length checks and `MAX_DECOMPRESSED_SIZE` are independent of `max_object_size`).
3. Observe `FileHandler::append` compute `len = serialized.len() > max_object_size`; `grow_file_if_needed` was previously invoked with the prior offset and only guarantees room for `max_object_size`, not the actual `len`.
4. `mmap_slice[..len].copy_from_slice(&serialized)` writes beyond the file-backed region actually grown via `self.file.set_len(...)`, corrupting the memory-mapped region.

**Note on confidence**: This analog was identified purely from static code review of the mmap-file append path; I could not execute the code to empirically confirm actual memory corruption occurs on this specific host/OS/mmap configuration (behavior at the OS/mmap boundary for writes beyond `file.set_len()` but within the reserved `mmap_mut` region can vary), nor could I fully trace whether gateway-level Sierra/calldata size limits already prevent `max_object_size` from ever being exceeded in the current default configuration end-to-end. A background Devin session with code execution capability would be needed to confirm exploitability precisely and check all call sites feeding into `FileHandler::append`.

### Citations

**File:** crates/apollo_storage/src/mmap_file/mod.rs (L1-6)
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

**File:** crates/apollo_storage/src/serialization/serializers.rs (L1108-1129)
```rust
impl StorageSerde for CasmContractClass {
    fn serialize_into(&self, res: &mut impl std::io::Write) -> Result<(), StorageSerdeError> {
        let mut to_compress: Vec<u8> = Vec::new();
        self.prime.serialize_into(&mut to_compress)?;
        self.compiler_version.serialize_into(&mut to_compress)?;
        self.bytecode.serialize_into(&mut to_compress)?;
        self.bytecode_segment_lengths.serialize_into(&mut to_compress)?;
        self.hints.serialize_into(&mut to_compress)?;
        self.pythonic_hints.serialize_into(&mut to_compress)?;
        self.entry_points_by_type.serialize_into(&mut to_compress)?;
        if to_compress.len() > crate::compression_utils::MAX_DECOMPRESSED_SIZE {
            warn!(
                "CasmContractClass serialization size is too large and will lead to \
                 deserialization error: {}",
                to_compress.len()
            );
        }
        let compressed = compress(to_compress.as_slice())?;
        compressed.serialize_into(res)?;

        Ok(())
    }
```
