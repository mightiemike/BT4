### Title
Unchecked write into memory-mapped storage buffer before capacity growth in `FileHandler::append` - (File: crates/apollo_storage/src/mmap_file/mod.rs)

### Summary
The reported `pnet_packet` bug class is a buffer overrun where a `set_payload`-style setter copies attacker/caller-influenced data into a buffer without first validating that the buffer is large enough, relying on an unenforced invariant. The analogous pattern in this repo is `FileHandler::append` in `crates/apollo_storage/src/mmap_file/mod.rs:233-255`, which copies a serialized value into the memory-mapped file's backing buffer and only grows the file *after* the copy, while the code's own documentation states the size invariant must be enforced by the *caller*, not by `append` itself.

### Finding Description
`append` computes `len = serialized.len()`, takes a mutable slice starting at `offset` from the mmap (`mmap_file.mmap[offset..]`), and immediately calls `mmap_slice[..len].copy_from_slice(&serialized)` [1](#0-0) . Only *after* this copy does it call `self.grow_file_if_needed(location.next_offset())` [2](#0-1) , and `grow_file_if_needed` only grows when `mmap_file.size < offset + max_object_size`, i.e. it is sized to guarantee headroom for the *next* object, not validated against the size of the object just written [3](#0-2) .

The module's own doc comment states the precondition explicitly: *"The caller **must** ensure that: The serialized data is not larger than the maximum object size"* [4](#0-3) . This mirrors the `pnet_packet` `set_payload` bug class exactly: a setter that writes attacker-influenced-length data into a fixed buffer, trusting an external invariant instead of checking bounds at the write site.

The mmap itself is created with a fixed virtual mapping of `config.max_size` (e.g. up to 1TB) via `MmapOptions::new().len(config.max_size).map_mut(&file)`, but the file's actual allocated size (`self.size`) is grown incrementally via `set_len`/`grow()` [5](#0-4) . If a serialized value's length exceeds the currently backed (grown) region of the file at the time of `copy_from_slice`, the write touches mmap pages beyond the file's actual length — on Linux this triggers `SIGBUS`, crashing the storage-writing process (batcher/state-sync/class-manager) rather than a classic heap overflow, but the root defect (bounds-unchecked copy into a buffer sized by an external, unenforced invariant) is the same class of bug as the advisory.

This code path is reached from `write_classes`/`write_deprecated_classes` in `crates/apollo_storage/src/class.rs:256-293`, which call `file_handlers.append_contract_class(...)` for every declared class committed to a block [6](#0-5) , i.e. it is exercised by ordinary contract-declaration transactions that a normal transaction sender can submit.

### Impact Explanation
If the serialized+compressed size of a stored object (contract class, state diff, transaction body, etc.) exceeds the `max_object_size`/growth assumptions of its corresponding `MmapFileConfig` at the moment of `append`, the write can touch unbacked mmap pages, causing the storage-writing process to receive `SIGBUS` and crash. Because this happens during block commit (all full nodes execute the same commit path when persisting a block), a value that triggers this on one honest node would trigger it on all honest nodes processing the same block, which can crash storage processes network-wide and halt block production/confirmation — a "network unable to confirm new transactions" condition. Some size checks exist upstream (gateway's `validate_class_length` against `max_contract_class_object_size`, and class-manager's `validate_class_length` against `max_compiled_contract_class_object_size`), but these checks are performed on different serialized representations (JSON string length, or pre-storage-compression executable size) than the `StorageSerde`-compressed bytes actually appended to the mmap file, and I could not confirm within the available context that the two size domains are guaranteed to stay under every configured `mmap_file_config.max_object_size` across all object kinds (classes, state diffs, headers, bodies) in every deployment configuration.

### Likelihood Explanation
Medium. The vulnerable code path (`append`) is architecturally always reached via normal block-commit logic for declared classes and other persisted objects, so no special privilege is required to reach the function. However, actually triggering an overrun requires a size mismatch between an externally-validated limit (e.g. gateway/class-manager size checks) and the internal `mmap_file_config` `max_object_size`/`growth_step` for that specific table, which I was not able to fully confirm is exploitable in the default production configuration (values like `1073741824`/`1GB` growth headroom appear far larger than default `max_compiled_contract_class_object_size` of ~4MB) — so likelihood is not "trivial" but the same unchecked-write pattern remains present in the code regardless of current config values, and future config changes or additional object types written through this API could reintroduce full exploitability.

### Recommendation
In `FileHandler::append` (`crates/apollo_storage/src/mmap_file/mod.rs`), validate `len` against `mmap_file.config.max_object_size` (returning an error rather than panicking/crashing) and call `grow_file_if_needed`/ensure the mmap is sized to accommodate `offset + len` *before* performing `copy_from_slice`, rather than growing only after the write and only with respect to future headroom. This removes the caller-trust invariant and enforces bounds at the point of the buffer write, directly analogous to the `packet` macro fix in the referenced advisory (bounds validation performed inside the setter itself instead of assumed by the caller).

### Proof of Concept
Not independently reproducible from the indexed context alone (would require constructing a `ValueSerde` value whose serialized/compressed size exceeds the specific mmap table's `max_object_size`/available grown region at append time, then committing a block containing it). Conceptually: submit a `DECLARE` transaction whose Sierra program compresses/serializes (via `StorageSerde` for `SierraContractClass`/`DeprecatedContractClass`, see `crates/apollo_storage/src/serialization/serializers.rs:1030-1084`) to a size that exceeds the storage-layer `mmap_file_config.max_object_size` configured for the classes table, bypassing the JSON-length-based gateway check (`crates/apollo_gateway/src/stateless_transaction_validator.rs:315-337`) and the class-manager's pre-compression size check (`crates/apollo_class_manager/src/class_manager.rs:157-179`), so that `write_classes` → `append_contract_class` → `FileHandler::append` copies more bytes than the currently backed mmap region, causing a `SIGBUS`/crash on all nodes committing that block.

### Citations

**File:** crates/apollo_storage/src/mmap_file/mod.rs (L1-5)
```rust
//! Interface for handling append only data that is backed up by mmap file directly.
//! Data is serialized directly into the mmap file.
//! The caller **must** ensure that:
//! * The serialized data is not larger than the maximum object size.
//! * New data is appended to the file (i.e, at the offset returned by the previous write).
```

**File:** crates/apollo_storage/src/mmap_file/mod.rs (L148-200)
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

    /// Flushes the mmap to the file.
    fn flush(&mut self) {
        trace!("Flushing mmap to file");
        self.mmap.flush().expect("Failed to flush the mmap");
        self.should_flush = false;
    }
}

/// Open a memory mapped file, create it if it doesn't exist.
#[instrument(level = "debug", err)]
pub(crate) fn open_file<V: ValueSerde>(
    config: MmapFileConfig,
    path: PathBuf,
    offset: usize,
) -> MmapFileResult<(FileHandler<V, RW>, FileHandler<V, RO>)> {
    let file = OpenOptions::new().read(true).write(true).create(true).truncate(false).open(path)?;
    let size = file.metadata()?.len();
    let mmap = unsafe { MmapOptions::new().len(config.max_size).map_mut(&file)? };
    let mmap_ptr = mmap.as_ptr();
    let mmap_file = MMapFile {
        config,
        file,
        mmap,
        size: size.try_into().expect("size should fit in usize"),
        offset,
        should_flush: false,
        _value_type: PhantomData {},
    };
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

**File:** crates/apollo_storage/src/mmap_file/mod.rs (L233-244)
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
```

**File:** crates/apollo_storage/src/mmap_file/mod.rs (L252-254)
```rust
        let location = LocationInFile { offset, len };
        self.grow_file_if_needed(location.next_offset());
        location
```

**File:** crates/apollo_storage/src/class.rs (L256-269)
```rust
fn write_classes<'env>(
    classes: &[(ClassHash, &SierraContractClass)],
    txn: &DbTransaction<'env, RW>,
    declared_classes_table: &'env DeclaredClassesTable<'env>,
    file_handlers: &FileHandlers<RW>,
    file_offset_table: &'env FileOffsetTable<'env>,
) -> StorageResult<()> {
    for (class_hash, contract_class) in classes {
        let location = file_handlers.append_contract_class(contract_class);
        declared_classes_table.insert(txn, class_hash, &location)?;
        file_offset_table.upsert(txn, &OffsetKind::ContractClass, &location.next_offset())?;
    }
    Ok(())
}
```
