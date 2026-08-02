[1](#0-0) [2](#0-1)

### Citations

**File:** storage/aptosdb/src/ledger_db/ledger_metadata_db.rs (L142-162)
```rust
    /// Returns the epoch state for the given epoch.
    pub(crate) fn get_epoch_state(&self, epoch: u64) -> Result<EpochState> {
        ensure!(epoch > 0, "EpochState only queryable for epoch >= 1.",);

        let ledger_info_with_sigs =
            self.db
                .get::<LedgerInfoSchema>(&(epoch - 1))?
                .ok_or_else(|| {
                    AptosDbError::NotFound(format!("Last LedgerInfo of epoch {}", epoch - 1))
                })?;
        let latest_epoch_state = ledger_info_with_sigs
            .ledger_info()
            .next_epoch_state()
            .ok_or_else(|| {
                AptosDbError::Other(
                    "Last LedgerInfo in epoch must carry next_epoch_state.".to_string(),
                )
            })?;

        Ok(latest_epoch_state.clone())
    }
```

**File:** storage/aptosdb/src/ledger_db/ledger_metadata_db.rs (L193-194)
```rust
    /// Writes `ledger_info_with_sigs` to `batch`.
    pub(crate) fn put_ledger_info(
```
