[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** storage/storage-interface/src/state_store/state_update_refs.rs (L221-232)
```rust
        Self {
            per_version: Self::concat_per_version_updates(
                for_last_checkpoint.as_ref().map(|x| &x.0),
                for_latest.as_ref().map(|x| &x.0),
            ),
            all_checkpoint_versions: all_checkpoint_indices
                .into_iter()
                .map(|index| first_version + index as Version)
                .collect(),
            for_last_checkpoint,
            for_latest,
        }
```

**File:** storage/aptosdb/src/state_store/mod.rs (L932-948)
```rust
        let txn_info_iter = state_db
            .ledger_db
            .transaction_info_db()
            .get_transaction_info_iter(snapshot_next_version, write_sets.len())?;
        let all_checkpoint_indices = txn_info_iter
            .into_iter()
            .collect::<Result<Vec<_>>>()?
            .into_iter()
            .positions(|txn_info| txn_info.has_state_checkpoint_hash())
            .collect();

        let state_update_refs = StateUpdateRefs::index_write_sets(
            snapshot_next_version,
            &write_sets,
            write_sets.len(),
            all_checkpoint_indices,
        );
```

**File:** execution/executor-types/src/transactions_with_output.rs (L103-119)
```rust
        let (all_checkpoint_indices, is_reconfig) =
            Self::get_all_checkpoint_indices(&transactions_with_output, must_be_block);

        TransactionsToKeepBuilder {
            transactions_with_output,
            is_reconfig,
            state_update_refs_builder: |transactions_with_output| {
                let write_sets = transactions_with_output
                    .transaction_outputs
                    .iter()
                    .map(TransactionOutput::write_set);
                StateUpdateRefs::index_write_sets(
                    first_version,
                    write_sets,
                    transactions_with_output.len(),
                    all_checkpoint_indices,
                )
```

**File:** storage/aptosdb/src/db/test_helper.rs (L169-188)
```rust
                // calculate state checkpoint hash and this must be the last txn
                let state_checkpoint_hash = if txn.has_state_checkpoint_hash() {
                    Some(state_checkpoint_root_hash)
                } else {
                    None
                };

                let auxiliary_info = AuxiliaryInfo::new(PersistedAuxiliaryInfo::V1 { transaction_index: idx as u32 }, None);

                let txn_info = TransactionInfo::builder_v0()
                    .transaction_hash(txn.transaction().committed_hash())
                    .state_change_hash(txn.write_set().hash())
                    .event_root_hash(event_root_hash)
                    .maybe_state_checkpoint_hash(state_checkpoint_hash)
                    .gas_used(placeholder_txn_info.gas_used())
                    .status(placeholder_txn_info.status().clone())
                    .maybe_auxiliary_info_hash(auxiliary_info.persisted_info_hash())
                    .build();
                txn_accumulator = txn_accumulator.append(&[txn_info.hash()]);
                txn.set_transaction_info(txn_info);
```
