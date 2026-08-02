[1](#0-0) [2](#0-1)

### Citations

**File:** storage/storage-interface/src/block_info.rs (L18-38)
```rust
    pub fn from_new_block_event(version: Version, new_block_event: &NewBlockEvent) -> Self {
        let NewBlockEvent {
            hash,
            epoch,
            round,
            height: _,
            previous_block_votes_bitvec: _,
            proposer,
            failed_proposer_indices: _,
            timestamp,
        } = new_block_event;

        Self::V0(BlockInfoV0 {
            id: HashValue::from_slice(hash.as_slice()).unwrap(),
            epoch: *epoch,
            round: *round,
            proposer: *proposer,
            first_version: version,
            timestamp_usecs: *timestamp,
        })
    }
```

**File:** storage/aptosdb/src/db/aptosdb_writer.rs (L499-513)
```rust
        // Write block index.
        for (i, txn_out) in chunk.transaction_outputs.iter().enumerate() {
            for event in txn_out.events() {
                if let Some(event_key) = event.event_key() {
                    if *event_key == new_block_event_key() {
                        let version = chunk.first_version + i as Version;
                        LedgerMetadataDb::put_block_info(
                            version,
                            event,
                            &mut ledger_metadata_batch,
                        )?;
                    }
                }
            }
        }
```
