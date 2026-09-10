## Analog Found: Malicious/incorrect Block Access List (BAL) values are trusted as read source-of-truth during BAL-parallel execution, poisoning canonical state without independent verification

### Title
Malicious BAL causes reth's BAL-parallel execution path to commit state reads it never independently verified against the real parent state - (File: `crates/engine/tree/src/tree/payload_processor/bal/execute.rs`, `crates/engine/tree/src/tree/payload_processor/bal/worker.rs`)

### Summary
The external report's root cause is "trusting a self-reported/attacker-supplied value instead of the value produced by real, verified computation." The reth analog is in the EIP-7928 Block Access List (BAL) execution path: worker threads execute transactions using state reads served from the **received** (network/peer-supplied) BAL rather than the real parent database, and their results are committed straight into the canonical block output. The only cross-check against reality is a **debug-level log of divergence**, not an enforced validation, and that check is structurally circular (it compares the produced BAL against the same received BAL that was used to generate the reads), so it cannot catch a self-consistent malicious BAL.

### Finding Description
`execute_block_inner` spawns worker threads to speculatively execute each transaction: [1](#0-0) 

Each worker's `State` is built with `.with_bal(received_bal_revm)` — installing the **received, unverified** BAL as the account/storage read source for that worker's speculative EVM execution (`execute_transaction_without_commit`). If the received BAL declares a value for a covered account/slot (e.g. a sender's balance) that differs from the true parent state, the worker's opcode/value-transfer logic will use the fabricated value instead of the real one.

The worker output (`output.result`, i.e. `ResultAndState` computed under the fabricated reads) is then applied directly to the canonical execution state without re-deriving it from the canonical DB: [2](#0-1) 

`canonical_state` is built from `make_db(false)` (the real provider-backed DB) but is never used to *re-verify* the worker's computed result — `commit_transaction(output.result)` merely applies the already-computed post-state.

The only sanity check against a bad/malicious BAL is: [3](#0-2) 

This is (a) gated behind `tracing::enabled!(... Level::DEBUG)` — a no-op in production, and (b) circular: `built_bal` is taken from `canonical_state`, whose bundle was populated exclusively from `output.result` values that were themselves computed using `received_bal` as the read source. A self-consistent, attacker-crafted BAL will therefore reproduce itself in `built_bal`, and no divergence will ever be observed — even with debug logging enabled.

The module's own doc comment concedes this design intent explicitly: [4](#0-3) 

### Impact Explanation
If the BAL accompanying a block is supplied (or influenced) by an untrusted block producer/peer and declares fabricated pre-state values for accounts/slots that a transaction reads (most critically account balances used for native value transfers, which are served through the `Database`/`State` layer rather than explicit `SLOAD`), reth's BAL-parallel execution path will compute a post-state that differs from what genuine serial EVM execution over the real parent state would produce. Because that computed post-state is committed as the canonical `BlockExecutionOutput` (feeding the state root, receipts, and post-state used for consensus validation), this breaks the equality "state read must match the parent's committed post-state" and "cached/parallel output must equal serial execution output." This can result in:
- reth computing a different state root than the spec/serial execution would (Critical — consensus split), or
- reth accepting a block as valid that other clients (or reth's own non-BAL serial path) would reject, or vice versa (High — wrongly valid/invalid chain).

### Likelihood Explanation
This requires that reth actually executes blocks through the BAL-parallel path with an externally-sourced/untrusted BAL (i.e., a block/payload advertising EIP-7928 BAL data that isn't independently re-derived from real state before being used for reads). Given `execute_block` is dispatched from `execute_block_bal` in the payload validator using `env.decoded_bal` taken from the incoming block/payload, the received BAL's provenance is exactly the untrusted network input this analysis targets. The lack of any non-debug-gated cross-check, and the circularity of the existing check, means a crafted self-consistent BAL would not be caught by this module at all.

### Recommendation
Do not use the received/network-supplied BAL as an unconditional read source for values that affect balances, nonces, or storage slots read by transaction logic without independently verifying those values against the real parent-state database (or by re-executing/serial-validating at least the divergent accounts against the canonical DB before committing). Promote the "divergence" check from a debug-only trace to a hard validation that compares against an actually-independent recomputation (e.g., real DB reads on the canonical state, not merely echoing back what was already derived from the received BAL), and reject/re-execute serially on any mismatch instead of silently trusting worker output.

### Proof of Concept
1. A block/payload arrives with an EIP-7928 BAL (`decoded_bal`) that declares a fabricated (too-high) balance for account `A` at the relevant `BlockAccessIndex`.
2. `execute_block` → `spawn_worker` builds worker `State` with `.with_bal(received_bal_revm)` [5](#0-4) , so reads of `A`'s balance during transaction execution return the fabricated value instead of the true (lower) balance from the canonical DB.
3. A transaction that spends more than `A`'s true balance (but not more than the fabricated balance) executes "successfully" in the worker, producing a `ResultAndState` reflecting the fabricated value.
4. `execute_block_inner` commits that result unconditionally via `canonical_executor.commit_transaction(output.result)` [6](#0-5) .
5. `take_built_bal_and_log_divergence` compares `built_bal` (derived from the same poisoned execution) against `received_bal` — they match, so no divergence is even logged, let alone rejected [7](#0-6) .
6. The resulting `BlockExecutionOutput`/state root differs from what real serial execution over the true parent state would produce, yet is accepted as canonical.

### Citations

**File:** crates/engine/tree/src/tree/payload_processor/bal/worker.rs (L74-80)
```rust
            let database = make_db(true).map_err(BalWorkerError::Setup)?;
            let mut worker_state = State::builder()
                .with_database(database)
                .with_bal(received_bal_revm)
                .with_bundle_update()
                .build();
            let evm = evm_config.evm_with_env(&mut worker_state, evm_env);
```

**File:** crates/engine/tree/src/tree/payload_processor/bal/execute.rs (L12-15)
```rust
//!
//! The rebuilt BAL is returned to the outer payload validator for consensus post-execution
//! validation. This module only logs the first divergence between the received BAL and the BAL
//! rebuilt from canonical execution.
```

**File:** crates/engine/tree/src/tree/payload_processor/bal/execute.rs (L148-166)
```rust
        for output in ordered_worker_outputs(&result_rx, transaction_count) {
            let output = output?;

            gas_tracker.validate_tx_limit(output.tx_gas_limit)?;
            gas_tracker.record_result(output.result.result());
            canonical_executor.evm_mut().db_mut().bump_bal_index();

            let _ = canonical_executor.commit_transaction(output.result);
            senders.push(output.signer);

            let current_len = canonical_executor.receipts().len();
            if current_len > last_sent_len {
                last_sent_len = current_len;
                if let Some(receipt) = canonical_executor.receipts().last() {
                    let tx_index = current_len - 1;
                    let _ = receipt_tx.send(IndexedReceipt::new(tx_index, receipt.clone()));
                }
            }
        }
```

**File:** crates/engine/tree/src/tree/payload_processor/bal/execute.rs (L202-226)
```rust
fn take_built_bal_and_log_divergence<DB>(
    canonical_state: &mut State<DB>,
    received_bal: &AlloyBal,
) -> BlockAccessList
where
    DB: Database,
{
    let built_bal = canonical_state.take_built_alloy_bal().expect("with_bal_builder set");
    if tracing::enabled!(target: "engine::tree::payload_processor::bal", tracing::Level::DEBUG) &&
        built_bal.as_slice() != received_bal.as_slice()
    {
        let rebuilt = compute_block_access_list_hash(built_bal.as_slice());
        let expected = compute_block_access_list_hash(received_bal.as_slice());
        let div = received_bal.diff(built_bal.as_slice());
        tracing::debug!(
            target: "engine::tree::payload_processor::bal",
            %rebuilt,
            %expected,
            %div,
            "first BAL divergence",
        );
    }

    built_bal
}
```
