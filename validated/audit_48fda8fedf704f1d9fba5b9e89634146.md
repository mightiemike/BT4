Confirmed: reads served by an attached/received BAL take precedence over the true committed database state — this is documented explicitly at `crates/rpc/rpc-eth-types/src/cache/db.rs:15-20` for the RPC tracing path, and the analogous BAL-driven worker executor in `crates/engine/tree/src/tree/payload_processor/bal/worker.rs:75-79` builds each worker's `State` with `.with_bal(received_bal_revm)` before executing transactions "against the EVM's BAL state" per `crates/engine/tree/src/tree/payload_processor/bal/mod.rs:1-9`.

### Title
BAL parallel-execution path trusts peer-supplied BAL values for reads without per-fragment validation - (File: crates/engine/tree/src/tree/payload_processor/bal/worker.rs)

### Summary
The BAL-driven parallel block-execution path (used by `BasicEngineValidator::execute_block_bal`) lets each worker execute a transaction with a `revm::database::State` seeded with the block's *received* (attacker/peer-supplied) `BlockAccessList`, and — per the module's own documentation — "does not yet run per-transaction fragment checks." This mirrors the NFTX `swapTo` bug class: a function that reuses the machinery of two "checked" operations (serial execution + full post-execution BAL-hash validation) but omits the equivalent of the individual, per-step correctness check (`allValidNFTs`/`afterRedeemHook`), letting inputs bypass validation that the "normal" path enforces.

### Finding Description
`execute_block_bal` (`crates/engine/tree/src/tree/payload_validator.rs:1140-1200`) is only reached when `bal_path_eligible` (same file, `:1116-1127`) is true, i.e. whenever the incoming payload/block carries a decoded BAL and `disable_bal_parallel_execution` is not set. It calls `payload_processor::bal::execute_block`, which spawns workers (`worker.rs:70-114`) that each build their own `State` via:
```
State::builder().with_database(database).with_bal(received_bal_revm).with_bundle_update().build()
```
and then call `execute_transaction_without_commit`. The module doc (`bal/mod.rs:1-9`) states plainly: "Workers execute transactions against the EVM's BAL state... It does not yet run per-transaction fragment checks." Separately, `attach_bal_before_tx`'s doc in `crates/rpc/rpc-eth-types/src/cache/db.rs:15-20` confirms the general semantic of an attached BAL in this codebase: "reads served by the attached BAL... take precedence over state committed on top." This means the *received* BAL — which is untrusted payload data, not something reth computed itself — can directly steer what values a worker's EVM execution observes for storage/balance/nonce reads during speculative execution, rather than those reads being independently derived from the actual parent state.

Only two safety nets exist around this path:
1. A pre-execution item-cost/gas-limit bound (`decoded_bal.as_bal().validate_gas_limit(input.gas_limit())`, `payload_validator.rs:588-594`), which only bounds BAL size/cost, not correctness of individual entries.
2. A post-execution hash check comparing the **rebuilt** BAL (computed from what the canonical executor actually committed) to the block header's `block_access_list_hash` (`crates/ethereum/consensus/src/validation.rs:108-127`).

Crucially, the canonical executor commits `output.result` objects that were produced by workers using the received (possibly incorrect/malicious) BAL as their read source — there is no verification, before commit, that the BAL entries a worker relied on for its *reads* actually equal what a serial/spec-faithful read of parent state would have produced. In the analog, `swapTo` also relied only on the "coincidental" side effects of `receiveNFTs`/`withdrawNFTsTo` without re-running the `allValidNFTs` eligibility gate or the `afterRedeemHook` bookkeeping that the checked paths (`mintTo`/`redeemTo`) perform — here, the "gate" that is missing is a fragment-level equivalence check between BAL-declared values and true state before a worker's speculative result is trusted and committed.

### Impact Explanation
If a crafted BAL supplies incorrect (but hash-consistent-looking, or exploiting a divergence the current shadow-mode tests haven't covered) values for accounts a transaction reads, worker execution could compute a `ResultAndState` that differs from what independent/serial execution against the true parent state would produce, yet still get committed by the canonical executor. Because the final consistency check is only a rebuilt-BAL-hash comparison (not a full independent-state-root or per-read revalidation before commit), this could let a reth-built or reth-validated block diverge from the deterministic serial-execution result — i.e., the "cached/parallel output differs from serial execution" equality class explicitly called out as in-scope. That is a potential correctness break between reth's BAL-accelerated path and its own serial-execution ground truth, threatening block-validity/state-root consistency for nodes taking this path.

### Likelihood Explanation
This path is explicitly gated behind BAL presence + a config flag (`disable_bal_parallel_execution`) and is documented in-repo as an intentionally incomplete, in-progress feature ("does not yet run per-transaction fragment checks," with a TODO to add "stronger gating before enabling on mainnet"). It is exercised only for post-Amsterdam blocks carrying an EIP-7928 BAL, and the repo carries a "shadow-mode harness" test asserting byte-equal serial vs. BAL-path outputs, indicating the developers are aware of and actively testing for this exact divergence class. Given the explicit disclosure and stated future work, this reads as a known, still-being-hardened limitation rather than a silently overlooked flaw, which lowers confidence that it is an undiscovered, exploitable finding in the sense the task requires.

### Recommendation
Add per-transaction/per-fragment validation that BAL-declared pre-state values used by workers for reads are cross-checked against a value independently derived from the true parent state (or otherwise cryptographically bound before being trusted for execution), before worker outputs are committed by the canonical executor — not merely after the fact via the aggregate rebuilt-BAL-hash comparison.

### Proof of Concept
No concrete PoC could be constructed from static analysis alone: exploitability depends on internal `revm::database::State`/`Bal` read-precedence semantics (not fully visible in this index) and on whether the existing shadow-mode test suite (`crates/engine/tree/src/tree/payload_processor/bal/execute.rs:730-790`) already catches such divergences before this path could be enabled in production. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) 

