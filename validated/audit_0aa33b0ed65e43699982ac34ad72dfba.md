### Title
Unenforced size invariant in mmap-backed storage append path can cause out-of-bounds write / crash - (File: `crates/apollo_storage/src/mmap_file/mod.rs`)

### Summary
`apollo_storage`'s append-only storage backend memory-maps a file with `unsafe { MmapOptions::new().len(config.max_size).map_mut(&file)? }` and then writes serialized objects directly into the map via raw slice indexing, relying entirely on an *undocumented-in-code, comment-only* invariant that "the serialized data is not larger than the maximum object size." [1](#0-0)  No code in `FileHandler::append` actually validates `serialized.len() <= config.max_object_size` before writing. [2](#0-1) 

### Finding Description
`open_file` maps a `max_size` region (1 TB by default) over a file whose *actual* on-disk length (`mmap_file.size`) is grown incrementally by `growth_step` (1 GB default), always kept at least `max_object_size` (256 MB default) ahead of the current write offset. [3](#0-2)  This "grow one step ahead" scheme is only correct if every object written through `append` is guaranteed to be `<= max_object_size` bytes when serialized — a precondition documented only in a comment, not enforced in code:

```
fn append(&mut self, val: &V::Value) -> LocationInFile {
    let serialized = V::serialize(val).expect("Should be able to serialize");
    let len = serialized.len();
    ...
    let mmap_slice = &mut mmap_file.mmap[offset..];
    mmap_slice[..len].copy_from_slice(&serialized);
    ...
    mmap_file.offset += len;
    ...
    self.grow_file_if_needed(location.next_offset());
``` [4](#0-3) 

If any value stored through this path serializes to more than `max_object_size` bytes, the `copy_from_slice` writes past the region that `grow_file_if_needed` guaranteed was backed by the underlying file, into pages of the `mmap` that are within the reserved 1 TB virtual mapping but not yet extended on disk. On Linux this manifests as either silent corruption of adjacent/reserved mapping state or a `SIGBUS` crash when writing past the file's real extent — the direct analog of the CVE's heap-based buffer overflow triggered by an oversized/malformed payload, except here the "packet" is any storage object (transaction, receipt, contract class, block header, etc.) whose size the gateway/mempool/batcher pipeline does not bound to `max_object_size` before it reaches storage. The `Reader::get` implementation has a matching unchecked raw-pointer read (`std::slice::from_raw_parts`) that trusts a previously stored `LocationInFile` without re-validating bounds. [5](#0-4) 

### Impact Explanation
A successful trigger crashes the storage subsystem of any sequencer/full node process that persists the offending object (batcher, gateway-adjacent storage, sync), which is a hard process crash rather than a graceful error — consistent with "network unable to confirm new transactions" if it hits nodes broadly (e.g., via a class, transaction field, or proof artifact that every node must persist identically). This is a memory-safety violation (`unsafe` mmap + unchecked slice write), not merely a panic, so worst case is undefined behavior/memory corruption in the storage process, not just a controlled error return.

### Likelihood Explanation
I could not, within the available search budget, conclusively trace an end-to-end path proving that an unprivileged transaction sender can produce a storage object whose serialized size exceeds the configured `max_object_size` (default 256 MB) — this would require confirming that no upstream size cap (gateway/mempool/stateless validator length checks on calldata, signature, paymaster_data, account_deployment_data, `Proof`/`ProofFacts`, or Sierra program size) is strictly tighter than every storage-layer `max_object_size` for every persisted type across all node configurations. Given the very large default `max_object_size` (256 MB) relative to typical transaction/class size limits, the likelihood under default configuration is low; it would be materially higher only under misconfiguration (a smaller `max_object_size` than the gateway's size limits) or for object types without an enforced upstream size cap.

### Recommendation
- Add an explicit runtime check in `FileHandler::append` that rejects (returns an error rather than silently writing) any `serialized.len() > config.max_object_size`, instead of relying on a caller-side comment.
- Consider bounds-checking `Reader::get` against `mmap_file.size`/mapping length before constructing the raw slice.
- Audit all `ValueSerde` implementors persisted through this path (especially newer variable-length fields such as `Proof`/`ProofFacts`, `Calldata`, `AccountDeploymentData`) to confirm gateway/mempool-side size limits are always strictly less than every relevant `max_object_size` in every deployed configuration.

### Proof of Concept
Not independently verified end-to-end (would require constructing a transaction/class whose stored serialized representation exceeds the configured `max_object_size` and observing a SIGBUS/crash in the storage process); the root-cause code path (missing bounds check before the raw `copy_from_slice` into an `unsafe`-mapped region) is confirmed by direct code inspection at the cited lines.

**Uncertainty note:** This finding is based on a confirmed code-level gap (unenforced size invariant around `unsafe` mmap writes) but I was not able to verify, within this session's tool budget, whether any concrete upstream (gateway/mempool) transaction field lacks a size cap tighter than `max_object_size` for every object type persisted via this path. If such a cap already exists everywhere it needs to, this issue is a defense-in-depth/robustness gap rather than an exploitable one under default configuration.

### Citations

**File:** crates/apollo_storage/src/mmap_file/mod.rs (L1-5)
```rust
//! Interface for handling append only data that is backed up by mmap file directly.
//! Data is serialized directly into the mmap file.
//! The caller **must** ensure that:
//! * The serialized data is not larger than the maximum object size.
//! * New data is appended to the file (i.e, at the offset returned by the previous write).
```

**File:** crates/apollo_storage/src/mmap_file/mod.rs (L148-230)
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

/// A wrapper around `MMapFile` that provides both write and read interfaces.
#[derive(Debug)]
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
