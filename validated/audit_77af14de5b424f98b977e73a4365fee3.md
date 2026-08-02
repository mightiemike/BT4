### Title
`replay-verify` accepts a locally re-executed transaction as matching the archived, consensus-authenticated `TransactionInfo` even when the state-checkpoint / hot-state / position-state roots diverge - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` (`types/src/transaction/mod.rs:2139-2204`) is the authenticated-vs-locally-executed comparison routine used by `storage/db-tool/src/replay_on_archive.rs` and `aptos-move/aptos-debugger/src/aptos_debugger.rs` to decide whether a freshly re-executed `TransactionOutput` matches the archived `TransactionInfo` that was signed off by validators. It checks status, gas used, write-set hash (`state_change_hash`), and event root hash, but it never compares `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`. This is called out by the code's own TODO comment at lines 2197-2202.

### Finding Description
`TransactionInfo` carries several roots that are supposed to bind the committed ledger state to consensus: `state_change_hash` (hash of the write set), `event_root_hash`, and — critically — `state_checkpoint_hash` (root of the Sparse/Jellyfish Merkle state tree at checkpoints), plus, in `TransactionInfoV1`, `hot_state_checkpoint_hash` and `position_state_checkpoint_hash` [1](#0-0) .

`ensure_match_transaction_info` is the function that is supposed to assert that a locally re-executed `TransactionOutput` is consistent with the archived, authenticated `TransactionInfo` for a given version. Its checks are: [2](#0-1) 

It ensures `status`, `gas_used`, `write_set_hash == state_change_hash`, and `event_root_hash` match, but it stops there — the function's own trailing comment documents the gap: "this comparator ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`), so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution."

This function is the sole verification gate used by the `replay_on_archive` tool: [3](#0-2) 

Note that `expected_txn_infos[idx]` (the archived, consensus-signed `TransactionInfo`) is passed in, but `ensure_match_transaction_info` never dereferences its `state_checkpoint_hash()`/`hot_state_checkpoint_hash()`/`position_state_checkpoint_hash()` accessors (which do exist on `TransactionInfo`, e.g. `state_checkpoint_hash()` at [4](#0-3) ). Consequently, a divergence purely in the state root — e.g. from a bug in write-set-to-state-value application, a JMT update bug, or (per the comment) a native-position state root computed incorrectly under `COMPUTE_TRADING_NATIVE_STATE_ROOTS` — will not surface as a replay-verify failure as long as the write set itself (pre-application) still hashes the same. This can also mask divergences that only manifest in the derived state root and not in the write set hash comparison, such as errors in how the JMT/hot-state/position-state trees are updated by a given write set (a bug downstream of the write-set hash check that these tools are specifically meant to catch).

### Impact Explanation
`replay_on_archive` and the debugger's replay path are the primary tools operators and auditors use to confirm that a full re-execution of archived transactions reproduces the exact ledger state that was authenticated by validator signatures (i.e., state-commitment integrity across a hard fork, storage/replay bug, or executor regression). Because the state-checkpoint/hot-state/position-state root fields are never compared, these tools can report "replay successful" even when the re-executed state root diverges from the authenticated one. This directly matches the required impact class: "Authenticated API or state-view output bound to the wrong version, object, or proof context" / "Hard-fork-only divergence during commit, replay, restore, or proof verification" going undetected by the very tool meant to catch it. In a mainnet incident (e.g. a subtle JMT/state-apply bug introduced by a future change, or the position-state-root feature diverging as explicitly warned in the code), this verification gap would let a corrupted or forked state root pass replay-verify undetected, delaying or preventing detection of a chain-state divergence.

### Likelihood Explanation
The gap is not a theoretical parsing error — it is explicitly acknowledged in the source as a known, currently-unaddressed TODO, and it is on the only comparison path exercised by `replay_on_archive`'s per-transaction verification loop. The likelihood of this actually being exploited/triggered is tied to whether some other part of the state-application pipeline (JMT commit, hot-state commit, or the newer native-position state root path) produces a divergent state root while leaving `write_set_hash` and `event_root_hash` unchanged — which the write-set hash check cannot catch by construction, since `state_change_hash` is computed over the write set object itself, not over the resulting Merkle root after applying it to storage. Given the codebase already flags `COMPUTE_TRADING_NATIVE_STATE_ROOTS` and `HOT_STATE_ROOT_IN_TXN_INFO` as newly introduced, actively-evolving features (`types/src/on_chain_config/aptos_features.rs:203-209`), the risk window for a root-computation bug going unnoticed by replay-verify is realistic and not merely hypothetical.

### Recommendation
Extend `ensure_match_transaction_info` to compare `txn_info.state_checkpoint_hash()` (and, when present, `hot_state_checkpoint_hash()` / `position_state_checkpoint_hash()`) against the locally recomputed roots for checkpoint transactions, failing the `ensure!` the same way as the existing write-set/event checks. This should be done before (or as a blocking precondition for) enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, as the code comment itself indicates.

### Proof of Concept
No standalone runnable PoC is possible purely from static review since it requires actually causing a state-root divergence in an execution/storage pipeline, but the exploitation path is directly demonstrable in code:
1. Call `TransactionOutput::ensure_match_transaction_info` with a `TransactionOutput` whose write set, when applied to the JMT, produces a state root different from `txn_info.state_checkpoint_hash()` (or `position_state_checkpoint_hash()`), but whose write-set bytes (and thus `state_change_hash`) and events are otherwise identical to the expected ones.
2. Observe that the function returns `Ok(())`, since only `status`, `gas_used`, `state_change_hash`, and `event_root_hash` are checked (`types/src/transaction/mod.rs:2148-2195`), and none of the `ensure!` blocks reference `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`.
3. In `replay_on_archive.rs::execute_and_verify` (lines 392-397), this means the replay loop treats the transaction as verified and continues, never reporting the state-root divergence to the caller.

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

**File:** types/src/transaction/mod.rs (L2336-2341)
```rust
    pub fn state_checkpoint_hash(&self) -> Option<HashValue> {
        match self {
            Self::V0(v) => v.state_checkpoint_hash,
            Self::V1(v) => v.state_checkpoint_hash,
        }
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
