### Title
`TransactionOutput::ensure_match_transaction_info` skips state-checkpoint hash validation, silently masking state-root divergence during replay-verify — (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the sole invariant check used by the `replay-verify` tooling (`storage/db-tool/src/replay_on_archive.rs`) to confirm that a freshly re-executed transaction's output matches the transaction info that was actually committed to the archived ledger. The function checks status, gas, write-set hash, and event-root hash, but explicitly does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — a gap the code itself documents as a TODO. As a result, replay-verify can report success even when the locally re-computed state (Sparse-Merkle / Jellyfish-Merkle / hot-state / position-state) root diverges from the authenticated root stored in the archive.

### Finding Description
`ensure_match_transaction_info` is defined at [1](#0-0)  and performs the comparison between a `TransactionOutput` and the expected `TransactionInfo`:
- status [2](#0-1) 
- gas used [3](#0-2) 
- write-set hash vs `state_change_hash` [4](#0-3) 
- event root hash [5](#0-4) 

Immediately after these checks, the function returns `Ok(())` without validating any checkpoint-hash field, and the surrounding comment explicitly states this omission: [6](#0-5) 

`TransactionInfo` (both `V0` and `V1`) carries `state_checkpoint_hash`, and `V1` additionally carries `hot_state_checkpoint_hash` and `position_state_checkpoint_hash`, all of which summarize the authenticated ledger state at that version [7](#0-6) . These fields exist precisely to bind a `TransactionInfo` (and thus a leaf of the transaction accumulator, whose root is signed by validators in the `LedgerInfo`) to the correct world-state root. Skipping them in the one function whose entire purpose is to assert "the locally computed output equals the authenticated result" breaks that binding for anyone relying on this comparator.

The only caller of this function is the replay-verify path: `execute_and_verify` in `storage/db-tool/src/replay_on_archive.rs` re-executes historical transactions from a backup/archive via `AptosVMBlockExecutor`, then calls `ensure_match_transaction_info` against the `expected_txn_info` read back from the backup [8](#0-7) . Because the comparator never checks `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash`, this tool can conclude "successful replay" for a chunk even when the actually-computed state tree (Move VM output → JMT/hot-state root) has silently diverged from the one bound into the signed `LedgerInfo` via the transaction accumulator. `aptos-move/cli/src/commands.rs` also calls this same comparator, inheriting the same gap.

### Impact Explanation
This breaks the proof/commitment-integrity invariant that a `TransactionInfo` leaf (and the state root it encodes) must be provably tied to the actual VM execution result. Replay-verify is the primary tool used to detect exactly this class of divergence (e.g., non-determinism bugs, storage corruption, backup tampering, or a hard-fork-only execution bug that changes state roots without changing write-set/event content). With the checkpoint-hash checks omitted, a state-root divergence — the highest-value signal replay-verify exists to catch — is not detected, and the tool reports a false "successful replay" over the affected range. This directly matches the "Hard-fork-only divergence during commit, replay, restore, or proof verification" impact category, since it can mask committed state differing from the correct VM result without any alert.

### Likelihood Explanation
The gap is deterministic and always present — it does not depend on adversarial behavior, only on triggering a genuine state-root divergence (from a bug, corruption, or non-determinism) during a replay-verify run over any version. It requires no privileged actor and is unprivileged/self-contained: the current comparator code, not external inputs, causes the miss. Given replay-verify's role as a safety-net for detecting deep consensus/execution divergence, this class of gap is high-likelihood to matter whenever a real divergence occurs, precisely when the check is most needed.

### Recommendation
Extend `ensure_match_transaction_info` to also validate `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` whenever the corresponding value can be computed from the current execution output (or is present in `expected_txn_info`), for example by threading through the post-execution state/hot-state/position roots the same way `write_set_hash` and `event_root_hash` are computed. At minimum, gate `COMPUTE_TRADING_NATIVE_STATE_ROOTS` (or an equivalent capability) on landing this fix, and fail closed (return an error) if the checkpoint hash exists in `expected_txn_info` but cannot be independently verified, so replay-verify cannot silently report success on divergent state roots.

### Proof of Concept
1. Take any archived version range containing a transaction with `state_checkpoint_hash` set (block boundary) in the backup manifest.
2. Introduce (or trigger) a benign divergence solely in the resulting state root — e.g., a stale/corrupted JMT node in the replay environment, or a hypothetical bug that alters `state_checkpoint_hash`/`hot_state_checkpoint_hash` computation without touching the write-set bytes or events — while keeping `status`, `gas_used`, write-set hash, and event root hash identical.
3. Run `storage/db-tool/src/replay_on_archive.rs`'s `verify`/`execute_and_verify`, which calls `executed_outputs[idx].ensure_match_transaction_info(version, &expected_txn_infos[idx], ...)` [9](#0-8) .
4. Observe that `ensure_match_transaction_info` returns `Ok(())` (per [10](#0-9) ) because it never compares `state_checkpoint_hash`, even though the true committed state root (as bound to the signed `LedgerInfo` via the transaction accumulator) differs from what was locally recomputed — the replay-verify tool reports success despite the divergence.

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
