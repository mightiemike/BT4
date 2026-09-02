### Title
Deposit removed from sequencer mempool even when the on-chain deposit system transaction reverts, permanently dropping a legitimate Bitcoin deposit - (File: `crates/sequencer/src/runner.rs`)

### Summary
`produce_l2_block_inner` fetches candidate deposits from the `DepositDataMempool` and, after block production, unconditionally removes the *entire original fetched batch* from the mempool — including any deposit whose system transaction was reverted and excluded from the produced block. This is the same bug class as the reported Timelock issue: an operation that can fail (or have no effect) still causes the queued item to be discarded, permanently losing state that should have been retried.

### Finding Description
In `produce_l2_block_inner`, the sequencer fetches pending deposits from the mempool without removing them: [1](#0-0) 

This `deposit_data` list is passed into `dry_run_transactions` → `produce_and_run_system_transactions` → `process_sys_txs`, which builds one system transaction per deposit and applies it. Critically, when a deposit system transaction fails, `process_sys_txs` reverts the working set for that transaction and simply `continue`s to the next event, without adding the failed transaction to `all_txs` (the list of transactions that actually end up in the block): [2](#0-1) 

After block production completes, the sequencer calls `remove_deposits` using the *original* `deposit_data` variable captured at line 517 — not the subset that was actually included in `all_txs`/the finalized block: [3](#0-2) 

`remove_deposits` deletes every deposit in the passed list from both `accepted_deposit_txs` and `pending_deposits`: [4](#0-3) 

Since `pending_deposits` is also cleared, the deposit's txid is no longer tracked as "already pending," so nothing prevents processing — but nothing re-submits it either, since the only path into the mempool is `citrea_sendRawDepositTransaction`, called once by Clementine's aggregator: [5](#0-4) 

A deposit's system transaction can legitimately fail for reasons unrelated to `moveTx` validity — e.g. `EvmSystemTransactionNotSuccessful` is returned whenever the EVM call to `BridgeWrapper::deposit` reverts (bad nonce sequencing during concurrent event processing, unrelated gas/base-fee edge cases, or transient EVM execution errors), a case explicitly anticipated by the code comment "deposits to non-EOA addresses can revert": [6](#0-5) 

In all such cases the deposit is stripped from the mempool as if it had been processed, exactly mirroring the reported vulnerability class: "queued data will be lost if Tx is unsuccessful," because the removal path does not distinguish between successfully-included and reverted/excluded transactions.

### Impact Explanation
This breaks the fundamental bridge invariant that a real Bitcoin-side deposit (`moveTx`) must eventually result in a matching cBTC credit on Citrea. Once the sequencer fetches a deposit and its system transaction fails for any reason during block assembly, the deposit is deleted from `accepted_deposit_txs`/`pending_deposits` and there is no other component in the ingestion path (`crates/sequencer/src/rpc.rs`, `crates/sequencer/src/deposit_data_mempool.rs`) that re-queues it. The depositor's BTC remains locked in the bridge's N-of-N vault UTXO on Bitcoin, but the cBTC mint never occurs and can never be retried through the normal flow, since Clementine's aggregator submits the raw deposit transaction only once via `citrea_sendRawDepositTransaction`. This is a permanent, unrecoverable loss of bridge funds — a Critical impact under "cBTC minted, credited or moved without a matching Bitcoin-side deposit" (inverse form: a Bitcoin-side deposit occurring with no matching cBTC credit, permanently).

### Likelihood Explanation
The trigger does not require any privileged role or malicious actor — it only requires one of the deposit system transactions in a batch to revert while other unrelated transactions/system events are processed in the same block-production cycle (e.g., nonce/ordering issues between sys events, transient EVM validation errors, or any future change to `BridgeWrapper::deposit` behavior that reverts under edge-case inputs the sequencer does not pre-validate). Because `process_sys_txs` explicitly documents that "deposits to non-EOA addresses can revert" as an expected, non-fatal case, this is a foreseeable and reachable condition in normal sequencer operation, not a purely theoretical scenario.

### Recommendation
Track which deposits were actually included in `all_txs` (successfully applied system transactions) separately from the deposits merely fetched for the block attempt, and only call `remove_deposits` with that successfully-included subset. Deposits whose system transaction failed/reverted should remain in `accepted_deposit_txs` (and `pending_deposits`) so they are retried in a subsequent block, mirroring the correct handling already used for EVM user transactions, which are kept in the mempool and marked invalid/retried rather than silently dropped.

### Proof of Concept
Conceptual PoC (would require a running sequencer + modified `Bridge.sol` test double, as no live harness is available in the index):
1. Submit a valid deposit via `citrea_sendRawDepositTransaction`; it is accepted into `DepositDataMempool.accepted_deposit_txs`.
2. Craft conditions so that when `process_sys_txs` builds the deposit's `BridgeWrapper::deposit` system transaction, execution returns `L2BlockModuleCallError::EvmSystemTransactionNotSuccessful` (e.g., by having a preceding system event or interaction shift the expected system-signer nonce, or by relying on a target-recipient path that reverts as already anticipated by the code comment at line 1683).
3. Observe `process_sys_txs` reverting the working set and `continue`-ing (lines 1682-1697), so the deposit transaction is excluded from `all_txs` and thus from the finalized L2 block.
4. Observe that `produce_l2_block_inner` still calls `self.deposit_mempool.lock().remove_deposits(&deposit_data)` with the full original `deposit_data` (line 638), removing the failed deposit's txid from both `accepted_deposit_txs` and `pending_deposits`.
5. Confirm no other code path resubmits the deposit — `citrea_sendRawDepositTransaction` is only invoked once by Clementine per deposit — so the recipient never receives cBTC despite the BTC being irrevocably locked on the Bitcoin side. [1](#0-0) [3](#0-2) [4](#0-3)

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

**File:** crates/sequencer/src/rpc.rs (L359-380)
```rust
        match tx_res {
            Ok(hex_res) => {
                tracing::debug!("Deposit tx processed successfully {}", hex_res);
                let add_result = self
                    .context
                    .deposit_mempool
                    .lock()
                    .add_deposit_tx(Deposit::from(deposit.to_vec()));

                match add_result {
                    Ok(true) => Ok(()),
                    Ok(false) => Err(jsonrpsee::types::error::ErrorObject::owned(
                        jsonrpsee::types::error::INVALID_PARAMS_CODE,
                        "Deposit already pending in mempool",
                        None::<()>,
                    )),
                    Err(_) => Err(jsonrpsee::types::error::ErrorObject::owned(
                        jsonrpsee::types::error::INVALID_PARAMS_CODE,
                        "Invalid deposit",
                        None::<()>,
                    )),
                }
```
