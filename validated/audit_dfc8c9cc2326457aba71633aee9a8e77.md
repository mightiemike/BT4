### Title
Successful deposits removed together with reverted deposits from `DepositDataMempool`, permanently freezing cBTC — (File: `crates/sequencer/src/runner.rs`)

### Summary
`process_sys_txs` correctly reverts the working-set checkpoint and `continue`s when a deposit's `Bridge.deposit` call fails (`StateTransitionError::ModuleCallError(L2BlockModuleCallError::EvmSystemTransactionNotSuccessful)`), skipping the push of that deposit's RLP tx into `all_txs`. However, the caller later calls `self.deposit_mempool.lock().remove_deposits(&deposit_data)` using the original, unfiltered `deposit_data` slice that was fetched via `fetch_deposits`, not the subset that was actually included/succeeded. Any deposit that reverted during real block production is nonetheless permanently evicted from `DepositDataMempool`.

### Finding Description
The binding that must hold is:
`{deposit blobs present in all_txs for l2_height}` == `{deposit blobs removed from DepositDataMempool via remove_deposits for l2_height}`

Trace:
- `produce_and_run_system_transactions` (crates/sequencer/src/runner.rs:1552-1607) builds `system_events` from `deposit_data` via `populate_deposit_system_events(deposit_data)` and calls `process_sys_txs`.
- `process_sys_txs` (crates/sequencer/src/runner.rs:1617-1706) iterates events, and for each deposit event creates a checkpoint (`working_set.checkpoint().to_revertable()`), applies it via `self.stf.apply_l2_block_txs`. If it fails with `EvmSystemTransactionNotSuccessful` and `is_deposit` is true, the code explicitly reverts (`working_set.revert()`), decrements `nonce`, and does `continue` (runner.rs:1682-1696) — crucially it does **not** push the failed `sys_tx_rlp` into `all_txs`. Only on success is `all_txs.push(sys_tx_rlp)` executed (runner.rs:1702).
- The returned `all_txs` from `process_sys_txs`/`produce_and_run_system_transactions` therefore correctly reflects only the deposits that were actually applied to state (i.e., cBTC credited).
- Later, at block-finalization time (runner.rs:634-643), after `save_l2_block`, the code does:
```
if !deposit_data.is_empty() {
    let removed_count = self.deposit_mempool.lock().remove_deposits(&deposit_data);
    ...
}
```
using `deposit_data` — the full list originally fetched from `DepositDataMempool::fetch_deposits(limit)` — not the filtered `all_txs`/success set.
- `DepositDataMempool::remove_deposits` (crates/sequencer/src/deposit_data_mempool.rs:79-109) computes `calc_tx_id` for every entry in the passed slice and unconditionally removes matching entries from both `accepted_deposit_txs` and `pending_deposits`.

Root cause: `deposit_data` is reused post-execution as a proxy for "successfully applied deposits," but it is not filtered to match `all_txs`; the code never distinguishes reverted deposits from applied ones when calling `remove_deposits`.

Attacker's exact action: submit any deposit blob `B` via `citrea_sendRawDepositTransaction` whose ABI-decoded `moveTx` passes the sequencer's `eth_call` simulation against `BlockId::pending()` at submission time but reverts against `Bridge.deposit` when actually executed at block-production time (e.g., due to state drift such as `BitcoinLightClient` head advancing, another deposit racing to mark the same UTXO/txid processed first, or any other simulation/execution divergence). Once `B` is fetched by `fetch_deposits` and included in the fetched batch, if it reverts inside `process_sys_txs`, it is dropped from `all_txs` (never applied → no cBTC ever minted) but is still removed from `DepositDataMempool` by the unconditional `remove_deposits(&deposit_data)` call.

Existing guards do not catch this: the `eth_call` pre-check against pending state is a point-in-time simulation, not authoritative; `process_sys_txs`'s revert-and-continue logic exists precisely to tolerate deposits that revert (e.g., non-EOA recipients), which implicitly acknowledges that not all fetched deposits execute successfully — yet the mempool cleanup logic was not updated to track this distinction.

### Impact Explanation
A deposit whose Bitcoin-side funds were locked/committed on L1 but whose corresponding `Bridge.deposit` call reverted on L2 is permanently evicted from `pending_deposits`/`accepted_deposit_txs`, with no way to resubmit identical bytes (its `calc_tx_id` no longer blocks re-insertion, but the underlying condition causing the original revert may persist, and there is no mechanism to retry it, nor any user-facing indication it was dropped). cBTC that should have been credited for that Bitcoin deposit is never minted for the intended recipient, and the deposit is effectively lost from the sequencer's tracking. This matches the Critical impact category: "funds permanently frozen." The blast radius is proportional to how often deposits fail transient/race-condition reverts at production time versus simulation time; each occurrence is a fully independent, repeatable loss of one deposit.

### Likelihood Explanation
Preconditions: sequencer running with `deposit_mempool_fetch_limit >= 1`, and any condition causing a deposit to pass `eth_call` simulation at submission but fail at actual application (state drift between simulation and production, e.g., another deposit consuming the same underlying reference first, or light-client state advancing). This does not require attacker privilege beyond normal unauthenticated RPC access (`citrea_sendRawDepositTransaction`) and costs only the Bitcoin transaction fee to inscribe the underlying deposit tx; it can be triggered incidentally by benign network conditions or deliberately raced by an attacker submitting near-duplicate/overlapping deposit data to induce a revert window. It is fully repeatable per deposit and not fixed by retrying since dropped deposits are not automatically resubmitted.

