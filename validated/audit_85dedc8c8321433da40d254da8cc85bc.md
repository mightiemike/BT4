The comment I found is a genuine, developer-acknowledged integrity gap that is a strong analog to the "unchecked timestamp/price" bug class: a verification routine skips validating an authenticated field that binds committed data to the correct state.

### Title
Replay-verify accepts divergent state-checkpoint (state root) hashes because `ensure_match_transaction_info` never validates them - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function used by `db-tool`'s replay-verify path (`storage/db-tool/src/replay_on_archive.rs`) to confirm that locally re-executed transactions match the `TransactionInfo` fetched from an archived/trusted backup. It checks execution status, gas used, write-set hash, and event root hash, but by its own acknowledged `TODO` comment, it never checks `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — the fields that authenticate the Sparse-Merkle/Jellyfish state root produced by a transaction.

### Finding Description
`ensure_match_transaction_info` performs four checks against the supplied `txn_info: &TransactionInfo`: transaction status, gas used, `state_change_hash` (write-set hash), and `event_root_hash`. It then returns `Ok(())` without comparing `self` (the freshly re-executed `TransactionOutput`) against `txn_info.state_checkpoint_hash()` / `hot_state_checkpoint_hash()` / `position_state_checkpoint_hash()`. [1](#0-0) 

These checkpoint hash fields are the authenticated commitments to the actual world-state root (and the newer "trading-native" hot-state/position state roots) at that version, as defined in `TransactionInfoV0`/`TransactionInfoV1`. [2](#0-1) 

The only consumer of this function is the `db-tool replay-on-archive` verifier, which fetches `TransactionInfo` from a backup/archive source and re-executes each transaction locally through `AptosVMBlockExecutor`, then calls `ensure_match_transaction_info` to accept or reject the replay as consistent: [3](#0-2) 

Because the state-root fields are skipped, if the archived/trusted `TransactionInfo` carries a `state_checkpoint_hash` that differs from what local, correct VM execution actually produces (whether due to a bug in the SMT/JMT computation, a corrupted archive entry, or a divergent on-disk state), the replay-verify tool will still report success. The gap is explicitly flagged in the code itself: [4](#0-3) 

### Impact Explanation
This breaks the state-commitment integrity invariant that replay/verification tooling exists to enforce: that locally computed state matches what was authenticated and stored. An attacker or a bug that corrupts the state root in an archived/backup `TransactionInfo` (or a state-computation regression that silently diverges from the correct VM result) would not be caught by `replay_on_archive`, giving false confidence that a given segment of the ledger's state roots are correct when they are not. This falls squarely under the requested impact class: "Wrong accumulator root, Merkle proof, transaction proof, event proof, or state proof accepted as valid" and "Hard-fork-only divergence during commit, replay, restore, or proof verification," since a state root mismatch would only be detectable off the normal consensus path (which does check `state_checkpoint_hash` at commit time via `do_state_checkpoint.rs`), but is invisible to this dedicated auditing/replay-verify tool.

### Likelihood Explanation
The condition triggers deterministically any time `replay_on_archive` is run against a version whose real, correctly-computed state root differs from the state root recorded in the source `TransactionInfo` — this requires no attacker action beyond supplying (or having) a backup/archive with a wrong `state_checkpoint_hash`, or a latent divergence in state-root computation logic. Since normal validator commit paths validate state checkpoint hashes separately (`execution/executor/src/workflow/do_state_checkpoint.rs`), the practical exposure is limited to this specific replay-verify tool, but that is exactly the tool whose job is to catch such divergences, so its blind spot is high-impact when relied upon (e.g., for post-mortem forensics, archive-node health checks, or auditing after an incident).

### Recommendation
Add explicit comparisons in `ensure_match_transaction_info` between the locally computed state checkpoint hash(es) — obtained from the ledger-update/state-checkpoint output that produced `self` — and `txn_info.state_checkpoint_hash()`, `hot_state_checkpoint_hash()`, and `position_state_checkpoint_hash()` (when the trading-native feature is enabled), returning an error on mismatch just as is done for `state_change_hash` and `event_root_hash`. This should be done before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, as the comment itself recommends.

### Proof of Concept
1. Prepare (or corrupt) an archive/backup transaction info entry so its `state_checkpoint_hash` does not correspond to the actual post-execution state root for that version (all other fields — status, gas, write-set hash, event root — remain correct/unchanged).
2. Run `db-tool replay-on-archive` (`storage/db-tool/src/replay_on_archive.rs`, `Verifier::verify`/`execute_and_verify`) against that version range.
3. Observe that `ensure_match_transaction_info` at `types/src/transaction/mod.rs:2139-2204` returns `Ok(())` because it never inspects `state_checkpoint_hash`, so `execute_and_verify` records no failure — the tool reports the replay as successful despite the corrupted/divergent state root.

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

**File:** types/src/transaction/mod.rs (L2405-2461)
```rust
    /// The hash value summarizing all changes caused to the world state by this transaction.
    /// i.e. hash of the output write set.
    state_change_hash: HashValue,

    /// The root hash of the Sparse Merkle Tree describing the world state at the end of this
    /// transaction. Depending on the protocol configuration, this can be generated periodical
    /// only, like per block.
    state_checkpoint_hash: Option<HashValue>,

    /// The hash value summarizing PersistedAuxiliaryInfo.
    auxiliary_info_hash: Option<HashValue>,
}

impl TransactionInfoV0 {
    pub fn new(
        transaction_hash: HashValue,
        state_change_hash: HashValue,
        event_root_hash: HashValue,
        state_checkpoint_hash: Option<HashValue>,
        gas_used: u64,
        status: ExecutionStatus,
        auxiliary_info_hash: Option<HashValue>,
    ) -> Self {
        Self {
            gas_used,
            status,
            transaction_hash,
            event_root_hash,
            state_change_hash,
            state_checkpoint_hash,
            auxiliary_info_hash,
        }
    }
}

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
