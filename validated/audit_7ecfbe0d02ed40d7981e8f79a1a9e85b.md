## Analysis Result

Based on the investigation, the strongest local candidate is the explicit, self-documented gap in `TransactionOutput::ensure_match_transaction_info`, which is the state-integrity analog of the ERC-721 "declared parameter not propagated/used" bug pattern: an input value that the function's own contract implies should be validated is silently excluded from the check.

### Title
`ensure_match_transaction_info` does not validate state/hot-state/position checkpoint hashes, allowing replay-verify tooling to accept a divergent authenticated state root - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the authoritative comparator used by replay/verification tooling (`storage/db-tool/src/replay_on_archive.rs`, `execution/executor/src/chunk_executor/mod.rs`, `aptos-move/aptos-debugger`, `aptos-move/cli`) to confirm that a locally re-executed `TransactionOutput` matches the `TransactionInfo` recorded/authenticated on an archive or backup. Similar to the ERC-721 report where a declared `bytes data` parameter is accepted but never propagated into the security-relevant callback, this function accepts `txn_info: &TransactionInfo` (which carries `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash`) but its own code comment states it deliberately does not check these fields.

### Finding Description [1](#0-0) 

The function checks `status`, `gas_used`, `write_set_hash` (state_change_hash), and `event_root_hash`, but explicitly skips the checkpoint hashes:
```
// TODO(trading-native): this comparator ignores the checkpoint hashes
// (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
// tooling (e.g. db-tool's `replay_on_archive`) can report a successful
// replay even when the authenticated position state root diverges from
// local execution. Validate the checkpoint hashes here before enabling
// COMPUTE_TRADING_NATIVE_STATE_ROOTS.
```
This means `TransactionInfoV1::state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` — each of which is committed into the transaction accumulator via `DoLedgerUpdate::assemble_transaction_infos` [2](#0-1)  and is thus part of the value authenticated by the accumulator/ledger-info signature — is never cross-checked against locally recomputed state roots by this verification entry point.

`replay_on_archive.rs`'s `execute_and_verify` calls exactly this function as its sole correctness gate after re-executing a chunk of transactions: [3](#0-2) 
If a build with `COMPUTE_TRADING_NATIVE_STATE_ROOTS` on-chain config enabled produces a state/hot-state/position Merkle root that differs from what is recorded in the archived, ledger-info-signed `TransactionInfo` — due to a bug in state-checkpoint computation, hot-state logic, or the native "position" state, any of which is plausible given this is a new, actively-changing subsystem (`compute_trading_native_state_roots`, `hot_state_root_in_txn_info` flags threaded through `do_get_execution_output.rs`) — `replay_on_archive` will still report success. The archive/backup replay path is one of the load-bearing invariants relied on to detect state divergence and hard-fork bugs before/after mainnet deployment.

### Impact Explanation
This breaks the "authenticated API or state-view output bound to the wrong version, object, or proof context" and "hard-fork-only divergence during commit/replay" invariants called out in the Required Impacts. A state root divergence (e.g., in the position/hot-state Merkle tree introduced by `COMPUTE_TRADING_NATIVE_STATE_ROOTS`) between what nodes actually compute and what is recorded as canonical would not be caught by the primary replay-verification tool, `db-tool replay-verify`, which is precisely the safety net meant to catch consensus/state divergence bugs. This is a High severity gap in defense-in-depth for state-commitment correctness: it doesn't itself corrupt the ledger, but it silently disables detection of ledger corruption for a whole class of new state-root fields, delaying or preventing discovery of a hard-fork-causing bug until it manifests as an actual consensus fork.

### Likelihood Explanation
The gap is not currently exploitable in production because `COMPUTE_TRADING_NATIVE_STATE_ROOTS` and `HOT_STATE_ROOT_IN_TXN_INFO` are new/gated features (per the code comment, this must be fixed "before enabling COMPUTE_TRADING_NATIVE_STATE_ROOTS"). The likelihood of triggering the underlying divergence is unknown without knowing whether the feature is enabled on mainnet or is still in development. This is a self-acknowledged TODO by the Aptos team rather than a fully independent discovery, so I flag lower confidence that this qualifies as a *novel* finding — but it is a genuine, currently-present integrity-check omission in unprivileged, mainnet-reachable code (any full node/tool can run `replay_on_archive` against an archive).

### Recommendation
Extend `ensure_match_transaction_info` to compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` between the locally computed `TransactionOutput`/`StateCheckpointOutput` and the `TransactionInfo` under verification, at least whenever those fields are `Some` in the txn_info, before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS` on any network. Update all callers (`replay_on_archive.rs`, `chunk_executor/mod.rs`, `aptos_debugger.rs`, `cli/commands.rs`) to pass through the necessary checkpoint hash data required for the comparison.

### Proof of Concept
Not independently reproducible from static analysis alone — the divergence requires (1) `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/hot-state features being enabled and (2) an actual bug causing local state-root computation to differ from the archived value. I was not able to fully verify whether these features are currently enabled on mainnet within the scope of this investigation; this should be checked before treating the finding as immediately exploitable.

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

**File:** storage/db-tool/src/replay_on_archive.rs (L392-406)
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
        }
```
