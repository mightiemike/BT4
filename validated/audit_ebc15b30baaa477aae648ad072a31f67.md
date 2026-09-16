### Title
Unsynchronized raw-pointer reads race with mutex-guarded mmap writes in `FileHandler` - (File: `crates/apollo_storage/src/mmap_file/mod.rs`)

### Summary
`apollo_storage`'s append-only mmap-backed storage (used for contract classes, CASM, deprecated classes, thin state diffs, and transactions) implements a manual `unsafe impl Send`/`unsafe impl Sync` for `FileHandler<V, Mode>` that shares a raw pointer (`memory_ptr`) into the mapped memory across threads. Unlike all mutations to that memory, which go through `Arc<Mutex<MMapFile<V>>>`, reads performed via `Reader::get()` dereference `memory_ptr` directly and never take the lock, creating unsynchronized concurrent access to the same underlying `MmapMut` allocation that a writer thread is actively mutating — the same class of bug as GHSA-wr55-mf5c-hhwm (an unsound `Sync` bound that allows a real data race to shared memory).

### Finding Description
`FileHandler<V, Mode>` stores both a raw pointer `memory_ptr: *const u8` and a `mmap_file: Arc<Mutex<MMapFile<V>>>` handle to the same mapped file, and is unconditionally declared `Send`/`Sync` via unsafe impls: [1](#0-0) 

The writer path takes the mutex and mutates the mmap buffer in place: [2](#0-1) 

But the reader path bypasses the mutex entirely and builds a slice straight from the shared raw pointer: [3](#0-2) 

Because `memory_ptr` was captured once at `open_file` time and is shared (via `Clone`) into every `FileHandler<V, RO>`/`FileHandler<V, RW>` instance that flows through the sequencer's `StorageReader`/`StorageWriter`, a reader thread performing `get()` on a class/state-diff/transaction record can run concurrently with a writer thread performing `append()`/`grow()` on the very same underlying allocation with no happens-before relationship between them — the mutex only serializes writer-vs-writer and writer-internal state (`offset`, `size`), not writer-vs-reader access to the mapped bytes. This is structurally the same defect pattern as the `late-static` advisory: a type is marked `Sync` (able to be shared across threads for reads) even though its actual memory access pattern is not properly synchronized with mutations, enabling a genuine data race (CWE-662).

This storage layer backs `contract_class`, `casm`, `deprecated_contract_class`, `thin_state_diff`, and `transaction` files that are populated and read while building/committing blocks: [4](#0-3) [5](#0-4) 

and it is exactly this storage that `native_blockifier` uses as the sequencer's state/class backing store during execution: [6](#0-5) 

### Impact Explanation
An attacker who submits an ordinary `DECLARE` transaction (or any transaction whose block commit appends new Sierra/CASM/state-diff bytes to these mmap files) drives concurrent writer activity on the same allocation that other in-flight reader transactions/RPC or execution threads are concurrently reading via the unsynchronized raw-pointer path. Because Rust's memory/aliasing model requires exclusivity for the region a `&mut`-derived write goes through, and here a raw pointer is read concurrently with no synchronization edge to that write, the compiler and CPU are both permitted to reorder, tear, or otherwise corrupt the observed bytes. A reader that races with a writer can observe stale/inconsistent bytes for a class or state-diff object, which upon deserialization can differ from what another honest node observes for the same object (since the race is timing-dependent per-process), leading to node-local corruption of contract class/CASM bytecode or state-diff content used in execution or state-root computation — producing honest-node divergence in computed state roots/block hashes, or non-deterministic panics that can halt block building on the affected node.

### Likelihood Explanation
Any unprivileged actor can trigger writer activity on these files simply by sending declare/invoke transactions that get included in blocks, and reader activity happens continuously for normal RPC/execution/gateway class lookups against the same storage. The race window exists on every block commit that appends objects to `contract_class`, `casm`, `deprecated_contract_class`, `thin_state_diff`, or `transaction` files while any other thread concurrently calls `Reader::get()` on the same `FileHandlers` instance, which is the normal operating mode of `StorageReader`/`StorageWriter` running in parallel. No special privileges, timing precision beyond ordinary concurrent load, or op access are required, though observing the corruption manifest deterministically (a torn read producing observably different, still-successfully-deserialized bytes) is probabilistic rather than guaranteed on every access.

### Recommendation
Remove the raw-pointer bypass: have `Reader::get()` acquire the same `Mutex<MMapFile<V>>` used by the writer (or otherwise establish an explicit happens-before relationship, e.g. via an atomic "committed offset" watermark with `Acquire`/`Release` ordering) before constructing the slice, instead of relying on `memory_ptr` captured outside of any synchronization. Re-audit the `unsafe impl Send`/`unsafe impl Sync for FileHandler` to ensure the soundness argument actually matches the access pattern, and add a Miri/loom-based concurrency test exercising concurrent `append()`/`get()` to catch data races going forward.

### Proof of Concept
1. Open storage via `apollo_storage::open_storage` and split the returned `(StorageReader, StorageWriter)`.
2. Spawn a writer thread that repeatedly calls `FileHandlers::append_contract_class`/`append_casm` (simulating block commits caused by attacker-submitted `DECLARE` transactions).
3. Spawn concurrent reader threads that repeatedly call `ClassStorageReader::get_class_from_location`/`get_class` for previously committed locations (simulating normal RPC/execution class lookups).
4. Run under Miri with strict-provenance/aliasing checks (or loom) — the reader's `std::slice::from_raw_parts` in `Reader::get` (`crates/apollo_storage/src/mmap_file/mod.rs:269-274`) races with the writer's `&mut mmap_file.mmap[offset..]` mutation (`mod.rs:243-244`) with no synchronizing lock/atomic on the read side, which Miri flags as a data race / undefined behavior.

### Citations

**File:** crates/apollo_storage/src/mmap_file/mod.rs (L202-218)
```rust
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

**File:** crates/apollo_storage/src/lib.rs (L871-882)
```rust
#[derive(Clone, Debug)]
struct FileHandlers<Mode: TransactionKind> {
    // TODO(Yoav): Try removing derive(Clone) from the inner types.
    thin_state_diff: FileHandler<VersionZeroWrapper<ThinStateDiff>, Mode>,
    contract_class: FileHandler<VersionZeroWrapper<SierraContractClass>, Mode>,
    casm: FileHandler<VersionZeroWrapper<CasmContractClass>, Mode>,
    deprecated_contract_class: FileHandler<VersionZeroWrapper<DeprecatedContractClass>, Mode>,
    transaction_output: FileHandler<VersionZeroWrapper<TransactionOutput>, Mode>,
    transaction: FileHandler<VersionZeroWrapper<Transaction>, Mode>,
    #[cfg(feature = "os_input")]
    accessed_keys: FileHandler<VersionZeroWrapper<AccessedKeys>, Mode>,
}
```

**File:** crates/apollo_storage/src/lib.rs (L1022-1057)
```rust
fn open_storage_files(
    db_config: &DbConfig,
    mmap_file_config: MmapFileConfig,
    db_reader: DbReader,
    file_offsets_table: &TableIdentifier<OffsetKind, NoVersionValueWrapper<usize>, SimpleTable>,
) -> StorageResult<(FileHandlers<RW>, FileHandlers<RO>)> {
    let db_transaction = db_reader.begin_ro_txn()?;
    let table = db_transaction.open_table(file_offsets_table)?;

    // Opens a single mmap file, returning (writer, reader) handles.
    // $name: string literal used as the file name without the `.dat` suffix.
    // $kind: OffsetKind variant.
    macro_rules! open_storage_file {
        ($name:literal, $kind:ident) => {{
            let offset = table.get(&db_transaction, &OffsetKind::$kind)?.unwrap_or_default();
            open_file(
                mmap_file_config.clone(),
                db_config.path().join(concat!($name, ".dat")),
                offset,
            )
        }};
    }

    let (thin_state_diff_writer, thin_state_diff_reader) =
        open_storage_file!("thin_state_diff", ThinStateDiff)?;
    let (contract_class_writer, contract_class_reader) =
        open_storage_file!("contract_class", ContractClass)?;
    let (casm_writer, casm_reader) = open_storage_file!("casm", Casm)?;
    let (deprecated_contract_class_writer, deprecated_contract_class_reader) =
        open_storage_file!("deprecated_contract_class", DeprecatedContractClass)?;
    let (transaction_output_writer, transaction_output_reader) =
        open_storage_file!("transaction_output", TransactionOutput)?;
    let (transaction_writer, transaction_reader) = open_storage_file!("transaction", Transaction)?;
    #[cfg(feature = "os_input")]
    let (accessed_keys_writer, accessed_keys_reader) =
        open_storage_file!("accessed_keys", AccessedKeys)?;
```

**File:** crates/native_blockifier/src/storage.rs (L51-78)
```rust
impl PapyrusStorage {
    pub fn new(config: StorageConfig) -> NativeBlockifierResult<PapyrusStorage> {
        log::debug!("Initializing Blockifier storage...");
        let db_config = DbConfig {
            path_prefix: config.path_prefix,
            enforce_file_exists: config.enforce_file_exists,
            chain_id: config.chain_id,
            min_size: 1 << 20, // 1MB.
            max_size: config.max_size,
            growth_step: 1 << 26, // 64MB.
            max_readers: 1 << 13, // 8K readers
        };
        let storage_config = ApolloStorageConfig {
            db_config,
            scope: StorageScope::StateOnly, // Only stores blockifier-related data.
            // Storage for large objects (state-diffs, contracts). This sets total storage
            // allocated, maximum space an object can take, and how fast the storage grows.
            mmap_file_config: MmapFileConfig {
                max_size: 1 << 40,        // 1TB
                growth_step: 2 << 30,     // 2GB
                max_object_size: 1 << 30, // 1GB
            },
        };
        let (reader, writer) = apollo_storage::open_storage(storage_config)?;
        log::debug!("Initialized Blockifier storage.");

        Ok(PapyrusStorage { reader: Some(reader), writer: Some(writer) })
    }
```
