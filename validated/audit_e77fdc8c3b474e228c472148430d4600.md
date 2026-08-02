### Title
`ensure_match_transaction_info` skips checkpoint-hash validation, allowing replay tooling to accept a corrupted state/hot-state/position-state root as matching - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function used by replay/debug/verification tooling to confirm that a locally re-executed transaction output matches the authenticated `TransactionInfo` fetched from the ledger. It checks status, gas used, write-set hash (`state_change_hash`), and event root hash, but never compares `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`. This is explicitly acknowledged in a TODO comment in the same function, and it means replay verification can report success even when the recomputed state (or hot-state, or native-position) root diverges from the value authenticated by consensus.

### Finding Description
`ensure_match_transaction_info` in [1](#0-0)  is meant to be the integrity check binding a re-executed `TransactionOutput` to the authenticated `TransactionInfo` (which is the leaf hashed into the transaction accumulator and thus root-hash-committed). It validates:
- execution status
- gas used
- `write_set_hash == txn_info.state_change_hash()`
- `event_root_hash == txn_info.event_root_hash()`

But it does not check `txn_info.state_checkpoint_hash()`, `txn_info.hot_state_checkpoint_hash()` (available on `TransactionInfoV1`), or `txn_info.position_state_checkpoint_hash()` at all. The comment right before `Ok(())` states this directly: [2](#0-1)  — acknowledging that "replay-verify tooling ... can report a successful replay even when the authenticated position state root diverges from local execution," and that this must be fixed "before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`."

This function is called directly by the Aptos CLI's transaction-replay path in two places, with no state-checkpoint-hash cross-check performed anywhere else in that flow: [3](#0-2) [4](#0-3) . The critical piece missing is any comparison of the locally computed Jellyfish Merkle state root (or hot-state / native-position root) against the `TransactionInfo`'s stored checkpoint hash(es), which is exactly the field that binds the ledger's global state root to the accumulator-proven `TransactionInfo`.

By contrast, the block-executor's own internal state-checkpoint pipeline (`DoStateCheckpoint`) does perform this comparison correctly when `known_state_checkpoints`/`known_hot_state_checkpoints`/`known_position_state_checkpoints` are supplied, e.g. [5](#0-4) , and the chunk-replay verifier `ensure_transaction_infos_match` compares full `TransactionInfo` hashes (which do include checkpoint hashes) rather than field-by-field. So the state-commit path used by normal node execution/replay (`ReplayChunkVerifier`/`StateSyncChunkVerifier` in [6](#0-5) ) is not affected — the gap is isolated to `ensure_match_transaction_info`, the field-level comparator used by the debugger/CLI replay path.

### Impact Explanation
This does not by itself corrupt mainnet committed state — the normal commit/consensus path (accumulator root verification, full `TransactionInfo` hash matching in chunk executor and storage verification paths) is unaffected, since those compare the whole `TransactionInfo` hash or explicit known-checkpoint values, not this field-level helper. The concrete, provable impact is limited to the CLI/debugger replay-verification tool: an operator or auditor using `aptos move replay`/simulate-and-compare tooling (`aptos-move/cli/src/commands.rs`) can be told a transaction replayed successfully and matches on-chain results, while the state root, hot-state root, or (once `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled) the native-position state root silently diverges. Given the code explicitly warns this must be fixed "before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`" — a feature not yet enabled based on default-off flags in `types/src/on_chain_config/aptos_features.rs` — the currently realized impact is a false-negative in an auditing/replay tool (reduced likelihood of detecting a genuine state-root divergence introduced elsewhere), rather than a live, exploitable path to corrupt mainnet committed data today.

### Likelihood Explanation
High likelihood of the code behaving exactly as described (it's a straightforward, deterministic omission, and self-documented by the TODO), but the reachable, mainnet-relevant severity is bounded because: (1) this is not on the consensus/commit-critical path — it's only invoked from CLI debug/replay tooling; (2) the missing checks matter most once `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/`HOT_STATE_ROOT_IN_TXN_INFO` are enabled on-chain, which the code indicates has not happened yet (`Aptos Features` are off by default and the comment says "before enabling"). This keeps it below "Critical/High mainnet impact today," though it is a real, locally-provable gap that should be fixed before those features ship, since divergence would otherwise pass replay audits undetected.

### Recommendation
In `ensure_match_transaction_info`, add comparisons for `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` against locally-recomputed roots (when the replay path has them available), mirroring the checks already done in `DoStateCheckpoint::get_state_checkpoint_hashes`. At minimum, ensure the function refuses to silently pass when a `TransactionInfoV1` carries a non-`None` checkpoint hash that the caller cannot independently verify, and gate this fix as a hard prerequisite before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/`HOT_STATE_ROOT_IN_TXN_INFO` as the code comment already recommends.

### Proof of Concept
Not applicable in the strict "exploit against mainnet state" sense — this is a code-review-level finding grounded in the acknowledged TODO. Conceptual repro: run `aptos move replay --txn-id <v>` (or the CLI's debugger-replay command in `aptos-move/cli/src/commands.rs`) against a version whose `TransactionInfoV1.state_checkpoint_hash` (or hot/position checkpoint hash) does not match what local re-execution computes, while `state_change_hash`/`event_root_hash`/gas/status all agree — `ensure_match_transaction_info` will return `Ok(())` and the CLI will report a successful, matching replay despite the state-root divergence.

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

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L206-220)
```rust
        if let Some(known) = known_state_checkpoints {
            ensure!(
                known.len() == num_txns,
                "Bad number of known {label} hashes. {} vs {}",
                known.len(),
                num_txns,
            );
            if let Some(idx) = last_checkpoint_index {
                ensure!(
                    known[idx] == Some(computed_last_checkpoint_hash),
                    "{label} root hash mismatch with known hashes passed in. {:?} vs {:?}",
                    known[idx],
                    Some(computed_last_checkpoint_hash),
                );
            }
```

**File:** execution/executor/src/chunk_executor/chunk_result_verifier.rs (L129-141)
```rust
pub struct ReplayChunkVerifier {
    pub transaction_infos: Vec<TransactionInfo>,
}

impl ChunkResultVerifier for ReplayChunkVerifier {
    fn verify_chunk_result(
        &self,
        _parent_accumulator: &InMemoryTransactionAccumulator,
        ledger_update_output: &LedgerUpdateOutput,
    ) -> Result<()> {
        ledger_update_output.ensure_transaction_infos_match(&self.transaction_infos)
    }

```
