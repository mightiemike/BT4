[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** crates/bitcoin-da/src/spec/utxo.rs (L1-29)
```rust
//! This module defines the UTXO struct and its conversion from ListUnspentResultEntry.

use bitcoin::address::NetworkUnchecked;
use bitcoin::{Address, Txid};
#[cfg(feature = "native")]
use bitcoincore_rpc::json::ListUnspentResultEntry;

/// Represents a UTXO (Unspent Transaction Output) in the Bitcoin network.
/// We use this struct instead of ListUnspentResultEntry because
/// we don't use all the fields from ListUnspentResultEntry.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct UTXO {
    /// The transaction ID of the UTXO.
    pub tx_id: Txid,
    /// The output index.
    pub vout: u32,
    /// The address associated with the UTXO, if available.
    pub address: Option<Address<NetworkUnchecked>>,
    /// The script public key.
    pub script_pubkey: String,
    /// The amount in satoshis.
    pub amount: u64,
    /// The number of confirmations for the UTXO.
    pub confirmations: u32,
    /// Whether the UTXO is spendable.
    pub spendable: bool,
    /// Whether the UTXO is solvable.
    pub solvable: bool,
}
```

**File:** crates/sequencer/src/deposit_data_mempool.rs (L265-296)
```rust
    #[test]
    fn test_deposit_lifecycle() {
        let mut mempool = DepositDataMempool::new();
        let deposit = hex::decode(DEPOSIT1).unwrap();

        // Add deposit
        assert!(mempool.add_deposit_tx(deposit.clone()).unwrap());

        // Cannot add duplicate
        assert!(!mempool.add_deposit_tx(deposit.clone()).unwrap());

        // Fetch the deposit
        let fetched = mempool.fetch_deposits(10);
        assert_eq!(fetched.len(), 1);
        assert_eq!(fetched[0], deposit);

        // Deposit is still in mempool after fetch
        assert_eq!(mempool.pending_deposits.len(), 1);
        assert_eq!(mempool.accepted_deposit_txs.len(), 1);

        // Still cannot add duplicate
        assert!(!mempool.add_deposit_tx(deposit.clone()).unwrap());

        // Remove the deposit
        let removed_count = mempool.remove_deposits(&fetched);
        assert_eq!(removed_count, 1);

        // Now the same deposit can be added again
        assert!(mempool.add_deposit_tx(deposit.clone()).unwrap());
        assert_eq!(mempool.pending_deposits.len(), 1);
        assert_eq!(mempool.accepted_deposit_txs.len(), 1);
    }
```

**File:** crates/sequencer/src/rpc.rs (L329-366)
```rust
    /// eth_sendRawDepositTransaction RPC call implementation
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
```

**File:** crates/sequencer/src/runner.rs (L516-521)
```rust
        // Get pending deposits up to configured limit
        let deposit_data = self
            .deposit_mempool
            .lock()
            .fetch_deposits(self.config.deposit_mempool_fetch_limit);

```

**File:** crates/sequencer/src/runner.rs (L628-643)
```rust
        // First set the state diff before committing the L2 block
        // This prevents race conditions where the sequencer might shut down
        // between committing the L2 block and saving the state diff
        self.ledger_db
            .set_state_diff(L2BlockNumber(l2_height), &l2_block_result.state_diff)?;

        self.save_l2_block(l2_block, l2_block_result, tx_hashes, blobs)?;

        // Remove successfully included deposits from the mempool
        if !deposit_data.is_empty() {
            let removed_count = self.deposit_mempool.lock().remove_deposits(&deposit_data);
            debug!(
                "Removed {} deposits from mempool after successful block production",
                removed_count
            );
        }
```
