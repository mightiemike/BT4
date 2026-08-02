Based on my investigation, I found one strong, code-proven analog to the reported bug-class: a validation function that omits checking commitment-critical hash fields during replay verification, exactly mirroring the Beanstalk pattern of "an integrity-critical field is not actually validated/set correctly during a commit-adjacent flow, silently letting committed state diverge from truth."

### Title
`TransactionOutput::ensure_match_transaction_info` skips validating state/hot-state/position checkpoint hashes, letting replay-verify accept a ledger whose committed state root diverges from local execution - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` is the authoritative check used by chunk-execution/replay-verify tooling to confirm that a locally re-executed `TransactionOutput` matches the previously committed, signature-authenticated `TransactionInfo` for the same version [1](#0-0) . It validates status, gas used, write-set hash (`state_change_hash`), and event root hash [2](#0-1) , but explicitly does **not** validate `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`, as documented in its own trailing comment [3](#0-2) .

### Finding Description
This is directly analogous to the Beanstalk report's root cause: a value that is supposed to gate/attest committed state (`stalkIssuedPerBdv` there, the checkpoint-hash fields here) is not actually enforced at the point where state is accepted as correct, so a wrong/uninitialized/mismatched value silently passes through into "confirmed" state.

Here, `TransactionInfo` (the object stored in the transaction accumulator and authenticated by validator signatures) carries a `state_checkpoint_hash` (root of the SMT/world-state at that version) and, in `TransactionInfoV1`, additionally a `hot_state_checkpoint_hash` and a `position_state_checkpoint_hash` [4](#0-3) . These are the exact fields used elsewhere in the executor to authenticate the new "trading-native" position-state feature: `do_state_checkpoint.rs`'s `get_position_checkpoint_hashes`/`extend` path computes a `position_state_checkpoint_hash` from position-state writes and compares it against the value embedded in `TransactionInfo` [5](#0-4) , and state-sync's bootstrapper gates whether to even sync the position-state snapshot based on whether `position_state_checkpoint_hash()` is set on the target `TransactionInfo` [6](#0-5) .

Despite these hashes being load-bearing for ledger-state authentication, `ensure_match_transaction_info` — the function invoked by `db-tool`'s `replay_on_archive` and the chunk executor to assert that locally computed execution output matches the already-committed, validator-signed `TransactionInfo` — never re-derives and compares them [7](#0-6) . If the locally recomputed state/hot-state/position-state root diverges from what was committed (e.g., due to a bug in `COMPUTE_TRADING_NATIVE_STATE_ROOTS` logic, a non-deterministic hot-state promotion, or a corrupted write-set replay), this comparator will report success anyway, since it only checks `state_change_hash`, `event_root_hash`, `status`, and `gas_used`.

### Impact Explanation
Replay-verify is one of the load-bearing tools used to detect state divergence against historical, validator-signed ledger data (used by db-tool's `replay_on_archive` and in the aptos-debugger/CLI paths that reference `ensure_match_transaction_info`). Because the state/hot-state/position-state checkpoint hashes are excluded from the comparison, a state root corruption — whether from a VM/executor bug, a storage bug, or a bug in the new trading-native position-state computation — can be re-executed locally and falsely certified as matching the authenticated on-chain commitment. This breaks the "committed state must match VM result, and proof/commitment fields must be verified against authoritative values" invariant central to the Proof and Storage Pivots in scope, undermining a primary safety net for detecting silent hard-fork-style state divergence. The gap is self-documented in the code as a known limitation (`TODO(trading-native)`), which corroborates that it is a genuine, currently-unmitigated hole rather than a misreading on my part.

### Likelihood Explanation
This code path executes on every replay-verify/chunk-execution match check where the feature paths that set these checkpoint hashes are exercised (i.e., whenever `TransactionInfoV1` fields are non-default, including hot-state and position-state). Because `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` are not periodically compared in this specific final safety-check, any latent bug or non-determinism in the newer trading-native/position-state or hot-state checkpoint computation paths (see the parent-state seeding logic in `do_state_checkpoint.rs`, which already carries special-cased "no in-memory parent at genesis" handling) would go undetected by this verification tool. The bug is a code omission, always active, and requires no attacker action to be dormant — it only requires a legitimate independent execution divergence to become impactful, which is a plausible occurrence given the acknowledged complexity of the new position/hot-state checkpoint machinery.

### Recommendation
Extend `ensure_match_transaction_info` to recompute and compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` against `txn_info`'s corresponding fields whenever they are `Some`, before enabling/relying on `COMPUTE_TRADING_NATIVE_STATE_ROOTS` in production replay-verify tooling, exactly as the existing TODO comment recommends.

