### Title
Failed system-layer deposits are permanently dropped from the sequencer's deposit mempool without ever being included in a block - (File: `crates/sequencer/src/runner.rs`)

### Summary
`produce_l2_block_inner` fetches a batch of deposits from `DepositDataMempool`, attempts to convert each into a system transaction via `process_sys_txs`, and blanket-removes the *entire originally fetched batch* from the mempool after block production — regardless of whether each individual deposit's system transaction actually succeeded and made it into the produced block.

### Finding Description
`produce_l2_block_inner` fetches deposits once: [1](#0-0) , and passes this fixed `deposit_data` slice into `dry_run_transactions`, which internally calls `produce_and_run_system_transactions` → `process_sys_txs`. Inside `process_sys_txs`, when applying a deposit's system transaction fails with `EvmSystemTransactionNotSuccessful`, the code explicitly reverts the working set, decrements the nonce, and **silently `continue`s to the next system event without adding that deposit's transaction to `all_txs`**: [2](#0-1) .

This means `all_txs` (and by extension `txs_to_run`) only contains the deposits that succeeded. However, back in `produce_l2_block_inner`, after the block is finalized and saved, the code calls `remove_deposits` on the **original, unfiltered** `deposit_data` variable — not on the subset that was actually included in `all_txs`/`txs_to_run`: [3](#0-2) 

`DepositDataMempool::remove_deposits` performs an unconditional removal of any deposit whose txid matches the passed list, with no correlation to whether that deposit was ever encoded into the produced L2 block: [4](#0-3) . There is no other code path that re-inserts a failed deposit back into the mempool or otherwise records it for retry — once fetched, if its system-tx application fails for any reason, it is removed forever from the pool on the very next successful block, breaking the invariant that a Bitcoin `moveTx` accepted by the mempool (already validated via `citrea_sendRawDepositTransaction`, i.e., dry-run-simulated as callable at time of RPC submission) will eventually be turned into a `deposit()` system transaction and credit cBTC.

The comment at the removal site ("Remove successfully included deposits from the mempool") indicates the intended behavior was to filter to only the deposits that were actually included, but the implementation does not perform this filtering — it operates on the pre-attempt fetch list.

### Impact Explanation
This breaks the binding: *cBTC credited on Citrea == BTC locked via a `moveTx` accepted into the deposit mempool*. If a deposit's system-transaction call reverts inside `Bridge.deposit()` for any reason at block-production time that did not manifest at RPC-admission time (e.g., the bridge is paused via `whenNotPaused` between admission and inclusion, a `replaceDeposit` invalidates assumptions, or any other state change between the `eth_call` simulation in `send_raw_deposit_transaction` and actual execution in `process_sys_txs`), the deposit is discarded from the mempool with the same code path used for genuinely successful deposits. The underlying Bitcoin-side BTC has already been locked into the N-of-N vault via the `moveTx`; unless Clementine's aggregator or some off-chain actor notices the omission and manually resubmits the exact deposit payload via `citrea_sendRawDepositTransaction`, the recipient's cBTC is never credited even though the equivalent BTC deposit genuinely occurred. This is a silent, protocol-level loss of a custody binding (BTC locked vs. cBTC minted), matching the Critical-impact class of "funds permanently frozen" absent manual off-chain intervention that this repository provides no automated mechanism for.

### Likelihood Explanation
Triggering the underlying revert requires a state change between RPC-time simulation (`get_call`, a snapshot-based `eth_call`) and block-production time (`process_sys_txs`, live sequential state application over a batch that also includes prior system events like `setBlockInfo`/`initialize` and other deposits in the same block) — e.g. the bridge owner calling `pause()`, another `replaceDeposit` overwriting a used txId slot, or ordering effects across multiple deposits fetched together (`deposit_mempool_fetch_limit` up to configured limit, e.g. 10 in the shipped configs). This is a plausible, permissionless-adjacent occurrence in normal operation (an owner pausing the bridge, or routine deposit batching) rather than requiring an attacker; it does not require any privileged role to trigger the *loss*, only ordinary operational conditions to cause a deposit's system tx to fail once it reaches `process_sys_txs`.

### Recommendation
Track which deposits from `deposit_data` were actually included in `all_txs`/`txs_to_run` (e.g., have `process_sys_txs`/`produce_and_run_system_transactions` return the subset of deposit payloads that succeeded) and call `remove_deposits` only with that successfully-included subset. Deposits that fail application should remain in the mempool (or be explicitly re-queued) so they are retried in a subsequent block instead of being silently discarded.

### Proof of Concept
1. Clementine submits a valid deposit `D` via `citrea_sendRawDepositTransaction`; `send_raw_deposit_transaction` calls `evm.get_call` against a *pending* snapshot, which succeeds, and `D` is added to `DepositDataMempool` [5](#0-4) .
2. Before the sequencer's next block-production cycle, the bridge owner calls `pause()` on `Bridge.sol` (a normal administrative action) — or a `replaceDeposit` is processed ahead of `D` in the same batch that invalidates an assumption `D`'s script check relies on.
3. `produce_l2_block_inner` fetches `D` among `deposit_data` [1](#0-0) ; `process_sys_txs` attempts to apply `D`'s system transaction, it reverts (`whenNotPaused` or similar), and is caught, reverted, and skipped without being added to `all_txs` [2](#0-1) .
4. The block is produced and saved successfully (containing other, unrelated txs). At the end, `remove_deposits(&deposit_data)` is called with the original fetched list, which still contains `D`, and `D` is permanently removed from the mempool [3](#0-2) [6](#0-5) .
5. The BTC underlying `D` remains locked in the Clementine vault UTXO; the depositor never receives cBTC, and no automated retry occurs — the deposit is lost from the sequencer's perspective unless manually resubmitted off-chain.

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

**File:** crates/sequencer/src/runner.rs (L1678-1699)
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
```

**File:** crates/sequencer/src/deposit_data_mempool.rs (L71-109)
```rust
    /// Removes specific deposits from the mempool after they have been successfully included in a block
    ///
    /// # Arguments
    /// * `deposits_to_remove` - The deposits that were successfully included
    ///
    /// # Returns
    /// The number of deposits actually removed
    #[instrument(level = "trace", skip_all, ret)]
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

**File:** crates/sequencer/src/rpc.rs (L339-366)
```rust
        let dep_tx = self
            .context
            .deposit_mempool
            .lock()
            .make_deposit_tx_from_data(deposit.clone().into());

        let start = std::time::Instant::now();
        let tx_res = evm.get_call(
            dep_tx,
            Some(BlockId::pending()),
            None,
            None,
            &mut working_set,
            &self.context.ledger,
        );
        let deposit_tx_call_duration = Instant::now()
            .saturating_duration_since(start)
            .as_secs_f64();
        SM.deposit_tx_call_duration.record(deposit_tx_call_duration);

        match tx_res {
            Ok(hex_res) => {
                tracing::debug!("Deposit tx processed successfully {}", hex_res);
                let add_result = self
                    .context
                    .deposit_mempool
                    .lock()
                    .add_deposit_tx(Deposit::from(deposit.to_vec()));
```
