### Title
`ensure_match_transaction_info` never validates state/hot-state/position-state checkpoint hashes, letting replay-verify tooling accept a locally-diverged state root as a valid replay - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` (the routine `db-tool`'s `replay_on_archive`, `replay_verify`, and the `aptos-debugger`/CLI replay path use to confirm a locally re-executed transaction matches the authenticated on-chain `TransactionInfo`) checks status, gas, write-set hash, and event root hash — but never checks `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`. This mirrors the YieldBox bug-class pattern of accepting a value as authoritative while a component that should be reconciled against ground truth (the real, on-chain-committed checkpoint root) is silently ignored, letting the "reported" state (successful replay) diverge from the real committed one.

### Finding Description
`ensure_match_transaction_info` in [1](#0-0)  is the single authenticity check used by replay/verification tooling to confirm that locally-recomputed transaction execution matches the historically committed, ledger-info-signed `TransactionInfo`. It validates:
- execution status,
- gas used,
- write-set hash (`state_change_hash`),
- event root hash.

It does **not** validate `TransactionInfo::state_checkpoint_hash()`, `hot_state_checkpoint_hash()`, or `position_state_checkpoint_hash()`. This gap is explicitly acknowledged in a code comment right at the end of the function: [2](#0-1) , which states verbatim that "replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution."

The function is used directly as the pass/fail gate in `storage/db-tool/src/replay_on_archive.rs`'s `execute_and_verify`: [3](#0-2) , and in the CLI replay command: [4](#0-3) . Neither of these callers independently validates the state/hot-state/position checkpoint hashes elsewhere, so the gap is not compensated.

This is a genuine local invariant break in the "proof/checkpoint binding" sense required by the task: a `TransactionInfo` field that is consensus-committed (part of the accumulator leaf hash, hence authenticated) is excluded from the equality check that is supposed to bind locally recomputed state to it. The native-position subsystem makes the impact concrete: its state root is deliberately gated behind `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, and code elsewhere assumes replay/verify tooling to be the safety net for catching V0/V1 write-set serialization divergence of `Extension::NativePosition` data (which is `#[serde(skip)]` on `WriteSetV0` and thus silently dropped unless the V1 write-set format is in use — see [5](#0-4)  and the gating rationale at [6](#0-5) ). If any bug causes the locally-recomputed position/state/hot-state root to diverge from the authenticated on-chain root (e.g. from a native-position extension being dropped, an off-by-one in checkpoint indexing, or a JMT extend bug), `ensure_match_transaction_info` will still report success.

### Impact Explanation
Replay-verify (`replay_on_archive`, `replay_verify`) and the debugger/CLI replay tooling are the primary tools operators and auditors use to independently confirm that historical Aptos ledger state is correct and was not corrupted by a bug in execution, checkpoint computation, or storage commit. If the state/hot-state/position checkpoint hash comparison is silently skipped, a genuine ledger-state divergence (e.g., corrupted JMT root, wrong position-state root, or any bug that only manifests in the state-checkpoint value while leaving the write-set hash unaffected — note: write-set hash and state-checkpoint hash are computed from different data, the former from the transaction's own writes, the latter from the accumulated state tree) would go completely undetected by these tools. This directly weakens the "authenticated API and proof-bearing responses must stay bound to the right ledger version, root" guarantee central to this task's scope, since it is precisely the check that is supposed to catch state root divergence during replay.

### Likelihood Explanation
The gap is unconditionally present today for every replay-verify run: `state_checkpoint_hash` is never checked, and the code comment confirms the risk was already identified. The immediate exploitation path (position-state divergence) requires `COMPUTE_TRADING_NATIVE_STATE_ROOTS` to be enabled on-chain (currently gated behind a feature flag, not yet enabled in the referenced feature list defaults), but the missing `state_checkpoint_hash`/`hot_state_checkpoint_hash` check applies unconditionally to all networks and all transactions, independent of that feature flag. Any latent bug in the main-state checkpoint computation (JMT/SMT logic) that is not exercised by write-set-hash checks would similarly go undetected by replay-verify today.

### Recommendation
Extend `ensure_match_transaction_info` to compare `self`'s recomputed checkpoint hash(es) against `txn_info.state_checkpoint_hash()`, `txn_info.hot_state_checkpoint_hash()` (when applicable), and `txn_info.position_state_checkpoint_hash()` (when `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled), following the same `ensure!` pattern already used for gas/status/write-set/events. Since `TransactionOutput` alone does not carry the recomputed checkpoint hash (that's produced by `DoStateCheckpoint`), the replay tooling call sites need to be updated to pass in the locally-computed checkpoint hash(es) for comparison, not just rely on `TransactionOutput`.

### Proof of Concept
Not applicable as a standalone exploit — this is a verification-logic gap, not an on-chain state-mutation exploit. It can be demonstrated by: (1) enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/native-position writes in a test network, (2) intentionally corrupting the locally-computed position-state root during replay (e.g. by feeding a V0-formatted write set that silently drops the `Extension::NativePosition` bucket per [7](#0-6) ), and (3) observing that `execute_and_verify` in `replay_on_archive.rs` still reports success because `ensure_match_transaction_info` never inspects `position_state_checkpoint_hash`.

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

**File:** storage/db-tool/src/replay_on_archive.rs (L392-405)
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
```

**File:** aptos-move/cli/src/commands.rs (L2809-2813)
```rust
        if !skip_comparison {
            txn_output
                .ensure_match_transaction_info(self.txn_id, &txn_info, None, None)
                .map_err(|msg| CliError::UnexpectedError(msg.to_string()))?;
        }
```

**File:** types/src/write_set.rs (L789-827)
```rust
/// `WriteSet` contains all access paths that one transaction modifies. Each of them is a `WriteOp`
/// where `Value(val)` means that serialized representation should be updated to `val`, and
/// `Deletion` means that we are going to delete this access path.
#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct WriteSetV0 {
    value_writes: WriteSetMut,
    /// Hot state promotions, non-empty only in block epilogues.
    #[serde(skip)]
    hotness: BTreeSet<StateKey>,
    /// Opt-in side-channels (see [`Extension`]). Skipped from serde so `TransactionInfo` hashes and
    /// the on-disk WriteSet format are unaffected.
    #[serde(skip)]
    extensions: Vec<Extension>,
}

impl WriteSetV0 {
    #[inline]
    pub fn iter(&self) -> btree_map::Iter<'_, StateKey, WriteOp> {
        self.value_writes.write_set.iter()
    }

    #[inline]
    pub fn into_write_op_iter(self) -> btree_map::IntoIter<StateKey, WriteOp> {
        self.value_writes.write_set.into_iter()
    }

    pub fn get(&self, key: &StateKey) -> Option<&WriteOp> {
        self.value_writes.get(key)
    }
}

/// Like [`WriteSetV0`], but serializes the hotness and extension
/// buckets alongside the value write set.
#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct WriteSetV1 {
    value_writes: WriteSetMut,
    hotness: BTreeSet<StateKey>,
    extensions: Vec<Extension>,
}
```

**File:** types/src/block_executor/config.rs (L180-188)
```rust
        // Requires transaction_info_v1 (the root rides in TransactionInfoV1) and
        // hotness_in_epilogue (only the V1 write-set format it enables serializes
        // the native-position extensions; V0 drops them, so output-replay would
        // diverge). Degrades to off if either is missing.
        self.compute_trading_native_state_roots = features
            .is_compute_trading_native_state_roots_enabled()
            && features.is_transaction_info_v1_enabled()
            && features.is_hotness_in_epilogue_enabled();
        self
```
