Based on my investigation, I found a concrete, self-documented integrity gap in the replay/verification path rather than a speculative one.

### Title
Replay-verification skips state/hot-state/position checkpoint hash checks, allowing corrupted state roots to pass as valid replay - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function used by replay-verification tooling (`db-tool`'s `replay_on_archive`, `aptos-debugger`, and the CLI) to confirm that a locally re-executed transaction output matches the `TransactionInfo` that was actually committed to the ledger accumulator. It checks status, gas, write-set hash, and event root hash, but explicitly does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`. This is stated directly in the code's own TODO comment. [1](#0-0) 

### Finding Description
`ensure_match_transaction_info` validates four fields between a freshly computed `TransactionOutput` and the authenticated `TransactionInfo` stored in the accumulator: execution status, gas used, write-set hash (`state_change_hash`), and event root hash. [2](#0-1) 

It stops there, and the trailing comment admits the omission: [3](#0-2) 

`TransactionInfoV1` carries three additional checkpoint-hash fields that are part of the authenticated, hashed `TransactionInfo` structure (and therefore part of what the accumulator/ledger proof binds to): `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash`. [4](#0-3) 

Since these fields are never compared against the locally-recomputed values in `ensure_match_transaction_info`, any divergence between what a node/verifier computes for the world-state root (or hot-state root, or position/trading-native state root) and what is actually committed on-chain in the `TransactionInfo` will not be caught by this comparison. Because this function is the sole full-fidelity match check used by replay tooling (confirmed via callers in `aptos-move/aptos-debugger/src/aptos_debugger.rs`, `aptos-move/cli/src/commands.rs`, and `execution/executor/src/chunk_executor/mod.rs`), a state root/proof mismatch introduced anywhere upstream — e.g. in `do_state_checkpoint.rs`'s checkpoint construction, or in the trading-native/position-state pipeline (`aptosdb_writer.rs::position_summary_at_commit`, `native_state_committer.rs`) — can silently pass replay verification.

### Impact Explanation
This is a proof-integrity/state-commitment blind spot: it does not itself corrupt state, but it removes the safety net that is supposed to detect when committed state (`state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash`) diverges from the correct VM result during replay-verify. In an ecosystem where `COMPUTE_TRADING_NATIVE_STATE_ROOTS` and hot-state/position accounting are being rolled out (as evidenced by the surrounding `trading-native` and `Position` code found across `storage/aptosdb/src/db/aptosdb_writer.rs` and `storage/aptosdb/src/db/aptosdb_native_position.rs`), a bug in that new accounting path that corrupts the committed root would go undetected by `replay_on_archive`/debugger tooling, since this is explicitly the function meant to catch such divergence. This matches the "Hard-fork-only divergence during commit, replay ... or proof verification" and "wrong ... state proof accepted as valid" categories in the impact gate, because the trust mechanism meant to flag it is disabled by omission.

### Likelihood Explanation
The gap is unconditional (not behind a feature flag) — it exists in all builds today, and the TODO says it should be validated "before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`," implying the feature currently under active development is expected to ship with this check still missing unless separately handled. However, I could not fully confirm from available code index results whether the executor/chunk-executor path performs an independent, equivalent checkpoint-hash comparison elsewhere before or after calling this function (my `read_file` calls into `execution/executor/src/chunk_executor/mod.rs` and `aptos-move/aptos-debugger/src/aptos_debugger.rs` returned no visible content, likely due to indexing/size limits), so I cannot rule out a redundant check existing in the caller that would reduce the practical severity. This uncertainty should be resolved by a Devin session with full file access before treating this as conclusively exploitable in production.

### Recommendation
Extend `ensure_match_transaction_info` to compare locally computed `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present in the recomputed execution output) against the corresponding fields in `txn_info`, mirroring the existing `ensure!` pattern used for `state_change_hash` and `event_root_hash`, before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` or hot-state/position features are enabled by default.

### Proof of Concept
Not applicable as a runnable exploit — this is a verification-omission finding, not a directly triggerable state-corruption bug. The "proof of concept" is the code path itself: any caller invoking `ensure_match_transaction_info` (`aptos-debugger`, `cli/commands.rs`, `chunk_executor`) with a `TransactionOutput` whose recomputed state/hot-state/position checkpoint hash differs from the `txn_info`'s corresponding hash will still return `Ok(())`, because those fields are never read or compared in the function body. [1](#0-0)

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
