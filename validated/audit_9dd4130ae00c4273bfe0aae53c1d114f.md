### Title
`DbStateView::get` silently skips proof verification when the proof-fetch call errors, letting a "verified" state view return unauthenticated state data - ([File: storage/storage-interface/src/state_store/state_view/db_state_view.rs])

### Summary
`VerifiedStateViewAtVersion::verified_state_view_at_version` builds a `DbStateView` that is supposed to bind every subsequent resource/module read to a trusted state root hash obtained from an authenticated `TransactionInfo` (via `txn_with_proof.verify(ledger_info)?`). However, the actual per-key verification performed in `DbStateView::get` only happens if `self.db.get_state_value_with_proof_by_version(key, version)` returns `Ok`. If that call errors for any reason, the `if let Ok(...)` simply does not execute the `proof.verify(...)` branch, and the function falls straight through to returning the raw value from `get_state_value_with_version_by_version`, unauthenticated. [1](#0-0) 

### Finding Description
`verified_state_view_at_version` is meant to hand callers a `TStateView` whose reads are cryptographically bound to `ledger_info`/`state_checkpoint_hash` (an accumulator/Merkle-proof-backed root), analogous to how `Vault_Lyra.sol` always fetches and applies the withdrawal fee before trusting a valuation. It fetches a `TransactionInfoWithProof`, verifies it against the `ledger_info`, and extracts `state_root_hash` from the verified `state_checkpoint_hash`: [2](#0-1) 

The value-returning path, `DbStateView::get`, is supposed to apply the same discipline for every read — like `Vault_Synths.sol` was supposed to apply the exchange fee for every valuation, but didn't. Here the code does attempt it, but only guards the value-return with `?` inside the `Ok` arm; a proof-fetch failure is silently swallowed by the `if let Ok(...)` pattern instead of being propagated, and the code unconditionally proceeds to fetch and return the plain (unauthenticated) value:
```rust
if let Some(root_hash) = self.maybe_verify_against_state_root_hash {
    if let Ok((value, proof)) =
        self.db.get_state_value_with_proof_by_version(key, version)
    {
        proof.verify(root_hash, *key.crypto_hash_ref(), value.as_ref())?;
    }
}
Ok(self.db.get_state_value_with_version_by_version(key, version)?)
``` [3](#0-2) 

Any transient or storage-layer condition that causes `get_state_value_with_proof_by_version` to return `Err` while `get_state_value_with_version_by_version` still returns a value (e.g., pruning race between the state-merkle proof path and the state-kv value path, a proof-construction error for a given key, or a storage backend inconsistency) results in the caller of `verified_state_view_at_version` receiving unverified state — silently, with no error and no log distinguishing "verified" from "unverified" reads. The code comment "DB doesn't support returning proofs for buffered state, so only optionally verify proof" already documents that verification is best-effort by design for buffered/non-checkpoint versions, but the additional silent-error-swallow on the proof-fetch call further widens that gap for any real error, not just the acknowledged buffered-state case.

### Impact Explanation
Consumers of `verified_state_view_at_version` (e.g., `execution/executor-test-helpers` integration flows that fetch account/resource state through a ledger-info-verified view) rely on this API to authenticate state against a proof-backed root hash before trusting it for correctness checks. If the underlying proof-fetch path errors on a subset of keys — which can legitimately happen under storage-layer skew, pruning windows, or sharded/hot-state read paths — those specific keys are returned without any Merkle-proof validation against the claimed `state_checkpoint_hash`, defeating the entire purpose of the "verified" view. This is a genuine proof-integrity gap: an authenticated API surface silently degrades to unauthenticated behavior instead of failing loudly, which could let stale, corrupted, or replay-diverged state values be treated as attested to a specific ledger version/root.

### Likelihood Explanation
The trigger condition (an `Err` from `get_state_value_with_proof_by_version` for a version/key where `get_state_value_with_version_by_version` still succeeds) is plausible under normal operational conditions — pruning races, pending state-merkle catch-up on buffered/non-checkpoint versions, or backend-specific errors — rather than requiring privileged access. However, this is not the "hot path" of consensus/execution and appears to gate a specific verification helper used by integration test tooling and any other production callers of `VerifiedStateViewAtVersion`, so the likelihood of a real mainnet impact depends on how broadly this trait is used outside of the currently-found test call sites, which the search tools available here did not turn up beyond `execution/executor-test-helpers`.

### Recommendation
Change `DbStateView::get` so that a failure to fetch the proof (when verification is required) is treated as a hard error rather than silently skipped, e.g.:
```rust
if let Some(root_hash) = self.maybe_verify_against_state_root_hash {
    let (value, proof) = self.db.get_state_value_with_proof_by_version(key, version)?;
    proof.verify(root_hash, *key.crypto_hash_ref(), value.as_ref())?;
}
```
This ensures that whenever a caller has requested a proof-bound/"verified" view, any inability to produce or check the proof surfaces as an error instead of silently falling back to unauthenticated reads.

### Proof of Concept
Conceptual repro (not executed, since this requires storage-layer fault injection):
1. Call `db.verified_state_view_at_version(Some(v), ledger_info)` to obtain a `DbStateView` with `maybe_verify_against_state_root_hash = Some(root)`.
2. For some key `k` at version `v`, arrange for `get_state_value_with_proof_by_version(k, v)` to return `Err` (e.g., simulate a state-merkle-db read failure or a pruning race) while `get_state_value_with_version_by_version(k, v)` still returns `Some(value)` from the state-kv db.
3. Call `view.get_state_value(k)` (via the `TStateView`/`get` path): observe that no proof error is raised and the (unauthenticated) `value` is returned as if it had been verified against `root`.

Note: I was not able to fully confirm within the indexed code whether `verified_state_view_at_version` is used anywhere else besides `execution/executor-test-helpers/src/integration_test_impl.rs`, since the index may not include every caller; a full-repo grep from a Devin session would be needed to establish the complete blast radius on mainnet-relevant code paths.

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

**File:** storage/storage-interface/src/state_store/state_view/db_state_view.rs (L118-148)
```rust
impl VerifiedStateViewAtVersion for Arc<dyn DbReader> {
    fn verified_state_view_at_version(
        &self,
        version: Option<Version>,
        ledger_info: &LedgerInfo,
    ) -> StateViewResult<DbStateView> {
        let db = self.clone();

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
        } else {
            Ok(DbStateView {
                db,
                version: None,
                maybe_verify_against_state_root_hash: None,
            })
        }
```
