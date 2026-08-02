Based on my research, I found a strong analog to the reported bug pattern (an "add" path that populates cached accounting/derived state, paired with an incomplete "revert/cleanup" path that leaves stale residue) in the transaction accumulator's root-hash cache during ledger DB truncation.

### Title
Truncation of the transaction accumulator leaves stale cached root hashes in `TransactionAccumulatorRootHashSchema`, allowing wrong accumulator roots to be served after crash-recovery re-commit - (File: `storage/aptosdb/src/utils/truncation_helper.rs`)

### Summary
`commit_transaction_accumulator` populates a per-version root-hash cache (`TransactionAccumulatorRootHashSchema`) for every version it commits, so that `get_root_hash` can return a value cheaply without recomputing from the Merkle Accumulator. When the ledger DB is truncated back to an earlier version (e.g. during crash-recovery consistency reconciliation across sub-DBs), `truncate_transaction_accumulator` deletes the underlying `TransactionAccumulatorSchema` frozen-node entries for the truncated version range, but never deletes the corresponding `TransactionAccumulatorRootHashSchema` cache entries for that same range. This exactly mirrors the reported bug's structure: a value is added on one path (`triggerDefaultWarning` / `commit_transaction_accumulator`) but only partially reversed on the corresponding undo path (`_revertDefaultWarning` / `truncate_transaction_accumulator`), leaving stale accounting data that is later served as if it were still correct.

