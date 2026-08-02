## Title
`ensure_match_transaction_info` replay-verification skips checkpoint-hash comparison, allowing divergent trading-native/hot-state roots to pass as "verified" - (File: types/src/transaction/mod.rs)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function that db-tool's `replay_on_archive` (and other replay/debugger paths) use to assert that a locally re-executed transaction output matches the `TransactionInfo` that was actually committed to the ledger accumulator. The function compares status, gas, write-set hash, and event-root hash against `txn_info`, but — by its own documented `TODO` — never compares `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`. This means replay-verify can report success even when the locally computed state/hot-state/position (trading-native) roots diverge from what is authenticated in the ledger.

### Finding Description
`TransactionOutput::ensure_match_transaction_info` at [1](#0-0)  checks four things against the on-chain `TransactionInfo`: execution status, gas used, write-set hash (`state_change_hash`), and event root hash. It explicitly does **not** validate:
- `state_checkpoint_hash` (main state Merkle root)
- `hot_state_checkpoint_hash`
- `position_state_checkpoint_hash` (native-trading state root, gated by the `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature)

The code contains a self-documenting acknowledgment of this gap: [2](#0-1) 

This matters because `TransactionInfoV1` was extended specifically to carry these checkpoint hashes so they are consensus-verified as part of the ledger accumulator — see the feature-flag documentation for `COMPUTE_TRADING_NATIVE_STATE_ROOTS` and `HOT_STATE_ROOT_IN_TXN_INFO`, which state the roots are "committed to `TransactionInfoV1`, so they are consensus-verified" [3](#0-2) . The corresponding execution-side logic (`DoStateCheckpoint::run` / `compute_position_checkpoint`) computes these roots deterministically from write-set replays and does cross-check them against "known" hashes supplied by an already-trusted transaction info during normal execution/replay pipelines [4](#0-3) . However, `ensure_match_transaction_info` is a *separate*, lower-level comparator used specifically by tooling such as `storage/db-tool/src/replay_on_archive.rs`, which calls it directly on VM-executed `TransactionOutput`s against `expected_txn_infos` fetched from backup/archive, without going through `DoStateCheckpoint`'s known-hash validation at all: [5](#0-4) .

Because `execute_and_verify` in `replay_on_archive.rs` relies solely on `ensure_match_transaction_info` to decide whether a chunk of replayed transactions matches the archived record, a state/hot-state/position root computed by the executor that differs from what's baked into `expected_txn_infos[idx]` will not be flagged as an error, while write-set-hash, status, gas, and event mismatches will be. This creates an authenticated-verification blind spot: the tool can print "ReplayVerify coordinator succeeded" / exit code 0 (see `storage/db-tool/src/replay_verify.rs`) even though the replayed state/hot-state/position checkpoint root diverges from the one committed and cryptographically bound into the accumulator via `TransactionInfoV1`.

### Impact Explanation
This breaks the "Committed state that differs from the correct VM result... accepted as valid" and "authenticated API or state-view output bound to the wrong version/root" invariants required by the gate. Specifically:
- Replay-verification (used for auditing archived/backed-up ledger history and detecting VM/storage nondeterminism or historical bugs) can silently pass over cases where a node's locally computed state root, hot-state root, or trading-native (position) state root diverges from the one actually committed on-chain, since none of these three checkpoint hashes are compared.
- Given the explicit warning in the code ("replay-verify tooling ... can report a successful replay even when the authenticated position state root diverges from local execution"), enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS` (or any future feature relying on this comparator for validation) would let a state-root divergence go undetected by this specific validation path, undermining confidence in replay-verify as an integrity check for hard forks or storage bugs in the native-trading subsystem.
- This does not directly forge a state proof accepted by consensus (the accumulator/root-hash checks elsewhere, e.g. `verify_extends_ledger`, `ensure_transaction_infos_match`, and `check_and_put_ledger_info`, remain intact for the actual consensus/state-sync commit paths), so the blast radius is scoped to this particular tooling comparator, not the core consensus commit pipeline. It is nonetheless a real, code-acknowledged integrity gap in a state-commitment verification utility.

### Likelihood Explanation
Currently low-to-moderate: `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is a new/permanent-lifetime feature flag [6](#0-5)  not yet broadly enabled (gated behind `TRANSACTION_INFO_V1` and `HOTNESS_IN_EPILOGUE`), so the practical exposure grows as that subsystem is rolled out. The gap is triggered automatically any time `replay_on_archive` (or any other caller of `ensure_match_transaction_info`, e.g. `aptos-debugger`, `aptos-move/cli`) is used to verify history for a range where these state roots differ — no attacker action or privilege is required, it's a latent correctness gap in the verification tool itself.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` against locally recomputed values (mirroring the "known hash" validation already done in `DoStateCheckpoint::get_state_checkpoint_hashes`), before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled on mainnet, exactly as the existing `TODO(trading-native)` comment recommends.

### Proof of Concept
Not independently reproducible as a mainnet exploit since it requires an already-enabled `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/`HOT_STATE_ROOT_IN_TXN_INFO` feature plus a real state divergence between the VM's local computation and the archived record; the code path itself is confirmed via static inspection:
1. `storage/db-tool/src/replay_on_archive.rs::execute_and_verify` re-executes archived transactions and calls `executed_outputs[idx].ensure_match_transaction_info(...)` [7](#0-6) .
2. `ensure_match_transaction_info` only checks status/gas/write-set-hash/event-root-hash [8](#0-7)  and explicitly skips checkpoint-hash checks per the trailing comment [2](#0-1) .
3. Consequently, if the executor's locally computed `position_state_checkpoint_hash` (or `state_checkpoint_hash`/`hot_state_checkpoint_hash`) for a `TransactionInfoV1` differs from the archived one while status/gas/write-set/events happen to match, `ensure_match_transaction_info` returns `Ok(())`, and the replay-verify tool reports success for that transaction/chunk despite the state-root divergence.

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

**File:** types/src/on_chain_config/aptos_features.rs (L203-209)
```rust
    /// When enabled, execution computes the native-position state root at the
    /// checkpoint stage and commits it to `TransactionInfoV1`, so it is
    /// consensus-verified. Requires `TRANSACTION_INFO_V1`.
    COMPUTE_TRADING_NATIVE_STATE_ROOTS = 122,
    /// When enabled, execution populates `TransactionInfoV1`'s hot state root hash, so it
    /// is committed to the ledger accumulator. Requires `TRANSACTION_INFO_V1`.
    HOT_STATE_ROOT_IN_TXN_INFO = 123,
```

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L192-234)
```rust
    fn get_state_checkpoint_hashes(
        execution_output: &ExecutionOutput,
        known_state_checkpoints: Option<Vec<Option<HashValue>>>,
        computed_last_checkpoint_hash: HashValue,
        label: &str,
    ) -> Result<Vec<Option<HashValue>>> {
        let _timer = OTHER_TIMERS.timer_with(&[&format!("get_{label}_checkpoint_hashes")]);

        let num_txns = execution_output.to_commit.len();
        let last_checkpoint_index = execution_output
            .to_commit
            .state_update_refs()
            .last_inner_checkpoint_index();

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
            Ok(known)
        } else {
            if !execution_output.is_block {
                // We should enter this branch only in test.
                execution_output.to_commit.ensure_at_most_one_checkpoint()?;
            }

            let mut out = vec![None; num_txns];
            if let Some(index) = last_checkpoint_index {
                out[index] = Some(computed_last_checkpoint_hash);
            }
            Ok(out)
        }
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

**File:** aptos-move/framework/move-stdlib/sources/configs/features.move (L949-955)
```text
    /// When enabled, execution computes the trading-native state roots and commits them to
    /// `TransactionInfoV1`, so they are consensus-verified. Requires `TRANSACTION_INFO_V1`.
    /// Covers the native-position tree today and is intended to cover the other trading-native
    /// trees as they are added. Enabling it first commits the (empty-tree) roots to transaction
    /// info; the actual Move-side writes to those trees are gated by separate flags.
    /// Lifetime: permanent
    const COMPUTE_TRADING_NATIVE_STATE_ROOTS: u64 = 122;
```
