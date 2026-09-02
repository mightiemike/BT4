### No vulnerability found for this question.

**Reasoning:**

The binding claimed as broken is: `Bitcoin moveTx output locked to vault == permanently minted cBTC credit` (no silent, unrecoverable drop of accepted deposit blobs). Tracing the actual removal semantics of the deposit pool shows this binding is preserved by the retry design, contrary to the premise.

- `DepositDataMempool::fetch_deposits` explicitly does **not** remove entries from `accepted_deposit_txs` or `pending_deposits` — it only clones and returns a bounded slice for the sequencer to attempt to include in the next block: [1](#0-0) .
- Removal only happens through `remove_deposits`, whose own docstring states it is meant to be called only "after they have been successfully included in a block": [2](#0-1) .
- In `process_system_transactions`, a deposit system-tx is only appended to the `all_txs` vector (the list of txs that actually landed in the block) when `apply_l2_block_txs` succeeds. On the specific failure path the question cites — `EvmSystemTransactionNotSuccessful` for a `BridgeDeposit` event — the code reverts the checkpointed working set, decrements the nonce, and `continue`s to the next event **without** pushing the tx to `all_txs`: [3](#0-2) .

Because `remove_deposits` is documented and designed to operate only on the set of deposits that were successfully applied (i.e. present in `all_txs`), a deposit that reverts at apply time (e.g. because `BitcoinLightClient` has not yet caught up to the target block, causing `validateAndCheckInclusion` to fail in `Bridge.sol`) is never removed from `accepted_deposit_txs`/`pending_deposits`. It remains queued and will be re-fetched by `fetch_deposits` and retried by `process_system_transactions` on subsequent block-production cycles until `BitcoinLightClient` catches up and the deposit call succeeds — at which point it is pushed into `all_txs`, applied, and only then removed from the deposit mempool.

The `evm.get_call(... Some(BlockId::pending()) ...)` simulation in `send_raw_deposit_transaction` at `crates/sequencer/src/rpc.rs:346` is only an **admission gate** for the initial RPC call (rejecting obviously malformed/invalid deposit blobs before they enter the pool); it does not affect the pool's retry behavior once a well-formed deposit blob is accepted into `accepted_deposit_txs`. Even if this pre-check races ahead of the light client and momentarily approves a deposit that then reverts at real apply time, the revert-and-continue path in `runner.rs` guarantees the blob is retried in later blocks rather than being purged.

No code path was found where a reverted/failed deposit's bytes are purged from `accepted_deposit_txs` or its txid removed from `pending_deposits` without a corresponding successful application. The premise that "the accepted-into-mempool Deposit bytes never resurface" does not hold given `fetch_deposits`'s non-destructive read and `remove_deposits`'s success-gated removal contract. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** crates/sequencer/src/deposit_data_mempool.rs (L50-69)
```rust
    /// Retrieves a limited number of deposit transactions from the mempool without removing them
    ///
    /// # Arguments
    /// * `limit_per_block` - Maximum number of deposits to return
    ///
    /// # Returns
    /// A vector of deposit transaction data, limited by the specified amount
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

**File:** crates/sequencer/src/runner.rs (L1675-1702)
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
                return Err(anyhow!("Failed to apply system transaction: {e:?}"));
            }
            evm_nonce += 1; // only increment on success
            working_set_to_discard = working_set.checkpoint().to_revertable();
            all_txs.push(sys_tx_rlp);
```

**File:** crates/sequencer/src/rpc.rs (L330-388)
```rust
    fn send_raw_deposit_transaction(&self, deposit: Bytes) -> RpcResult<()> {
        debug!("Sequencer: citrea_sendRawDepositTransaction");

        let deposit_tx_size = deposit.len();
        SM.deposit_tx_size.record(deposit_tx_size as f64);

        let evm = Evm::<DefaultContext>::default();
        let mut working_set = WorkingSet::new(self.context.storage.clone());

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
            }
            Err(e) => {
                error!("Error processing deposit tx: {:?}", e);
                SM.unaccepted_deposit_txs.increment(1);
                Err(e)
            }
        }
    }
```
