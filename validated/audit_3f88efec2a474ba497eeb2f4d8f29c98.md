This confirms the epoch boundaries (`start_height`/`end_height` for `Epoch40`, e.g. `BITCOIN_MAINNET_STACKS_40_BURN_HEIGHT`) are fixed, hardcoded, static consensus constants baked into `STACKS_EPOCHS_MAINNET`/`STACKS_EPOCHS_TESTNET`/`STACKS_EPOCHS_REGTEST` and inserted verbatim into the SortitionDB's `epochs` table via `validate_and_insert_epochs` [1](#0-0) , and `SortitionDB::get_stacks_epoch` performs a pure, deterministic lookup `start_block_height <= burn_block_height < end_block_height` against that fixed table [2](#0-1) . There is no mechanism by which "differently-ordered PoX-reward-cycle epoch bump" or differing burnchain-sync speed can change what epoch a given `block_tenure_burn_height` maps to — the mapping is a static function of the burn height alone, identical on every honest node once each has processed up to that height, and is not affected by processing order of PoX reward cycles.

### Citations

**File:** stackslib/src/chainstate/burn/db/sortdb.rs (L3008-3029)
```rust
    /// Validates given StacksEpochs (will runtime panic if there is any invalid StacksEpoch structuring) and
    ///  inserts them into the SortitionDB's epochs table.
    fn validate_and_insert_epochs(
        db_tx: &Transaction,
        epochs: &[StacksEpoch],
    ) -> Result<(), db_error> {
        let epochs = StacksEpoch::validate_epochs(epochs);
        for epoch in epochs.into_iter() {
            let args = params![
                (epoch.epoch_id as u32),
                u64_to_sql(epoch.start_height)?,
                u64_to_sql(epoch.end_height)?,
                epoch.block_limit,
                epoch.network_epoch,
            ];
            db_tx.execute(
                "INSERT INTO epochs (epoch_id,start_block_height,end_block_height,block_limit,network_epoch) VALUES (?1,?2,?3,?4,?5)",
                args
            )?;
        }
        Ok(())
    }
```

**File:** stackslib/src/chainstate/burn/db/sortdb.rs (L5410-5422)
```rust
    /// Get the StacksEpoch for a given burn block height
    pub fn get_stacks_epoch(
        conn: &DBConn,
        burn_block_height: u64,
    ) -> Result<Option<StacksEpoch>, db_error> {
        let sql =
            "SELECT * FROM epochs WHERE start_block_height <= ?1 AND ?2 < end_block_height LIMIT 1";
        let args = params![
            u64_to_sql(burn_block_height)?,
            u64_to_sql(burn_block_height)?,
        ];
        query_row(conn, sql, args)
    }
```
