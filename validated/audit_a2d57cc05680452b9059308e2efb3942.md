## Analysis

Confirmed: `verified_state_view_at_version` never checks `txn_with_proof.version == version` (the caller-supplied parameter). It only calls `txn_with_proof.verify(ledger_info)`, which internally uses `self.version` — the `version` field embedded in the `TransactionWithProof` struct itself — as the accumulator leaf index, not the `version` argument passed into the function [1](#0-0) .

`TransactionWithProof::verify` binds the accumulator inclusion proof to `self.version` (its own field), then `verify_transaction_info` uses that same self-reported `transaction_version` as the explicit leaf index in `TransactionAccumulatorProof::verify` [2](#0-1) [3](#0-2) . So the accumulator proof is internally self-consistent (it proves the `TransactionInfo` is at position `self.version` in the accumulator), but nothing forces `self.version` to equal the `version` parameter the caller originally requested.

Back in `verified_state_view_at_version`, after `verify()` succeeds, the code extracts `state_root_hash` from `txn_with_proof.proof.transaction_info` and constructs `DbStateView { version: Some(version), maybe_verify_against_state_root_hash: Some(state_root_hash) }` — using the *original requested* `version` for the state view's version field, but the state root that was bound is whatever `TransactionInfo` the `DbReader` returned [1](#0-0) .

If a `DbReader` implementation (buggy caching layer, off-by-one in an underlying index lookup, etc.) returns a self-consistent `TransactionWithProof` for `version+1` (i.e., its `version` field is `version+1`, matching its own accumulator proof), `txn_with_proof.verify(ledger_info)` will pass, because the proof correctly proves inclusion at index `version+1` in the accumulator committed to by `ledger_info` — the function never cross-checks this against the caller's requested `version`. The resulting `DbStateView` then silently binds `version` to `version+1`'s state root: `DbStateView::get` subsequently calls `get_state_value_with_proof_by_version(key, version)` and verifies against that mismatched root hash [4](#0-3) .

This differs from the production `AccountOrderedTransactionsWithProof::verify` path, which does explicitly assert `self.version == version` before calling proof verification [5](#0-4)  — a pattern absent from `verified_state_view_at_version`.

### Title
Missing requested-version cross-check in `verified_state_view_at_version` allows binding to an adjacent-version state root - (File: `storage/storage-interface/src/state_store/state_view/db_state_view.rs`)

### Summary
`verified_state_view_at_version` fetches a `TransactionWithProof` for a requested `version`, but only verifies internal self-consistency of that struct (its own embedded `version` field vs. its own accumulator proof) rather than asserting that the embedded `version` equals the caller-supplied `version` parameter, before binding the returned `DbStateView` to that transaction's state-checkpoint root hash.

### Finding Description
`TransactionWithProof::verify(ledger_info)` proves that the embedded `TransactionInfo` is included in the accumulator at leaf index `self.version`. It does not know or check anything about a separately supplied "expected version." `verified_state_view_at_version` never compares `txn_with_proof.version` to its own `version` parameter after this call; it just extracts `state_checkpoint_hash` from the info and constructs a `DbStateView` tagged with the original `version` argument. A `DbReader` (or a caching/index layer under it) that returns a valid, self-consistent proof for a neighboring version due to an off-by-one bug would pass all checks in this function while causing `DbStateView` to authenticate reads at `version` against the state root of `version±1`.

### Impact Explanation
This breaks the fundamental invariant that a `DbStateView`'s state reads at version V are proven against V's own root. Any reads later performed through this `DbStateView` (e.g. `get_state_value_with_proof_by_version`) would be checked against the wrong root, silently corrupting the authenticated version↔root binding used by downstream consumers (executor test harnesses, restore/replay verification, and any caller of `VerifiedStateViewAtVersion`).

### Likelihood Explanation
Requires an off-by-one or adjacent-version bug in the underlying `DbReader`/storage layer (e.g., an index or caching bug) rather than a purely malicious/unprivileged external input — the accumulator proof itself is genuinely valid for the wrong leaf, so no cryptographic forgery is needed, only a storage-side lookup defect. `get_transaction_by_version` is expected to return the transaction at exactly the requested version, so this is a defense-in-depth gap rather than an directly externally-triggerable exploit under normal, correct storage implementations.

### Recommendation
In `verified_state_view_at_version`, after fetching `txn_with_proof`, explicitly assert `txn_with_proof.version == version` before trusting `state_checkpoint_hash`, mirroring the check already done in `AccountOrderedTransactionsWithProof::verify` (`ensure!(self.version == version, ...)`).

### Proof of Concept
Unit test sketch: implement a mock `DbReader` whose `get_transaction_by_version(version, ..)` ignores the requested `version` and returns a real, correctly-proven `TransactionWithProof` for `version + 1` (self-consistent accumulator proof, `version` field = `version+1`). Call `verified_state_view_at_version(Some(version), ledger_info)`. Observe that `txn_with_proof.verify(ledger_info)` succeeds (it only checks internal consistency), and the resulting `DbStateView` is returned with `version: Some(version)` but `maybe_verify_against_state_root_hash` set to `version+1`'s root — no error is raised, confirming the missing cross-check.

### Citations

**File:** storage/storage-interface/src/state_store/state_view/db_state_view.rs (L26-46)
```rust
impl DbStateView {
    fn get(&self, key: &StateKey) -> StateViewResult<Option<(Version, StateValue)>> {
        if let Some(version) = self.version {
            if let Some(root_hash) = self.maybe_verify_against_state_root_hash {
                // TODO(aldenhu): sample-verify proof inside DB
                // DB doesn't support returning proofs for buffered state, so only optionally
                // verify proof.
                // TODO: support returning state proof for buffered state.
                if let Ok((value, proof)) =
                    self.db.get_state_value_with_proof_by_version(key, version)
                {
                    proof.verify(root_hash, *key.crypto_hash_ref(), value.as_ref())?;
                }
            }
            Ok(self
                .db
                .get_state_value_with_version_by_version(key, version)?)
        } else {
            Ok(None)
        }
    }
```

**File:** storage/storage-interface/src/state_store/state_view/db_state_view.rs (L126-141)
```rust
        if let Some(version) = version {
            let txn_with_proof =
                db.get_transaction_by_version(version, ledger_info.version(), false)?;
            txn_with_proof.verify(ledger_info)?;

            let state_root_hash = txn_with_proof
                .proof
                .transaction_info
                .state_checkpoint_hash()
                .ok_or_else(|| StateViewError::NotFound("state_checkpoint_hash".to_string()))?;

            Ok(DbStateView {
                db,
                version: Some(version),
                maybe_verify_against_state_root_hash: Some(state_root_hash),
            })
```

**File:** types/src/transaction/mod.rs (L1688-1710)
```rust
    pub fn verify(&self, ledger_info: &LedgerInfo) -> Result<()> {
        let txn_hash = self.transaction.committed_hash();
        ensure!(
            txn_hash == self.proof.transaction_info().transaction_hash(),
            "Transaction hash ({}) not expected ({}).",
            txn_hash,
            self.proof.transaction_info().transaction_hash(),
        );

        if let Some(events) = &self.events {
            let event_hashes: Vec<_> = events.iter().map(CryptoHash::hash).collect();
            let event_root_hash =
                InMemoryEventAccumulator::from_leaves(&event_hashes[..]).root_hash();
            ensure!(
                event_root_hash == self.proof.transaction_info().event_root_hash(),
                "Event root hash ({}) not expected ({}).",
                event_root_hash,
                self.proof.transaction_info().event_root_hash(),
            );
        }

        self.proof.verify(ledger_info, self.version)
    }
```

**File:** types/src/transaction/mod.rs (L1733-1738)
```rust
        ensure!(
            self.version == version,
            "Version ({}) is not expected ({}).",
            self.version,
            version,
        );
```

**File:** types/src/proof/mod.rs (L40-61)
```rust
fn verify_transaction_info(
    ledger_info: &LedgerInfo,
    transaction_version: Version,
    transaction_info: &TransactionInfo,
    ledger_info_to_transaction_info_proof: &TransactionAccumulatorProof,
) -> Result<()> {
    ensure!(
        transaction_version <= ledger_info.version(),
        "Transaction version {} is newer than LedgerInfo version {}.",
        transaction_version,
        ledger_info.version(),
    );

    let transaction_info_hash = transaction_info.hash();
    ledger_info_to_transaction_info_proof.verify(
        ledger_info.transaction_accumulator_hash(),
        transaction_info_hash,
        transaction_version,
    )?;

    Ok(())
}
```