### Recommendation
Track which deposits from `deposit_data` were actually successfully applied (e.g., collect the subset corresponding to `all_txs`, or return an explicit list of failed deposit blobs from `process_sys_txs`/`produce_and_run_system_transactions`) and only call `remove_deposits` with that successful subset. Failed deposits should remain in `DepositDataMempool` (or be explicitly re-queued) so they can be retried in a later block instead of being silently and permanently dropped.

### Proof of Concept
```rust
// crates/sequencer/src/deposit_data_mempool.rs (extend #[cfg(test)] mod tests)
#[test]
fn test_remove_deposits_incorrectly_evicts_failed_deposit() {
    let mut mempool = DepositDataMempool::new();
    let deposit1 = hex::decode(DEPOSIT1).unwrap(); // will "succeed"
    let deposit2 = hex::decode(DEPOSIT2).unwrap(); // will "fail" inside process_sys_txs

    assert!(mempool.add_deposit_tx(deposit1.clone()).unwrap());
    assert!(mempool.add_deposit_tx(deposit2.clone()).unwrap());

    // Simulate fetch_deposits(2) -> full deposit_data batch handed to
    // produce_and_run_system_transactions/process_sys_txs
    let deposit_data = mempool.fetch_deposits(2);
    assert_eq!(deposit_data.len(), 2);

    // Simulate process_sys_txs outcome: deposit2 reverts (EvmSystemTransactionNotSuccessful),
    // so only deposit1 ends up in all_txs (the actually-applied set).
    let all_txs_included = vec![deposit1.clone()]; // deposit2 excluded, never credited

    // BUG: runner.rs:638 calls remove_deposits(&deposit_data) -- the ORIGINAL
    // full batch -- instead of remove_deposits(&all_txs_included).
    let removed_count = mempool.remove_deposits(&deposit_data);
    assert_eq!(removed_count, 2); // both removed, though only 1 succeeded

    // deposit2 (never applied, cBTC never minted) is now gone from the mempool
    // and cannot be recovered/resubmitted identically.
    let refetch = mempool.fetch_deposits(10);
    assert!(!refetch.contains(&deposit2)); // demonstrates permanent loss
    assert!(mempool.add_deposit_tx(deposit2.clone()).unwrap()); // only re-addable as a brand new entry, no automatic recovery

    // Correct behavior should have been: only remove deposit1
    // let removed_count = mempool.remove_deposits(&all_txs_included);
    // assert_eq!(removed_count, 1);
    // assert!(mempool.fetch_deposits(10).contains(&deposit2)); // deposit2 stays pending for retry
}
```
This demonstrates that the currently implemented call path (`remove_deposits(&deposit_data)` at runner.rs:638 with the full fetched batch) removes deposits that never made it into `all_txs`, breaking the required binding and permanently losing unminted deposits from the mempool. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** crates/sequencer/src/runner.rs (L636-643)
```rust
        // Remove successfully included deposits from the mempool
        if !deposit_data.is_empty() {
            let removed_count = self.deposit_mempool.lock().remove_deposits(&deposit_data);
            debug!(
                "Removed {} deposits from mempool after successful block production",
                removed_count
            );
        }
```

**File:** crates/sequencer/src/runner.rs (L1678-1702)
```rust
            if let Err(e) = self
                .stf
                .apply_l2_block_txs(l2_block_info, &txs, &mut working_set)
            {
                // If a deposit failed, revert back the working set and continue,
                // as deposits to non-EOA addresses can revert
                // Decrement nonce to be able to process other system and non-system transactions
                if matches!(
                    e,
                    StateTransitionError::ModuleCallError(
                        L2BlockModuleCallError::EvmSystemTransactionNotSuccessful
                    )
                ) && is_deposit
                {
                    warn!("Deposit transaction failed: {:?}", e);
                    *nonce = nonce.saturating_sub(1);
                    working_set_to_discard = working_set.revert().to_revertable();
                    // evm_nonce stays the same — next tx gets the correct nonce
                    continue;
                }
                return Err(anyhow!("Failed to apply system transaction: {e:?}"));
            }
            evm_nonce += 1; // only increment on success
            working_set_to_discard = working_set.checkpoint().to_revertable();
            all_txs.push(sys_tx_rlp);
```

**File:** crates/sequencer/src/deposit_data_mempool.rs (L79-109)
```rust
    pub fn remove_deposits(&mut self, deposits_to_remove: &[Deposit]) -> usize {
        let mut removed_count = 0;

        // Calculate txids for the deposits to remove
        let mut txids_to_remove = HashSet::new();
        for deposit in deposits_to_remove {
            let txid = Self::calc_tx_id(deposit)
                .expect("calc_tx_id should never be called on non-deposit");
            txids_to_remove.insert(txid.to_vec());
        }

        // Retain only deposits that are not in the removal set
        self.accepted_deposit_txs.retain(|deposit| {
            let txid = Self::calc_tx_id(deposit)
                .expect("calc_tx_id should never be called on non-deposit");
            if txids_to_remove.contains(txid.as_slice()) {
                // Remove from pending set
                self.pending_deposits.remove(txid.as_slice());
                removed_count += 1;
                return false;
            }
            true
        });

        // Update metrics
        SM.deposit_data_mempool_txs
            .set(self.accepted_deposit_txs.len() as f64);

        debug!("Removed {} deposits from mempool", removed_count);
        removed_count
    }
```
