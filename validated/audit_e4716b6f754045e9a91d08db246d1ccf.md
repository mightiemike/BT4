### Title
`ensure_match_transaction_info` skips state/hot-state/position checkpoint hash verification, letting replay-verify tooling accept a corrupted state root - (File: types/src/transaction/mod.rs)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the authenticated cross-check used to confirm that a locally re-executed `TransactionOutput` matches the `TransactionInfo` committed on-chain (and covered by the transaction accumulator/ledger-info signatures). It validates status, gas used, write-set hash (`state_change_hash`), and event root hash, but explicitly does **not** validate `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — all of which are proof-bearing fields committed into `TransactionInfo` and thus into the transaction accumulator root.

### Finding Description
The function's own comment documents the gap: [1](#0-0) 

It checks only `status`, `gas_used`, `write_set` hash and `event_root_hash`: [2](#0-1) 

`state_checkpoint_hash` is the root of the Sparse/Jellyfish Merkle state tree at a checkpoint boundary, and `position_state_checkpoint_hash` is the native-position (trading) state root, both stored inside the authenticated `TransactionInfo`: [3](#0-2) 

This comparator is used by replay/verification tooling — e.g. `storage/db-tool/src/replay_on_archive.rs`, `aptos-move/aptos-debugger/src/aptos_debugger.rs`, `aptos-move/cli/src/commands.rs`, and `execution/executor/src/chunk_executor/mod.rs` — as the correctness oracle when re-executing historical transactions against an archived/synced database to confirm the local VM/state result matches the chain-committed result.

Because the checkpoint-hash fields are skipped, if local re-execution produces a different state-checkpoint root (main state tree, hot state, or position/native-trading state) than what is embedded in the persisted `TransactionInfo`/accumulator, `ensure_match_transaction_info` still returns `Ok(())`. The divergence is silently accepted as long as status/gas/write-set/events happen to match.

### Impact Explanation
This breaks the "committed state must not differ from the correct VM result" and "proof-bearing responses must stay bound to the right root" invariants required by the scan: replay-verification and debugging tools that rely on this function as their correctness oracle (`replay_on_archive`, `aptos-debugger`) can report a **successful** replay even though the authenticated state-checkpoint root (and, once `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled, the native-position root) diverges from the value actually committed to the ledger accumulator. This masks state-corruption or non-determinism bugs that would otherwise be caught by proof verification, undermining confidence in fast-sync/backup validation and archive-replay auditing — a real proof-integrity gap, though it is a detection/verification-completeness bug rather than a directly exploitable state-corruption primitive by an unprivileged actor.

### Likelihood Explanation
The gap is unconditionally present in code today; the TODO explicitly flags it as something to fix "before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`," implying the authors know the position-checkpoint case is live risk once that feature flips on. For the main `state_checkpoint_hash`/`hot_state_checkpoint_hash` fields, the gap already exists for every call site today, but its exploitability is bounded by the fact that a real state-checkpoint divergence would typically also need to be introduced by a separate underlying bug (execution non-determinism, storage corruption, etc.) for this to matter in practice — this function's flaw is that it fails to *detect* such divergence rather than causing it.

### Recommendation
Extend `ensure_match_transaction_info` to compare `self`'s locally computed state-checkpoint hash (when available/expected), hot-state checkpoint hash, and position-state checkpoint hash against `txn_info.state_checkpoint_hash()`, `txn_info.hot_state_checkpoint_hash()`, and `txn_info.position_state_checkpoint_hash()` respectively, returning an error on mismatch — mirroring the existing pattern used for `state_change_hash` and `event_root_hash`. This should be done before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled on mainnet, as the code comment itself indicates.

### Proof of Concept
Not applicable as a runtime exploit PoC — this is a verification-completeness gap in local library code, not an attacker-triggerable state-corruption primitive. Demonstration is by code inspection: call `ensure_match_transaction_info` with a `TransactionOutput` whose write set/events/status/gas match `txn_info` but whose corresponding checkpoint state root (simulate by constructing a `TransactionInfo` with a different `state_checkpoint_hash`/`position_state_checkpoint_hash` than what local state-checkpoint computation would produce) — the function returns `Ok(())` regardless, confirmed by reading the function body which never reads or compares those fields [4](#0-3) .

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
