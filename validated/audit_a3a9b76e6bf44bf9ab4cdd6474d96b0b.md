## Finding

### Title
Missing aggregator-table hint for gas-fee burning breaks sharded-execution/BlockSTM equivalence - (File: `types/src/transaction/analyzed_transaction.rs`)

### Summary
`rw_set_for_coin_transfer` (lines 195-221) only records `aptos_coin_info_location()` — the `CoinInfo<AptosCoinType>` resource state key — as a read hint, and never records the separate state key that actually stores the mutable total-supply value.

### Finding Description
`CoinInfoResource` does not store the mutable supply value inline. The struct only holds an `Option<OptionalAggregatorV1Resource>` that *points to* a separate aggregator state key, accessed via `supply_aggregator_state_key()`: [1](#0-0) 

The actual total-supply value is physically stored under this distinct state key (a table item), which `to_writeset` writes to separately from the `CoinInfo` resource itself: [2](#0-1) 

When `TransactionFeeBurnCap` is enabled, the VM epilogue burns the gas fee by decrementing this aggregator value for essentially every user transaction (any transaction paying gas). However, `rw_set_for_coin_transfer`'s hint set only includes the `CoinInfo` resource key and the `TransactionFeeBurnCap` config key as *reads*: [3](#0-2) 

The state key that is actually mutated (the aggregator table item returned by `supply_aggregator_state_key()`) is absent from both `read_hints` and `write_hints`. These hints are what the sharded block partitioner uses to assign transactions to shards and to build cross-shard read/write dependency edges (per the type's own doc comment: "Set of storage locations that are ... read/written by the transaction ... This can be accurate or strictly overestimated"): [4](#0-3) 

Because the total-supply aggregator write is invisible to the partitioner, two `coin::transfer`/`aptos_account::transfer` transactions from unrelated senders/receivers — which otherwise have disjoint account/coin-store hints — can legitimately be assigned to different shards. Each independently burns gas against the same shared aggregator item, but the partitioner has no dependency information to serialize or track this cross-shard conflict, unlike sequential BlockSTM execution, which discovers the real read/write set from actual VM execution and therefore always detects and validates this shared write correctly.

### Impact Explanation
If the sharded executor relies on these hints (rather than a fully independent runtime validation pass) to build cross-shard merge/ordering of aggregator deltas, a block executed under the sharded path can produce a different final `AptosCoin` total-supply value than the same block executed sequentially via BlockSTM. This is a ledger-state divergence between two supported execution engines processing the identical block — a hard-fork-class inconsistency, since different validators/execution modes could compute different state roots for the same input transactions.

### Likelihood Explanation
Trigger conditions require only unprivileged inputs: any user submitting ordinary `coin::transfer` or `aptos_account::transfer` transactions with `TransactionFeeBurnCap` enabled causes gas-fee burning against the shared aggregator on every transaction. No special account or permission is needed — likelihood is high assuming sharded execution is active and depends on these hints for correctness (see caveat below).

### Caveat / Unverified Assumption
I was not able to trace, within the available index, the downstream consumer code (the sharded block partitioner and sharded executor's cross-shard dependency resolution) to confirm definitively whether it relies solely on these `read_hints`/`write_hints` for correctness or whether it has an independent runtime safety net (e.g., a full re-validation/merge pass for aggregator deltas across shards regardless of hints). Some file contents relating to the partitioner/executor consumption of these hints may not be present in the indexed subset of this repository. If a background Devin session is available, it should trace the consumers of `AnalyzedTransaction::read_hints()`/`write_hints()` in the sharded execution/partitioner crates to confirm whether missing this hint produces a genuinely undetected conflict or is safely caught elsewhere before concluding this is exploitable in production.

### Recommendation
Include the aggregator state key (`CoinInfoResource::supply_aggregator_state_key()`) as a write hint (not merely a read hint) in `rw_set_for_coin_transfer`, and audit all other gas-metering/epilogue side effects (e.g., fee-burn destination accounts, storage-fee refunds) for similar omissions from the analyzed-transaction hint generation.

### Proof of Concept
1. Enable `TransactionFeeBurnCap` on-chain.
2. Submit N `aptos_account::transfer` transactions from N distinct sender/receiver pairs (chosen so the partitioner assigns them to different shards based on account/coin-store hints) in one block.
3. Execute the block via the sharded executor and separately via sequential BlockSTM.
4. Compare `CoinInfo<AptosCoinType>` total supply (via the aggregator state key) after each execution mode; a difference confirms cross-shard aggregator conflict was missed.

### Citations

**File:** types/src/account_config/resources/coin_info.rs (L79-87)
```rust
    pub fn supply_aggregator_state_key(&self) -> StateKey {
        self.supply
            .as_ref()
            .unwrap()
            .aggregator
            .as_ref()
            .unwrap()
            .state_key()
    }
```

**File:** types/src/account_config/resources/coin_info.rs (L91-104)
```rust
    pub fn to_writeset(&self, supply: u128) -> anyhow::Result<WriteSet> {
        let value_state_key = self.supply_aggregator_state_key();
        // We store CoinInfo and aggregatable value separately.
        let write_set = vec![
            (
                StateKey::resource_typed::<Self>(&C::coin_info_address())?,
                WriteOp::legacy_modification(bcs::to_bytes(&self).unwrap().into()),
            ),
            (
                value_state_key,
                WriteOp::legacy_modification(bcs::to_bytes(&supply).unwrap().into()),
            ),
        ];
        Ok(WriteSetMut::new(write_set).freeze().unwrap())
```

**File:** types/src/transaction/analyzed_transaction.rs (L26-32)
```rust
    /// Set of storage locations that are read by the transaction - this doesn't include location
    /// that are written by the transactions to avoid duplication of locations across read and write sets
    /// This can be accurate or strictly overestimated.
    pub read_hints: Vec<StorageLocation>,
    /// Set of storage locations that are written by the transaction. This can be accurate or strictly
    /// overestimated.
    pub write_hints: Vec<StorageLocation>,
```

**File:** types/src/transaction/analyzed_transaction.rs (L195-221)
```rust
pub fn rw_set_for_coin_transfer(
    sender_address: AccountAddress,
    receiver_address: AccountAddress,
    receiver_exists: bool,
) -> (Vec<StorageLocation>, Vec<StorageLocation>) {
    let mut write_hints = vec![
        account_resource_location(sender_address),
        coin_store_location(sender_address),
    ];
    if sender_address != receiver_address {
        write_hints.push(coin_store_location(receiver_address));
    }
    if !receiver_exists {
        // If the receiver doesn't exist, we create the receiver account, so we need to write the
        // receiver account resource.
        write_hints.push(account_resource_location(receiver_address));
    }

    let read_hints = vec![
        current_ts_location(),
        features_location(),
        aptos_coin_info_location(),
        chain_id_location(),
        transaction_fee_burn_cap_location(),
    ];
    (read_hints, write_hints)
}
```
