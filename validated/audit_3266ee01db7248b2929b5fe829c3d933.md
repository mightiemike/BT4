### Title
`ensure_match_transaction_info` skips checkpoint-hash comparison, letting replay-verify accept a divergent state/position root - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the invariant used by chunk-executor's `verify_execution` (state-sync replay-verify path) and by CLI/debugger replay tooling to confirm that a locally re-executed transaction matches the authenticated `TransactionInfo` retrieved from a proof/backup. The function checks status, gas used, write-set hash (`state_change_hash`), and event-root hash, but a code comment explicitly documents that it does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`.

### Finding Description [1](#0-0) 

The function computes and compares:
- `status` vs `expected_txn_status`
- `gas_used`
- `write_set_hash` vs `txn_info.state_change_hash()`
- `event_root_hash` vs `txn_info.event_root_hash()`

But the trailing comment states:
```
// TODO(trading-native): this comparator ignores the checkpoint hashes
// (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
// tooling (e.g. db-tool's `replay_on_archive`) can report a successful
// replay even when the authenticated position state root diverges from
// local execution.
``` [2](#0-1) 

This function is invoked from `execution/executor/src/chunk_executor/mod.rs::verify_execution`, which is the code path used by `ReplayVerifyCoordinator` (backup/replay-verify tooling) to confirm that re-executing a chunk of backed-up transactions against a re-synced state reproduces the committed results: [3](#0-2) 

It is also called from `aptos-debugger` and the `aptos-move/cli` transaction-replay tooling. [4](#0-3) 

Because `state_checkpoint_hash` (the state-tree/Jellyfish-Merkle root) is never compared here, a replay run can report success even when the resulting state-Merkle-tree root or position-state root diverges from the on-chain committed root, as long as the write-set bytes and event bytes happen to hash correctly. Note that during actual block execution (the normal execution/commit pipeline, `do_state_checkpoint.rs::get_state_checkpoint_hashes`) the checkpoint hash *is* validated against `known_state_checkpoints`, so this gap is confined to the replay-verify/backup-verification and CLI replay flows, not the live consensus commit path.

### Impact Explanation
This breaks the "wrong accumulator root, Merkle proof… accepted as valid" and "authenticated API… bound to the wrong version/root" invariants for the specific tooling that exists to detect exactly that class of divergence (replay-verify against archived backups, and `db-tool`'s `replay_on_archive`). A state-root divergence introduced by a storage bug, replay bug, or corrupted backup chunk that still produces the same write-set/event bytes (e.g. differences confined to derived/position-state roots, or a bug in how the state-checkpoint hash is derived from an otherwise-correct write set) would go undetected by this verification tool, giving false confidence that historical/backup state is correct. Since this only affects the verification/audit tooling rather than the live state-machine commit itself, actual mainnet consensus state is not directly corrupted by this gap alone; the impact is limited to the integrity guarantee of replay-verification and archive-node self-audits, which is why the authors flagged it as a known TODO to fix before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`.

### Likelihood Explanation
This is not an attacker-triggerable exploit against consensus; it requires the checkpoint hash to genuinely diverge (e.g., a storage/replay bug or corrupted archive) for the gap to matter, and the authors already know about and documented it. Likelihood of the underlying divergence occurring in practice is low, and the code explicitly marks this as a pending TODO gate before enabling trading-native root computation, indicating it is an acknowledged, tracked limitation rather than a live exploitable defect today.

### Recommendation
Extend `ensure_match_transaction_info` to compare `self` write-set/committed roots against `txn_info.state_checkpoint_hash()`, `hot_state_checkpoint_hash()`, and `position_state_checkpoint_hash()` when those fields are present (e.g., threading through the computed state-checkpoint hash from `DoStateCheckpoint`), completing the TODO before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` or any position-state feature reaches mainnet.

### Proof of Concept
Not independently exploitable as a live-network attack; the gap is demonstrable by inspection: run `ReplayVerifyCoordinator`/`replay_on_archive` against a backup where the state-checkpoint root recorded in `TransactionInfo` differs from what local execution computes while write-set and event bytes are unchanged (e.g., by feeding an execution output whose `state_checkpoint_hash` field was corrupted independent of the write set) — `verify_execution` will still report success because `ensure_match_transaction_info` never inspects `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash`.

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

**File:** execution/executor/src/chunk_executor/mod.rs (L685-706)
```rust
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

**File:** aptos-move/cli/src/commands.rs (L2809-2813)
```rust
        if !skip_comparison {
            txn_output
                .ensure_match_transaction_info(self.txn_id, &txn_info, None, None)
                .map_err(|msg| CliError::UnexpectedError(msg.to_string()))?;
        }
```
