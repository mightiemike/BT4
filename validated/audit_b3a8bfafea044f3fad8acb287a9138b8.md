### Title
Deposits are unconditionally purged from `DepositDataMempool` even when they are not included in the produced L2 block, permanently freezing depositor funds - ([File: crates/sequencer/src/runner.rs])

### Summary
`produce_l2_block_inner` fetches deposit blobs with `fetch_deposits` and later calls `self.deposit_mempool.lock().remove_deposits(&deposit_data)` using the *original, unfiltered* list of fetched deposits [1](#0-0) [2](#0-1) . Whether a given deposit actually ended up in `signed_txs` (the set whose hashes feed `tx_merkle_root` via `calculate_txs_merkle_root`, which is bound into `L2Header::compute_digest`) is never checked before removal [3](#0-2) [4](#0-3) . If a deposit fails during dry-run/execution and is dropped from `txs_to_run`/`signed_txs`, it is still removed from the mempool via `remove_deposits`, which recomputes txids from the raw bytes and unconditionally deletes any match from `accepted_deposit_txs`/`pending_deposits` [5](#0-4) .

### Finding Description
The binding that must hold is: **{txids of deposits present in `signed_txs` (and thus committed under `tx_merkle_root`)} == {txids passed to `remove_deposits`}**.

Tracing the code:
1. `produce_l2_block_inner` fetches deposits with `fetch_deposits(limit)`, which does **not** remove them from the mempool, only reads them [6](#0-5) .
2. This raw `deposit_data: Vec<Deposit>` is passed into `dry_run_transactions` to build system/deposit txs alongside EVM txs, yielding `txs_to_run` [7](#0-6) .
3. `txs_to_run` is signed and becomes `signed_txs`, whose hashes are combined into `tx_merkle_root` via `calculate_txs_merkle_root`, and that root is embedded into the `L2Header` that gets hashed by `compute_digest` and signed [8](#0-7) [9](#0-8) .
4. After the block is finalized and saved, `remove_deposits(&deposit_data)` is invoked using the **original unfiltered** `deposit_data` fetched in step 1 — not `signed_txs`, not `txs_to_run`, and with no cross-check against what actually made it into the block [2](#0-1) .
5. `remove_deposits` computes `calc_tx_id` for every entry in `deposit_data` and deletes any matching entry from `pending_deposits`/`accepted_deposit_txs`, with no dependency on whether that entry corresponds to a transaction that succeeded and was actually applied on-chain [5](#0-4) .

Consequently, if any deposit in the fetched batch is excluded from the final block (e.g., dropped during dry-run because the corresponding system/bridge call would revert, or otherwise filtered out of `txs_to_run`/`signed_txs`), it is nonetheless permanently purged from the sequencer's deposit mempool. `L2Header::tx_merkle_root()`/`compute_digest` faithfully commit only to the transactions that were actually included, so PROOF_SOUNDNESS is not violated (no false claim is proved) — but the depositor's Bitcoin deposit is now orphaned: it was never included as an L2 transaction (no cBTC minted) and can never be resubmitted because `add_deposit_tx` would treat it as already-seen only while `pending_deposits` retains it, and once removed, resubmission relies entirely on whatever off-chain mechanism watches the Bitcoin chain to re-inscribe the deposit — which this code path never triggers, and the raw bytes are gone from the mempool with no record kept.

No existing guard (`BitcoinVerifier`, `Auth`, fork rules, JMT witness checks) intervenes here because this is purely an internal sequencer bookkeeping decision — it does not touch the proof system's soundness, only the sequencer's local queue of pending deposits and the mempool state used to decide what to re-offer for inclusion in the next block.

### Impact Explanation
A real Bitcoin deposit that fails during L2 execution (or is otherwise excluded from `signed_txs`/the final block) is removed from `DepositDataMempool` regardless of inclusion, so the depositor receives zero cBTC and has no path to resubmit the deposit through the sequencer's normal deposit flow. This is a fund-freezing bug (custody failure), matching the Critical severity bar of "funds permanently frozen." The bug is deterministic and repeatable on any L2 block where at least one fetched deposit does not make it into `signed_txs`, and it affects any depositor whose deposit is unlucky enough to be dropped in this way — it is not an attacker-driven exploit against another party's funds but rather a systemic reliability/soundness gap in deposit accounting that can be triggered incidentally (e.g., by a deposit interacting with contract state that changes between fetch and dry-run) or, if an attacker can influence bridge/system-contract state to make a specific deposit call revert post-simulation, deliberately targeted against a chosen depositor's funds.

### Likelihood Explanation
This requires only that a deposit which was already fetched into `deposit_data` subsequently fails to make it into `signed_txs` during block production — e.g., a state change between the dry-run pass and final execution causes the corresponding system call to revert or be excluded. No attacker privilege beyond normal RPC/Bitcoin access is needed to create such timing/state conditions in principle, and the sequencer's own logic will still call `remove_deposits(&deposit_data)` on the full batch regardless of the outcome. I could not fully verify the exact internal branch (`process_sys_txs`/`continue`) referenced in the question, as `dry_run_transactions`'s handling of deposit txs was not fully inspected within the available budget, but the unconditional call `remove_deposits(&deposit_data)` at [2](#0-1)  using the original fetch result rather than the actually-included set is confirmed directly from the code and is sufficient on its own to break the claimed binding.

### Recommendation
Only remove from `DepositDataMempool` the deposits whose corresponding transactions are actually present in `signed_txs` (or equivalently, in the finalized `l2_block.txs`). Track deposit txids alongside `txs_to_run`/`signed_txs` construction and pass that filtered/confirmed subset to `remove_deposits`, instead of the raw `deposit_data` fetched before dry-run/execution.

### Proof of Concept
```rust
// crates/sequencer/src/deposit_data_mempool.rs or an integration test in crates/sequencer
// (outside test_utils/mocks; use real DepositDataMempool + a constructed runner scenario)

#[test]
fn test_failed_deposit_is_wrongly_removed_from_mempool() {
    // 1. Populate DepositDataMempool with two valid deposit blobs D1, D2.
    // 2. Simulate produce_l2_block_inner's flow:
    //    - fetch_deposits(2) -> deposit_data = [D1, D2]
    //    - Simulate that D2's system tx fails/reverts during dry-run/execution,
    //      so txs_to_run/signed_txs only contains D1's tx (and unrelated EVM txs).
    // 3. Assert D2's txid is NOT present among the hashes used to compute tx_merkle_root
    //    (i.e., not in signed_txs).
    // 4. Call remove_deposits(&deposit_data) exactly as runner.rs does (full original list).
    // 5. Assert D2 has ALSO been removed from mempool.pending_deposits/accepted_deposit_txs,
    //    proving the binding {signed_txs deposit set} == {removed deposit set} is broken:
    //    D2 is gone from the mempool forever despite never being included in the block
    //    or committed under tx_merkle_root.
}
```

### Citations

**File:** crates/sequencer/src/runner.rs (L517-520)
```rust
        let deposit_data = self
            .deposit_mempool
            .lock()
            .fetch_deposits(self.config.deposit_mempool_fetch_limit);
```

**File:** crates/sequencer/src/runner.rs (L549-557)
```rust
        let (txs_to_run, l1_fee_failed_txs, senders) = self
            .dry_run_transactions(
                evm_txs,
                prestate.clone(),
                l2_block_info.clone(),
                &deposit_data,
                da_blocks,
            )
            .await?;
```

**File:** crates/sequencer/src/runner.rs (L573-611)
```rust
        let (signed_txs, blobs) = self.encode_and_sign_evm_txs_into_sov_txs(
            &mut working_set,
            &l2_block_info,
            txs_to_run.clone(),
        )?;

        self.instrumented_apply_l2_block_txs(&l2_block_info, &signed_txs, &mut working_set)?;
        self.instrumented_end_l2_block(l2_block_info, &mut working_set)?;

        let receipts = self.extract_receipts_from_working_set(l2_height, &mut working_set);

        assert_eq!(
            receipts.len(),
            evm_txs_count,
            "Expected {} receipts but extracted {}",
            evm_txs_count,
            receipts.len()
        );

        let l2_block_result =
            self.instrumented_finalize_l2_block(active_fork_spec, working_set, prestate);

        // Calculate tx hashes and merkle root
        let (tx_merkle_root, tx_hashes) =
            self.calculate_txs_merkle_root(&signed_txs, active_fork_spec);

        // create the l2 block header
        let header = L2Header::new(
            l2_height,
            self.l2_block_hash,
            l2_block_result.state_root_transition.final_root,
            l1_fee_rate,
            tx_merkle_root,
            timestamp,
        );

        let signed_header = self.sign_l2_block_header(header)?;
        // TODO: cleanup l2 block structure once we decide how to pull data from the running sequencer in the existing form
        let l2_block = L2Block::new(signed_header, signed_txs);
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

**File:** crates/sovereign-sdk/rollup-interface/src/state_machine/block.rs (L63-79)
```rust
    /// Computes the cryptographic digest of the block header using the specified hash function.
    ///
    /// # Type Parameters
    /// * `D` - The type of hash function to use (must implement the `Digest` trait)
    ///
    /// # Returns
    /// The computed hash digest of the header
    pub fn compute_digest(&self) -> [u8; 32] {
        let mut hasher = sha2::Sha256::new();
        hasher.update(self.height.to_be_bytes());
        hasher.update(self.prev_hash);
        hasher.update(self.state_root);
        hasher.update(self.l1_fee_rate.to_be_bytes());
        hasher.update(self.tx_merkle_root);
        hasher.update(self.timestamp.to_be_bytes());
        <[u8; 32]>::from(hasher.finalize_fixed())
    }
```

**File:** crates/sovereign-sdk/rollup-interface/src/state_machine/block.rs (L160-163)
```rust
    /// Returns the merkle root of all transactions in this block.
    pub fn tx_merkle_root(&self) -> [u8; 32] {
        self.header.inner.tx_merkle_root
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
