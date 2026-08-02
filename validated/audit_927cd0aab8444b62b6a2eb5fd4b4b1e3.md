### Title
`ensure_match_transaction_info` skips state/hot-state/position checkpoint hash validation, allowing replay-verify and restore tooling to accept a wrong SMT/JMT root as matching the authenticated `TransactionInfo` - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info()` is the function used by replay/verify and restore tooling to prove that a locally re-executed (or locally-loaded) `TransactionOutput` matches the authenticated `TransactionInfo` sealed by the transaction accumulator/ledger info. It checks status, gas, write-set hash (`state_change_hash`) and event root hash, but explicitly and admittedly does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` at all. [1](#0-0) 

### Finding Description
`ensure_match_transaction_info` is the exact analog of the report's core invariant break: a caller-facing "verification" step silently omits a security-relevant check that its own docstring/callers assume is being performed (nonce check skipped for smart-contract wallets → checkpoint-root check skipped for state/hot-state/position roots). The function validates:
- status
- gas_used
- `write_set` hash vs `state_change_hash`
- event root hash

but never touches `txn_info.state_checkpoint_hash()`, `txn_info.hot_state_checkpoint_hash()`, or `txn_info.position_state_checkpoint_hash()`, even though `TransactionInfo` carries these fields specifically to bind the Sparse-Merkle/Jellyfish-Merkle state root (and the hot-state/position state roots) to a version proven by the transaction accumulator. [2](#0-1) 

The gap is called out by the code's own author in a TODO comment, which confirms this is a genuine, currently-live gap rather than a hypothetical:
"this comparator ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`), so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution." [3](#0-2) 

This function is called from `storage/db-tool/src/replay_on_archive.rs` (the tool operators run to replay-verify an archive against a trusted `TransactionInfo`/ledger-info-authenticated chain) and from `aptos-move/cli/src/commands.rs`. [4](#0-3) 

Because `TransactionInfo::state_checkpoint_hash` is exactly the value that other integrity paths *do* validate correctly — e.g. `state_summary.update(...)` in `DoStateCheckpoint::run` computes the checkpoint root from local execution and is meant to be compared against the authenticated value at checkpoint boundaries [5](#0-4)  — `ensure_match_transaction_info` is the place where that authenticated value should be re-validated when replaying from an external/archived source, but it is not.

### Impact Explanation
If the locally-computed state (main SMT), hot-state, or (once `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled) native-position Merkle root diverges from the value implied by the authenticated `TransactionInfo` — due to a local storage bug, a schema/restore reinterpretation bug, non-determinism, or corruption — `ensure_match_transaction_info` will still report success as long as status/gas/write-set-hash/event-root happen to match. Replay-verify (`replay_on_archive`) and CLI simulate/replay tooling are explicitly relied upon by operators, auditors, and the release process to catch state divergence and hard forks before they become production incidents. A tool that reports "match" despite the state root diverging is a proof-integrity failure matching the required impact class: "Hard-fork-only divergence during commit, replay, restore, or proof verification" and "Wrong ... state proof accepted as valid." The consequence in the worst case is that a divergent full/archive node's checkpoint state passes replay verification, propagating a wrong ledger state as if authenticated, undermining confidence in the very mechanism meant to detect it.

### Likelihood Explanation
The path is not gated behind privileged access — it only requires running the standard operator/developer tooling (`db-tool replay-on-archive`, CLI simulate/replay) over a chain segment where the locally recomputed checkpoint root differs from the correct one, which can happen from any local bug affecting the SMT/JMT root computation, hot-state summary, or (once the trading-native feature ships) position-state summary. The gap is deterministic and always present (no timing race needed), and the developers' own TODO comment confirms it is a known, currently-unaddressed omission, raising confidence that it holds without further gating conditions for the state/hot-state fields (the position-state field is additionally gated by an as-yet-unlaunched feature flag, but the state/hot-state omission is unconditional today).

### Recommendation
Extend `ensure_match_transaction_info` to compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when locally computable, i.e., at checkpoint versions) against the corresponding fields on `txn_info`, mirroring the effort already made for `write_set`/`state_change_hash` and `event_root_hash`. At minimum, gate `COMPUTE_TRADING_NATIVE_STATE_ROOTS` from being enabled until this validation exists, as the TODO already suggests, and additionally close the pre-existing state/hot-state checkpoint gap that is unconditional today.

### Proof of Concept
1. Run `db-tool replay-on-archive` (or the CLI replay/simulate path) over a version range that crosses a state-checkpoint boundary, using a locally maintained database whose Sparse Merkle Tree (or hot-state / position-state) root at that checkpoint has diverged from the authenticated chain (e.g., due to a local restore/schema bug reinterpreting committed data, or corrupted state-kv data that still produces the same write-set bytes and events).
2. Because `ensure_match_transaction_info` at `types/src/transaction/mod.rs:2139-2204` never reads `txn_info.state_checkpoint_hash()`/`hot_state_checkpoint_hash()`/`position_state_checkpoint_hash()`, the check passes as long as status, gas_used, write_set hash, and event root hash agree.
3. The tool reports the chunk as successfully replayed/verified even though the authenticated checkpoint root does not match local execution, demonstrating the exact analog of the report's "check skipped, invariant not enforced" pattern.

Note: I was not able to fully trace every caller of `ensure_match_transaction_info` (e.g., the two call sites in `aptos-move/cli/src/commands.rs`) due to running out of tool iterations; verifying whether any of those call sites are used in a context closer to consensus-critical commit (rather than purely offline tooling) would further sharpen the impact assessment.

### Citations

**File:** types/src/transaction/mod.rs (L2139-2204)
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
    }
```

**File:** storage/db-tool/src/replay_on_archive.rs (L42-60)
```rust
// Replay Verify controller is responsible for providing legit range with start and end versions.
#[derive(Parser)]
pub struct Opt {
    #[clap(
        long,
        help = "The first transaction version required to be replayed and verified"
    )]
    start_version: Version,

    #[clap(
        long,
        help = "The last transaction version required to be replayed and verified"
    )]
    end_version: Version,

    #[clap(flatten)]
    replay_concurrency_level: ReplayConcurrencyLevelOpt,

    #[clap(long = "target-db-dir", value_parser)]
```

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L36-49)
```rust
        let state_summary = parent_state_summary.update(
            persisted_state_summary,
            &execution_output.hot_state_updates,
            execution_output.to_commit.state_update_refs(),
        )?;

        let last_checkpoint = state_summary.last_checkpoint();

        let state_checkpoint_hashes = Self::get_state_checkpoint_hashes(
            execution_output,
            known_state_checkpoints,
            last_checkpoint.root_hash(),
            "state",
        )?;
```