### Finding Description
- On commit, `commit_transaction_accumulator` writes a root hash into `TransactionAccumulatorRootHashSchema` for **every version** in the committed batch: [1](#0-0) 

- `get_root_hash` prefers this cache and returns it directly without validating that the accumulator nodes backing it still exist: [2](#0-1) 

- On truncation (used by crash-recovery / restart consistency reconciliation via `truncate_ledger_db` → `truncate_ledger_db_single_batch`), `truncate_transaction_accumulator` deletes the frozen `TransactionAccumulatorSchema` node entries at-or-after the truncation point, but performs no corresponding delete of `TransactionAccumulatorRootHashSchema`: [3](#0-2) [4](#0-3) 

- Contrast this with the pruner's equivalent path, `TransactionAccumulatorDb::prune`, which correctly deletes `TransactionAccumulatorRootHashSchema` entries alongside the frozen node entries it removes: [5](#0-4) 

The asymmetry is the root cause: the "add" path (`commit_transaction_accumulator`) and the pruning "remove" path (`prune`) both touch `TransactionAccumulatorRootHashSchema`, but the truncation "undo" path (`truncate_transaction_accumulator`) only touches `TransactionAccumulatorSchema`. After truncation, the DB is left with dangling root-hash cache entries for versions whose underlying accumulator nodes no longer exist.

### Impact Explanation
`AptosDB` uses truncation to reconcile inconsistent commit progress across its sub-DBs (ledger, state KV, state merkle) at restart, rolling the ledger DB back to a `target_version` and later re-committing transactions from `target_version + 1` onward as execution resumes. If the batch of transactions re-executed and re-committed after recovery covers fewer versions than were truncated (a legitimate scenario when the resumed block execution differs in size/content from the pre-crash attempt, or reconciliation happens incrementally across multiple restarts/rounds), some versions in the previously-truncated range never have their `TransactionAccumulatorRootHashSchema` entry overwritten by `commit_transaction_accumulator`. For those versions, `get_root_hash` will keep returning the stale, pre-crash root hash — computed over data that has since been deleted and superseded by different (or as-yet-uncommitted) accumulator state — instead of erroring or recomputing. This corrupts a durable, authenticated value (the accumulator root for a given version) that downstream consumers (root-hash lookups feeding proof construction/consistency checks) treat as ground truth, i.e., "wrong accumulator root ... accepted as valid" and "authenticated API ... output bound to the wrong version/root," which are explicitly in-scope, high-impact categories.

### Likelihood Explanation
Truncation of the ledger DB is a normal part of `AptosDB`'s startup/consistency-reconciliation logic (not an attacker-controlled or privileged-only code path) and can be triggered by any node restart where sub-DB commit progress diverges (e.g., due to a crash mid-batch-commit). The missing cleanup is a straightforward code omission (the pruner's equivalent function correctly handles it, showing the intended invariant), so it can be reproduced deterministically by: committing several versions, crashing/simulating a partial-progress restart that truncates to an earlier version, and re-committing a different-length batch, then observing `get_root_hash` return the pre-truncation value for versions never re-written.

### Recommendation
In `truncate_transaction_accumulator` (`storage/aptosdb/src/utils/truncation_helper.rs`), delete the `TransactionAccumulatorRootHashSchema` entries for every version being truncated (`start_version..`), mirroring what `TransactionAccumulatorDb::prune` already does for the pruning path. Add a regression test (analogous to the existing `verify_transaction_accumulator_pruned` pruner test) that truncates the ledger DB and asserts `TransactionAccumulatorRootHashSchema` no longer contains entries for truncated versions, and that `get_root_hash` fails or recomputes correctly rather than returning stale data.

### Proof of Concept
1. Commit versions `0..=N` via `commit_transaction_accumulator`, which populates `TransactionAccumulatorRootHashSchema` for every version in `0..=N` (`storage/aptosdb/src/db/aptosdb_writer.rs:605-629`).
2. Call `truncate_ledger_db(ledger_db, target_version = T)` where `T < N`, which invokes `truncate_transaction_accumulator` and deletes `TransactionAccumulatorSchema` frozen-node entries for the range `(T, N]` but leaves `TransactionAccumulatorRootHashSchema` entries for `(T, N]` untouched (`storage/aptosdb/src/utils/truncation_helper.rs:415-439`).
3. Re-commit a new, shorter batch of transactions starting at `T+1` that only reaches version `M < N` (a realistic outcome when block execution after crash recovery is re-run with different batching/content).
4. Call `get_root_hash(version)` for any version in `(M, N]`. Because the cache lookup at `storage/aptosdb/src/ledger_db/transaction_accumulator_db.rs:129-136` short-circuits and returns the still-present stale entry without checking that the backing accumulator nodes exist, it returns the pre-truncation root hash instead of an error or the correct (as of the new commit) result — demonstrating the corrupted/inconsistent accumulator root being served as valid.

### Citations

**File:** storage/aptosdb/src/db/aptosdb_writer.rs (L605-629)
```rust
        let mut batch = SchemaBatch::new();
        let all_versions: Vec<_> = (first_version..first_version + num_txns).collect();
        THREAD_MANAGER
            .get_non_exe_cpu_pool()
            .install(|| -> Result<()> {
                let all_root_hashes = all_versions
                    .into_par_iter()
                    .with_min_len(64)
                    .map(|version| {
                        self.ledger_db
                            .transaction_accumulator_db()
                            .get_root_hash(version)
                    })
                    .collect::<Result<Vec<_>>>()?;
                all_root_hashes
                    .iter()
                    .enumerate()
                    .try_for_each(|(i, hash)| {
                        let version = first_version + i as u64;
                        batch.put::<TransactionAccumulatorRootHashSchema>(&version, hash)
                    })?;
                self.ledger_db
                    .transaction_accumulator_db()
                    .write_schemas(batch)
            })?;
```

**File:** storage/aptosdb/src/ledger_db/transaction_accumulator_db.rs (L128-137)
```rust
    /// Returns the root hash at given `version`.
    pub fn get_root_hash(&self, version: Version) -> Result<HashValue> {
        if let Some(hash) = self
            .db
            .get::<TransactionAccumulatorRootHashSchema>(&version)?
        {
            return Ok(hash);
        }
        Accumulator::get_root_hash(self, version + 1).map_err(Into::into)
    }
```

**File:** storage/aptosdb/src/ledger_db/transaction_accumulator_db.rs (L139-172)
```rust
    /// Deletes the transaction accumulator between a range of version in [begin, end).
    ///
    /// To avoid always pruning a full left subtree, we uses the following algorithm.
    /// For each leaf with an odd leaf index.
    /// 1. From the bottom upwards, find the first ancestor that's a left child of its parent.
    /// (the position of which can be got by popping "1"s from the right of the leaf address).
    /// Note that this node DOES NOT become non-useful.
    /// 2. From the node found from the previous step, delete both its children non-useful, and go
    /// to the right child to repeat the process until we reach a leaf node.
    /// More details are in this issue https://github.com/aptos-labs/aptos-core/issues/1288.
    pub(crate) fn prune(begin: Version, end: Version, db_batch: &mut SchemaBatch) -> Result<()> {
        for version_to_delete in begin..end {
            db_batch.delete::<TransactionAccumulatorRootHashSchema>(&version_to_delete)?;
            // The even version will be pruned in the iteration of version + 1.
            if version_to_delete % 2 == 0 {
                continue;
            }

            let first_ancestor_that_is_a_left_child =
                Self::find_first_ancestor_that_is_a_left_child(version_to_delete);

            // This assertion is true because we skip the leaf nodes with address which is a
            // a multiple of 2.
            assert!(!first_ancestor_that_is_a_left_child.is_leaf());

            let mut current = first_ancestor_that_is_a_left_child;
            while !current.is_leaf() {
                db_batch.delete::<TransactionAccumulatorSchema>(&current.left_child())?;
                db_batch.delete::<TransactionAccumulatorSchema>(&current.right_child())?;
                current = current.right_child();
            }
        }
        Ok(())
    }
```

**File:** storage/aptosdb/src/utils/truncation_helper.rs (L415-439)
```rust
fn truncate_transaction_accumulator(
    transaction_accumulator_db: &DB,
    start_version: Version,
    batch: &mut SchemaBatch,
) -> Result<()> {
    let mut iter = transaction_accumulator_db.iter::<TransactionAccumulatorSchema>()?;
    iter.seek_to_last();
    let (position, _) = iter.next().transpose()?.unwrap();
    let num_frozen_nodes = position.to_postorder_index() + 1;
    let num_frozen_nodes_after = num_frozen_nodes_in_accumulator(start_version);
    let mut num_nodes_to_delete = num_frozen_nodes - num_frozen_nodes_after;

    let start_position = Position::from_postorder_index(num_frozen_nodes_after)?;
    iter.seek(&start_position)?;

    for item in iter {
        let (position, _) = item?;
        batch.delete::<TransactionAccumulatorSchema>(&position)?;
        num_nodes_to_delete -= 1;
    }

    assert_eq!(num_nodes_to_delete, 0);

    Ok(())
}
```

**File:** storage/aptosdb/src/utils/truncation_helper.rs (L441-477)
```rust
fn truncate_ledger_db_single_batch(
    ledger_db: &LedgerDb,
    transaction_store: &TransactionStore,
    start_version: Version,
) -> Result<()> {
    let mut batch = LedgerDbSchemaBatches::new();

    delete_transaction_index_data(
        ledger_db,
        transaction_store,
        start_version,
        &mut batch.transaction_db_batches,
    )?;
    delete_per_epoch_data(
        &ledger_db.metadata_db_arc(),
        start_version,
        &mut batch.ledger_metadata_db_batches,
    )?;
    delete_per_version_data(ledger_db, start_version, &mut batch)?;

    delete_event_data(ledger_db, start_version, &mut batch.event_db_batches)?;

    truncate_transaction_accumulator(
        ledger_db.transaction_accumulator_db_raw(),
        start_version,
        &mut batch.transaction_accumulator_db_batches,
    )?;

    let mut progress_batch = SchemaBatch::new();
    progress_batch.put::<DbMetadataSchema>(
        &DbMetadataKey::LedgerCommitProgress,
        &DbMetadataValue::Version(start_version - 1),
    )?;
    ledger_db.metadata_db().write_schemas(progress_batch)?;

    ledger_db.write_schemas(batch)
}
```