### Proof of Concept
Conceptual PoC (cannot be executed without a full node/replay environment):
1. Run a chunk with hot-state or position-state writes enabled such that `TransactionInfoV1.hot_state_checkpoint_hash` / `position_state_checkpoint_hash` are set to specific values in the authenticated, committed ledger.
2. Introduce (or trigger via an existing non-determinism/bug) a local re-execution whose resulting hot-state or position-state root differs from the committed one, while write_set hash, event root, status, and gas used remain identical (e.g., a divergence purely in the auxiliary state trees, not the primary write set).
3. Call `ensure_match_transaction_info` (as used by `replay_on_archive`/chunk executor) — it returns `Ok(())`, incorrectly certifying that local re-execution matches the authenticated ledger state, despite an actual state-root divergence in the hot/position-state trees. [7](#0-6) 

**Caveat/uncertainty**: I could not verify at what production maturity `COMPUTE_TRADING_NATIVE_STATE_ROOTS` / native-position and hot-state checkpoint features currently are (feature-flag gating found in `types/src/on_chain_config/aptos_features.rs` and `types/src/block_executor/config.rs`), nor whether any other check (outside this function) independently re-validates these specific hash fields elsewhere in the replay pipeline. If such a redundant check exists elsewhere, the practical exploitability of this specific gap would be reduced; I was not able to fully trace all call sites of `replay_on_archive`/chunk-executor verification within the available iterations to rule this out with certainty.

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

**File:** types/src/transaction/mod.rs (L2440-2461)
```rust
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[cfg_attr(any(test, feature = "fuzzing"), derive(Arbitrary))]
pub struct TransactionInfoV1 {
    gas_used: u64,
    status: ExecutionStatus,
    transaction_hash: HashValue,
    event_root_hash: HashValue,
    state_change_hash: HashValue,
    state_checkpoint_hash: Option<HashValue>,
    hot_state_checkpoint_hash: Option<HashValue>,
    auxiliary_info_hash: Option<HashValue>,

    /// Repurposed reserved field; `None` matches the prior BCS encoding.
    position_state_checkpoint_hash: Option<HashValue>,
    placeholder1: Option<HashValue>,
    placeholder2: Option<HashValue>,
    placeholder3: Option<HashValue>,
    placeholder4: Option<HashValue>,
    placeholder5: Option<HashValue>,
    placeholder6: Option<HashValue>,
    placeholder7: Option<HashValue>,
}
```

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L179-189)
```rust
        // Per-tx hash vector + known-hash validation (shared with main/hot state).
        let hashes = Self::get_state_checkpoint_hashes(
            execution_output,
            known_position_state_checkpoints,
            new_last_checkpoint.root_hash(),
            "position_state",
        )?;

        let summary =
            LedgerWithSummary::from_latest_and_last_checkpoint(new_latest, new_last_checkpoint);
        Ok((summary, hashes))
```

**File:** state-sync/state-sync-driver/src/bootstrapper.rs (L1120-1142)
```rust
    /// Whether the given snapshot kind participates in the fast sync to this
    /// target. Main state always does. Native-position only participates once the
    /// target's `TransactionInfo` commits a position state root: until the
    /// executor sets it there is no authenticated position state to sync, so the
    /// stage is skipped rather than trusting an unproved peer-supplied root.
    /// Requires the target transaction output to already be fetched.
    fn snapshot_kind_applies_to_target(&mut self, kind: StateKind) -> Result<bool, Error> {
        match kind {
            StateKind::MainState => Ok(true),
            StateKind::Position => {
                let transaction_output_to_sync = self.get_transaction_output_to_sync()?;
                let target_transaction_info = transaction_output_to_sync
                    .get_output_list_with_proof()
                    .proof
                    .transaction_infos
                    .first()
                    .ok_or_else(|| {
                        Error::UnexpectedError("Target transaction info does not exist!".into())
                    })?;
                Ok(target_transaction_info
                    .position_state_checkpoint_hash()
                    .is_some())
            },
```
