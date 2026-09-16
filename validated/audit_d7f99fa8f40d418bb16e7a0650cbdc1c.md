### Title
TOCTOU race in `FsClassStorage::rename_to_persistent_dir` allows a concurrent class declaration to transiently make a marked-as-existent class unreadable - (File: crates/apollo_class_manager/src/class_storage.rs)

### Summary
`FsClassStorage::set_class` is reachable directly from an unprivileged `DECLARE` transaction via `ClassManager::add_class` [1](#0-0) , and the class-manager component is served by a `ConcurrentLocalComponentServer`, which clones the component and processes requests concurrently per incoming request [2](#0-1) [3](#0-2) . The underlying `FsClassStorage` (filesystem + `ClassHashStorage`) is shared across all concurrently-cloned instances, so two `AddClass` requests for the same `class_id` (e.g., the same class declared by two accounts, or double-submitted) can execute `write_class_atomically` → `rename_to_persistent_dir` concurrently against the identical persistent directory path.

### Finding Description
`rename_to_persistent_dir` performs a classic check-then-act sequence that is not atomic with respect to concurrent callers targeting the same `class_id`: [4](#0-3) 

```
fn rename_to_persistent_dir(...) {
    let persistent_dir = self.get_persistent_dir_with_create(class_id)?;
    if persistent_dir.exists() {
        std::fs::remove_dir_all(&persistent_dir)?;
    }
    std::fs::rename(tmp_dir, persistent_dir)?;
    Ok(())
}
```

This logic was explicitly added as crash recovery for a single writer (per the surrounding comment and the `set_class_recovers_from_orphaned_class_dir` test) [5](#0-4) , but it is not guarded against concurrent legitimate writers. `FsClassStorage::set_class` is: [6](#0-5) 

The `contains_class` check and the subsequent `write_class_atomically` + `mark_class_id_as_existent` are not protected by any mutex/lock on `class_id`, and `FsClassStorage` holds no per-class lock — the file `persistent_root` and `class_hash_storage` writer mutex only serialize the marker DB write, not the directory rename/remove sequence.

Race sequence (two concurrent `add_class` calls for the same `class_id`, arriving from two different account declare transactions, both reaching the gateway/class-manager concurrently since it runs on a `ConcurrentLocalComponentServer` with `max_concurrency > 1`):
1. Thread A: `contains_class` → false; writes to tmp dir A; calls `rename_to_persistent_dir`; `persistent_dir.exists()` → false; proceeds to `std::fs::rename(tmp_A, persistent_dir)` — succeeds. Thread A then calls `mark_class_id_as_existent`, writing the DB marker that the class exists.
2. Thread B (racing concurrently, started its check before A's rename completed): `contains_class` → false (checked before A committed the marker); writes to tmp dir B; calls `rename_to_persistent_dir`; now `persistent_dir.exists()` → true (A already renamed); Thread B calls `std::fs::remove_dir_all(persistent_dir)`, **deleting A's just-written and possibly already-marked-existent class directory**, then `std::fs::rename(tmp_B, persistent_dir)`.
3. Between B's `remove_dir_all` and its `rename`, any concurrent reader (`get_sierra`/`get_executable`, reachable from any other in-flight transaction, `get_compiled_class` in RPC execution, or the Starknet OS/blockifier when fetching the class for execution/fee computation) that has already observed the "existent" marker (set by Thread A) will find the persistent directory-in a state of only a subset of file, or momentarily absent — resulting in `FsClassStorageError::ClassNotFound` / `UndeclaredClassHash` errors for a class the state marks as already declared.

Because `class_id` is the Sierra class hash, both writers ultimately write byte-identical content, so there is no data corruption in the final state, but the deletion window is a genuine TOCTOU flaw: the "check" (`exists()`) and the "act" (`remove_dir_all` + `rename`) are two separate filesystem syscalls with no lock, exactly the bug class described in the GNU tar report (check-then-use across a directory rename/removal boundary).

### Impact Explanation
An unprivileged attacker can trigger two near-simultaneous declare transactions for the same contract class (e.g., from two different sender accounts, both computing/declaring an identical Sierra class) causing the class-manager component to race on the same persistent class directory. During the tiny window between `remove_dir_all` and `rename`, any node process reading that class (needed to execute subsequent transactions referencing this class, or to serve RPC `get_executable`/state-sync reads) can spuriously fail with a storage error, even though the class-hash-existence marker says the class is declared. This is a transient sequencer-visible failure that can affect execution/validation for the entire node (all transactions in-flight that need this class within the race window fail with `ClassNotFound`), i.e., a localized denial for otherwise-valid transactions and a correctness-vs-marker inconsistency, without requiring any privileged or operator/prover role. Severity is bounded by the transient nature of the window, but it is directly triggerable by any two colliding declare submissions from ordinary transaction senders and is a real filesystem-state hazard the code's own comments (`checking file existence is not enough, since by reading it can be deleted`) already acknowledge exists for adjacent code (`contains_deprecated_class`), confirming the class of bug is known but not fully closed for this rename path.

### Likelihood Explanation
Likelihood is moderate: it requires two class-manager requests for the identical `class_id` to be in flight concurrently, which the `ConcurrentLocalComponentServer` explicitly enables (`max_concurrency` clones of the component processing requests in parallel) [7](#0-6) . An attacker fully controls timing by submitting two declare transactions (from two funded accounts) with the same contract class back-to-back, or by simply resubmitting/duplicating a declare request, to maximize the chance both requests are dispatched before either commits its marker.

### Recommendation
Serialize `write_class_atomically`/`rename_to_persistent_dir`/`mark_class_id_as_existent` per `class_id` (e.g., a per-class-id lock or a global mutex around the write path in `FsClassStorage`), or make `rename_to_persistent_dir` idempotent/atomic without a destructive `remove_dir_all` (e.g., use `renameat2`/atomic directory swap, or skip the rename entirely and no-op if the destination already exists and is non-empty and valid, verifying content instead of blindly deleting). At minimum, re-check `contains_class`/existence immediately before `remove_dir_all` while holding an exclusive per-class lock so a second writer for the same already-completed class becomes a no-op rather than performing destructive removal.

### Proof of Concept
1. Start a node with the class-manager component configured with `max_concurrency > 1` (default deployment as shown in `crates/apollo_node/src/servers.rs`).
2. From two different funded accounts, submit `DECLARE` transactions carrying the byte-identical Sierra contract class concurrently (so both `AddClass` requests land in the class-manager's queue and get processed by two concurrently-cloned `ClassManager` instances before either completes `mark_class_id_as_existent`).
3. Concurrently, poll `get_executable`/`get_sierra` (or execute a transaction that needs to load that class, e.g., via RPC execution or a dependent `INVOKE`) in a tight loop.
4. Observe intermittent `FsClassStorageError::ClassNotFound` / `StateError::UndeclaredClassHash` responses for a class hash that the class-hash-storage marker already reports as declared, demonstrating the TOCTOU window in `rename_to_persistent_dir` [4](#0-3) .

### Citations

**File:** crates/apollo_class_manager/src/class_manager.rs (L70-109)
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
```

**File:** crates/apollo_infra/src/component_server/local_component_server.rs (L286-300)
```rust
impl<Component, Request, Response> ConcurrentLocalComponentServer<Component, Request, Response>
where
    Component: ComponentRequestHandler<Request, Response> + Clone + Send + 'static,
    Request: Send + Debug + PrioritizedRequest + LabeledRequest + 'static,
    Response: Send + Debug + 'static,
{
    pub fn new(
        component: Component,
        config: &LocalServerConfig,
        rx: Receiver<RequestWrapper<Request, Response>>,
        metrics: &'static LocalServerMetrics,
    ) -> Self {
        let local_component_server = LocalComponentServer::new(component, config, rx, metrics);
        Self { local_component_server, max_concurrency: config.max_concurrency }
    }
```

**File:** crates/apollo_infra/src/component_server/local_component_server.rs (L321-353)
```rust
        let task_limiter = Arc::new(Semaphore::new(self.max_concurrency));

        tokio::spawn(async move {
            loop {
                // TODO(Tsabary): add a test for the queueing time metric.
                let (request, tx, request_id) = get_next_request_for_processing(
                    &mut high_rx,
                    &mut normal_rx,
                    &component_name,
                    metrics,
                )
                .await;

                // Acquire a permit to run the task.
                let permit = task_limiter.clone().acquire_owned().await.unwrap();

                // Clone the component for concurrent request processing.
                let mut cloned_component = component.clone();
                tokio::spawn(async move {
                    process_request(
                        &mut cloned_component,
                        request,
                        request_id,
                        tx,
                        metrics,
                        processing_time_warning_threshold_ms,
                    )
                    .await;
                    // Drop the permit to allow more tasks to be created.
                    drop(permit);
                });
            }
        });
```

**File:** crates/apollo_node/src/servers.rs (L305-317)
```rust
    let class_manager_server = create_local_server!(
        CONCURRENT_LOCAL_SERVER,
        &config.components.class_manager.execution_mode,
        &mut components.class_manager,
        &config
            .components
            .class_manager
            .local_server_config
            .as_ref()
            .expect("Class manager local server config should be available."),
        communication.take_class_manager_rx(),
        &CLASS_MANAGER_INFRA_METRICS.get_local_server_metrics(),
    );
```

**File:** crates/apollo_class_manager/src/class_storage.rs (L487-500)
```rust
    fn rename_to_persistent_dir(
        &self,
        tmp_dir: PathBuf,
        class_id: ClassId,
    ) -> FsClassStorageResult<()> {
        let persistent_dir = self.get_persistent_dir_with_create(class_id)?;
        if persistent_dir.exists() {
            warn!("Recovering orphaned class dir from a prior partial write: {persistent_dir:?}");
            std::fs::remove_dir_all(&persistent_dir)?;
        }
        std::fs::rename(tmp_dir, persistent_dir)?;

        Ok(())
    }
```

**File:** crates/apollo_class_manager/src/class_storage.rs (L506-522)
```rust
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

**File:** crates/apollo_class_manager/src/class_storage_test.rs (L192-223)
```rust
/// Reproduces the production sync deadlock: a crash between writing the class files and committing
/// the existence marker leaves an orphaned, non-empty persistent directory. Re-running `set_class`
/// must recover and complete the write; previously `std::fs::rename` failed with ENOTEMPTY and
/// wedged sync on the class forever.
#[tokio::test]
async fn set_class_recovers_from_orphaned_class_dir() {
    let persistent_root = tempfile::tempdir().unwrap();
    let class_hash_storage_path_prefix = tempfile::tempdir().unwrap();
    let mut storage =
        FsClassStorage::new_for_testing(&persistent_root, &class_hash_storage_path_prefix);

    let class_id = ClassHash(felt!("0x1234"));
    let class = RawClass::try_from(SierraContractClass::default()).unwrap();
    let executable_class = RawExecutableClass::test_casm_contract_class();
    let executable_class_hash_v2 = CompiledClassHash(felt!("0x5678"));

    // Simulate a partial write: class files are on disk, but the existence marker was never
    // committed (the process crashed in between).
    storage.write_class_atomically(class_id, class.clone(), executable_class.clone()).unwrap();
    assert_eq!(storage.get_executable_class_hash_v2(class_id), Ok(None));
    assert!(storage.get_persistent_dir(class_id).join("sierra").exists());

    // `set_class` must recover the orphaned directory and complete the write.
    storage
        .set_class(class_id, class.clone(), executable_class_hash_v2, executable_class.clone())
        .unwrap();

    // The class is now fully readable and marked as existent.
    assert_eq!(storage.get_sierra(class_id).unwrap(), Some(class));
    assert_eq!(storage.get_executable(class_id).unwrap(), Some(executable_class));
    assert_eq!(storage.get_executable_class_hash_v2(class_id), Ok(Some(executable_class_hash_v2)));
}
```
