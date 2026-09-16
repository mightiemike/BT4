This confirms the vulnerability path. The `ContractClassManager` cache is explicitly shared across proposal attempts, view calls, and block production for the lifetime of the batcher process (`crates/apollo_batcher/src/batcher.rs:1747-1761`), and `try_declare` in `crates/blockifier/src/transaction/transactions.rs:387-407` writes the class into state (and thus into this global cache via `get_compiled_class`) as soon as a Declare transaction executes — before that block/proposal is finalized or committed. Since aborted/rejected proposals do not purge this cache (`clear()` is only called in narrow paths), and the Cairo0 path in `get_compiled_from_class_manager` (`crates/blockifier/src/state/state_reader_and_contract_manager.rs:73-82`) skips the `is_declared` re-validation that Cairo1 explicitly performs "since existence in the cache does not guarantee that [declaration]... it might contain a declared class from a reverted block", this is a concrete, unpatched analog of the classic-builder cache-poisoning bug class.

### Title
Undeclared Cairo0 class execution via stale global contract-class cache — cache poisoning bypasses declaration check ([File: crates/blockifier/src/state/state_reader_and_contract_manager.rs])

### Summary
The sequencer's `ContractClassManager` (`RawClassCache` / `GlobalContractCache`) is a single, process-wide, long-lived cache keyed only by `ClassHash`, shared across block-building attempts, aborted proposals, and view calls for the batcher's entire lifetime [1](#0-0) . When `get_compiled_from_class_manager` serves a cache hit for a Cairo1 class, it explicitly re-validates that the class is still declared in canonical state, noting the class "might contain a declared class from a reverted block" [2](#0-1) . For Cairo0 (`RunnableCompiledClass::V0`) the code path is a no-op — no equivalent check exists, and `is_declared` cannot even check Cairo0 declarations by design [3](#0-2) .

### Finding Description
A Declare transaction populates state (and therefore the global class cache, on the subsequent `get_compiled_class` cache-miss/fill path) as soon as it runs `try_declare`, which unconditionally calls `state.set_contract_class` before the containing block/proposal is finalized [4](#0-3) . This happens during speculative/candidate execution — e.g. a block-building attempt that is later aborted (`BlockBuilder::build_block` calls `abort_block()` on failure) [5](#0-4) , a rejected/failed proposal, or a validator that discards a proposal — none of which necessarily clear the shared `ContractClassManager`. Since the compiled-class entry for a Cairo0 class hash is written into the global cache during that speculative execution, and the "declared" re-check on cache-hit is only implemented for Cairo1 classes, a subsequent unrelated block (or a concurrent view call, since view calls and block production share the same cache) can retrieve and execute that Cairo0 class as if it were validly declared, even though the declaring transaction was never actually committed to canonical state.

### Impact Explanation
This allows a contract to be deployed or replaced (via `replace_class`/`deploy`) to reference a Cairo0 class hash that was never actually declared in the committed chain state, executing its bytecode as though it were a legitimate declared class. This is an unauthorized action (bypassing the declaration/fee requirement and the "must be declared" protocol invariant enforced everywhere else, e.g. `replace_class` syscall handlers explicitly assert declaration via `get_compiled_class`) [6](#0-5) . Because the poisoning depends on which speculative/aborted executions a given sequencer node happened to run locally, different honest nodes can diverge on whether a given class hash is "available," leading to honest-node divergence in execution results and committed state roots — matching the required severity bar (state/root divergence, unauthorized action).

### Likelihood Explanation
Reachable purely from a single account submitting ordinary Declare + follow-up transactions; no privileged, malicious-operator, or p2p-level behavior is required — a normal proposal that gets aborted, times out, or loses to another proposer (all routine, honest-node events in `BlockBuilder::build_block_inner`) is sufficient to populate and retain the stale cache entry, since `ContractClassManager::clear()` is not shown to be invoked on ordinary proposal abort/failure.

### Recommendation
Apply the same "does the cache entry still correspond to canonical declared state" verification used for Cairo1 classes to the Cairo0 (`RunnableCompiledClass::V0`) branch in `get_compiled_from_class_manager`, e.g. by checking `state_reader.get_class_hash_at`/a dedicated Cairo0 declaration-lookup on every cache hit, or by invalidating/removing entries written during a proposal from the shared `ContractClassManager` whenever that proposal is aborted or fails to become canonical.

### Proof of Concept
1. Submit Declare V0/V1 transaction `D` for Cairo0 class `C` (hash `h`) as part of a candidate block being built by a proposer node.
2. Trigger the proposal to be aborted/discarded before commit (deadline reached, being outbid by another proposer, or any ordinary abort path in `BlockBuilder::build_block`) — `D` never lands in canonical state, so `h` is not declared.
3. The `ContractClassManager`'s global cache, however, retains the compiled class for `h` from the aborted execution (no invalidation on abort).
4. Submit an unrelated transaction in a later, real block that calls `replace_class(h)` or `deploy(h)` on a contract.
5. `get_compiled_from_class_manager` hits the cache for `h`, and — because it is a `V0` class — skips any declared-state check, returning the cached compiled class and allowing the call to succeed, even though `h` is genuinely undeclared in committed state.

### Citations

**File:** crates/apollo_batcher/src/batcher.rs (L1747-1761)
```rust
    // Block production and view calls share one class cache.
    let contract_class_manager =
        ContractClassManager::start(config.static_config.contract_class_manager_config.clone());
    let block_builder_factory = Box::new(BlockBuilderFactory {
        block_builder_config: config.static_config.block_builder_config.clone(),
        storage_reader: storage_reader.clone(),
        contract_class_manager: contract_class_manager.clone(),
        class_manager_client: class_manager_client.clone(),
        proof_manager_client,
        worker_pool,
    });
    let view_state_reader_factory = Box::new(StorageViewStateReaderFactory {
        storage_reader: storage_reader.clone(),
        contract_class_manager,
        class_manager_client,
```

**File:** crates/blockifier/src/state/state_reader_and_contract_manager.rs (L16-21)
```rust
pub trait FetchCompiledClasses: StateReader {
    fn get_compiled_classes(&self, class_hash: ClassHash) -> StateResult<CompiledClasses>;

    /// Returns whether the given class hash corresponds to a declared Cairo 1 class.
    /// Cairo 0 classes always return `false`.
    fn is_declared(&self, class_hash: ClassHash) -> StateResult<bool>;
```

**File:** crates/blockifier/src/state/state_reader_and_contract_manager.rs (L70-82)
```rust
        if let Some(runnable_class) =
            self.contract_class_manager.get_runnable(&class_hash, &self.native_classes_whitelist)
        {
            match &runnable_class {
                RunnableCompiledClass::V0(_) => {}
                _ => {
                    // The Cairo1 class is cached; verify it is declared,
                    // since existence in the cache does not guarantee that
                    // (it might contain a declared class from a reverted block, for example).
                    if !self.state_reader.is_declared(class_hash)? {
                        return Err(StateError::UndeclaredClassHash(class_hash));
                    }
                }
```

**File:** crates/blockifier/src/transaction/transactions.rs (L392-401)
```rust
) -> TransactionExecutionResult<()> {
    match state.get_compiled_class(class_hash) {
        Err(StateError::UndeclaredClassHash(_)) => {
            // Class is undeclared; declare it.
            state.set_contract_class(class_hash, tx.contract_class().try_into()?)?;
            if let Some(compiled_class_hash) = compiled_class_hash {
                state.set_compiled_class_hash(class_hash, compiled_class_hash)?;
            }
            Ok(())
        }
```

**File:** crates/apollo_batcher/src/block_builder.rs (L306-318)
```rust
    async fn build_block(&mut self) -> BlockBuilderResult<BlockExecutionArtifacts> {
        let res = self.build_block_inner().await;
        if res.is_err() {
            let executor = self.executor.clone();
            spawn_blocking(move || {
                let mut locked_executor = executor.blocking_lock();
                locked_executor.abort_block();
            })
            .await
            .expect("Aborting block should succeed.");
        }
        res
    }
```

**File:** crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs (L795-806)
```rust
    fn replace_class(
        request: ReplaceClassRequest,
        _vm: &mut VirtualMachine,
        syscall_handler: &mut Self,
    ) -> DeprecatedSyscallResult<ReplaceClassResponse> {
        // Ensure the class is declared (by reading it).
        syscall_handler.state.get_compiled_class(request.class_hash)?;
        syscall_handler
            .state
            .set_class_hash_at(syscall_handler.storage_address, request.class_hash)?;

        Ok(ReplaceClassResponse {})
```
