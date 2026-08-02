### Title
Replay-verification bypasses divergence in position/state-checkpoint roots - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info`, the function used by replay-verify tooling (`storage/db-tool/src/replay_on_archive.rs`) and other tools (`aptos-move/aptos-debugger/src/aptos_debugger.rs`, `aptos-move/cli/src/commands.rs`) to confirm that a freshly re-executed transaction output matches the authenticated `TransactionInfo` stored on-chain, only checks status, gas, write-set hash, and event root hash. It explicitly skips comparing `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` — a gap the code itself documents with a `TODO(trading-native)` comment.

### Finding Description
`ensure_match_transaction_info` at [1](#0-0)  validates a re-executed `TransactionOutput` against the ledger's committed `TransactionInfo` by comparing:
- execution status,
- gas used,
- write-set hash vs. `state_change_hash`,
- event root hash.

It never compares the state-checkpoint related hashes, and the code says so directly:

```
// TODO(trading-native): this comparator ignores the checkpoint hashes
// (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
// tooling (e.g. db-tool's `replay_on_archive`) can report a successful
// replay even when the authenticated position state root diverges from
// local execution. Validate the checkpoint hashes here before enabling
// COMPUTE_TRADING_NATIVE_STATE_ROOTS.
``` [2](#0-1) 

This is invoked at the end of a chunked replay in `storage/db-tool/src/replay_on_archive.rs`, where each re-executed output is checked against the `expected_txn_info` pulled from the backup/archive: [3](#0-2) . If this call returns `Ok(())`, the transaction is treated as verified and no error is reported, even though `TransactionInfo` carries a `state_checkpoint_hash` (and, in the V1 variant, `hot_state_checkpoint_hash`/`position_state_checkpoint_hash`) that is part of the authenticated, accumulator-committed record: [4](#0-3) .

The gated feature `COMPUTE_TRADING_NATIVE_STATE_ROOTS` (referenced across `storage/aptosdb/src/db/aptosdb_writer.rs`, `storage/aptosdb/src/db/aptosdb_reader.rs`, `execution/executor-types/src/execution_output.rs`, `execution/executor/src/workflow/do_state_checkpoint.rs`, `storage/aptosdb/src/trading_native.rs`, etc.) governs whether the position/trading-native state root is actually computed and committed. Because `ensure_match_transaction_info` does not gate on or validate these hashes, any bug in that computation path (e.g., in `storage/aptosdb/src/db/aptosdb_native_position.rs` or `execution/executor/src/workflow/do_state_checkpoint.rs`) that causes the locally-derived position/state-checkpoint root to diverge from the value bound into the committed `TransactionInfo` would go completely undetected by replay-verify, db-tool, and debugger sanity checks.

### Impact Explanation
Replay-verify (`replay_on_archive`) and related debugger/CLI tools exist specifically to catch divergence between authenticated ledger data and independently re-executed results — this is the exact "hard-fork-only divergence during commit, replay, restore, or proof verification" category called out as in-scope. With this gap, a corruption or non-determinism affecting only the position/state-checkpoint hash (but not the write-set hash, event root, or status/gas) would silently pass all replay verification. Since `TransactionInfo` (including these checkpoint hashes) is the leaf hashed into the transaction accumulator and the object proven by `TransactionInfoWithProof`/`TransactionAccumulatorProof`, an undetected local divergence here means the operational safety net for catching consensus/state-commitment bugs before or during the trading-native rollout is broken exactly where it matters — for the very state root (`position_state_checkpoint_hash`) whose computation is newest and most likely to have edge-case bugs.

### Likelihood Explanation
This is not a hypothetical: the code comment demonstrates the authors are aware the checkpoint hashes are unauthenticated by this comparator and explicitly flag it as a prerequisite before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`. If that feature is turned on before this TODO is addressed (or if any caller relies on `ensure_match_transaction_info` as the sole correctness check, as `replay_on_archive` currently does), any divergence in position/hot-state checkpoint computation is guaranteed to be missed by this tool, matching the "hardcoded tolerance that ignores a growing set of contributing values" pattern from the seed bug report (rebalance delta check ignoring newly added holdings) — here, the verifier's fixed set of checked fields ignores newly added checkpoint-hash fields as the ledger format grows (V0 → V1 `TransactionInfo`).

### Recommendation
Extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present in the `TransactionInfo` variant and known/derivable at replay time) against the locally recomputed values before this comparator is relied upon as a correctness gate, and before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled on mainnet, exactly as the existing TODO comment specifies.

### Proof of Concept
1. Enable (or simulate a future state where) `COMPUTE_TRADING_NATIVE_STATE_ROOTS` causes `position_state_checkpoint_hash` to be computed and included in committed `TransactionInfoV1` for a range of transactions.
2. Introduce (or trigger via an existing latent bug in `aptosdb_native_position.rs` / `do_state_checkpoint.rs`) a divergence so that local re-execution computes a different `position_state_checkpoint_hash` than the one persisted in the archived `TransactionInfo`, while write-set, events, gas, and status remain identical.
3. Run `storage/db-tool/src/replay_on_archive.rs`'s `Verifier::verify` over that version range.
4. Observe that `execute_and_verify` calls `ensure_match_transaction_info` [5](#0-4)  and, because that function never inspects `position_state_checkpoint_hash`, returns `Ok(())`/no error, causing the tool to report a clean replay despite the authenticated state root differing from local execution — the precise scenario the code's own TODO comment predicts.

**Uncertainty**: I could not fully verify (within available tool budget) whether `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is currently enabled on mainnet or still gated off, nor trace the exact computation in `storage/aptosdb/src/db/aptosdb_native_position.rs` for a concrete divergence-triggering bug. The finding is therefore an authenticated-response/proof-integrity gap that is self-acknowledged in code but whose real-world triggerability depends on the current rollout status of the trading-native feature, which I was unable to confirm with certainty.

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

**File:** types/src/transaction/mod.rs (L2261-2284)
```rust
    #[builder(finish_fn = build)]
    pub fn builder_v1(
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
        Self::V1(TransactionInfoV1::new(
            transaction_hash,
            state_change_hash,
            event_root_hash,
            state_checkpoint_hash,
            hot_state_checkpoint_hash,
            gas_used,
            status,
            auxiliary_info_hash,
            position_state_checkpoint_hash,
        ))
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
