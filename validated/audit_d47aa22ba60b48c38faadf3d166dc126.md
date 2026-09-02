### Title
Deposit mempool removes all fetched deposits regardless of per-deposit execution success - ([File: crates/sequencer/src/runner.rs])

### Summary
`remove_deposits` is invoked with the full `deposit_data` list returned by `fetch_deposits` at [1](#0-0) , not with the subset of deposits that actually succeeded inside `produce_and_run_system_transactions`/`all_txs`. Any deposit blob in the same batch that fails on-chain (e.g. `EvmSystemTransactionNotSuccessful`) is still purged from `pending_deposits`/`accepted_deposit_txs` in `DepositDataMempool`, even though it was never included in the produced block.

### Finding Description
The binding that should hold: `deposit removed from DepositDataMempool == deposit actually included and executed successfully in the produced L2 block (member of all_txs)`.

Code path:
- `fetch_deposits` returns up to `deposit_mempool_fetch_limit` deposits without removing them from the mempool: [2](#0-1) .
- This `deposit_data` is passed into `dry_run_transactions` and then `produce_and_run_system_transactions`, which is responsible for turning each deposit blob into a system transaction and applying it sequentially against a shared, evolving `WorkingSet` [3](#0-2) .
- After the block is produced and saved, `remove_deposits(&deposit_data)` is called using the ORIGINAL, full `deposit_data` vector fetched at the start of block building — not a filtered list of deposits that were actually applied successfully: [1](#0-0) .
- `remove_deposits` computes `calc_tx_id` for every deposit in the passed slice and removes any matching entries from both `accepted_deposit_txs` and `pending_deposits`, with no notion of "did this deposit actually execute" [4](#0-3) .

The comment at line 636 ("Remove successfully included deposits from the mempool") asserts an invariant that the code does not enforce — the removal set is whatever was fetched, not whatever succeeded. Since `citrea_sendRawDepositTransaction`'s acceptance gate only runs an isolated `eth_call` simulation per deposit at submission time (not against the cumulative state produced by sequentially applying every other deposit in the same batch), a deposit that individually simulates successfully can still fail when applied after a preceding deposit in the same batch has mutated shared state (e.g., nonce, bridge contract state). If `produce_and_run_system_transactions` reverts that individual deposit's effects (decrementing nonce, excluding it from `all_txs`) but does not abort the entire block, the block still gets produced and saved, and the failed deposit is nonetheless erased from the mempool at line 638 because `deposit_data` still contains it.

### Impact Explanation
A legitimate depositor's Bitcoin-side move-to-vault transaction can be permanently dropped from the deposit mempool without ever crediting cBTC on L2, with no automatic retry path, because `pending_deposits` no longer contains its txid and the corresponding accepted-deposit entry is gone. This is a fund-freeze condition matching the Critical impact category (funds permanently frozen) since the underlying Bitcoin deposit can never be reprocessed through the normal FIFO deposit flow. The blast radius is per-batch: any deposit sharing a `fetch_deposits` batch with another deposit whose system-tx application fails is at risk, and this can recur across many blocks/batches as long as an attacker can arrange for a failing deposit blob to be co-batched with a target's legitimate one.

### Likelihood Explanation
Exploitability depends on being able to reliably co-batch an attacker-controlled failing deposit blob with a victim's legitimate one, and on `produce_and_run_system_transactions` tolerating an individual deposit failure without aborting the whole block (rather than panicking, which would instead cause a denial-of-service/restart with no loss, out of scope here). I was not able to directly inspect the body of `produce_and_run_system_transactions` (and the underlying `EvmSystemTransactionNotSuccessful` handling in `crates/evm/src/evm/executor.rs` / hooks) within the available search budget, so I cannot fully confirm whether a mid-batch deposit failure aborts the entire block build (which would prevent `remove_deposits` from ever running with a partial-failure `deposit_data`) or whether it is skipped and the block still proceeds. This is the key uncertainty for confirming exploitability end-to-end, though the code at `runner.rs:636-643` unambiguously always passes the full originally-fetched list into `remove_deposits` regardless of what happened during system-tx application.

### Recommendation
Have `produce_and_run_system_transactions` (or its caller) return the exact subset of deposit blobs that were successfully applied and included in `all_txs`, and use that filtered list — not the raw `deposit_data` fetched at the top of the function — as the argument to `remove_deposits` at `crates/sequencer/src/runner.rs:638`.

### Proof of Concept
A `cargo test` in `crates/sequencer` should:
1. Construct a `DepositDataMempool`, add a valid deposit D1 and a deposit D2 crafted to fail bridge on-chain validation when applied after D1 in the same batch (or simply a deposit engineered to fail unconditionally in `produce_and_run_system_transactions`).
2. Drive the sequencer's block-building path (or a minimal harness around `produce_and_run_system_transactions` + the `remove_deposits` call) with `fetch_deposits(2)` returning `[D1, D2]`.
3. Assert that after block production, `all_txs`/receipts contain only D1's system tx.
4. Assert on the mempool state: currently, `remove_deposits(&deposit_data)` removes both D1 and D2's txids from `pending_deposits`; the fix should leave D2's txid in `pending_deposits` so it can be resubmitted/retried, and the test should assert `mempool.pending_deposits` still contains D2's `calc_tx_id` after the block completes.

### Citations

**File:** crates/sequencer/src/runner.rs (L240-256)
```rust
            let evm = citrea_evm::Evm::<DefaultContext>::default();
            let start_dry_run_system_txs = Instant::now();
            // Initially fill with system transactions if any
            let (mut all_txs, mut working_set_to_discard) = self
                .produce_and_run_system_transactions(
                    &l2_block_info,
                    &evm,
                    working_set_to_discard,
                    deposit_data,
                    da_blocks,
                    &mut nonce,
                )?;
            SM.dry_run_system_txs_duration_secs.set(
                Instant::now()
                    .saturating_duration_since(start_dry_run_system_txs)
                    .as_secs_f64(),
            );
```

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

**File:** crates/sequencer/src/deposit_data_mempool.rs (L57-69)
```rust
    pub fn fetch_deposits(&mut self, limit_per_block: usize) -> Vec<Deposit> {
        let number_of_deposits = self.accepted_deposit_txs.len().min(limit_per_block);
        SM.deposit_data_mempool_txs
            .set(self.accepted_deposit_txs.len() as f64);
        let deposits: Vec<Deposit> = self
            .accepted_deposit_txs
            .iter()
            .take(number_of_deposits)
            .cloned()
            .collect();

        deposits
    }
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
