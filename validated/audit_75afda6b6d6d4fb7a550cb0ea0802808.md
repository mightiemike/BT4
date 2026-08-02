## Analysis

The external report's bug class is: a value that is *supposed to be updated to reflect a state that already happened* is left stale, so a downstream consumer trusts an incomplete/incorrect accounting of what was actually committed, causing funds/data to be silently lost or mismatched between two views of the same event.

The closest Aptos-native analog I can substantiate from the code is in the state-commitment integrity check `TransactionOutput::ensure_match_transaction_info`, which is the function relied upon by replay/verification tooling to prove that a freshly re-executed `TransactionOutput` matches the `TransactionInfo` that was actually committed to the ledger (and thus to the transaction accumulator / proof root).

### Title
Replay/state-integrity verification (`ensure_match_transaction_info`) never validates checkpoint hashes, allowing a diverged state root to pass verification - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the authoritative check used by `db-tool`'s `replay_on_archive` (`storage/db-tool/src/replay_on_archive.rs`) and by other replay/debugger call sites (`aptos-move/aptos-debugger/src/aptos_debugger.rs`, `aptos-move/cli/src/commands.rs`) to prove that locally re-executed VM output matches the `TransactionInfo` recorded on-chain. It checks status, gas, the write-set hash, and event root hash, but explicitly skips comparing the state checkpoint hash / hot-state checkpoint hash / `position_state_checkpoint_hash` fields carried in `TransactionInfoV1`, per its own inline comment.

### Finding Description [1](#0-0) 

The function computes and asserts equality only for:
- transaction status
- gas used
- write-set hash vs `state_change_hash`
- event root hash vs `event_root_hash`

It does **not** assert equality for `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`, which are the fields that bind a `TransactionInfo` to the actual state Merkle root at that version. The code's own comment states this directly:

```
// TODO(trading-native): this comparator ignores the checkpoint hashes
// (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
// tooling (e.g. db-tool's `replay_on_archive`) can report a successful
// replay even when the authenticated position state root diverges from
// local execution. Validate the checkpoint hashes here before enabling
// COMPUTE_TRADING_NATIVE_STATE_ROOTS.
``` [2](#0-1) 

`replay_on_archive.rs`'s `execute_and_verify` calls exactly this function as its sole correctness gate per transaction, treating an `Ok(())` result as full agreement between locally executed output and the committed record: [3](#0-2) 

Because the checkpoint-hash fields are the ones that actually commit to the authenticated state root at a version (as opposed to just the write set of a single transaction), a divergence in the state tree itself — for example a bug in state-checkpoint materialization, hot-state root computation, or the new `position_state_checkpoint_hash` path — would not be detected by this comparator even though `TransactionInfo::hash()` (and thus the transaction accumulator leaf/proof) includes these fields via full field hashing of the `TransactionInfoV1` struct.

### Impact Explanation
This is a proof/verification-integrity gap rather than a live consensus divergence: it does not corrupt committed data by itself, and `TransactionInfo::hash()` still commits to all fields for accumulator/proof purposes, so a genuinely malicious/incorrect root would still fail signature/accumulator verification through other paths (e.g., signature checking against `LedgerInfo`, or independent tree verification). However, `ensure_match_transaction_info` is specifically the check that operators and CI (`replay-verify`) rely on to catch **silent state-root divergence introduced by local execution bugs** during replay/backfill/hard-fork validation. If a bug in state-checkpoint hash computation (state root, hot-state root, or the newer position-state root) diverges from the historical committed value, this tool will report a "successful replay" while the underlying state root is wrong — precisely the "hard-fork-only divergence during commit, replay ... proof verification" class called out in the task's required impacts. Given `COMPUTE_TRADING_NATIVE_STATE_ROOTS` / `position_state_checkpoint_hash` is new/gated functionality, this leaves the primary automated safety net (replay-verify used before promoting or trusting a re-executed archive) blind to exactly the kind of new state-root bug it's meant to catch.

### Likelihood Explanation
The gap is deterministic and always present — it's not a race condition, it's an explicit `TODO` acknowledging the check is incomplete. It will only manifest as a real problem if/when there is an actual state-checkpoint-hash computation bug elsewhere (e.g., in hot-state or the new position-state subsystem) that this tool is depended upon to catch. I cannot confirm from the available code whether `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is already enabled on any live network or whether `position_state_checkpoint_hash` is currently exercised — the on-chain feature flag definition (`types/src/on_chain_config/aptos_features.rs`) and its current gating state were not fully retrievable in this session, so I cannot assert this is exploitable today versus being a documented, tracked gap awaiting the feature flag's activation.

### Recommendation
Extend `ensure_match_transaction_info` to also assert equality of `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` against locally recomputed values (when available/applicable for the given `TransactionInfo` variant), before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` (or any feature relying on `position_state_checkpoint_hash`) is enabled, consistent with the existing inline TODO.

### Proof of Concept
Not independently demonstrable as a state-corruption exploit from static analysis alone — the finding is a verification-logic gap, confirmed directly by the source's own TODO comment plus the call site in `replay_on_archive.rs` that treats the incomplete check as full verification: [4](#0-3) [5](#0-4) 

I was not able to fully trace whether `position_state_checkpoint_hash`/`COMPUTE_TRADING_NATIVE_STATE_ROOTS` is reachable/active in the current build (index truncated the feature-flag file contents), so confidence is capped at "confirmed logic gap with acknowledged but currently-gated impact" rather than a demonstrated live-network exploit. If you need the exact feature-flag activation status and all call sites of `position_state_checkpoint_hash` to determine current exploitability, a full Devin session with repo access would be needed to inspect `types/src/on_chain_config/aptos_features.rs` and related gating code in full.

### Citations

**File:** types/src/transaction/mod.rs (L2139-2203)
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
        ensure!(
            self.status() == &expected_txn_status,
            "{}: version:{}, status:{:?}, auxiliary data:{:?}, expected:{:?}",
            ERR_MSG,
            version,
            self.status(),
            self.auxiliary_data(),
            expected_txn_status,
        );

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

**File:** storage/db-tool/src/replay_on_archive.rs (L388-406)
```rust
        for idx in 0..cur_txns.len() {
            let version = *current_version;
            *current_version += 1;

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
