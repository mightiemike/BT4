### Title
Unhandled file-open failure in `SerializedClass::write_to_file` crashes the sequencer on file-descriptor exhaustion during Declare processing - ([File: crates/apollo_compile_to_casm_types/src/lib.rs])

### Summary
`SerializedClass::write_to_file` uses `.expect(...)` on the result of `OpenOptions::open()`, assuming the open call can never fail. This function is invoked on the hot path of every accepted `DECLARE` transaction, when the class manager persists a newly compiled Sierra/CASM class to disk. If the process is under file-descriptor pressure (a resource condition that can be driven up simply by legitimate/attacker-influenced spikes of concurrent Declare transactions each opening multiple files concurrently), the `open()` call returns an `Err`, and the `.expect()` panics, crashing the whole sequencer process — mirroring exactly the class of bug fixed upstream in bitcoin-cash-node's `SaveAllToDisk`.

### Finding Description
`SerializedClass::write_to_file` opens a file for writing and explicitly asserts that the open cannot fail: [1](#0-0) 

```rust
pub fn write_to_file(self, path: PathBuf) -> RawClassResult<()> {
    ...
    let file = OpenOptions::new()
        .create(true)
        .write(true)
        .truncate(true)
        .open(path)
        .expect("Failing to open file with given options is impossible");
    ...
}
```

This comment is factually wrong: `OpenOptions::open` can fail for many OS-level reasons beyond the caller's control, including `EMFILE`/`ENFILE` (process or system file-descriptor exhaustion), disk-quota/`ENOSPC`, or permission races on the containing directory.

This function is reachable from an unprivileged transaction sender through the standard Declare flow:
- `GenericGateway::add_tx` triggers Sierra→CASM compilation for `DECLARE` transactions, invoking `ClassManager::add_class`. [2](#0-1) 
- `ClassManager::add_class` calls `self.classes.set_class(...)`, which for the filesystem-backed storage implementation goes to `FsClassStorage::set_class` → `write_class_atomically`. [3](#0-2) [4](#0-3) 
- `write_class_atomically` calls `class.write_to_file(...)` and `executable_class.write_to_file(...)` for every newly declared class, each of which opens a fresh temp file via `OpenOptions::open`.

Each declared class thus consumes at least two fresh file descriptors on this path (plus whatever else the storage layer, mmap files, DB environment, and network connections are already holding open concurrently). A sequencer under sustained or bursty Declare traffic (which is entirely attacker-controllable — an unprivileged party can just submit many distinct valid Declare transactions, or trigger repeated concurrent compilations up to the `max_concurrent_declare_compilations` semaphore limit) can push the process toward its file-descriptor ulimit, especially in constrained/containerized deployments with low `nofile` limits. Once `open()` fails, `.expect()` triggers a panic.

### Impact Explanation
A panic here crashes the async task; depending on the panic strategy and executor configuration, this can escalate to crashing the sequencer's class-manager component or the whole node process. Because this is on the class-registration critical path for every Declare transaction, a single resource-exhaustion event (transient FD pressure caused by concurrent Declares, other open connections, or storage file handles) turns into an unrecoverable panic instead of a recoverable error, taking down block production/validation for the node — i.e., a liveness/availability impact ("a network unable to confirm new transactions" if it hits enough sequencers, or at minimum denial-of-service against a single sequencer/validator).

### Likelihood Explanation
Reaching this code path requires nothing more than submitting a valid `DECLARE` transaction — no privileged role is needed. Triggering the actual `open()` failure needs the process to be near its file-descriptor limit, which can occur under normal operational conditions (many concurrent RPC/P2P connections, DB/mmap file handles, concurrent compilations) or be pushed toward by an attacker flooding the node with valid Declare transactions containing distinct classes (each triggering two additional file opens). This is a rare-but-realistic operational condition, exactly as characterized in the original report ("Rarely, it would be possible for the process to run out of file descriptors").

### Recommendation
Replace the `.expect(...)` in `SerializedClass::write_to_file` with proper error propagation: convert the `OpenOptions::open()` error into `RawClassError::IoError` via `?`, matching the existing error-plumbing already used elsewhere in the same function (e.g., `create_dir_all(parent)?`). This makes a transient file-descriptor exhaustion surface as a `Result::Err` that callers (`write_class_atomically`, `ClassManager::add_class`) already handle as a recoverable per-request error, instead of crashing the process.

### Proof of Concept
1. Lower the process `nofile` ulimit (or otherwise cause the sequencer to approach its FD limit through concurrent load) so that a subsequent `open()` call is likely to fail with `EMFILE`.
2. Submit a `DECLARE` transaction for a new (previously unseen) contract class to the gateway's `add_transaction` endpoint.
3. This flows through `ClassManager::add_class` → `CachedClassStorage::set_class` → `FsClassStorage::write_class_atomically` → `SerializedClass::write_to_file`, which calls `OpenOptions::new().create(true).write(true).truncate(true).open(path)`.
4. When `open()` returns `Err(EMFILE)`, the `.expect("Failing to open file with given options is impossible")` panics, crashing the handling task/process instead of returning a `RawClassError` to the caller.

### Citations

**File:** crates/apollo_compile_to_casm_types/src/lib.rs (L113-130)
```rust
    pub fn write_to_file(self, path: PathBuf) -> RawClassResult<()> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }

        // Open a file for writing, deleting any existing content.
        let file = OpenOptions::new()
            .create(true)
            .write(true)
            .truncate(true)
            .open(path)
            .expect("Failing to open file with given options is impossible");

        let writer = BufWriter::new(file);
        serde_json::to_writer_pretty(writer, &self.into_value())?;

        Ok(())
    }
```

**File:** crates/apollo_class_manager/src/class_manager.rs (L70-113)
```rust
    #[instrument(skip(self, class), ret, err)]
    pub async fn add_class(&mut self, class: RawClass) -> ClassManagerResult<ClassHashes> {
        let sierra_class = SierraContractClass::try_from(&class)?;
        let class_hash = sierra_class.calculate_class_hash();
        if let Ok(Some(executable_class_hash_v2)) =
            self.classes.get_executable_class_hash_v2(class_hash)
        {
            // Class already exists.
            return Ok(ClassHashes { class_hash, executable_class_hash_v2 });
        }

        let compilation_start_time = Instant::now();
        let (raw_executable_class, executable_class_hash_v2) =
            self.compiler.compile(class.clone()).await.map_err(|err| match err {
                SierraCompilerClientError::SierraCompilerError(error) => {
                    ClassManagerError::SierraCompiler { class_hash, error }
                }
                SierraCompilerClientError::ClientError(error) => {
                    ClassManagerError::Client(error.to_string())
                }
            })?;
        debug!(
            %class_hash,
            compiled_class_hash = %executable_class_hash_v2,
            compilation_elapsed_ms = compilation_start_time.elapsed().as_millis(),
            class_size_bytes =
                class.size().map_or("Failed to get class size".to_owned(), |size| size.to_string()),
            casm_size_bytes =
                raw_executable_class.size().map_or("Failed to get casm size".to_owned(), |size| size.to_string()),
            "Finished compiling class."
        );

        self.validate_class_length(&raw_executable_class)?;
        Self::validate_class_version(&sierra_class)?;
        self.classes.set_class(
            class_hash,
            class,
            executable_class_hash_v2,
            raw_executable_class,
        )?;

        let class_hashes = ClassHashes { class_hash, executable_class_hash_v2 };
        Ok(class_hashes)
    }
```

**File:** crates/apollo_class_manager/src/class_storage.rs (L442-454)
```rust
    fn write_class_atomically(
        &self,
        class_id: ClassId,
        class: RawClass,
        executable_class: RawExecutableClass,
    ) -> FsClassStorageResult<()> {
        // Write classes to a temporary directory.
        let (_tmp_root, tmp_dir, persistent_dir) = self.create_tmp_dir(class_id)?;
        class.write_to_file(concat_sierra_filename(&tmp_dir))?;
        executable_class.write_to_file(concat_executable_filename(&tmp_dir))?;

        self.rename_to_persistent_dir(tmp_dir, persistent_dir)
    }
```

**File:** crates/apollo_class_manager/src/class_storage.rs (L503-522)
```rust
impl ClassStorage for FsClassStorage {
    type Error = FsClassStorageError;

    #[instrument(skip(self, class, executable_class), level = "debug", ret, err)]
    fn set_class(
        &mut self,
        class_id: ClassId,
        class: RawClass,
        executable_class_hash_v2: ExecutableClassHash,
        executable_class: RawExecutableClass,
    ) -> Result<(), Self::Error> {
        if self.contains_class(class_id)? {
            return Ok(());
        }

        self.write_class_atomically(class_id, class, executable_class)?;
        self.mark_class_id_as_existent(class_id, executable_class_hash_v2)?;

        Ok(())
    }
```
