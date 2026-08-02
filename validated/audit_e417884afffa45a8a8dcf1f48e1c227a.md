### Title
`replay_on_archive`/CLI replay-verify accepts divergent state roots because `TransactionOutput::ensure_match_transaction_info` never checks `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` - (File: types/src/transaction/mod.rs)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the authenticated-comparison routine used by replay/verification tooling (`storage/db-tool/src/replay_on_archive.rs`, `aptos-move/cli/src/commands.rs`, `aptos-move/aptos-debugger/src/aptos_debugger.rs`, and `execution/executor/src/chunk_executor/mod.rs`) to confirm that a locally re-executed `TransactionOutput` matches the `TransactionInfo` that was actually committed to the ledger accumulator. The function checks status, gas used, write-set hash, and event root hash, but explicitly skips the state-checkpoint-related hash fields of `TransactionInfo` (`state_checkpoint_hash`, `hot_state_checkpoint_hash`, `position_state_checkpoint_hash`), as the code itself documents.

### Finding Description
`ensure_match_transaction_info` at [1](#0-0)  validates:
- transaction status
- gas used
- write-set hash (`state_change_hash`)
- event root hash

but the code contains a self-documented gap: [2](#0-1) 
```
// TODO(trading-native): this comparator ignores the checkpoint hashes
// (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
// tooling (e.g. db-tool's `replay_on_archive`) can report a successful
// replay even when the authenticated position state root diverges from
// local execution. Validate the checkpoint hashes here before enabling
// COMPUTE_TRADING_NATIVE_STATE_ROOTS.
```

`TransactionInfoV1` carries `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` — all authenticated fields committed under the transaction accumulator root and thus covered by consensus signatures/state proofs [3](#0-2) . These are exactly the fields that bind a committed transaction to a particular Merkle/JMT state root — the core state-commitment invariant this scan is meant to protect. `execution/executor/src/workflow/do_ledger_update.rs` computes these checkpoint hashes from local execution and assembles them into `TransactionInfo` at commit time [4](#0-3) , so any bug in state-checkpoint computation (JMT root construction, hot-state root, or the new native-position state root path) that causes a *local* recompute to diverge from the *committed* on-chain value will not be caught by the tooling that is supposed to detect exactly that divergence.

`storage/db-tool/src/replay_on_archive.rs::execute_and_verify` is the concrete caller: it re-executes transactions from backup/archive data and calls `ensure_match_transaction_info` against the `expected_txn_infos` read from storage, and only raises `Error` (feeding "failed txns") when this check fails [5](#0-4) . Because the check omits state/hot-state/position checkpoint hashes, replay-verify (a tool explicitly designed to catch exactly this class of divergence between committed ledger state and correct VM execution) will report success even when the state root diverges.

### Impact Explanation
This is a proof/verification-integrity gap rather than a live-network state-corruption bug: it does not itself corrupt any committed data, but it disables the safety net meant to detect committed-state divergence from correct VM execution during replay/audit. Given the state-integrity gate explicitly calls out "Hard-fork-only divergence during commit, replay, restore, or proof verification" as in-scope, and this defect is precisely a replay/proof-verification integrity break (an incorrect state-checkpoint root can be committed and subsequently pass local replay-verification unnoticed), the impact is High: it undermines confidence that replay-verify/audit tooling would catch a consensus-level state-root bug (e.g., in `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, hot-state root, or checkpoint-hash computation) before or after it reaches mainnet, effectively blinding the primary detection mechanism for state divergence.

### Likelihood Explanation
The gap is unconditional in the current code — it applies to every call to `ensure_match_transaction_info` regardless of feature flags, and is used by multiple tools (`replay_on_archive`, `aptos-debugger`, CLI). Its practical trigger requires a separate root-cause bug in the checkpoint-hash computation path (e.g., in `assemble_transaction_infos`'s state/hot-state/position checkpoint hash inputs, or the JMT/hot-state summary construction feeding into `DoStateCheckpoint`) to actually produce a divergent hash. The comment itself indicates the maintainers are aware of and tracking this gap in preparation for enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, implying it is a known, currently-live limitation rather than a hypothetical one.

### Recommendation
Extend `ensure_match_transaction_info` to compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` between the locally recomputed output/state-checkpoint result and the expected `TransactionInfo`, gated appropriately behind whichever local values are available (e.g., only compare hashes that the local run actually computed), before treating a replay as verified. This should be done prior to enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, `HOT_STATE_ROOT_IN_TXN_INFO`, or relying on `replay_on_archive`/CLI replay results as an integrity guarantee.

### Proof of Concept
1. `execution/executor/src/workflow/do_ledger_update.rs::assemble_transaction_infos` computes `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` from local execution and bakes them into the committed `TransactionInfo` [6](#0-5) .
2. Suppose a bug in state-checkpoint/hot-state/position-state root computation causes a different (wrong) checkpoint hash to be committed on one node relative to what correct re-execution would produce elsewhere (write set, events, status, and gas remain identical because the bug only affects the summarized checkpoint root, not the underlying resource writes).
3. Run `storage/db-tool/src/replay_on_archive.rs` (or the equivalent CLI/`aptos-debugger` replay tool) against the archived data; `execute_and_verify` re-executes and calls `ensure_match_transaction_info` [7](#0-6) .
4. Because `ensure_match_transaction_info` only compares status/gas/write-set hash/event root hash [8](#0-7) , the check passes and no error is reported, even though the authenticated state-checkpoint root differs from the correct VM result — silently masking a state-commitment integrity break.

Note: I was unable to fully trace every downstream caller (`aptos-move/cli/src/commands.rs`, `aptos-move/aptos-debugger/src/aptos_debugger.rs`) within the available iterations to confirm each one's exact usage context; the core defect (the omitted comparisons in `ensure_match_transaction_info` and their explicit acknowledgment in the source) was verified directly.

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

**File:** types/src/transaction/mod.rs (L2440-2461)
```rust
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[cfg_attr(any(test, feature = "fuzzing"), derive(Arbitrary))]
pub struct TransactionInfoV1 {
    gas_used: u64,
    status: ExecutionStatus,
    transaction_hash: HashValue,
    event_root_hash: HashValue,
    state_change_hash: HashValue,
    state_checkpoint_hash: Option<HashValue>,
    hot_state_checkpoint_hash: Option<HashValue>,
    auxiliary_info_hash: Option<HashValue>,

    /// Repurposed reserved field; `None` matches the prior BCS encoding.
    position_state_checkpoint_hash: Option<HashValue>,
    placeholder1: Option<HashValue>,
    placeholder2: Option<HashValue>,
    placeholder3: Option<HashValue>,
    placeholder4: Option<HashValue>,
    placeholder5: Option<HashValue>,
    placeholder6: Option<HashValue>,
    placeholder7: Option<HashValue>,
}
```

**File:** execution/executor/src/workflow/do_ledger_update.rs (L58-126)
```rust
    fn assemble_transaction_infos(
        to_commit: &TransactionsWithOutput,
        transaction_info_v1: bool,
        state_checkpoint_hashes: &[Option<HashValue>],
        hot_state_checkpoint_hashes: Option<&[Option<HashValue>]>,
        position_state_checkpoint_hashes: Option<&[Option<HashValue>]>,
    ) -> (Vec<TransactionInfo>, Vec<HashValue>) {
        let _timer = OTHER_TIMERS.timer_with(&["assemble_transaction_infos"]);

        (0..to_commit.len())
            .into_par_iter()
            .with_min_len(optimal_min_len(to_commit.len(), 64))
            .map(|i| {
                let txn = &to_commit.transactions[i];
                let txn_output = &to_commit.transaction_outputs[i];
                let persisted_auxiliary_info = &to_commit.persisted_auxiliary_infos[i];
                // Use the auxiliary info hash directly from the persisted info
                let auxiliary_info_hash = match persisted_auxiliary_info {
                    PersistedAuxiliaryInfo::None => None,
                    PersistedAuxiliaryInfo::V1 { .. } => {
                        Some(CryptoHash::hash(persisted_auxiliary_info))
                    },
                    PersistedAuxiliaryInfo::TimestampNotYetAssignedV1 { .. } => None,
                };
                let state_checkpoint_hash = state_checkpoint_hashes[i];
                let event_hashes = txn_output
                    .events()
                    .iter()
                    .map(CryptoHash::hash)
                    .collect::<Vec<_>>();
                let event_root_hash =
                    InMemoryEventAccumulator::from_leaves(&event_hashes).root_hash();
                let write_set_hash = CryptoHash::hash(txn_output.write_set());
                let status = txn_output
                    .status()
                    .as_kept_status()
                    .expect("Already sorted.");
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
                let txn_info_hash = txn_info.hash();
                (txn_info, txn_info_hash)
            })
            .unzip()
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
