## Finding

The described TOCTOU chain in the question (`eth_call` simulation vs. `Bridge` owner-gated state) does not hold — `setFailedDepositVault`, `depositPrefix`/`depositSuffix` etc. are `onlyOwner` [1](#0-0)  and unreachable by an unprivileged caller, and the deposit's `txId` is cryptographically bound to the actual Bitcoin `MoveToVault` transaction bytes via `calc_tx_id`, so an attacker cannot forge a collision or arbitrarily flip Bridge-relevant state between simulation and production. However, tracing the exact call sequence the question asks about (`fetch_deposits` → `process_sys_txs` → post-block cleanup) surfaces a real, independent bug that produces exactly the "funds permanently frozen" impact claimed.

### Title
Deposits are unconditionally removed from the mempool after block production even if `process_sys_txs` reverted them, causing silent permanent loss of valid BTC deposits - (File: crates/sequencer/src/runner.rs)

### Summary
`process_sys_txs` intentionally tolerates individual deposit failures by reverting the checkpoint and `continue`-ing, skipping that deposit for the current block [2](#0-1) . However, `produce_l2_block_inner` calls `self.deposit_mempool.lock().remove_deposits(&deposit_data)` using the *original, unfiltered* `deposit_data` list fetched at the top of the function, not the subset that actually succeeded in `process_sys_txs` [3](#0-2) . This means any deposit that fails during actual execution is removed from the FIFO deposit mempool exactly as if it had succeeded, and is never retried.

### Finding Description
The binding that should hold is: `deposit ∈ deposit_data removed from mempool` ⇔ `deposit was successfully applied in process_sys_txs (i.e. present in all_txs)`.

Tracing the code:
- `produce_l2_block_inner` fetches deposits: `let deposit_data = self.deposit_mempool.lock().fetch_deposits(self.config.deposit_mempool_fetch_limit);` [4](#0-3) 
- These are passed into `produce_and_run_system_transactions` → `process_sys_txs`, which builds `all_txs` containing only transactions that succeeded; any deposit whose `EvmSystemTransactionNotSuccessful` error occurs is reverted and skipped via `continue`, decrementing the sov-tx nonce but **not being recorded anywhere as "failed"** [5](#0-4) 
- After the block is finalized and saved, the *original* `deposit_data` (not `all_txs`, not any filtered/successful subset) is passed to `remove_deposits`: `let removed_count = self.deposit_mempool.lock().remove_deposits(&deposit_data);` [3](#0-2) 
- `remove_deposits` deletes the deposit from both `accepted_deposit_txs` (the FIFO queue) and `pending_deposits` (the dedupe set) unconditionally for every deposit in the passed-in list [6](#0-5) 

Once removed from `pending_deposits`, the deposit's `txId` dedupe entry is gone, but nothing resubmits the exact original raw bytes automatically — the Clementine aggregator or user would have to notice the failure and resubmit, and the sequencer itself gives no signal that the deposit was dropped rather than included. The BTC is already locked in the vault on the Bitcoin side (`MoveToVault` already confirmed), but the corresponding cBTC mint never happens and the deposit is permanently gone from the L2 mempool after exactly one failed attempt.

The comment at the failure branch — "as deposits to non-EOA addresses can revert" [7](#0-6)  — confirms the code authors anticipated real-world deposit failures at production time (e.g., recipient contract reverting, or any state divergence between the `eth_call` simulation against `BlockId::pending()` and the actual `prestate` used at production, as the question describes). The system was designed to *tolerate* such failures per-block, but the mempool bookkeeping does not distinguish "included" from "attempted-and-dropped", defeating that tolerance and turning a transient failure into permanent fund loss.

### Impact Explanation
A deposit that is valid on the Bitcoin side (real BTC locked in the Clementine vault) can be permanently dropped from the L2 sequencer's deposit mempool after a single failed production attempt, with no retry and no cBTC ever minted for it. This matches the "funds permanently frozen" Critical impact category. It is repeatable for every deposit that experiences any transient execution failure at production time (nonce ordering artifacts, recipient-contract reverts, or genuine TOCTOU divergence between the `eth_call` pending-state simulation and the real prestate) and affects any node/sequencer running this code, not just a single block.

### Likelihood Explanation
This does not require owner privileges or bypassing SPV/light-client checks — it only requires a deposit to fail once during `process_sys_txs` after having passed the earlier `eth_call` simulation, which the code's own comments acknowledge as an expected, non-exceptional occurrence (e.g., recipient logic reverting due to intervening state changes). Given the sequencer already anticipates and handles per-deposit failure gracefully at the EVM layer, the missing filter in the mempool-cleanup step is a straightforward oversight, likely to occur in production without any attacker action, and trivially triggerable by an attacker who arranges for their own deposit's target recipient/state to revert on the second real execution attempt after passing simulation once.

### Recommendation
Track which deposits actually succeeded during `process_sys_txs` (e.g., collect them alongside `all_txs`, or return per-deposit success/failure) and pass only that successful subset to `remove_deposits` in `produce_l2_block_inner`, instead of the full `deposit_data` fetched at the start of block production. Failed deposits should remain in `accepted_deposit_txs`/`pending_deposits` so they are retried on a subsequent block, or at minimum be surfaced via a metric/log so operators can intervene.

### Proof of Concept
`cargo test` plan (sequencer integration test crate):
1. Deploy a recipient contract on L2 whose fallback/receive reverts only when a specific storage flag is set (attacker or test controls the flag via a prior ordinary tx).
2. Craft/submit a deposit blob via `citrea_sendRawDepositTransaction` whose `eth_call` simulation (against `BlockId::pending()`, flag unset) succeeds → `add_deposit_tx` returns `true`.
3. Before block production, submit an ordinary tx that flips the flag so the recipient now reverts.
4. Force block production (`citrea_testPublishBlock`); assert `process_sys_txs` hits the `EvmSystemTransactionNotSuccessful` branch and skips the deposit.
5. Assert on the mempool state after block production: expect the deposit to still be present in `DepositDataMempool` (retryable) — but observe that `remove_deposits(&deposit_data)` was called with the original list, so `accepted_deposit_txs.len() == 0` and `pending_deposits.len() == 0`, i.e. the deposit is gone despite never being minted, proving the funds are unrecoverable through the sequencer's normal retry path.

### Citations

**File:** crates/evm/src/evm/system_contracts/src/Bridge.sol (L1-1)
```text
// SPDX-License-Identifier: GPL-3.0-only
```

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
