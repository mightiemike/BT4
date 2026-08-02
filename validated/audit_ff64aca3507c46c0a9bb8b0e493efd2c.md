[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** execution/executor/src/chunk_executor/chunk_commit_queue.rs (L93-114)
```rust
    pub(crate) fn next_chunk_to_update_ledger(
        &mut self,
    ) -> Result<(
        LedgerStateSummary,
        Option<LedgerWithSummary<PositionStateWithSummary>>,
        Arc<InMemoryTransactionAccumulator>,
        ChunkToUpdateLedger,
    )> {
        let chunk_opt = self
            .to_update_ledger
            .front_mut()
            .ok_or_else(|| anyhow!("No chunk to update ledger."))?;
        let chunk = chunk_opt
            .take()
            .ok_or_else(|| anyhow!("Next chunk to update ledger has already been processed."))?;
        Ok((
            self.latest_state_summary.clone(),
            self.latest_position_state_summary.clone(),
            self.latest_txn_accumulator.clone(),
            chunk,
        ))
    }
```

**File:** execution/executor/src/chunk_executor/chunk_commit_queue.rs (L116-143)
```rust
    pub(crate) fn save_ledger_update_output(&mut self, chunk: ExecutedChunk) -> Result<()> {
        let _timer = CHUNK_OTHER_TIMERS.timer_with(&["save_ledger_update_output"]);

        ensure!(
            !self.to_update_ledger.is_empty(),
            "to_update_ledger is empty."
        );
        ensure!(
            self.to_update_ledger.front().unwrap().is_none(),
            "Head of to_update_ledger has not been processed."
        );
        self.latest_state_summary = chunk
            .output
            .ensure_state_checkpoint_output()?
            .state_summary
            .clone();
        self.latest_position_state_summary = chunk
            .output
            .ensure_state_checkpoint_output()?
            .position_state_summary
            .clone();
        self.latest_txn_accumulator = chunk
            .output
            .ensure_ledger_update_output()?
            .transaction_accumulator
            .clone();
        self.to_update_ledger.pop_front();
        self.to_commit.push_back(Some(chunk));
```
