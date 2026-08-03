No vulnerability found for this question.

**Rationale:** `check_and_put_ledger_info` at `storage/aptosdb/src/db/aptosdb_writer.rs:703-764` indeed only checks version, root-hash and epoch continuity, and does **not** call `verify_signatures` itself [1](#0-0) . However, `DbWriter::commit_ledger` is not an externally-reachable, unprivileged entry point — it is an internal storage API that is only invoked by trusted, already-verified callers:

- In the consensus commit path, the `LedgerInfoWithSignatures` fed into `commit_ledger` is produced by `try_advance_to_aggregated`, which calls `aggregate_and_verify(validator)` — this cryptographically verifies the aggregated BLS signatures against the `ValidatorVerifier` before the `LedgerInfoWithSignatures` object is ever constructed [2](#0-1) , and this verified proof is passed through `commit_proof_fut` into `pipeline_builder::commit_ledger` before invoking the executor's `commit_ledger` [3](#0-2) .
- In the state-sync path (`DbWriter::save_transactions`), the ledger info is likewise expected to already carry verified signatures/quorum proof before being handed to storage's `commit_ledger` [4](#0-3) .
- The wire-level messages carrying ledger infos (`CommitDecision`, `WrappedLedgerInfo`) explicitly verify signatures against the `ValidatorVerifier`/`EpochState` before being trusted anywhere downstream [5](#0-4) [6](#0-5) .

There is no unprivileged, production entry point (transaction, API, view, bytecode, or proof input as required by the scope rules) that can construct an unsigned/unverified `LedgerInfoWithSignatures` and reach `AptosDB::commit_ledger` directly — doing so would require bypassing the consensus/state-sync verification layers entirely, which is a "trusted operator/internal-caller" bypass rather than an unprivileged-input exploit, and is explicitly out of scope per the decision standard.

### Citations

**File:** storage/aptosdb/src/db/aptosdb_writer.rs (L703-764)
```rust
    fn check_and_put_ledger_info(
        &self,
        version: Version,
        ledger_info_with_sig: &LedgerInfoWithSignatures,
        ledger_batch: &mut SchemaBatch,
    ) -> Result<(), AptosDbError> {
        let ledger_info = ledger_info_with_sig.ledger_info();

        // Verify the version.
        ensure!(
            ledger_info.version() == version,
            "Version in LedgerInfo doesn't match last version. {:?} vs {:?}",
            ledger_info.version(),
            version,
        );

        // Verify the root hash.
        let db_root_hash = self
            .ledger_db
            .transaction_accumulator_db()
            .get_root_hash(version)?;
        let li_root_hash = ledger_info_with_sig
            .ledger_info()
            .transaction_accumulator_hash();
        ensure!(
            db_root_hash == li_root_hash,
            "Root hash pre-committed doesn't match LedgerInfo. pre-commited: {:?} vs in LedgerInfo: {:?}",
            db_root_hash,
            li_root_hash,
        );

        // Verify epoch continuity.
        let current_epoch = self
            .ledger_db
            .metadata_db()
            .get_latest_ledger_info_option()
            .map_or(0, |li| li.ledger_info().next_block_epoch());
        ensure!(
            ledger_info_with_sig.ledger_info().epoch() == current_epoch,
            "Gap in epoch history. Trying to put in LedgerInfo in epoch: {}, current epoch: {}",
            ledger_info_with_sig.ledger_info().epoch(),
            current_epoch,
        );

        // Ensure that state tree at the end of the epoch is persisted.
        if ledger_info_with_sig.ledger_info().ends_epoch() {
            let state_snapshot = self.state_store.get_state_snapshot_before(version + 1)?;
            ensure!(
                state_snapshot.is_some() && state_snapshot.as_ref().unwrap().0 == version,
                "State checkpoint not persisted at the end of the epoch, version {}, next_epoch {}, snapshot in db: {:?}",
                version,
                ledger_info_with_sig.ledger_info().next_block_epoch(),
                state_snapshot,
            );
        }

        // Put write to batch.
        self.ledger_db
            .metadata_db()
            .put_ledger_info(ledger_info_with_sig, ledger_batch)?;
        Ok(())
    }
```

**File:** consensus/src/pipeline/buffer_item.rs (L385-397)
```rust
                    if let Ok(commit_proof) = signed_item
                        .partial_commit_proof
                        .clone()
                        .aggregate_and_verify(validator)
                        .map(|(ledger_info, aggregated_sig)| {
                            LedgerInfoWithSignatures::new(ledger_info, aggregated_sig)
                        })
                    {
                        return Self::Aggregated(Box::new(AggregatedItem {
                            executed_blocks: signed_item.executed_blocks,
                            commit_proof,
                        }));
                    }
```

**File:** consensus/src/pipeline/pipeline_builder.rs (L1314-1342)
```rust
    /// Precondition: 1. pre-commit finishes, 2. parent block's phase finishes 3. commit proof is available
    /// What it does: Commit the ledger info to storage, this makes the data visible for clients
    async fn commit_ledger(
        pre_commit_fut: TaskFuture<PreCommitResult>,
        commit_proof_fut: TaskFuture<LedgerInfoWithSignatures>,
        parent_block_commit_fut: TaskFuture<CommitLedgerResult>,
        executor: Arc<dyn BlockExecutorTrait>,
        block: Arc<Block>,
    ) -> TaskResult<CommitLedgerResult> {
        let mut tracker = Tracker::start_waiting("commit_ledger", &block);
        parent_block_commit_fut.await?;
        pre_commit_fut.await?;
        let ledger_info_with_sigs = commit_proof_fut.await?;

        // it's committed as prefix
        if ledger_info_with_sigs.commit_info().id() != block.id() {
            return Ok(None);
        }

        tracker.start_working();
        let block_id = block.id();
        let ledger_info_with_sigs_clone = ledger_info_with_sigs.clone();
        tokio::task::spawn_blocking(move || {
            executor
                .commit_ledger(ledger_info_with_sigs_clone)
                .map_err(anyhow::Error::from)
        })
        .await
        .expect("spawn blocking failed")?;
```

**File:** storage/storage-interface/src/lib.rs (L620-641)
```rust
    /// Persist transactions. Called by state sync to save verified transactions to the DB.
    fn save_transactions(
        &self,
        chunk: ChunkToCommit,
        ledger_info_with_sigs: Option<&LedgerInfoWithSignatures>,
        sync_commit: bool,
    ) -> Result<()> {
        // For reconfig suffix.
        if ledger_info_with_sigs.is_none() && chunk.is_empty() {
            return Ok(());
        }

        if !chunk.is_empty() {
            self.pre_commit_ledger(chunk.clone(), sync_commit)?;
        }
        let version_to_commit = if let Some(ledger_info_with_sigs) = ledger_info_with_sigs {
            ledger_info_with_sigs.ledger_info().version()
        } else {
            chunk.expect_last_version()
        };
        self.commit_ledger(version_to_commit, ledger_info_with_sigs, Some(chunk))
    }
```

**File:** consensus/consensus-types/src/pipeline/commit_decision.rs (L49-59)
```rust
    pub fn verify(&self, validator: &ValidatorVerifier) -> anyhow::Result<()> {
        ensure!(
            !self.ledger_info.commit_info().is_ordered_only(),
            "Unexpected ordered only commit info"
        );
        // We do not need to check the author because as long as the signature tree
        // is valid, the message should be valid.
        self.ledger_info
            .verify_signatures(validator)
            .context("Failed to verify Commit Decision")
    }
```

**File:** consensus/src/consensus_observer/network/observer_message.rs (L502-511)
```rust
    /// Verifies the commit proof and returns an error if the proof is invalid
    pub fn verify_commit_proof(&self, epoch_state: &EpochState) -> Result<(), Error> {
        epoch_state.verify(&self.commit_proof).map_err(|error| {
            Error::InvalidMessageError(format!(
                "Failed to verify commit proof ledger info: {:?}, Error: {:?}",
                self.proof_block_info(),
                error
            ))
        })
    }
```
