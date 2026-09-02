## Analysis: Deposit mempool removal after failed system transaction execution [1](#0-0) 

This is the closest analog to the reported bug class in the in-scope Citrea repository.

### Title
Sequencer removes fetched deposit data from `DepositDataMempool` regardless of whether the corresponding Bridge deposit system transaction actually succeeded on-chain - (File: `crates/sequencer/src/runner.rs`)

### Summary
`produce_and_run_system_transactions` fetches a batch of deposit payloads from `DepositDataMempool::fetch_deposits` and turns them into system events/transactions via `process_sys_txs`. [2](#0-1)  When a deposit's system transaction fails to apply (`EvmSystemTransactionNotSuccessful`), the working set is reverted, the nonce is rolled back, and the loop `continue`s to the next event — meaning that specific deposit was **not** included in the produced L2 block. [3](#0-2)  Despite this, after the block is committed, the sequencer removes the *entire originally fetched* `deposit_data` slice from `DepositDataMempool` via `remove_deposits`, with a comment claiming these are the "successfully included deposits". [4](#0-3) 

### Finding Description
The binding that should hold is: *every deposit whose Bitcoin-side move transaction is valid and whose system transaction is actually applied should be, and remain, retriable until it is actually applied* (i.e., `deposit removed from mempool` ⇔ `deposit system tx succeeded and is included in an L2 block`). The code path that determines whether a deposit tx succeeded (`process_sys_txs`, which `continue`s past failed deposits without recording which ones failed) is decoupled from the code path that decides what gets purged from the retry queue (`remove_deposits(&deposit_data)`, which operates on the full, unfiltered batch that was fetched before execution). This mirrors the LayerZeroAdapter class of bug: the item that is *actually processed* (or, here, *not* processed) is not the same set of items that is *removed from the pending/retry store*.

`DepositDataMempool::remove_deposits` itself matches deposits by content-derived txid rather than position [5](#0-4) , so it is not vulnerable to a positional index-shift the way the LayerZero `pop()` bug was. However, that guarantees only that the *correct* deposit is removed for whatever is passed to it — it does not guarantee that only *successfully executed* deposits are passed to it. If `produce_and_run_system_transactions`/`process_sys_txs` returns without communicating which individual deposits were skipped due to revert, the caller in `runner.rs` has no way to exclude them from the removal set, and the full pre-execution list is removed unconditionally.

### Impact Explanation
If a deposit's system transaction reverts for any reason once accepted into a block-production round (e.g., a transient EVM/gas condition, or any `EvmSystemTransactionNotSuccessful` case reachable through the deposit path), the corresponding Bitcoin move-transaction deposit is removed from the sequencer's retry mempool even though cBTC was never credited on L2. Because the mempool's de-duplication logic keys off `pending_deposits` derived from the same txid, and that entry was cleared by `remove_deposits`, the user (or the relayer resubmitting via `send_raw_deposit_transaction`) could theoretically resubmit — but if this is the sequencer's only automatic retry path, an operationally-dropped deposit that never gets resubmitted results in a Bitcoin-side deposit that occurred with no matching cBTC credit, i.e. permanently frozen bridged funds — one of the explicitly listed Critical impacts (cBTC credited/moved without a matching Bitcoin-side deposit is the mirror image of this: here funds are permanently withheld despite a valid deposit).

### Likelihood Explanation
This requires only an unprivileged deposit whose system transaction happens to fail on first attempt during the specific block-production round it was batched into — a scenario the code already anticipates and explicitly handles as "expected" (the `continue` branch exists specifically for this case), rather than a contrived adversarial condition. No sequencer/prover/operator misbehavior is required — this is a correctness gap in how success is tracked versus how the retry queue is pruned in the standard code path.

### Recommendation
Have `process_sys_txs`/`produce_and_run_system_transactions` return the concrete list of deposit payloads whose system transaction was actually applied successfully (mirroring which ones are included in `all_txs`), and use exactly that filtered list — not the original fetched batch — as the argument to `DepositDataMempool::remove_deposits`. This restores the equality between "system tx applied" and "removed from pending/retry store."

### Proof of Concept
Not runnable from the indexed context alone: reproducing requires forcing `apply_l2_block_txs` to return `EvmSystemTransactionNotSuccessful` for a deposit inside `process_sys_txs` (e.g. by crafting a deposit whose corresponding EVM call reverts for a non-`failedDepositVault`-covered reason) during a `produce_l2_block` cycle, then observing that `remove_deposits` at `crates/sequencer/src/runner.rs:638` purges that deposit from `DepositDataMempool` even though no bridge deposit event was actually committed to the L2 block.

**Note on confidence**: I was unable to fully trace, within the remaining iteration budget, the exact variable-flow proving that the `deposit_data` binding used at `runner.rs:638` is *always* the unfiltered pre-execution batch rather than an already-filtered "successful only" list produced elsewhere in `produce_l2_block`. The function signature of `produce_and_run_system_transactions` returns only `(Vec<RlpEvmTransaction>, WorkingSet)` — not a filtered deposit list — which strongly suggests no filtering occurs before the removal call, but I could not directly inspect the full body of the caller function (`produce_l2_block`) that owns `deposit_data` at line 638 to rule out an intermediate filter. A background agent with full repository access should verify this call chain before treating this as fully confirmed.

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

**File:** crates/sequencer/src/runner.rs (L1596-1607)
```rust
        let deposit_events = populate_deposit_system_events(deposit_data);

        system_events.extend(deposit_events);

        self.process_sys_txs(
            l2_block_info,
            working_set_to_discard,
            nonce,
            evm,
            system_events,
        )
    }
```

**File:** crates/sequencer/src/runner.rs (L1678-1703)
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
        }
```

**File:** crates/sequencer/src/deposit_data_mempool.rs (L79-101)
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
```
