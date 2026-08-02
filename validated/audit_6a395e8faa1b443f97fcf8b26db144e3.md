## Title
Chunk-executor / replay-verify state-checkpoint (Merkle root) mismatches are not detected during commit — `ensure_match_transaction_info` skips `state_checkpoint_hash` and related checkpoint roots (File: `types/src/transaction/mod.rs`)

## Summary
`TransactionOutput::ensure_match_transaction_info` is the integrity check used by the chunk executor / replay tooling to confirm that a locally-recomputed `TransactionOutput` (status, gas, write-set hash, event root) matches an authenticated `TransactionInfo` fetched from storage/backup/peers before the output is accepted and committed. The function explicitly does **not** verify `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — the fields that commit the Sparse-Merkle-Tree root of the actual world state. This is the direct Aptos analog of the reported bug class: a value that should gate acceptance of committed state (the checkpoint root) is not re-validated/re-derived at the point where new state is accepted, so a state root diverging from the correct VM result can pass unnoticed.

## Finding Description
`ensure_match_transaction_info` at [1](#0-0)  validates transaction status, gas used, write-set hash (`state_change_hash`) and event root hash against the given `TransactionInfo`. It explicitly leaves out the state-checkpoint hashes, with an in-code acknowledgment of the gap: [2](#0-1) 

This comment states directly: *"this comparator ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`), so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution."*

`TransactionInfo` carries these checkpoint roots as first-class commitments to the world state, e.g. `state_checkpoint_hash` ("The root hash of the Sparse Merkle Tree describing the world state at the end of this transaction") and `position_state_checkpoint_hash`, both defined on `TransactionInfoV0`/`TransactionInfoV1`: [3](#0-2) [4](#0-3) .

`ensure_match_transaction_info` is the sole consistency check invoked from:
- the chunk executor's commit/apply path (`execution/executor/src/chunk_executor/mod.rs`),
- the debugger (`aptos-move/aptos-debugger/src/aptos_debugger.rs`),
- the Move CLI (`aptos-move/cli/src/commands.rs`),
- the archive replay-verification tool (`storage/db-tool/src/replay_on_archive.rs`).

None of these call sites independently verify the state-checkpoint root against the recomputed state; they all rely on this single function as the authenticity gate before treating the locally re-executed output as matching the authenticated, accumulator-committed `TransactionInfo`. Because the checkpoint hash comparison is omitted, a state root produced by local re-execution that differs from the one bound into the accumulator-proven `TransactionInfo` will not cause `ensure_match_transaction_info` to fail — the function returns `Ok(())` regardless of state-checkpoint divergence.

This directly parallels the external bug's root cause: a critical dependent value (interest-rate checkpoint / here, the state-root checkpoint) can silently diverge from the true underlying value (total supply / here, the true post-transaction state) because the code path responsible for keeping it in sync/validated does not include it in its invariant check.

## Impact Explanation
This breaks the proof/commit-integrity invariant that "VM outputs, transaction infos, ... must survive executor-to-storage handoff unchanged" and that replay/restore paths must not silently accept a different ledger state than the authenticated one. Concretely:
- `replay_on_archive` and chunk-executor apply-paths are exactly the tools meant to catch divergence between local re-execution and the authenticated on-chain state root (e.g., after a state-sync chunk, backup restore-replay, or debugging tool run). If the local execution produces a different Sparse Merkle root (e.g., due to a state-store bug, a non-deterministic native, a storage schema misinterpretation, or a hidden regression on a hot/position sub-tree), this security net will not flag it, allowing corrupted or incorrect state to be silently treated as validated and committed/persisted.
- This is a hard-fork-only-class divergence: if local execution's state result differs from the network's canonical result but write-set hash/status/gas/events happen to match (or the divergence is confined to the state tree, e.g. hot-state or position-state sub-roots introduced by newer `TransactionInfoV1` fields), it will go undetected by this integrity gate, exactly matching the "Hard-fork-only divergence during commit, replay, restore, or proof verification" impact category.
- The comment itself flags that enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS` (a newer feature computing `position_state_checkpoint_hash`) is unsafe to turn on until this validation gap is closed, indicating the aptos team is aware but has not yet remediated it — an authenticated, proof-bound root (accumulator → `TransactionInfo` → state_checkpoint_hash) can diverge from locally computed state without failing verification.

## Likelihood Explanation
The bug is a real, already-acknowledged gap (visible directly in the source comment) in code that is on the critical path for state-sync chunk execution, DB replay-verification tooling, and debugger/CLI transaction replay. It requires no attacker privilege to trigger the missing check — it is a latent verification omission that activates whenever local re-execution's state-checkpoint hash differs from the authenticated one, for any reason (bug, storage corruption, non-determinism, or future feature interaction such as trading-native state roots). Given it's called from multiple important tools (`chunk_executor`, `replay_on_archive`, `aptos-debugger`), the likelihood of this masking a real divergence during code changes to state-store/hot-state/position-state paths is non-trivial and increases specifically as new checkpoint-hash fields (`hot_state_checkpoint_hash`, `position_state_checkpoint_hash`) are added to `TransactionInfoV1` without back-filling this comparator.

## Recommendation
Extend `ensure_match_transaction_info` to also assert that the locally computed state-checkpoint hash (and, where applicable, `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`) matches `txn_info.state_checkpoint_hash()` / the corresponding fields, whenever the checkpoint hash is present (`Some`) in both the recomputed output and the authenticated `TransactionInfo`. This must be enabled before turning on `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, and should be treated as a release blocker for any code path relying on `ensure_match_transaction_info` as the authoritative correctness gate for replay/chunk execution.

## Proof of Concept
No standalone PoC transaction is provided — the flaw is demonstrated directly by the code: `ensure_match_transaction_info` at [5](#0-4)  performs `ensure!` checks only for status, gas, write-set hash, and event root, then returns `Ok(())` without ever comparing `txn_info.state_checkpoint_hash()`, `txn_info.hot_state_checkpoint_hash()`, or `txn_info.position_state_checkpoint_hash()` to any locally-derived value — confirmed by the developer's own TODO in the function body acknowledging that a diverging "authenticated position state root" would still report a successful replay.

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

**File:** types/src/transaction/mod.rs (L2405-2416)
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
