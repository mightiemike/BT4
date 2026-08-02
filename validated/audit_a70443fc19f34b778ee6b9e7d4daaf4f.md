## Finding

### Title
Replay-verification comparator silently skips checkpoint-hash fields, masking state-root divergence bugs - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function used by `db-tool`'s `replay_on_archive` (and other debugging tools) to confirm that a freshly re-executed transaction's output matches the authenticated `TransactionInfo` recorded on-chain. The function checks status, gas, write-set hash, and event root hash, but explicitly does **not** validate `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — the very fields that commit to the resulting state root. This is a real analog to the HydraDX bug class: a state-changing/commit-relevant signal (here, the state root fields of `TransactionInfo`) is silently dropped from the verification path that is supposed to consume it, so a downstream consumer (the replay-verify tool) operates on an incomplete/stale view of correctness.

### Finding Description
`TransactionInfo` carries multiple checkpoint-hash fields that bind a transaction to its resulting state: [1](#0-0) 

`ensure_match_transaction_info` is meant to be the authoritative match check between a locally re-executed `TransactionOutput` and the archived, ledger-info-authenticated `TransactionInfo`. It verifies status, gas, write-set hash, and event root hash, but the code contains a self-documented admission that it does **not** verify the checkpoint hashes: [2](#0-1) 

The comment is explicit: *"this comparator ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`), so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution."*

This function is the sole correctness gate in `storage/db-tool/src/replay_on_archive.rs`, which re-executes historical transactions and is designed specifically to catch state-computation divergence bugs (e.g., ahead of enabling features like `compute_trading_native_state_roots`): [3](#0-2) 

It is also used by `aptos-move/aptos-debugger/src/aptos_debugger.rs` and `aptos-move/cli/src/commands.rs` for similar verification purposes.

### Impact Explanation
`replay_on_archive` and the debugger tools exist to detect exactly the class of bug where local re-execution produces a state root that diverges from what was actually committed/authenticated on-chain — i.e., non-determinism or state-computation bugs that could cause a hard fork or silent state corruption across nodes. Because the comparator never checks `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`, a bug that corrupts state-root computation (while leaving write-set hash, events, gas, and status unchanged — plausible since these hashes are computed from separate state-checkpointing/Merkleization logic, e.g. hot-state or "trading native" position-state paths) would pass replay-verify as a false positive. This directly undermines the "Proof And Storage Pivots" invariant that authenticated proof-bearing responses (`TransactionInfo`) must be fully validated during replay, and could delay detection of a genuine consensus-breaking state divergence until it manifests on mainnet.

### Likelihood Explanation
This is not a hypothetical: the gap is explicitly and voluntarily documented in the code as a known incompleteness ("Validate the checkpoint hashes here before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`"), meaning the feature that stresses this exact path (native/position state root) is being staged while its verification tooling is known-incomplete. Any bug in the hot-state / position-state checkpoint computation introduced before this TODO is addressed would go undetected by `replay_on_archive`, the primary tool relied upon for pre-hardfork/replay auditing.

### Recommendation
Extend `ensure_match_transaction_info` to compare `state_checkpoint_hash()`, `hot_state_checkpoint_hash()` (if present in `TransactionInfoV1`), and `position_state_checkpoint_hash()` against values computed from the locally re-executed state, failing the check (as the write-set/event checks already do) on mismatch. This should be done before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled, as the code comment itself recommends.

### Proof of Concept
Not applicable as an exploit against a live network — this is a tooling/verification-completeness gap rather than an attacker-triggerable state corruption. The proof of the gap is the code itself:
1. `ensure_match_transaction_info` checks `status`, `gas_used`, `write_set` hash, and `event_root_hash` only — no checkpoint-hash fields are read or compared: [4](#0-3) 
2. `replay_on_archive::execute_and_verify` treats an `Ok(())` return from this function as proof of correct replay, with no supplementary state-root check: [3](#0-2) 

Note on scope/uncertainty: I could not fully confirm within the available tool budget whether the state-sync/chunk-executor's `ReplayChunkVerifier::verify_chunk_result` (via `ledger_update_output.ensure_transaction_infos_match`) independently performs a full-field comparison that would catch this divergence in the live syncing path — that function is distinct from `ensure_match_transaction_info` and I was not able to inspect its body before running out of iterations. If it does perform a complete comparison, the practical exposure of this finding is limited to the offline `db-tool`/debugger auditing paths rather than live consensus/state-sync commit paths.

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
