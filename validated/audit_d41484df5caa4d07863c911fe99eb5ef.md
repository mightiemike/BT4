### Title
Unbounded serialized object write past mmap file bounds in `FileHandler::append` - (File: crates/apollo_storage/src/mmap_file/mod.rs)

### Summary
The FFmpeg CVE-2026-66040 bug class is: a size is *estimated* ahead of time to size a buffer, but the actual serialized payload written afterward can exceed that estimate, so the writer blows past the allocated region and corrupts memory. The sequencer's `apollo_storage` mmap-file layer has the same structural pattern: `FileHandler::append` (`crates/apollo_storage/src/mmap_file/mod.rs:233-255`) blindly `copy_from_slice`s a freshly-serialized object into the memory-mapped file at the current offset, relying entirely on an *undocumented, unenforced, caller-must-ensure* invariant that "the serialized data is not larger than `max_object_size`" [1](#0-0) . There is no runtime check comparing `serialized.len()` to `max_object_size` (or to remaining mapped/allocated space) before the write.

### Finding Description
`MMapFile`/`FileHandler` map a fixed virtual region of `max_size` bytes (`MmapOptions::new().len(config.max_size)`), but the file is grown on disk only incrementally, by `growth_step`, and only when `mmap_file.size < offset + mmap_file.config.max_object_size` [2](#0-1) . This growth check is a **pre-check based on `max_object_size`**, not on the actual size of the object about to be written — exactly analogous to `add_exif_profile_size()` in the FFmpeg bug being a size *estimate* used to size an allocation ahead of the real write.

The actual write path is:
```
let serialized = V::serialize(val).expect("Should be able to serialize");
let len = serialized.len();
...
let mmap_slice = &mut mmap_file.mmap[offset..];
mmap_slice[..len].copy_from_slice(&serialized);
``` [3](#0-2) 

If `len` (the real serialized size of the value being appended — `ThinStateDiff`, `SierraContractClass`, `CasmContractClass`, `DeprecatedContractClass`, `Transaction`, `TransactionOutput`, etc.) exceeds `max_object_size`, the previous `grow_file_if_needed` guarantee (`file_size >= offset + max_object_size`) is insufficient, and the write can extend past the currently backed/grown file region while still landing inside the `max_size` virtual mmap reservation. Writing to mmap pages beyond the file's actual on-disk length triggers `SIGBUS`/undefined behavior in the writer process (the batcher/central-sync/native-blockifier process performing block building or sync), i.e. a heap/mmap-backed out-of-bounds write reachable purely by the content of a value that a single transaction, declared class, or state diff produces.

None of the size guards found upstream are wired to `max_object_size` (default `1 << 28` = 256 MiB, see `MmapFileConfig::default()` at `crates/apollo_storage/src/mmap_file/mod.rs:73-81`):
- Gateway calldata/signature length limits (`max_calldata_length`, `max_signature_length`) bound `Transaction`/`TransactionOutput` size, but are independent config knobs from `max_object_size` and not enforced against it.
- Sierra→CASM compilation bounds `max_bytecode_size` (felts) via `SierraCompilationConfig` (`crates/apollo_sierra_compilation_config/src/config.rs`) and class-manager checks `max_compiled_contract_class_object_size` in `ClassManager::validate_class_length` (`crates/apollo_class_manager/src/class_manager.rs:157-179`) — but this validation happens in the **Class Manager** component, a *separate* service from the storage/batcher/central-sync writers that actually call `append_casm`/`append_classes` (see `crates/apollo_storage/src/compiled_class.rs:145-165`, `crates/apollo_batcher/src/batcher.rs:1957-1978`, `crates/apollo_central_sync/src/lib.rs:603-643`, `crates/native_blockifier/src/storage.rs:209-241`). Those storage-writing paths do not themselves re-validate serialized size against `max_object_size` before calling `FileHandlers::append_*` / `FileHandler::append`.
- `ThinStateDiff`/other types compare against `MAX_DECOMPRESSED_SIZE` only to emit a `warn!` log, not to reject or bound the write (`crates/apollo_storage/src/serialization/serializers.rs:1159-1165`).

So there is no guaranteed, storage-layer-local bound tying the actual bytes written by `FileHandler::append` to `max_object_size` — the module's own doc-comment concedes this is a caller obligation, not an enforced invariant, which is the same "size estimate vs. actual serialized size" gap the FFmpeg advisory describes.

### Impact Explanation
A successful trigger causes a memory-safety violation (SIGBUS/heap corruption) in the process performing block building, sync, or offline execution (batcher / central-sync / native_blockifier), i.e. any honest sequencer node processing the same transaction/class/state-diff deterministically hits the same fault. This satisfies the "network unable to confirm new transactions" / crash-affecting-all-honest-nodes bar: a single malicious declared class, transaction, or induced large state diff can deterministically crash every node that reaches the corresponding `append_*` call, halting block production/liveness. Depending on how the OOB write lands relative to adjoining mmap-mapped structures, it can also manifest as heap corruption rather than a clean SIGBUS, which is undefined behavior and could, in principle, be leveraged beyond a crash.

### Likelihood Explanation
Exploitability depends on whether any code path actually allows a serialized `ThinStateDiff`, `Transaction`, `TransactionOutput`, `SierraContractClass`, `DeprecatedContractClass`, or `CasmContractClass` to exceed `max_object_size` (256 MiB default) before reaching `FileHandler::append`. I was not able to fully confirm, within available tool budget, that every one of these object kinds is bounded below `max_object_size` end-to-end by upstream gateway/bouncer/class-manager checks in every deployment topology (e.g., `native_blockifier` and `apollo_central_sync` write CASM/state-diffs directly to storage without going through the Class Manager's `validate_class_length`). Given the layered architecture and that `max_object_size` is an operator-configurable, storage-local parameter never cross-checked against gateway/bouncer/class-manager limits, I assess this as a plausible but not fully proven analog — likelihood is **medium** pending confirmation of whether any reachable object type can exceed `max_object_size` in a real deployment.

### Recommendation
Add an explicit runtime check in `FileHandler::append` (or in `MMapFile`) that rejects/errors when `serialized.len() > config.max_object_size`, rather than relying on the doc-comment's "caller must ensure" contract. Additionally, audit every direct caller of `append_casm`, `append_classes`, `append_state_diff`, `append_transaction`, and `append_transaction_output` (batcher, central-sync, native_blockifier, state-sync) to confirm each object type is provably bounded below `max_object_size` before reaching the storage layer, and make that bound explicit/enforced at the boundary rather than implicit across multiple independently-configured limits (`max_calldata_length`, `max_bytecode_size`, `max_compiled_contract_class_object_size`, `max_object_size`).

### Proof of Concept
Not fully constructible from static analysis alone: a concrete PoC requires confirming a code path where a transaction, declared class, or resulting state diff can be crafted (bypassing bytecode/calldata length limits, or via compilation size blow-up) such that its `StorageSerde`/`ValueSerde` serialization exceeds `max_object_size` (256 MiB default / operator-configured value) before calling the corresponding `append_*` function in `apollo_storage`. This would need to be validated by attempting to compile/declare a class or submit a transaction whose serialized storage representation approaches or exceeds the configured `max_object_size`, then observing whether `FileHandler::append` writes past the currently grown file/mmap region.

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

**File:** crates/apollo_storage/src/mmap_file/mod.rs (L234-249)
```rust
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
```
