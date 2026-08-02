## Title
`replay_on_archive`'s state-integrity check silently ignores state-checkpoint root hashes, letting divergent VM state pass verification - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the invariant that is supposed to guarantee "committed data on-disk matches the VM's actual output" when replay-verifying an archived ledger (`storage/db-tool/src/replay_on_archive.rs`) or when re-executing via the debugger (`aptos-move/aptos-debugger/src/aptos_debugger.rs`). It checks status, gas, write-set hash, event root hash, and transaction hash, but explicitly skips validating `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` — the fields that authenticate the Sparse-Merkle/Jellyfish state root and the "trading-native" position state root at that version. [1](#0-0) 

### Finding Description
`ensure_match_transaction_info` is the sole function used to assert that a freshly-computed `TransactionOutput` matches the archived `TransactionInfo` during replay verification. It only checks status, gas, write-set hash and event root hash, then returns `Ok(())` without ever comparing checkpoint hashes: [2](#0-1) 

The comment left in the code is itself an admission of the gap:
```
// TODO(trading-native): this comparator ignores the checkpoint hashes
// (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
// tooling (e.g. db-tool's `replay_on_archive`) can report a successful
// replay even when the authenticated position state root diverges from
// local execution. Validate the checkpoint hashes here before enabling
// COMPUTE_TRADING_NATIVE_STATE_ROOTS.
``` [3](#0-2) 

`storage/db-tool/src/replay_on_archive.rs`'s `execute_and_verify` re-executes archived transactions with `AptosVMBlockExecutor` and calls exactly this function as its only correctness gate before declaring the chunk verified: [4](#0-3) 

`TransactionInfoV1` (and the new `position_state_checkpoint_hash` field introduced for "trading-native" object roots) carries these checkpoint hashes as part of the durable, hash-chained `TransactionInfo` that is itself committed into the transaction accumulator and authenticated by ledger-info signatures: [5](#0-4) 

Because the comparator never re-derives/re-checks `state_checkpoint_hash` / `hot_state_checkpoint_hash` / `position_state_checkpoint_hash` against the locally computed state roots, any divergence between the archived (committed) state root and the root that local re-execution would produce is invisible to this integrity check. This mirrors the reported oracle bug's root cause: a "sanity/consistency" verification routine that is supposed to reject divergent state but has a blind spot that lets bad state pass silently.

### Impact Explanation
This breaks the "committed state must match correct VM result" invariant and the "hard-fork-only divergence during commit/replay/restore" impact category explicitly in scope: if a bug in state-checkpoint-hash computation (e.g., JMT/hot-state/position-state root logic) causes an incorrect state root to be committed on mainnet, `replay-verify` tooling — the primary automated defense used to catch exactly this class of bug before/after releases — will report a clean pass because it never inspects those roots. This can let a consensus-breaking, storage-corrupting divergence in the state tree ship undetected, since the dedicated verification tool is blind to it. The TODO makes clear this is guarding a still-unlaunched feature flag `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, meaning the gap is intentional-but-unfixed technical debt rather than a hypothetical.

### Likelihood Explanation
The code path is exercised on every run of `replay_on_archive`/`db-tool replay-verify`, which is the standard tool for validating historical replay and catching state divergence bugs before they're considered safe. The gap is not behind any privilege boundary — it's a pure logic omission in a public verification API (`TransactionOutput::ensure_match_transaction_info`) that any caller (debugger, replay tooling, future validators) relies on for correctness assurance. The feature (`COMPUTE_TRADING_NATIVE_STATE_ROOTS` and the new `position_state_checkpoint_hash`) is present in code and reader/writer paths already (`storage/aptosdb/src/db/aptosdb_reader.rs`, `aptosdb_writer.rs`, `execution/executor/src/workflow/do_ledger_update.rs`), meaning the state-checkpoint hash machinery is actively being built and can be enabled without this comparator being completed first, unless it's tracked and blocked before rollout.

### Recommendation
Extend `ensure_match_transaction_info` to independently recompute (or accept as parameters) the state-checkpoint hash, hot-state-checkpoint hash, and position-state-checkpoint hash for the version and assert equality with `txn_info.state_checkpoint_hash()`, `txn_info.hot_state_checkpoint_hash()`, and `txn_info.position_state_checkpoint_hash()` whenever those fields are `Some`, before this comparator is relied upon as a correctness gate for any feature that produces those roots (in particular before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`).

### Proof of Concept
Not applicable as a runnable exploit — this is a verification-logic gap, not an externally triggerable state transition bug. Demonstration path: (1) enable/hypothesize a divergent `position_state_checkpoint_hash`/`state_checkpoint_hash` computation in local re-execution vs. the archived value at some version; (2) run `storage/db-tool/src/replay_on_archive.rs` against that version range; (3) observe that `execute_and_verify` → `ensure_match_transaction_info` returns `Ok(())` and the chunk is reported verified despite the checkpoint-hash mismatch, because the comparator never inspects those fields [6](#0-5) [2](#0-1) .

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
