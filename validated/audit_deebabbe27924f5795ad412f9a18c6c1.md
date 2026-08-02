## Title
Replay-verify accepts transactions whose committed state/hot-state/position-state checkpoint roots diverge from local re-execution - ([File: types/src/transaction/mod.rs])

## Summary
`TransactionOutput::ensure_match_transaction_info` is the single-transaction integrity check used by chunk-replay and archive-replay tooling to confirm that a locally re-executed transaction matches the `TransactionInfo` recorded in the (authenticated, accumulator-committed) ledger. It validates status, gas used, write-set hash, and event-root hash, but by its own documented admission does **not** validate `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`.

## Finding Description
`ensure_match_transaction_info` in `types/src/transaction/mod.rs` performs the following checks between a freshly-computed `TransactionOutput` and the `TransactionInfo` retrieved from (or proven against) the accumulator: [1](#0-0) 

It checks `status`, `gas_used`, and `write_set_hash` against `txn_info.state_change_hash()`, and the event root hash against `txn_info.event_root_hash()`. It never checks `txn_info.state_checkpoint_hash()`, `hot_state_checkpoint_hash()`, or `position_state_checkpoint_hash()`. This is explicitly flagged in the function's own trailing comment: [2](#0-1) 

`TransactionInfo` (both V0 and V1) is the object accumulated into the transaction accumulator and is the thing whose hash is bound into ledger-info signatures; `state_checkpoint_hash` is the Jellyfish Merkle root (and, in V1, the hot-state and position-state roots) computed at the state-checkpoint boundary of a block. This is exactly the commitment the executor computes in `do_state_checkpoint.rs` / `assemble_transaction_infos` and stores into `TransactionInfo`: [3](#0-2) 

This function is used by replay/verification tools (`storage/db-tool/src/replay_on_archive.rs`, `aptos-move/aptos-debugger/src/aptos_debugger.rs`, `aptos-move/cli/src/commands.rs`, `execution/executor/src/chunk_executor/mod.rs`) as the correctness oracle when replaying committed transactions from a backup or archive against a locally executed VM to confirm the archived output is faithful to the real ledger. Because the state/hot-state/position-state checkpoint hash comparisons are skipped, a state root that was mis-computed (or corrupted) somewhere in the executor→storage pipeline (e.g. a bug in `do_state_checkpoint.rs`, in `commit_transaction_accumulator`, or in `assemble_transaction_infos`) — while write set, gas, status, and events happen to still hash-match — would **not be detected** by replay-verify. The same code path is what `db-tool replay_verify`/`replay_on_archive` and `aptos-debugger` rely on as their state-integrity gate.

## Impact Explanation
This weakens the "detect committed-state divergence" property of the archive/replay-verification tooling: it is possible for the on-chain `state_checkpoint_hash` (and hot/position state roots) recorded in `TransactionInfo` — the value bound into the accumulator and ultimately into signed `LedgerInfo` — to differ from what local execution of the same transaction/write-set produces, and `ensure_match_transaction_info` will still report success as long as write-set bytes, gas, status and events match. Since `write_set_hash` only covers the transaction's own write set and not the resulting global Merkle root, a divergence introduced anywhere in state-tree construction, checkpoint-hash aggregation, hot-state root computation, or the newer position-state-root path (`COMPUTE_TRADING_NATIVE_STATE_ROOTS`) is invisible to this gate. That directly matches the "Committed state that differs from the correct VM result" and "Wrong ... state proof accepted as valid" state-integrity categories: the authenticated proof-bearing artifact (`TransactionInfo.state_checkpoint_hash`) is not actually checked by the tool whose job is to validate it.

## Likelihood Explanation
The bug requires that a real divergence between the recorded state-checkpoint root and locally re-executed state exist (e.g. from a storage/commit bug, an under-tested feature interaction, or corrupted archive data) for this gap to matter; it does not itself corrupt consensus-committed state on a live validator, since normal block execution/commit paths have their own `ensure!` checks (e.g. `check_and_put_ledger_info`'s accumulator-root comparison) that are unaffected by this function. The exposure is specifically in the replay/backup/debugging verification tooling, which is exactly the safety net meant to catch such divergences after the fact — the authors' own comment confirms this is a known, currently-unaddressed gap ("Validate the checkpoint hashes here before enabling COMPUTE_TRADING_NATIVE_STATE_ROOTS").

## Recommendation
Extend `ensure_match_transaction_info` to also compare a locally-computed `state_checkpoint_hash` (and, when applicable, `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`) against the corresponding fields of `txn_info`, at least whenever the caller has the checkpoint version's computed root available (i.e., at true checkpoint boundaries). Until that is done, replay-verify/replay-on-archive tooling should not be treated as validating full ledger-state integrity, and `COMPUTE_TRADING_NATIVE_STATE_ROOTS` should not be enabled without closing this gap first, per the existing TODO.

## Proof of Concept
Not independently exploitable as a live-consensus attack (no `ensure!`/panic bypass on hot commit paths was found); this is a verification-tool gap, demonstrable by inspection:
1. Construct (or corrupt) a `TransactionInfo` whose `state_checkpoint_hash` differs from the true JMT root after applying the transaction's write set (write set bytes, gas, status, and events left unchanged so their hashes still match).
2. Call `TransactionOutput::ensure_match_transaction_info(version, &corrupted_txn_info, ...)` — per [4](#0-3)  it returns `Ok(())` because only `status`, `gas_used`, `write_set_hash`, and `event_root_hash` are compared.
3. Any tool relying on this function (`replay_on_archive`, `aptos-debugger`, chunk executor's txn-info matching) would report the replay/backup as consistent even though the ledger's committed state root is provably wrong.

### Citations

**File:** types/src/transaction/mod.rs (L2139-2203)
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
```

**File:** execution/executor/src/workflow/do_ledger_update.rs (L95-121)
```rust
                let txn_info = if transaction_info_v1 {
                    TransactionInfo::builder_v1()
                        .transaction_hash(txn.committed_hash())
                        .state_change_hash(write_set_hash)
                        .event_root_hash(event_root_hash)
                        .maybe_state_checkpoint_hash(state_checkpoint_hash)
                        .maybe_hot_state_checkpoint_hash(
                            hot_state_checkpoint_hashes.and_then(|hot| hot[i]),
                        )
                        .gas_used(txn_output.gas_used())
                        .status(status)
                        .maybe_auxiliary_info_hash(auxiliary_info_hash)
                        .maybe_position_state_checkpoint_hash(
                            position_state_checkpoint_hashes.and_then(|p| p[i]),
                        )
                        .build()
                } else {
                    TransactionInfo::builder_v0()
                        .transaction_hash(txn.committed_hash())
                        .state_change_hash(write_set_hash)
                        .event_root_hash(event_root_hash)
                        .maybe_state_checkpoint_hash(state_checkpoint_hash)
                        .gas_used(txn_output.gas_used())
                        .status(status)
                        .maybe_auxiliary_info_hash(auxiliary_info_hash)
                        .build()
                };
```
