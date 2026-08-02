Based on my investigation, I found a genuine, self-documented integrity gap in the transaction-output-to-transaction-info verification logic, analogous to the reported bug class of "a value that should be fully unpacked/verified is instead accepted without full validation."

### Title
`TransactionOutput::ensure_match_transaction_info` silently skips checkpoint-hash validation, letting a corrupted state root be accepted as verified - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function used to check that a freshly-computed `TransactionOutput` (from re-executing/replaying a transaction) matches an already-authenticated `TransactionInfo` (the object that is bound into the transaction accumulator and thus into a signed `LedgerInfo`). The function validates status, gas used, `write_set_hash` (state_change_hash) and `event_root_hash`, but explicitly does **not** validate `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — fields that are the actual authenticated commitments to the post-transaction state root(s). [1](#0-0) 

### Finding Description
`ensure_match_transaction_info` is called from `chunk_executor/mod.rs`, `aptos-debugger/src/aptos_debugger.rs`, `aptos-move/cli/src/commands.rs`, and `storage/db-tool/src/replay_on_archive.rs` to confirm that locally re-executed transaction output is consistent with the `TransactionInfo` fetched with a Merkle-accumulator proof (i.e., an authenticated value). [2](#0-1) 

The comment left directly in the code acknowledges the gap: [3](#0-2) 

```
// TODO(trading-native): this comparator ignores the checkpoint hashes
// (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
// tooling (e.g. db-tool's `replay_on_archive`) can report a successful
// replay even when the authenticated position state root diverges from
// local execution. Validate the checkpoint hashes here before enabling
// COMPUTE_TRADING_NATIVE_STATE_ROOTS.
```

`TransactionInfo` carries `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` as the durable, accumulator-bound commitments to the JMT/state-tree roots at that version: [4](#0-3) 

Because `ensure_match_transaction_info` never compares these fields against the locally computed checkpoint hashes, a consumer relying on this function to validate re-executed output against ground truth (chunk executor consuming remote-supplied outputs, or replay/debug tooling auditing an already-committed chain) can conclude "match" even though the actual Sparse-Merkle/Jellyfish-Merkle state root, hot-state root, or the newly added position-state root diverges from what was truly computed by the VM.

### Impact Explanation
This breaks the "proof and storage pivot" invariant that VM outputs and their checkpoint/state roots must survive the executor→storage handoff and their validation unchanged and complete. A wrong state-checkpoint hash is exactly the class of bug this gate is meant to catch (wrong accumulator/state root accepted as valid). If this comparator is used on the ingest path for chunk execution/state sync (`execution/executor/src/chunk_executor/mod.rs`) rather than purely in offline audit tooling, a peer serving a `TransactionOutput` with the correct write-set/events but a mismatched state-checkpoint root could pass this check, letting the node's local pipeline diverge from the authenticated ledger. At minimum, it silently defeats the replay-verify auditing tool (`db-tool replay_on_archive`) meant to catch state divergence.

### Likelihood Explanation
Uncertain/Medium. I could not fully confirm at what point `execution/executor/src/chunk_executor/mod.rs` calls this function — the file contents were not available through the indexed search (likely an indexing size-limit exclusion), so I cannot verify whether the mismatch could actually be exploited on the primary consensus/commit path versus only in the auxiliary replay/debug tools (`aptos-debugger`, `db-tool replay_on_archive`, `aptos-move/cli`). The comment in the source itself indicates the immediate, acknowledged consequence is limited to "replay-verify tooling" reporting false-positive success, which is a lower-severity, tooling-only issue rather than a live consensus/commit-path acceptance of a wrong state root. This is gated behind the not-yet-enabled `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature flag for the position-root part specifically. [3](#0-2) 

Given the index limitation prevented me from reading `execution/executor/src/chunk_executor/mod.rs` to confirm whether this check gates state-sync commit decisions on mainnet (versus only offline tooling), I cannot assert with certainty that this is a live-node, unprivileged, mainnet-exploitable path today. I recommend a Devin session with full filesystem access to trace all call sites of `ensure_match_transaction_info` (`aptos-debugger/src/aptos_debugger.rs`, `aptos-move/cli/src/commands.rs`, `execution/executor/src/chunk_executor/mod.rs`, `storage/db-tool/src/replay_on_archive.rs`) to determine definitively whether any of them feed into the actual consensus/state-sync commit decision versus purely diagnostic/offline replay.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present on the corresponding `TransactionInfo` variant) against the locally computed checkpoint hashes for that version, exactly as is already done for `write_set_hash` and `event_root_hash`, before this function reports a match to any caller (whether commit-path or replay-verify tooling).

### Proof of Concept
Not independently reproducible from index-only access: the concrete PoC would require constructing a `TransactionOutput`/`TransactionInfo` pair with identical `write_set`/events (so `state_change_hash` and `event_root_hash` match) but a state tree mutated such that the resulting state-checkpoint root differs, then showing `ensure_match_transaction_info` returns `Ok(())` despite the state root mismatch — this logical gap is directly demonstrated by the source code and its own TODO comment, but full exploitation confirmation requires exercising the call sites (particularly `chunk_executor/mod.rs`), which I could not read due to indexing limits. A Devin session with full repo access is recommended to build and run this PoC end-to-end.

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
