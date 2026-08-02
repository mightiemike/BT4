### Title
`TransactionOutput::ensure_match_transaction_info` never checks the state/hot-state/position checkpoint hashes, letting replay-verify accept a diverged ledger state — ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` at [1](#0-0)  is the single correctness gate used by replay/verification tooling to decide whether a locally-executed `TransactionOutput` matches the authenticated `TransactionInfo` recorded on-chain. It only checks status, gas used, the write-set hash (`state_change_hash`) and the event root hash. It explicitly skips comparing `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` — fields that are part of `TransactionInfo` and are exactly what gets committed into the transaction accumulator / signed by validators as the authoritative state root for a version [2](#0-1) .

### Finding Description
`TransactionInfo` (V0/V1) carries multiple checkpoint-hash fields that authenticate different parts of ledger state: `state_checkpoint_hash` (regular JMT state root), and in `TransactionInfoV1`, `hot_state_checkpoint_hash` and `position_state_checkpoint_hash` (for the "trading-native" state) [3](#0-2) . These hashes are part of the accumulator leaf's authenticated content — any consumer that wants to assert "my locally executed output equals the canonical, validator-signed result" must check them.

`ensure_match_transaction_info` is the function that is supposed to do exactly that comparison, but it validates only:
- `status()` 
- `gas_used()`
- `write_set` hash vs `txn_info.state_change_hash()`
- event root hash vs `txn_info.event_root_hash()`

and then returns `Ok(())` without ever touching `state_checkpoint_hash()`, `hot_state_checkpoint_hash()` or `position_state_checkpoint_hash()`. The code even contains a self-documenting TODO acknowledging the gap: "this comparator ignores the checkpoint hashes ... so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution. Validate the checkpoint hashes here before enabling COMPUTE_TRADING_NATIVE_STATE_ROOTS." [4](#0-3) 

This function is not test-only: it is the load-bearing verification primitive wired into:
- `storage/db-tool/src/replay_on_archive.rs`'s `execute_and_verify`, which is the standard/CI tool ("replay-verify") used to detect divergence between historical archived transaction results and freshly re-executed VM output [5](#0-4) .
- `execution/executor/src/chunk_executor/mod.rs`, `aptos-move/aptos-debugger/src/aptos_debugger.rs`, and `aptos-move/cli/src/commands.rs`.

Because the state-checkpoint hashes are silently excluded, a divergence that only manifests in the state root (e.g. a bug in state-checkpoint computation, hot-state/position-native-state materialization, or JMT construction that leaves the write-set/events/gas/status unchanged but corrupts the resulting Merkle root) is invisible to every one of these call sites.

### Impact Explanation
This breaks the state-commitment integrity gate described in the report's scope: "Committed state that differs from the correct VM result... accepted as valid" and "Authenticated API or state-view output bound to the wrong version, object, or proof context." Any local bug that corrupts the accumulated state root — rather than the direct per-transaction write set — would pass `replay_on_archive`/replay-verify checks used to gate mainnet upgrades and audit historical execution correctness. This directly undermines the safety net whose entire purpose is to catch hard-fork-causing state divergence during commit/replay/verification, and its blind spot is explicitly acknowledged in-code for the newer hot-state/position-state ("trading-native") roots that are being rolled out.

### Likelihood Explanation
The gap is deterministic and unconditional — it does not depend on adversarial input, timing, or a race; it is a structural omission in the verification function that runs on every replay-verify invocation, every time `ensure_match_transaction_info` is called with `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/hot-state features involved. The comment in the code confirms the maintainers are aware but have not yet closed the gap ("Validate the checkpoint hashes here before enabling..."), indicating the feature that depends on this check (`compute_trading_native_state_roots`) is in-progress and this validation is a known prerequisite that is currently missing.

### Recommendation
Extend `ensure_match_transaction_info` to also validate `state_checkpoint_hash()`, `hot_state_checkpoint_hash()`, and `position_state_checkpoint_hash()` (when present in `TransactionInfo::V1` / the corresponding execution output) against the locally computed roots before enabling any feature (e.g., `COMPUTE_TRADING_NATIVE_STATE_ROOTS`) that depends on those roots for correctness guarantees, and audit call sites (`replay_on_archive.rs`, `chunk_executor/mod.rs`, `aptos-debugger`, `cli/src/commands.rs`) to ensure the fuller check is exercised wherever ledger-state authenticity is being asserted.

### Proof of Concept
Not independently exploitable as a state-corruption PoC from the info available — the finding is a *missing verification* rather than a wrong computed value, and its severity is contingent on a second, currently-unidentified bug in the local state-checkpoint computation path. I could not locate (within the available index) an actual reachable bug that produces a diverging state-checkpoint hash while keeping the write-set/events/status/gas identical; confirming a concrete, mainnet-triggerable corruption would require deeper analysis of `execution/executor/src/workflow/do_state_checkpoint.rs` and the hot-state/position-state root computation code, which is only partially indexed here. I flag this explicitly as an open gap: due to index size limits, `execution/executor/src/chunk_executor/mod.rs` contents were not retrievable, and full review of `do_state_checkpoint.rs` would benefit from a full Devin session with complete repository access.

### Citations

**File:** types/src/transaction/mod.rs (L2139-2157)
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
```

**File:** types/src/transaction/mod.rs (L2196-2204)
```rust

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

**File:** storage/db-tool/src/replay_on_archive.rs (L392-405)
```rust
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
```
