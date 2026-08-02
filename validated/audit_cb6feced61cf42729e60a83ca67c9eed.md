### Title
Replay-verification comparator skips checkpoint-hash validation, allowing state-root divergence to go undetected - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function that `ChunkExecutor::verify_execution` (used by backup/replay-verify tooling such as `TransactionRestoreBatchController` and `storage/db-tool/src/replay_on_archive.rs`) calls to confirm that locally re-executed transaction output matches the `TransactionInfo` recorded on the authenticated ledger. The comparator checks status, gas used, write-set hash, and event root hash, but explicitly and knowingly skips comparing `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` — the very fields that commit the state/hot-state/position Merkle roots into the consensus-signed `TransactionInfo`/accumulator.

### Finding Description
`ensure_match_transaction_info` in [1](#0-0)  verifies only `status`, `gas_used`, write-set hash (`state_change_hash`), and `event_root_hash` against the supplied `txn_info`. The function's own trailing comment documents the gap: [2](#0-1) 

This comparator is invoked from `ChunkExecutor::verify_execution` in [3](#0-2) , which is the routine that replays transactions locally and checks the result against the `transaction_infos`/`write_sets`/`events` pulled from an (already proof-verified) backup or archive, as used by the backup-cli transaction restore/replay-verify path (`storage/backup/backup-cli/src/backup_types/transaction/restore.rs`) and `storage/db-tool/src/replay_on_archive.rs`.

Because `TransactionInfoV1` carries `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` — each an authenticated Merkle root over state, hot-state, or the native-position tree, hashed into the `TransactionInfo` that is itself accumulated into the ledger's Merkle accumulator (see the field definitions at [4](#0-3)  and their accessors at [5](#0-4) ) — any local re-execution divergence limited to these fields (e.g. a bug in hot-state promotion or the native-position tree computed by `DoStateCheckpoint`, as wired through `execution/executor/src/chunk_executor/mod.rs:363-413` and `execution/executor/src/workflow/do_ledger_update.rs:30-45`) will not be flagged by `verify_execution`. The write-set hash, event hash, gas, and status can all match while the state/hot-state/position roots silently diverge from what is authenticated on-chain.

### Impact Explanation
Replay-verify tooling (`replay_on_archive`, backup-cli's `VerifyExecutionMode`) exists specifically to catch cases where local re-execution of committed transactions produces a different ledger state than what was consensus-committed — i.e., to detect state-commitment bugs before or after they reach mainnet. By omitting the checkpoint-hash comparison, this tool can report a **successful, verified replay** even when the locally computed state root, hot-state root, or native-position root actually diverges from the authenticated value recorded in the ledger's `TransactionInfo`. This directly undermines the "authenticated API/proof output must stay bound to the right ledger root" and "replay paths must preserve deterministic proof binding" invariants: a real state-divergence bug (from a VM/state-checkpoint computation error) would go undetected by the one tool meant to catch it, letting corrupted-state claims pass as verified.

### Likelihood Explanation
This is a real, currently-shipped gap (not a hypothetical): it is the exact and only checkpoint-hash validation path in `verify_execution`, and the code's own comment states the risk. It requires a pre-existing divergence bug in state/hot-state/position-tree computation to be masked — such a bug would otherwise still be caught by the accumulator-hash check in the primary block-execution/state-sync path (`chunk_result_verifier.rs`'s `ensure_transaction_infos_match`, which compares the full `TransactionInfo`, including checkpoint hashes) — so the practical exposure is confined to the replay-verify/backup-audit tooling rather than mainnet consensus itself. Still, this is the "safety net" tool operators rely on to detect committed-state corruption during archive replay and forensic audits, and its blind spot is exact and reproducible.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash` (when present on both sides), `hot_state_checkpoint_hash` (gated on `HOT_STATE_ROOT_IN_TXN_INFO`), and `position_state_checkpoint_hash` (gated on `COMPUTE_TRADING_NATIVE_STATE_ROOTS`) against locally recomputed roots before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, per the existing TODO.

### Proof of Concept
1. Enable `TRANSACTION_INFO_V1` + `HOT_STATE_ROOT_IN_TXN_INFO` (and/or `COMPUTE_TRADING_NATIVE_STATE_ROOTS`).
2. Introduce (or trigger via an existing latent bug in) hot-state promotion / native-position tree computation such that the recomputed `hot_state_checkpoint_hash` or `position_state_checkpoint_hash` differs from the value in the backed-up/archived `TransactionInfo`, while write-set hash, events, gas, and status remain identical.
3. Run `db-tool replay-on-archive` or a backup-cli transaction restore in `VerifyExecutionMode`.
4. `ChunkExecutor::verify_execution` calls `ensure_match_transaction_info` (`execution/executor/src/chunk_executor/mod.rs:692-705` → `types/src/transaction/mod.rs:2139-2204`), which does not compare the checkpoint hashes and returns `Ok(())`, reporting the replay as verified despite the state-root divergence.

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

**File:** types/src/transaction/mod.rs (L2336-2364)
```rust
    pub fn state_checkpoint_hash(&self) -> Option<HashValue> {
        match self {
            Self::V0(v) => v.state_checkpoint_hash,
            Self::V1(v) => v.state_checkpoint_hash,
        }
    }

    pub fn has_state_checkpoint_hash(&self) -> bool {
        self.state_checkpoint_hash().is_some()
    }

    pub fn ensure_state_checkpoint_hash(&self) -> Result<HashValue> {
        self.state_checkpoint_hash()
            .ok_or_else(|| format_err!("State checkpoint hash not present in TransactionInfo"))
    }

    pub fn hot_state_checkpoint_hash(&self) -> Option<HashValue> {
        match self {
            Self::V0(_) => None,
            Self::V1(v) => v.hot_state_checkpoint_hash,
        }
    }

    pub fn position_state_checkpoint_hash(&self) -> Option<HashValue> {
        match self {
            Self::V0(_) => None,
            Self::V1(v) => v.position_state_checkpoint_hash,
        }
    }
```

**File:** types/src/transaction/mod.rs (L2442-2461)
```rust
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

**File:** execution/executor/src/chunk_executor/mod.rs (L692-705)
```rust
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
```
