## Finding: Replay-verify comparator omits state-checkpoint hash validation, so a wrong state root produced during backup replay is not detected

### Title
`TransactionOutput::ensure_match_transaction_info()` never validates `state_checkpoint_hash` / `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`, letting a corrupted state root pass replay-verify - (`types/src/transaction/mod.rs`)

### Summary
The external report's bug class is: a value that should be bound to a specific denominator/scale is silently compared/used without that scale, so a wrong value is accepted as correct. The Aptos analog is `TransactionOutput::ensure_match_transaction_info()` in [1](#0-0) , the canonical comparator used to decide whether a locally re-executed transaction "matches" an authenticated, on-chain-committed `TransactionInfo`. It checks status, gas, write-set hash (`state_change_hash`) and event root hash, but it never checks `TransactionInfo::state_checkpoint_hash()`, `hot_state_checkpoint_hash()`, or `position_state_checkpoint_hash()` — the fields that actually commit the state Merkle/JMT root, hot-state root, and native-position root to consensus. This gap is called out in the code itself: [2](#0-1) 

### Finding Description
`ensure_match_transaction_info` computes and checks only:
- `status` vs. `txn_info.status()`
- `gas_used` vs. `txn_info.gas_used()`
- `write_set_hash = hash(write_set)` vs. `txn_info.state_change_hash()`
- `event_root_hash` vs. `txn_info.event_root_hash()` [3](#0-2) 

None of these fields depend on the *post-application* Merkle state root — `state_change_hash` is a hash of the write set (the intended deltas), not of the resulting state tree. The actual state-commitment fields living on `TransactionInfoV1` — `state_checkpoint_hash`, `hot_state_checkpoint_hash`, `position_state_checkpoint_hash` — are never compared against anything locally recomputed by this function. [4](#0-3) 

This comparator is the verification primitive used by the chunk executor's `verify_execution` path (invoked from backup/restore "replay-verify" flows) and by `db-tool`'s archive replay verifier: [5](#0-4) [6](#0-5) 

Both call sites pass the archived, ledger-signed `TransactionInfo` as ground truth and rely on `ensure_match_transaction_info` returning `Ok(())` to conclude "the archived history is provably correct." Because the function never checks the state-checkpoint hash, a divergence anywhere between the write set and the actual state root it produces — e.g. a bug in Jellyfish Merkle Tree construction, hot-state promotion, or the position-state tree used by `COMPUTE_TRADING_NATIVE_STATE_ROOTS` (`types/src/on_chain_config/aptos_features.rs` FeatureFlag 122/123) — is invisible to this check even though the ledger-committed `TransactionInfo` (and therefore the transaction accumulator root that consensus and light clients trust) is wrong.

### Impact Explanation
Replay-verify and `db-tool`'s `replay-on-archive` are the primary mechanisms operators, auditors, and Aptos Labs itself use to prove that an archived/backed-up chain history is authentic and that a full-history replay reproduces the exact same ledger state that was originally certified by validator signatures. Because the comparator silently ignores the state/hot-state/position checkpoint hashes, a bug that corrupts the *state root computation* (while leaving the write set itself, gas, events, and status unaffected) — for instance a JMT update defect, an incorrect hot-state promotion, or a defect in the newly added native-position tree merklization used by `DoStateCheckpoint::compute_position_checkpoint` (`execution/executor/src/workflow/do_state_checkpoint.rs`) — would pass replay-verify as a clean, fully-verified match. This breaks the "committed state must match the correct VM result" and "wrong accumulator/Merkle root accepted as valid" integrity gates: an operator or backup consumer restoring from that backup would silently inherit and continue committing on top of an incorrect, unauthenticated state root, with no tooling-level detection.

### Likelihood Explanation
This is not a hypothetical: the gap is pre-existing for `state_checkpoint_hash`/`hot_state_checkpoint_hash` (used whenever `TRANSACTION_INFO_V1`/`HOT_STATE_ROOT_IN_TXN_INFO` is enabled) and is explicitly flagged in-repo as a blocker to enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS` safely. The condition to trigger it is simply "state root computation diverges from the write set it was derived from" — which is exactly the class of bug (Merkle tree construction/restore/serialization defects) that this same task description calls out as in-scope, and is precisely the kind of bug replay-verify exists to catch.

### Recommendation
Extend `ensure_match_transaction_info` to recompute and compare `state_checkpoint_hash`, `hot_state_checkpoint_hash` (when `hot_state_root_in_txn_info` is active), and `position_state_checkpoint_hash` (when `compute_trading_native_state_roots` is active) against the corresponding fields on `txn_info`, propagating a hard failure on mismatch, before any feature that populates these fields is safe to rely on for replay-verify guarantees.

### Proof of Concept
1. Enable `TRANSACTION_INFO_V1` (and optionally `HOT_STATE_ROOT_IN_TXN_INFO` / `COMPUTE_TRADING_NATIVE_STATE_ROOTS`) so `TransactionInfoV1` carries real checkpoint hashes.
2. Construct/replay a chunk where the recomputed write set, gas, status, and events are identical to the archived `TransactionInfo`, but the state Merkle tree update (or hot-state/position-state update) applied by the local `DoStateCheckpoint` produces a different root than the one embedded in the archived `TransactionInfo` (e.g., feed a state snapshot restore with a subtly corrupted leaf or an off-by-one in hot-state promotion).
3. Call `execution/executor/src/chunk_executor/mod.rs::verify_execution` (or `db-tool`'s `replay_on_archive`) on this chunk.
4. Observe `ensure_match_transaction_info` returns `Ok(())` and replay-verify reports success, despite the state root diverging from the ledger-committed value.

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

**File:** types/src/transaction/mod.rs (L2463-2493)
```rust
impl TransactionInfoV1 {
    pub fn new(
        transaction_hash: HashValue,
        state_change_hash: HashValue,
        event_root_hash: HashValue,
        state_checkpoint_hash: Option<HashValue>,
        hot_state_checkpoint_hash: Option<HashValue>,
        gas_used: u64,
        status: ExecutionStatus,
        auxiliary_info_hash: Option<HashValue>,
        position_state_checkpoint_hash: Option<HashValue>,
    ) -> Self {
        Self {
            gas_used,
            status,
            transaction_hash,
            event_root_hash,
            state_change_hash,
            state_checkpoint_hash,
            hot_state_checkpoint_hash,
            auxiliary_info_hash,
            position_state_checkpoint_hash,
            placeholder1: None,
            placeholder2: None,
            placeholder3: None,
            placeholder4: None,
            placeholder5: None,
            placeholder6: None,
            placeholder7: None,
        }
    }
```

**File:** execution/executor/src/chunk_executor/mod.rs (L685-706)
```rust
        for (version, txn_out, txn_info, write_set, events) in multizip((
            begin_version..end_version,
            &execution_output.to_commit.transaction_outputs,
            transaction_infos.iter(),
            write_sets.iter(),
            event_vecs.iter(),
        )) {
            if let Err(err) = txn_out.ensure_match_transaction_info(
                version,
                txn_info,
                Some(write_set),
                Some(events),
            ) {
                return if verify_execution_mode.is_lazy_quit() {
                    error!("(Not quitting right away.) {}", err);
                    verify_execution_mode.mark_seen_error();
                    Ok(version + 1)
                } else {
                    Err(err)
                };
            }
        }
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
