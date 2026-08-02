## Analysis

The external report's core invariant is: *a state-derived value (voting power) must be computed identically across every code path of a consistency check; if one path silently omits a term, the check accepts a wrong result.* The closest local analog is `TransactionOutput::ensure_match_transaction_info` in `types/src/transaction/mod.rs`.

### Title
Replay-verification integrity check skips state-checkpoint/state-root hash comparison, accepting divergent execution results - (File: `types/src/transaction/mod.rs`)

### Summary
`ensure_match_transaction_info` is the function used by the replay-verify tool (`storage/db-tool/src/replay_on_archive.rs`) to confirm that a freshly re-executed `TransactionOutput` matches the `TransactionInfo` recorded on an archived/backup ledger. [1](#0-0) [2](#0-1) 

### Finding Description
The function validates status, gas used, write-set hash, and event root hash against `TransactionInfo`, but explicitly does **not** compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — the fields that bind a transaction's execution result to the actual state Merkle root. The code even documents this gap itself: [3](#0-2) 

This mirrors the `getPriorVotes` bug class exactly: some fields of the compared/committed structure are checked, others are silently skipped, so two structurally different results (a correct state root vs. a wrong/corrupted one) can both pass the same "match" check.

### Impact Explanation
This function is the sole authenticity check used by `replay_on_archive.rs`'s `execute_and_verify`, which is the verification step of the `replay-verify` CI/testsuite pipeline (`testsuite/replay-verify/main.py`) that gates historical/mainnet-archive replay correctness before releases. Because `state_checkpoint_hash`/`hot_state_checkpoint_hash` are never compared, a bug in VM execution, state-tree construction, or `TransactionInfo` assembly that produces the *wrong state Merkle root* (while write-set/event hashes still happen to match, or where the mismatch is confined to the checkpoint hash fields) will not be flagged by replay-verify. This can let a state-root divergence between the historical, canonical ledger and a freshly re-executed one go undetected until it manifests as an actual consensus/hard-fork divergence, satisfying the "hard-fork-only divergence during commit, replay ... proof verification" impact category.

### Likelihood Explanation
Likelihood is limited: this defect only manifests when a *separate* root-cause bug already exists in state-checkpoint-hash generation (e.g., in `assemble_transaction_infos`, `do_ledger_update.rs`) — this function on its own does not corrupt state, it only fails to *detect* a corruption once one occurs. The comment in the code is explicit that this is a known, currently-open gap ("Validate the checkpoint hashes here before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`"), i.e., it is an acknowledged unfinished area guarded (in intent) by an unlaunched feature flag `COMPUTE_TRADING_NATIVE_STATE_ROOTS`. I could not confirm from the available code whether this feature flag is enabled anywhere on mainnet, nor find the git history/authorship of this TODO to determine whether `state_checkpoint_hash` comparison was intentionally removed recently versus always absent. Because I could not fully verify the feature-flag rollout status or original commit intent (`get_blame` was unavailable to me), I am not fully confident that this is exploitable independent of a pre-existing separate bug, and it may be an already-tracked, intentional limitation rather than a new exploitable path.

### Recommendation
Extend `ensure_match_transaction_info` to compare `self` write-set-derived state/hot-state checkpoint hash (when computable) against `txn_info.state_checkpoint_hash()` / `hot_state_checkpoint_hash()` / `position_state_checkpoint_hash()` before accepting a match, so replay-verify cannot silently pass over a state-root divergence.

### Proof of Concept
Not constructed — this analysis reduces to a documented code-path gap rather than a demonstrated exploit; a PoC would require independently reproducing a state-checkpoint-hash divergence (e.g. an execution bug that changes the JMT root but preserves write-set/event hash equality), which is outside the scope of the local code reviewed.

**Caveat:** Given the explicit `TODO` acknowledgment in-code and my inability to verify feature-flag activation status or blame history in this session, I present this with reduced confidence rather than as a fully proven, independently exploitable vulnerability.

### Citations

**File:** types/src/transaction/mod.rs (L2139-2148)
```rust
    pub fn ensure_match_transaction_info(
        &self,
        version: Version,
        txn_info: &TransactionInfo,
        expected_write_set: Option<&WriteSet>,
        expected_events: Option<&[ContractEvent]>,
    ) -> Result<()> {
        const ERR_MSG: &str = "TransactionOutput does not match TransactionInfo";

        let expected_txn_status: TransactionStatus = txn_info.status().clone().into();
```

**File:** types/src/transaction/mod.rs (L2159-2203)
```rust
        ensure!(
            self.gas_used() == txn_info.gas_used(),
            "{}: version:{}, gas_used:{:?}, expected:{:?}",
            ERR_MSG,
            version,
            self.gas_used(),
            txn_info.gas_used(),
        );

        let write_set_hash = CryptoHash::hash(self.write_set());
        ensure!(
            write_set_hash == txn_info.state_change_hash(),
            "{}: version:{}, write_set_hash:{:?}, expected:{:?}, write_set: {:?}, expected(if known): {:?}",
            ERR_MSG,
            version,
            write_set_hash,
            txn_info.state_change_hash(),
            self.write_set,
            expected_write_set,
        );

        let event_hashes = self
            .events()
            .iter()
            .map(CryptoHash::hash)
            .collect::<Vec<_>>();
        let event_root_hash = InMemoryEventAccumulator::from_leaves(&event_hashes).root_hash;
        ensure!(
            event_root_hash == txn_info.event_root_hash(),
            "{}: version:{}, event_root_hash:{:?}, expected:{:?}, events: {:?}, expected(if known): {:?}",
            ERR_MSG,
            version,
            event_root_hash,
            txn_info.event_root_hash(),
            self.events(),
            expected_events,
        );

        // TODO(trading-native): this comparator ignores the checkpoint hashes
        // (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
        // tooling (e.g. db-tool's `replay_on_archive`) can report a successful
        // replay even when the authenticated position state root diverges from
        // local execution. Validate the checkpoint hashes here before enabling
        // COMPUTE_TRADING_NATIVE_STATE_ROOTS.
        Ok(())
```

**File:** storage/db-tool/src/replay_on_archive.rs (L392-406)
```rust
            if let Err(err) = executed_outputs[idx].ensure_match_transaction_info(
                version,
                &expected_txn_infos[idx],
                Some(&expected_writesets[idx]),
                Some(&expected_events[idx]),
            ) {
                cur_txns.drain(0..idx + 1);
                cur_persisted_aux_info.drain(0..idx + 1);
                expected_txn_infos.drain(0..idx + 1);
                expected_events.drain(0..idx + 1);
                expected_writesets.drain(0..idx + 1);

                return Ok(Some(err));
            }
        }
```