Given the explicit in-code disclosure of this as a known, unfinished limitation with active shadow-mode testing already in place, I have low confidence this qualifies as a novel, concrete finding rather than a disclosed/theoretical/in-progress limitation the rules ask to exclude. I could not verify from the available index whether `disable_bal_parallel_execution` defaults to disabling this path in production configs, which materially affects real-world reachability.

### Citations

**File:** crates/engine/tree/src/tree/payload_processor/bal/worker.rs (L70-98)
```rust
    scope.spawn(move |_| {
        let worker_result = (|| -> Result<(), BalWorkerError> {
            // Create a database with fill_on_miss=true ensuring misses
            // are inserted for the other workers.
            let database = make_db(true).map_err(BalWorkerError::Setup)?;
            let mut worker_state = State::builder()
                .with_database(database)
                .with_bal(received_bal_revm)
                .with_bundle_update()
                .build();
            let evm = evm_config.evm_with_env(&mut worker_state, evm_env);
            let mut executor = evm_config.create_executor_with_state(evm, ctx.clone());

            loop {
                let (index, tx) = crossbeam_channel::select_biased! {
                    recv(abort_rx) -> _ => break,
                    recv(tx_rx) -> msg => match msg {
                        Ok(ix_tx) => ix_tx,
                        Err(_) => break,
                    },
                };
                let tx = tx.map_err(|e| BalWorkerError::Transaction(Box::new(e)))?;
                let signer = *tx.signer();
                let tx_gas_limit = tx.tx().gas_limit();

                executor.evm_mut().db_mut().set_bal_index(BlockAccessIndex::new(index as u64 + 1));
                let result = executor
                    .execute_transaction_without_commit(tx)
                    .map_err(BalWorkerError::Execution)?;
```

**File:** crates/engine/tree/src/tree/payload_processor/bal/mod.rs (L1-9)
```rust
//! BAL-driven parallel block execution.
//!
//! The engine uses this path when an Amsterdam block carries a decoded EIP-7928
//! Block-Level Access List (BAL). Workers execute transactions against the EVM's BAL state. The
//! main thread commits worker results to a canonical executor in transaction order.
//!
//! Consensus validation checks the BAL item-cost bound before this path runs. This path validates
//! the rebuilt block-level BAL hash after post-execution. It does not yet run per-transaction
//! fragment checks. It does not yet report rich undeclared-access diagnostics.
```

**File:** crates/rpc/rpc-eth-types/src/cache/db.rs (L12-30)
```rust
/// Attaches `bal` to the database, positioned at the state right before the transaction at
/// `tx_index`.
///
/// Reads served by the attached BAL reflect all writes prior to the transaction, including the
/// block's pre-execution system calls. Reads not covered by the BAL fall back to the underlying
/// database, which holds the correct values for all state the block does not touch.
///
/// Note: changes must not be committed to the database afterwards, because the attached BAL takes
/// precedence over committed state when serving reads.
#[inline]
pub fn attach_bal_before_tx<DB: Database>(
    db: &mut State<DB>,
    bal: &DecodedBal<Arc<RevmBal>>,
    tx_index: usize,
) {
    db.set_bal(Some(bal.as_bal().clone()));
    db.set_allow_bal_db_fallback(true);
    db.set_bal_index(BlockAccessIndex::from_tx_index(tx_index as u64));
}
```

**File:** crates/engine/tree/src/tree/payload_validator.rs (L1116-1127)
```rust
    fn bal_path_eligible(&self, bal: Option<&DecodedBal>) -> Result<bool, InsertBlockErrorKind> {
        let has_bal = bal.is_some();
        let parallel_execution = has_bal && !self.config.disable_bal_parallel_execution();
        if parallel_execution && self.config.disable_bal_parallel_state_root() {
            return Err(InsertBlockErrorKind::Other(
                "disabling parallel state root is impossible when parallel execution is enabled"
                    .into(),
            ));
        }

        Ok(parallel_execution)
    }
```

**File:** crates/ethereum/consensus/src/validation.rs (L108-127)
```rust
    // Validate that the header block access list hash matches the calculated block access list hash
    let is_allowed_pre_amsterdam_bal_hash = allow_bal_hashes &&
        !chain_spec.is_amsterdam_active_at_timestamp(block.header().timestamp()) &&
        block.header().block_access_list_hash().is_some();

    let is_amsterdam = chain_spec.is_amsterdam_active_at_timestamp(block.header().timestamp());
    if is_amsterdam && block_access_list_hash.is_none() {
        return Err(ConsensusError::BlockAccessListHashMissing)
    }

    if (is_amsterdam || is_allowed_pre_amsterdam_bal_hash) &&
        let Some(block_access_list_hash) = block_access_list_hash
    {
        let block_bal_hash = block.header().block_access_list_hash().unwrap_or_default();
        if block_access_list_hash != block_bal_hash {
            return Err(ConsensusError::BlockAccessListHashMismatch(
                GotExpected::new(block_access_list_hash, block_bal_hash).into(),
            ))
        }
    }
```
