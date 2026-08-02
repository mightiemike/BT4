## Title
Replay-verification (`ensure_match_transaction_info`) never validates `state_checkpoint_hash`, allowing a corrupted or diverged state root to pass as a verified replay — ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` is the sole per-transaction integrity check used by chunk replay-verification (`execution/executor/src/chunk_executor/mod.rs::verify_execution`), the Move/`aptos-debugger` CLI replay tooling (`aptos-move/cli/src/commands.rs`), and `db-tool`'s `replay_on_archive`. It checks status, gas, write-set hash, and event root hash against the authenticated `TransactionInfo`, but it **never checks `state_checkpoint_hash` (nor `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`)** against the locally-recomputed state root. This is explicitly acknowledged in a TODO comment in the function itself.

### Finding Description
`ensure_match_transaction_info` at [1](#0-0)  performs four checks — status, gas, write-set hash, event root hash — then returns `Ok(())`. The trailing comment explicitly documents the gap: [2](#0-1) 

This function is the authoritative comparator used to validate that a locally re-executed transaction matches the authenticated on-chain `TransactionInfo` during:
- Chunk executor replay-verification of archived/backup transactions: [3](#0-2) 
- CLI/debugger replay of individual transactions: [4](#0-3)  and [5](#0-4) 

Because `state_checkpoint_hash` (the field that binds a `TransactionInfo` to the state-tree root produced after applying the write set) is never compared, a replay tool can recompute a state root that diverges from what was actually committed to the ledger accumulator — due to a storage bug, a non-deterministic VM state application, or a corrupted `state_checkpoint_hashes` propagation in `DoStateCheckpoint`/`DoLedgerUpdate` — and `ensure_match_transaction_info` will still return `Ok(())` as long as write-set bytes, events, gas, and status match. Note that the write-set hash check only proves the *raw write ops* match; it says nothing about the resulting Jellyfish Merkle root, which is what `state_checkpoint_hash` is supposed to attest to.

### Impact Explanation
This breaks the "authenticated response/proof stays bound to the correct root" invariant for the replay/verification tooling explicitly called out in the task's Proof and Storage Pivots. `replay-verify` (used to validate `db-tool`'s `replay_on_archive`, and `TransactionReplayer::verify_execution` during chunk-executor-based state-sync/backup verification) is the mechanism operators and the Aptos Labs backup-verification pipeline rely on to detect a divergence between locally-computed state and the historically-committed, consensus-authenticated ledger state. If the local state-tree computation diverges from the authenticated `state_checkpoint_hash` (e.g., due to a bug in state-checkpoint hash propagation, hot-state/position-state hashing, or a storage/replay bug reinterpreting committed data), this specific check would silently pass, masking a real state-root divergence. This matches the required impact class: "Wrong accumulator root ... or state proof accepted as valid" / "Hard-fork-only divergence during ... replay ... accepted as valid" — the verification tool's job is precisely to catch such divergence, and it is blind to state-root mismatches by construction.

### Likelihood Explanation
This is a **detection gap**, not an active exploit path by itself — a corrupt state root does not currently get produced by an unprivileged attacker input on its own; this weakness only matters *in combination* with some other integrity bug in state-checkpoint hash computation, hot-state checkpoint hashing (`HOT_STATE_ROOT_IN_TXN_INFO`), or `position_state_checkpoint_hash` (the trading-native feature referenced in the comment). The comment itself flags this as a known, currently-relevant gap that must be closed "before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`" — indicating the aptos-core maintainers are aware this needs fixing prior to shipping the associated feature, and that in the interim any state-checkpoint-hash-affecting bug is unverifiable via this path. Given that it's a self-acknowledged (not stealthy) gap gated behind an unshipped feature flag, likelihood of exploitation today is low, but the invariant break itself is real and directly relevant to the "authenticated response bound to right root" pivot.

### Recommendation
Extend `ensure_match_transaction_info` to also recompute and compare `state_checkpoint_hash` (and, when present, `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`) against the values on the authenticated `TransactionInfo`, mirroring how `write_set_hash` and `event_root_hash` are already validated. This should be done before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` (or any feature relying on `position_state_checkpoint_hash`) is enabled, per the existing TODO.

### Proof of Concept
1. In `execution/executor/src/chunk_executor/mod.rs::verify_execution`, replay a chunk whose locally-recomputed state tree (after `DoGetExecutionOutput`/state-checkpoint step) has a different Merkle root than the `state_checkpoint_hash` stored in the archived `TransactionInfo` for that version (simulate via a unit test that mutates `state_checkpoint_hash` on the fed-in `TransactionInfo` while keeping write set/events/gas/status identical).
2. Call `txn_out.ensure_match_transaction_info(version, txn_info, Some(write_set), Some(events))` — observe it returns `Ok(())` despite the mismatched `state_checkpoint_hash`, at [6](#0-5) .
3. This demonstrates that `replay-verify`/`replay_on_archive` tooling would report success even though the state-tree root diverges from the authenticated ledger state — confirmed directly by the maintainers' own TODO at [2](#0-1) .

**Caveat/uncertainty:** I could not fully trace whether `state_checkpoint_hash` is independently re-verified at a *different* layer (e.g., inside `DoStateCheckpoint` or state-sync's chunk verifier) before ledger commit for the live (non-replay-verify) commit path — the size limits on the code index prevented me from retrieving the complete body of `execution/executor/src/chunk_executor/mod.rs` and `execution/executor/src/workflow/do_state_checkpoint.rs`. If such an independent check exists on the normal commit path, this finding is scoped strictly to the **replay-verification/debugging tooling path** rather than mainnet consensus-driven commit itself. I recommend a Devin session with full file access to confirm whether any other code path re-validates `state_checkpoint_hash` before this gap is treated as production-critical beyond the replay-verify tooling.

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

**File:** execution/executor/src/chunk_executor/mod.rs (L684-706)
```rust
        // not `zip_eq`, deliberately
        for (version, txn_out, txn_info, write_set, events) in multizip((
            begin_version..end_version,
            &execution_output.to_commit.transaction_outputs,
            transaction_infos.iter(),
            write_sets.iter(),
            event_vecs.iter(),
        )) {
            if let Err(err) = txn_out.ensure_match_transaction_info(
                version,
                txn_info,
                Some(write_set),
                Some(events),
            ) {
                return if verify_execution_mode.is_lazy_quit() {
                    error!("(Not quitting right away.) {}", err);
                    verify_execution_mode.mark_seen_error();
                    Ok(version + 1)
                } else {
                    Err(err)
                };
            }
        }
```

**File:** aptos-move/cli/src/commands.rs (L2651-2655)
```rust
                if !self.skip_comparison {
                    txn_output
                        .ensure_match_transaction_info(self.txn_id, &txn_info, None, None)
                        .map_err(|msg| CliError::UnexpectedError(msg.to_string()))?;
                }
```

**File:** aptos-move/cli/src/commands.rs (L2809-2813)
```rust
        if !skip_comparison {
            txn_output
                .ensure_match_transaction_info(self.txn_id, &txn_info, None, None)
                .map_err(|msg| CliError::UnexpectedError(msg.to_string()))?;
        }
```
