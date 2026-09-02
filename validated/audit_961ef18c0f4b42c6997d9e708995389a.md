### Title
Deposit mempool eviction uses the full fetched batch, not the subset `process_sys_txs` actually applied - (`crates/sequencer/src/runner.rs`)

### Summary
`CitreaSequencer` calls `self.deposit_mempool.lock().remove_deposits(&deposit_data)` using the raw `deposit_data` vector returned by `fetch_deposits`, without ever intersecting it with the subset of deposits that `process_sys_txs` actually committed. When a deposit's system transaction fails and hits the revert-and-continue branch, its state change is discarded, but it is still evicted from the mempool as if it had succeeded.

### Finding Description
The claimed binding: `set(deposits passed to DepositDataMempool::remove_deposits)` == `set(deposits for which process_sys_txs's apply_l2_block_txs returned Ok(_))` for that block.

Trace:
1. `deposit_data` is fetched once via `fetch_deposits` (without removing) at [1](#0-0) .
2. It is passed into system-transaction production, which flows into `process_sys_txs`, where each deposit's `BridgeDeposit` event is applied inside a revertable checkpoint: [2](#0-1) . On failure specific to a deposit (`EvmSystemTransactionNotSuccessful`), the working set is reverted via `working_set.revert()` and the loop `continue`s without pushing anything to `all_txs` for that deposit — i.e., that deposit is silently dropped from the produced transaction set.
3. Only on success is the deposit's system tx appended to `all_txs` [3](#0-2) , which is the actual code path that determines what gets applied to the real block.
4. However, after the block is finalized and saved, mempool eviction is done with the *original, unfiltered* `deposit_data` variable, not with any set derived from `process_sys_txs`'s success/failure decisions: [4](#0-3) .
5. `DepositDataMempool::remove_deposits` unconditionally evicts every deposit passed to it, recomputing `calc_tx_id` and removing matching entries from both `accepted_deposit_txs` and `pending_deposits`: [5](#0-4) .

There is no code anywhere in `runner.rs` that threads back which specific deposits from the batch survived `process_sys_txs`'s revert-and-continue branch to filter the removal call. `produce_and_run_system_transactions`/`process_sys_txs` return only `(Vec<RlpEvmTransaction>, WorkingSet)` with no per-deposit success/failure list [6](#0-5) , so the block-production caller has no way to reconstruct the successfully-applied subset even if it wanted to.

Consequently, if any deposit in a `fetch_deposits` batch fails inside `process_sys_txs` while sibling deposits succeed, **all** fetched deposits, including the failed one, are removed from the mempool at line 638, even though the failed deposit's cBTC mint was reverted and never landed on-chain.

### Impact Explanation
A depositor whose deposit hits the revert-and-continue branch has their deposit permanently evicted from `accepted_deposit_txs`/`pending_deposits` without the corresponding cBTC ever being credited on L2, since `working_set.revert()` undid the state change. This is a fund-freezing bug matching the Critical category (funds effectively lost from the mempool's bookkeeping perspective for that specific fetch cycle), independent of whether re-submission via `citrea_sendRawDepositTransaction` succeeds later — the guarantee that "removed == applied" is broken, which is the exact bug being probed. This affects every sequencer-produced block that contains a mixed batch of successful and failing deposits, and is fully deterministic/repeatable given such a batch composition.

### Likelihood Explanation
This requires no attacker privilege beyond being able to submit an ordinary deposit whose recipient/contract call reverts under real system-tx conditions (already an accepted, in-scope scenario per the question's precondition about the OOG/state-mismatch branch), combined with at least one sibling deposit in the same `fetch_deposits(limit_per_block)` batch that succeeds. Since `deposit_mempool_fetch_limit` batches multiple pending deposits together by design, this is a normal operating condition, not a rare edge case, and reproducible by controlling deposit ordering/content.

### Recommendation
Track which deposits were actually applied (i.e., which `BridgeDeposit` events made it past the revert-and-continue branch in `process_sys_txs`, e.g. by returning a `Vec<Deposit>` of applied deposits from `produce_and_run_system_transactions`/`process_sys_txs`), and call `remove_deposits` only with that successfully-applied subset instead of the raw `deposit_data` fetched at the start of block production.

### Proof of Concept
```
cargo test -p citrea-sequencer -- runner::tests
```
Construct a `deposit_data` batch of two deposits (D1, D2) where D1's `BridgeDeposit` system tx is engineered to hit `L2BlockModuleCallError::EvmSystemTransactionNotSuccessful` inside `process_sys_txs` (e.g., target a contract that reverts on receive) while D2 succeeds normally. After block production completes:
- Assert D2's `calc_tx_id` is absent from `accepted_deposit_txs`/`pending_deposits` (correctly removed).
- Assert D1's `calc_tx_id` is still present in `accepted_deposit_txs`/`pending_deposits` (should NOT have been removed, since it was never applied) — this assertion currently FAILS against the code at `runner.rs:636-643`, since `remove_deposits` is invoked with the full unfiltered `deposit_data` including D1.

### Citations

**File:** crates/sequencer/src/runner.rs (L516-520)
```rust
        // Get pending deposits up to configured limit
        let deposit_data = self
            .deposit_mempool
            .lock()
            .fetch_deposits(self.config.deposit_mempool_fetch_limit);
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

**File:** crates/sequencer/src/runner.rs (L1552-1563)
```rust
    fn produce_and_run_system_transactions(
        &mut self,
        l2_block_info: &HookL2BlockInfo,
        evm: &Evm<DefaultContext>,
        working_set_to_discard: WorkingSet<<DefaultContext as Spec>::Storage>,
        deposit_data: &[Deposit],
        da_blocks: Vec<Da::FilteredBlock>,
        nonce: &mut u64,
    ) -> anyhow::Result<(
        Vec<RlpEvmTransaction>,
        WorkingSet<<DefaultContext as Spec>::Storage>,
    )> {
```

**File:** crates/sequencer/src/runner.rs (L1675-1697)
```rust
            // Create checkpoint for potential revert
            let mut working_set = working_set_to_discard.checkpoint().to_revertable();

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
```

**File:** crates/sequencer/src/runner.rs (L1700-1702)
```rust
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
