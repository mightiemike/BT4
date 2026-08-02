### Title
`ensure_match_transaction_info` skips checkpoint-hash validation, letting replay-verify tooling accept a divergent state root - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` is the authenticated-integrity check used to confirm that a locally re-executed `TransactionOutput` matches an already-committed, trusted `TransactionInfo` (the object stored in the transaction accumulator and covered by ledger-info signatures). It checks status, gas, write-set hash (`state_change_hash`), and event root hash, but by its own admission — via an explicit `TODO(trading-native)` comment — it never checks `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`.

### Finding Description
`TransactionInfoV1`/`TransactionInfo` carries multiple root-hash fields that bind a transaction to specific state roots: `state_change_hash` (write set), `event_root_hash`, `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` [1](#0-0) . The verification routine `ensure_match_transaction_info` is documented and used to prove that a `TransactionOutput` produced by re-execution is consistent with the trusted, accumulator-committed `TransactionInfo`. It validates `status`, `gas_used`, `write_set` hash against `state_change_hash`, and the event root, but stops there, with a comment explicitly acknowledging the gap [2](#0-1) .

This function is the sole state-integrity gate used by:
- `storage/db-tool/src/replay_on_archive.rs`'s `Verifier::execute_and_verify`, which re-executes archived transactions and calls `ensure_match_transaction_info` to decide pass/fail of replay-verify [3](#0-2) .
- `aptos-move/aptos-debugger/src/aptos_debugger.rs` and `aptos-move/cli/src/commands.rs` (same call site pattern, not shown in full but confirmed present).

Because the checkpoint-hash fields are excluded from comparison, a divergence between the locally re-computed state/hot-state/position state root and the authenticated root embedded in the trusted `TransactionInfo` will not be detected by these tools — they will report the replay/verification as successful ("Ok(())" is returned unconditionally after the incomplete checks).

### Impact Explanation
This breaks the "Proof And Storage Pivot" invariant that *"Storage schemas, replay paths, and restore helpers must not reinterpret committed data into a different ledger state"* and that state roots must remain provably bound to their authenticated values. Concretely:
- `replay_on_archive` is one of the primary hard-fork/consensus-divergence detection tools used to validate that a candidate binary/VM change reproduces the exact same state root as the historical, signed ledger. With this gap, a change that silently corrupts `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` computation (e.g., a VM/state-summary bug affecting the new "trading-native" position-state feature gated by `COMPUTE_TRADING_NATIVE_STATE_ROOTS`) would pass replay-verify even though the actual ledger state diverges from the authenticated state.
- This directly matches the "Hard-fork-only divergence during commit, replay, restore, or proof verification" and "Authenticated API or state-view output bound to the wrong version, object, or proof context" categories in the state-integrity gate, since the tool is meant to authenticate that re-executed state matches the committed/proof-bound `TransactionInfo`.

### Likelihood Explanation
This is not a hypothetical extrapolation from the EIP-712 report — it is an explicit, admitted TODO in the local codebase describing exactly this gap, gated behind the not-yet-fully-enabled `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature. The comment itself states the consequence ("replay-verify tooling ... can report a successful replay even when the authenticated position state root diverges from local execution"), so the root cause and exact corrupted/uninspected value (`state_checkpoint_hash` / `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`) are confirmed in code, not speculative. The trigger requires only a bug (VM/state-summary logic error, or a malicious/compromised archive/backup source feeding a mismatched but internally-consistent write-set/event set) surfacing in one of the un-checked fields, which the existing checks (write-set hash, event hash) would not catch. It is important to note this validation path is only used by offline replay/debugging tools (`replay_on_archive`, `aptos-debugger`, `aptos` CLI), not the live consensus commit path (`do_state_checkpoint.rs` computes and persists checkpoint hashes independently via `state_summary` during normal execution) — so this weakens *detection* of divergence rather than directly corrupting live consensus state.

### Recommendation
Extend `ensure_match_transaction_info` to compare the locally computed `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when available/expected) against the corresponding fields on `txn_info`, returning an error on mismatch, consistent with how `write_set_hash` and `event_root_hash` are already validated. This should be completed and enabled prior to turning on `COMPUTE_TRADING_NATIVE_STATE_ROOTS` in any environment where replay-verify integrity is relied upon.

### Proof of Concept
Not applicable as a runnable exploit — the flaw is demonstrated directly by the code and its own TODO comment: `ensure_match_transaction_info` returns `Ok(())` unconditionally after checking only `status`, `gas_used`, `write_set_hash`, and `event_root_hash` [4](#0-3) , while `TransactionInfoV1` defines additional checkpoint-hash fields that are never read/compared in this function [1](#0-0) . Any test that constructs an `expected_txn_info` with a correct `state_change_hash`/`event_root_hash` but a deliberately wrong `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` will show `ensure_match_transaction_info` returning `Ok(())`, confirming replay-verify tooling (`storage/db-tool/src/replay_on_archive.rs`, lines 388-406) would accept the divergent state as valid.

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
