[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** crates/sequencer/src/deposit_data_mempool.rs (L19-25)
```rust
#[derive(Clone, Debug, Default)]
pub struct DepositDataMempool {
    /// Queue of accepted deposit transaction data
    accepted_deposit_txs: VecDeque<Deposit>,
    /// Set of pending deposit TxIds to prevent duplicates
    pending_deposits: HashSet<Vec<u8>>,
}
```

**File:** crates/sequencer/src/deposit_data_mempool.rs (L57-109)
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

**File:** crates/sequencer/src/deposit_data_mempool.rs (L119-136)
```rust
    pub fn add_deposit_tx(&mut self, req: Deposit) -> anyhow::Result<bool> {
        let txid = Self::calc_tx_id(&req)?;

        debug!("Adding deposit with tx: {}", hex::encode(txid));

        // Check if deposit is already pending
        if !self.pending_deposits.insert(txid.to_vec()) {
            tracing::debug!("Deposit already pending in mempool");
            return Ok(false);
        }

        self.accepted_deposit_txs.push_back(req);
        SM.deposit_data_mempool_txs_inc.increment(1);
        SM.deposit_data_mempool_txs
            .set(self.accepted_deposit_txs.len() as f64);

        Ok(true)
    }
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
