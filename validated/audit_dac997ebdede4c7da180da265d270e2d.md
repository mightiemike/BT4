### Title
Unchecked object size vs. `max_object_size` invariant leads to out-of-bounds mmap write in append-only storage - (File: `crates/apollo_storage/src/mmap_file/mod.rs`)

### Summary
The CVE analog is a class of bugs where a low-level buffer-writing routine trusts an undocumented/unenforced size invariant instead of validating it, letting attacker-influenced data overflow the destination buffer. `bit_write_TF` in LibreDWG wrote bytes into a buffer without validating that the input fit, causing a heap over-read/overflow on crafted input. The Starknet sequencer's memory-mapped append-only storage layer (`apollo_storage::mmap_file`) has the same root-cause pattern: `FileHandler::append` copies a serialized object into the mmap'd file based purely on the caller-supplied invariant that "the serialized data is not larger than the maximum object size," without ever checking `serialized.len()` against `MmapFileConfig::max_object_size` before/while writing.

### Finding Description
`MMapFile`/`FileHandler` implement an append-only, memory-mapped storage format used to persist large objects (e.g., contract classes/CASM, transactions, state diffs) written by the node as part of normal write-path processing [1](#0-0) .

The file's growth logic only guarantees enough space for the *next* append up to `max_object_size`, checked *before* an append happens, based on the previous object's end offset: [2](#0-1) 

The actual write, however, never checks the real `serialized.len()` against `max_object_size` — it simply slices into the underlying mmap and copies raw bytes: [3](#0-2) 

The mmap itself is created with a fixed virtual size of `config.max_size` (e.g. 1TB by default) via `MmapOptions::new().len(config.max_size).map_mut(&file)`, while the *physical* file backing it is grown incrementally in `growth_step` increments via `File::set_len`: [4](#0-3) 

Because `mmap_slice[..len].copy_from_slice(&serialized)` indexes directly into the mmap'd region (which is always `max_size` long in address space, regardless of the file's real length), a `len` larger than the invariant assumed by `grow_file_if_needed` can write into virtual pages that are mapped but not yet backed by real file blocks (beyond the file's true end-of-file), which is a documented cause of `SIGBUS` on Linux/mmap-backed files, or, at minimum, silently writes over data belonging to subsequent not-yet-flushed regions. The code comment at the top of the module acknowledges this is an unenforced caller responsibility: "The caller must ensure that: The serialized data is not larger than the maximum object size" [1](#0-0) , but no assertion or bounds check exists in `append` to enforce it.

### Impact Explanation
If any object serialized through this path (contract class/CASM bytecode from a declared class, transaction data, or state-diff-derived data) can be made to exceed the configured `max_object_size` (default 256MB per `MmapFileConfig::default`) [5](#0-4) , the node's storage subsystem can crash (SIGBUS on writing past the file's actual backing length) or corrupt adjacent stored data. A single node crash caused deterministically by processing an attacker-crafted class/transaction is a "network unable to confirm new transactions" class impact if it propagates to multiple/most nodes that store the same oversized artifact (e.g., a declared class that every full node must persist).

### Likelihood Explanation
Reaching this code requires an attacker to get an oversized serialized object (e.g., an unusually large declared Sierra/CASM class or transaction payload) persisted through the storage write path that ultimately calls `Writer::append`. This depends on whether upstream gateway/mempool/blockifier size limits (e.g., max contract class size, max calldata size) already cap serialized object size below `max_object_size` in all configurations. I was not able to fully trace every call site (`crates/apollo_storage/src/lib.rs`, `body/mod.rs`, `state/mod.rs`, `header.rs`) to confirm whether all production-configured limits guarantee `serialized.len() <= max_object_size` in every deployment/config combination (e.g., `class_manager_config.json`, `batcher_config.json`), so likelihood is assessed as plausible-but-unconfirmed pending a full audit of size-limit enforcement upstream of storage.

### Recommendation
Add an explicit bounds check in `FileHandler::append` that validates `serialized.len() <= self.mmap_file.lock().config.max_object_size` (returning an error/panic deterministically across all nodes, not writing OOB) before performing `copy_from_slice`, decoupling storage-layer safety from assumptions about upstream validators/gateway limits.

### Proof of Concept
Conceptual repro (would need to be executed in a Devin session against the storage crate directly, since this analysis is based on static code review only):
1. Configure a small `MmapFileConfig` with `max_object_size` set below the size of a legitimately declarable class (e.g., set `max_object_size` to 1MB in `class_manager_config.json` while still allowing declare transactions with larger casm/sierra via the gateway's own size limits — or, more directly, unit test `FileHandler::append` with a `V::Value` whose `serialize_into` output exceeds `max_object_size`).
2. Call `append` with this oversized value.
3. Observe that `grow_file_if_needed` was only sized for the previous offset + `max_object_size`, so the `copy_from_slice` write can extend past the file's true backing length within the `max_size`-sized virtual mmap, producing a `SIGBUS`/crash or silent overwrite instead of a controlled error. [3](#0-2)

### Citations

**File:** crates/apollo_storage/src/mmap_file/mod.rs (L1-6)
```rust
//! Interface for handling append only data that is backed up by mmap file directly.
//! Data is serialized directly into the mmap file.
//! The caller **must** ensure that:
//! * The serialized data is not larger than the maximum object size.
//! * New data is appended to the file (i.e, at the offset returned by the previous write).

```

**File:** crates/apollo_storage/src/mmap_file/mod.rs (L73-81)
```rust
impl Default for MmapFileConfig {
    fn default() -> Self {
        Self {
            max_size: 1 << 40,        // 1TB
            growth_step: 1 << 30,     // 1GB
            max_object_size: 1 << 28, // 256MB
        }
    }
}
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

**File:** crates/apollo_storage/src/mmap_file/mod.rs (L220-231)
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
