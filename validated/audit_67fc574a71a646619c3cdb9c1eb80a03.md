## Finding

### Title
Replay-verification skips checkpoint-hash validation, allowing an authenticated position/state-checkpoint root divergence to go undetected - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function used by replay/verification tooling (e.g. `db-tool`'s `replay_on_archive`) to confirm that a locally re-executed `TransactionOutput` matches the `TransactionInfo` that was actually committed on-chain. The function validates status, gas, write-set hash (`state_change_hash`), and event root hash, but its own code comment admits it deliberately omits validation of the state-checkpoint hash and the newly introduced `position_state_checkpoint_hash` field. This mirrors the structural class of bug in the external report: a field that is supposed to carry the authoritative "root of truth" for a sub-system (there, `s.sys.fields[fieldId]`; here, `position_state_checkpoint_hash`/`state_checkpoint_hash`) is not actually wired into the code path that is responsible for confirming state integrity, so the check silently no-ops on the very data it's supposed to be verifying.

### Finding Description
`ensure_match_transaction_info` is defined in `types/src/transaction/mod.rs`: [1](#0-0) 

It checks `status`, `gas_used`, `write_set_hash` against `txn_info.state_change_hash()`, and `event_root_hash` against `txn_info.event_root_hash()`. It never compares the locally computed state-checkpoint hash (or hot-state / `position_state_checkpoint_hash`) against `txn_info.state_checkpoint_hash()` / `txn_info.position_state_checkpoint_hash()`. This is explicitly called out in the code itself: [2](#0-1) 

The `position_state_checkpoint_hash` field is a newly repurposed reserved field in `TransactionInfoV1` that is meant to authenticate the native-position Jellyfish Merkle root produced by the position-state pipeline: [3](#0-2) 

This root is computed by `merklize_position` in the executor's commit path and is treated as the trust anchor for state-sync fast-sync bootstrapping of the position state — the bootstrapper explicitly states it will not sync an "unproved peer-supplied root" and instead waits for the `TransactionInfo`'s committed `position_state_checkpoint_hash`: [4](#0-3) 

Because the trust model of both state-sync (position snapshot bootstrapping) and the archival replay/verify tooling both rely on `TransactionInfo.position_state_checkpoint_hash`/`state_checkpoint_hash` being the ground truth for "state after this transaction," any consumer that treats a successful `ensure_match_transaction_info` result as proof that locally re-executed state matches the chain is being given a false guarantee: the function returns `Ok(())` even when the replayed write-set produces a state (or position-state) root that diverges from what is committed in the ledger.

### Impact Explanation
Replay-verify style tooling (`db-tool replay-on-archive`/`replay_verify` coordinator) is a core state-integrity gate for validating that an executor version's re-execution reproduces the authenticated on-chain ledger. If this check silently passes despite a genuine divergence in `state_checkpoint_hash` or `position_state_checkpoint_hash`, a corrupted commit, a storage/commit bug (e.g. in `merklize_position`, `native_state_committer.rs`, or the main JMT commit path), or a hard-fork-only divergence in checkpoint computation would not be caught by the verification tool that is specifically supposed to catch it. This directly falls into the in-scope category "Hard-fork-only divergence during commit, replay, restore, or proof verification" and "Wrong accumulator root... proof accepted as valid" — here a wrong *checkpoint hash* is accepted as valid because the verifier never inspects it.

### Likelihood Explanation
This is not a hypothetical trigger — the gap is self-documented by the authors as a known, currently-live limitation ("this comparator ignores the checkpoint hashes... so replay-verify tooling... can report a successful replay even when the authenticated position state root diverges from local execution"). Any bug elsewhere in the checkpoint-hash computation pipeline (state Merkle tree, hot-state root, or the newly added native-position JMT) will not be caught by the standard replay-verification safety net, meaning latent correctness bugs in those subsystems could go undetected through the exact tooling meant to detect them.

### Recommendation
Extend `ensure_match_transaction_info` to also compare the locally derived state-checkpoint hash and (when present) `position_state_checkpoint_hash` against the values carried in `txn_info`, at least when those hashes are expected to be present (i.e., at checkpoint boundaries), before any feature (e.g. `COMPUTE_TRADING_NATIVE_STATE_ROOTS`) that depends on this verification path is enabled in production.

### Proof of Concept
1. Introduce (or trigger, via a genuine bug) a divergence between the position-state JMT root computed during local re-execution/replay and the `position_state_checkpoint_hash` recorded in the on-chain `TransactionInfoV1` for a given version (e.g. a discrepancy in `merklize_position`'s delta computation).
2. Run `db-tool`'s replay/verify flow, which calls `ensure_match_transaction_info` to validate the replayed `TransactionOutput` against the archived `TransactionInfo`.
3. Because `ensure_match_transaction_info` never reads `state_checkpoint_hash()` or `position_state_checkpoint_hash()` from `txn_info`, the call returns `Ok(())` and the tool reports a successful replay, even though the position-state root differs from the authenticated ledger value — masking the divergence from operators/auditors who rely on this tool as their integrity gate. [1](#0-0)

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

**File:** state-sync/state-sync-driver/src/bootstrapper.rs (L1120-1144)
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
        }
    }
```
