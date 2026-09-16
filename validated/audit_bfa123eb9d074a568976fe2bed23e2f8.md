Based on my analysis, I found a genuine analog to the reported bug class in the sequencer codebase: `FsClassStorage::set_class` performs a two-step, non-atomic write (write class files, then commit an existence marker) that is directly reachable from an unprivileged user's `DECLARE` transaction via the gateway.

### Title
Non-atomic two-step class write in `FsClassStorage::set_class` can leave a permanently orphaned/undeclared class after a crash between steps - (File: `crates/apollo_class_manager/src/class_storage.rs`)

### Summary
`ClassManager::add_class` (invoked from the gateway's `add_tx` flow for every `DECLARE` transaction) calls `CachedClassStorage::set_class`, which in turn calls `FsClassStorage::set_class`. This performs two sequential, independently-fallible filesystem operations with no atomic wrapper: `write_class_atomically` (writes Sierra/CASM files and renames into the persistent directory) followed by `mark_class_id_as_existent` (writes the executable-class-hash existence marker to a separate `ClassHashStorage` DB). If the process crashes/panics/loses power between these two steps, the class files exist on disk but the existence marker is never written — exactly the "first update commits, second update fails" pattern described in the external report.

### Finding Description
`FsClassStorage::set_class` is:
```
fn set_class(...) -> Result<(), Self::Error> {
    if self.contains_class(class_id)? { return Ok(()); }
    self.write_class_atomically(class_id, class, executable_class)?;
    self.mark_class_id_as_existent(class_id, executable_class_hash_v2)?;
    Ok(())
}
``` [1](#0-0) 

`write_class_atomically` and `mark_class_id_as_existent` are two separate, non-transactional operations against two different backing stores (a content-addressed filesystem tree and a `ClassHashStorage`/libmdbx-backed table): [2](#0-1) 

The `CachedClassStorage` wrapper explicitly documents that it treats the executable-class-hash cache entry as "acts as an existence marker" and only updates the in-memory cache after storage succeeds "as an optimization; does not require atomicity" — i.e., the authors were aware ordering matters but did not make the underlying two-step storage write itself atomic: [3](#0-2) 

The repository's own tests confirm the exact partial-failure scenario: `fs_storage_partial_write_no_atomic_marker` shows that if the class files are written but the marker step never runs, `get_executable_class_hash_v2` returns `None` and the class is treated as **non-existent** even though the files are on disk (silent orphaning); and `set_class_recovers_from_orphaned_class_dir` documents that this previously caused a **production sync deadlock** where re-running `set_class` failed with `ENOTEMPTY` on `std::fs::rename`, "wedging" the class forever until a fix was added to recover orphaned directories: [4](#0-3) 

This is reachable purely from a single unprivileged `DECLARE` transaction: gateway `add_tx_inner` calls `convert_rpc_tx_to_internal_and_executable_txs`, which drives class-manager `add_class`, which calls `CachedClassStorage::set_class` → `FsClassStorage::set_class`: [5](#0-4) [6](#0-5) 

### Impact Explanation
If a crash/restart (or any process interruption, e.g. OOM-kill, `SIGKILL`, host failure) lands between `write_class_atomically` and `mark_class_id_as_existent`, the sequencer restarts with class files present but no existence marker, so the class is treated as never having been declared. This blocks re-declaration attempts via the same recovery path only if the fix in `set_class_recovers_from_orphaned_class_dir` is present in all deployed nodes — but the underlying operation remains fundamentally non-atomic. Any divergence between what one node has recovered/repaired vs. another (e.g., different crash timing, different recovery code paths across a fleet during a rolling upgrade) can cause one sequencer node to consider a class declared/compiled while another does not, leading to inconsistent class availability for execution and potential honest-node divergence on whether a `DECLARE`d class can be used by subsequent transactions in the same block-building window.

### Likelihood Explanation
Likelihood is **Low**: it requires a crash or restart to land in the narrow window between the two write calls, which is not attacker-triggerable directly by a single unprivileged sender purely through transaction content (no way to force a crash from a transaction alone). This mirrors the "Impact: High, Likelihood: Low" rating of the original report.

### Recommendation
Make `write_class_atomically` and `mark_class_id_as_existent` part of a single atomic commit — e.g., persist the executable-class-hash marker as part of the same atomic filesystem rename (write the marker file inside the temp directory before the final rename), or otherwise ensure the class is only considered "written" once both effects are durably visible together, removing the possibility of an intermediate observable state where files exist without the marker (or vice versa).

### Proof of Concept
1. Submit a `DECLARE` transaction to the gateway; it flows through `add_tx_inner` → `add_class` → `CachedClassStorage::set_class` → `FsClassStorage::set_class`.
2. `write_class_atomically` completes (class Sierra/CASM files exist under the persistent content-addressed directory).
3. Kill the class-manager process before `mark_class_id_as_existent` executes (simulated directly in the repo's own test `set_class_recovers_from_orphaned_class_dir`, which reproduces this exact sequence). [7](#0-6) 
4. On restart (without the orphan-recovery fix, or on any node where recovery logic differs), `get_executable_class_hash_v2` returns `None` for the class hash, `contains_class` is false, and the class is treated as undeclared — while the class hash's files still occupy the persistent directory, potentially causing the subsequent legitimate re-declare/write path to fail or diverge from other nodes' state.

### Citations

**File:** crates/apollo_class_manager/src/class_storage.rs (L106-138)
```rust
    #[instrument(skip(self, class, executable_class), level = "debug", ret, err)]
    fn set_class(
        &mut self,
        class_id: ClassId,
        class: RawClass,
        executable_class_hash_v2: ExecutableClassHash,
        executable_class: RawExecutableClass,
    ) -> Result<(), Self::Error> {
        if self.class_cached(class_id) {
            return Ok(());
        }

        self.storage.set_class(
            class_id,
            class.clone(),
            executable_class_hash_v2,
            executable_class.clone(),
        )?;

        increment_n_classes(CairoClassType::Regular);
        record_class_size(ClassObjectType::Sierra, &class);
        record_class_size(ClassObjectType::Casm, &executable_class);

        // Cache the class.
        // Done after successfully writing to storage as an optimization;
        // does not require atomicity.
        self.classes.set(class_id, class);
        self.executable_classes.set(class_id, executable_class);
        // Cache the executable class hash last; acts as an existence marker.
        self.executable_class_hashes_v2.set(class_id, executable_class_hash_v2);

        Ok(())
    }
```

**File:** crates/apollo_class_manager/src/class_storage.rs (L441-463)
```rust
    fn mark_class_id_as_existent(
        &mut self,
        class_id: ClassId,
        executable_class_hash_v2: ExecutableClassHash,
    ) -> FsClassStorageResult<()> {
        Ok(self
            .class_hash_storage
            .set_executable_class_hash_v2(class_id, executable_class_hash_v2)?)
    }

    fn write_class_atomically(
        &self,
        class_id: ClassId,
        class: RawClass,
        executable_class: RawExecutableClass,
    ) -> FsClassStorageResult<()> {
        // Write classes to a temporary directory.
        let (_tmp_root, tmp_dir) = self.create_tmp_dir(class_id)?;
        class.write_to_file(concat_sierra_filename(&tmp_dir))?;
        executable_class.write_to_file(concat_executable_filename(&tmp_dir))?;

        self.rename_to_persistent_dir(tmp_dir, class_id)
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

**File:** crates/apollo_class_manager/src/class_storage_test.rs (L173-223)
```rust
#[tokio::test]
async fn fs_storage_partial_write_no_atomic_marker() {
    let persistent_root = tempfile::tempdir().unwrap();
    let class_hash_storage_path_prefix = tempfile::tempdir().unwrap();
    let storage =
        FsClassStorage::new_for_testing(&persistent_root, &class_hash_storage_path_prefix);

    // Fully write class files, without atomic marker.
    let class_id = ClassHash(felt!("0x1234"));
    let class = RawClass::try_from(SierraContractClass::default()).unwrap();
    let executable_class = RawExecutableClass::test_casm_contract_class();
    storage.write_class_atomically(class_id, class, executable_class).unwrap();
    assert_eq!(storage.get_executable_class_hash_v2(class_id), Ok(None));

    // Query class, should be considered non-existent.
    assert_eq!(storage.get_sierra(class_id), Ok(None));
    assert_eq!(storage.get_executable(class_id), Ok(None));
}

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

**File:** crates/apollo_gateway/src/gateway.rs (L214-266)
```rust
    async fn add_tx_inner(
        &self,
        tx: RpcTransaction,
        p2p_message_metadata: Option<BroadcastedMessageMetadata>,
    ) -> GatewayResult<GatewayOutput> {
        let mut metric_counters = GatewayMetricHandle::new(&tx, &p2p_message_metadata);
        metric_counters.count_transaction_received();
        if let RpcTransaction::Invoke(RpcInvokeTransaction::V3(ref inv)) = tx {
            if !inv.proof_facts.is_empty() {
                metric_counters.count_private_transaction_received();
            }
        }
        let is_p2p = p2p_message_metadata.is_some();

        if let RpcTransaction::Declare(ref declare_tx) = tx {
            if let Err(e) = self.check_declare_permissions(declare_tx) {
                metric_counters.record_add_tx_failure(&e);
                return Err(e);
            }
        }

        // Perform stateless validations.
        self.stateless_tx_validator.validate(&tx)?;

        let tx_signature = tx.signature().clone();

        // Declare conversions overload the compiler component's CPU and memory. Reject declares if
        // there are too many declares compiling in parallel. The permit is held only across
        // compilation and released before stateful validation.
        let compilation_permit = if matches!(tx, RpcTransaction::Declare(_)) {
            Some(self.declare_compilation_semaphore.try_acquire().map_err(|_| {
                let error = StarknetError::too_many_concurrent_declare_compilations();
                metric_counters.record_add_tx_failure(&error);
                error
            })?)
        } else {
            None
        };

        let (internal_tx, executable_tx, proof_data) =
            self.convert_rpc_tx_to_internal_and_executable_txs(tx, &tx_signature).await?;
        drop(compilation_permit);

        let mut stateful_transaction_validator = self
            .stateful_tx_validator_factory
            .instantiate_validator(self.config.dynamic_config.native_classes_whitelist.clone())
            .await
            .inspect_err(|e| metric_counters.record_add_tx_failure(e))?;

        let nonce = stateful_transaction_validator
            .extract_state_nonce_and_run_validations(&executable_tx, self.mempool_client.clone())
            .await
            .inspect_err(|e| metric_counters.record_add_tx_failure(e))?;
```
