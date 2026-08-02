### Title
`VerifiedStateViewAtVersion` silently returns unverified state values when proof lookup fails - (File: `storage/storage-interface/src/state_store/state_view/db_state_view.rs`)

### Summary
`DbStateView::get` is supposed to authenticate every state read against a caller-trusted Merkle root before returning it, when constructed via `verified_state_view_at_version`. The verification and the actual value returned come from two independent DB calls, and the verification is only attempted on the "happy path" — any error from the proof-fetch call causes verification to be silently skipped while the (separately fetched, unverified) value is still returned to the caller as if authenticated.

### Finding Description
`VerifiedStateViewAtVersion::verified_state_view_at_version` obtains a proof-verified `state_root_hash` from a `TransactionWithProof` and stores it in `maybe_verify_against_state_root_hash`, promising that all subsequent reads from the returned `DbStateView` are checked against this authenticated root: [1](#0-0) 

The actual read implementation is: [2](#0-1) 

The bug: the proof check is wrapped in `if let Ok((value, proof)) = self.db.get_state_value_with_proof_by_version(key, version) { proof.verify(...)?; }`. If `get_state_value_with_proof_by_version` returns an `Err` (e.g., a transient DB error, a key/version combination that hits a different code path than the versioned lookup, or any other failure mode of that specific accessor), the `if let` simply falls through — there is no `else` branch that propagates an error or refuses to serve the read. Execution then proceeds unconditionally to:
```
Ok(self.db.get_state_value_with_version_by_version(key, version)?)
```
which is a completely separate, non-Merkle-authenticated lookup path. The caller receives a value from a `DbStateView` that was explicitly constructed to be "verified," with no indication that verification was actually skipped for that particular key.

This is the same integrity-check-invariant class as the Ajna finding: a security check (`kickResult_.lup` boundary check / here, the Merkle proof check) is derived from or gated on one code path, while the value actually acted upon (the debt-adjusted kick / here, the returned state value) comes from an independently-computed path that is not guaranteed to be consistent with what the check covered. In the Ajna case the mismatch was between the state used for the check vs. the state after the real mutation; here the mismatch is between the state that was (or wasn't) proof-checked vs. the state that is actually returned.

### Impact Explanation
This directly matches the required "Authenticated API or state-view output bound to the wrong version, object, or proof context" impact category: any consumer that requests a `verified_state_view_at_version` view believing every read is bound to and checked against an authenticated `state_checkpoint_hash` can silently receive an unverified/incorrect value whenever the internal proof-fetch call errors for that key, without any error surfacing to the caller. Because the function's entire contract is "return only proof-checked reads," a silent verification bypass defeats the reason this API exists and could let downstream logic (replay/debugging tools, or any other consumer relying on `VerifiedStateViewAtVersion` for a trust boundary) act on unauthenticated state data while believing it has been Merkle-verified.

### Likelihood Explanation
The condition (the `Ok(...)` proof-fetch call failing while the plain versioned-value fetch on the same key/version succeeds) requires the two accessor methods (`get_state_value_with_proof_by_version` vs `get_state_value_with_version_by_version`) to diverge in error behavior for the same key/version — e.g., due to pruning windows, root_depth assumptions, or other internal edge cases specific to the proof-returning code path. I was not able to fully trace every caller of `verified_state_view_at_version` within the available iterations to confirm how frequently/deliberately this divergence can be triggered by an external actor, so likelihood is uncertain and would need further investigation (in particular whether an attacker can force the proof-fetch branch to error, e.g. by requesting boundary/pruned versions) before this can be conclusively rated as high-likelihood.

### Recommendation
Remove the silent-skip behavior: if `maybe_verify_against_state_root_hash` is `Some`, any error from `get_state_value_with_proof_by_version` must be propagated as an error (or otherwise cause the read to fail), never silently fall through to the unauthenticated `get_state_value_with_version_by_version` path. Alternatively, derive the returned value directly from the same `(value, proof)` tuple that was verified, rather than performing a second, independent DB lookup.

### Proof of Concept
Conceptual PoC (cannot be fully executed without live DB access to force the divergent error path, which is the remaining uncertainty noted above):
1. Construct a `DbStateView` via `verified_state_view_at_version(Some(v), ledger_info)`, obtaining an authenticated `state_root_hash` from `TransactionWithProof`.
2. Call `get_state_slot`/`get` for a `StateKey` where `db.get_state_value_with_proof_by_version(key, v)` errors (e.g., due to pruning-window/root-depth edge case) but `db.get_state_value_with_version_by_version(key, v)` still succeeds and returns a value.
3. Observe that `DbStateView::get` returns `Ok(Some(value))` with no error and no proof check performed, even though the view was constructed specifically to guarantee Merkle-verified reads — demonstrating the silent bypass of the intended proof/state-binding invariant.

### Citations

**File:** storage/storage-interface/src/state_store/state_view/db_state_view.rs (L27-46)
```rust
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

**File:** storage/storage-interface/src/state_store/state_view/db_state_view.rs (L118-149)
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
    }
```
