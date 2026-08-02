### Title
`ensure_match_transaction_info` skips checkpoint-hash validation, letting replay-verify tooling accept a corrupted state root - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` (used by `storage/db-tool/src/replay_on_archive.rs`) is the local-execution-vs-authenticated-`TransactionInfo` integrity check used by replay/verification tooling. It validates status, gas, write-set hash, and event-root hash against the on-chain `TransactionInfo`, but explicitly skips validating `state_checkpoint_hash`, the hot-state checkpoint hash, and `position_state_checkpoint_hash`. This is called out in-code as a known gap.

### Finding Description
`ensure_match_transaction_info` in [1](#0-0)  checks:
- `status` vs `txn_info.status()`
- `gas_used` vs `txn_info.gas_used()`
- `write_set` hash vs `txn_info.state_change_hash()`
- event root hash vs `txn_info.event_root_hash()`

but the function ends with an explicit TODO acknowledging it does **not** validate the checkpoint hashes: [2](#0-1) 

That comment states verbatim that this gap allows `db-tool`'s `replay_on_archive` to "report a successful replay even when the authenticated position state root diverges from local execution," and that the checkpoint hashes must be validated "before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`."

This is the direct Aptos-native analog of the external report's core defect: a committed/authenticated artifact (the `markPrice`, there; the `state_checkpoint_hash` / `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`, here) is used downstream for verification/acceptance decisions without validating a value that should gate correctness. Just as the perps protocol accepted mid/impact prices derived from data that should have been excluded, this replay-verification path accepts a `TransactionOutput` as "matching" the ledger's authenticated `TransactionInfo` while never checking that the locally computed state root (main state Merkle root, hot-state root, or the trading-native `position_state_checkpoint_hash`) equals the value actually committed on-chain.

The `position_state_checkpoint_hash` field and the `compute_trading_native_state_roots` computation path exist and are wired through the executor (`execution/executor-types/src/execution_output.rs`, `execution/executor/src/workflow/do_state_checkpoint.rs`, `execution/executor/src/workflow/do_get_execution_output.rs`), confirming the checkpoint hash is a real, committed field on `TransactionInfoV1` that this verifier is supposed to check but doesn't.

### Impact Explanation
If a validator/full-node's local state computation (main state root, hot-state root, or trading-native position-state root) diverges from what was actually committed to the chain — due to a bug in state-checkpoint computation, a non-deterministic execution path, or a malicious/buggy node under investigation — the `replay_on_archive` verification tool, whose entire purpose is to catch exactly this kind of divergence, will report success. This defeats a hard-fork/divergence-detection safety mechanism: operators and auditors relying on `replay_on_archive` to confirm that historical execution matches the authenticated ledger state will get false assurance, masking committed-state corruption that should have been flagged as high/critical. This matches the "Hard-fork-only divergence during commit, replay, restore, or proof verification" and "committed state that differs from the correct VM result" impact categories.

### Likelihood Explanation
The gap is unconditional in the current code — it is not behind a feature flag; the check is simply absent from `ensure_match_transaction_info` regardless of whether `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled. It only manifests when there is an actual state-root divergence to detect (making it a "detection failure" rather than an active corruption trigger by itself), which is why the immediate exploitability is lower, but the comment in the code confirms the maintainers are aware this must be fixed before further reliance on the trading-native root, and the tool's core promise (bit-for-bit replay verification of committed state) is silently unmet today for the state/hot-state/position checkpoint roots.

### Recommendation
Extend `ensure_match_transaction_info` to also compare the locally computed `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present on the `TransactionInfo` variant) against the expected values before returning `Ok(())`, and thread these expected checkpoint hashes into the call sites in `storage/db-tool/src/replay_on_archive.rs`, consistent with the TODO already present in the code.

### Proof of Concept
Conceptual PoC (verification bypass, not remote exploit):
1. Run `replay_on_archive` (or any caller of `ensure_match_transaction_info`) against a `TransactionOutput` produced by execution where the write-set, events, gas, and status all match the authenticated `TransactionInfo`, but the resulting state Merkle root (or hot-state/position-state root) differs from `txn_info.state_checkpoint_hash()` — e.g., due to a state-tree bug that drops or misapplies one key while keeping the write set's byte-serialized hash identical, or more directly, seed a `TransactionInfo` with an intentionally wrong `state_checkpoint_hash`/`position_state_checkpoint_hash`.
2. Call `ensure_match_transaction_info` with this pair.
3. Observe: the function returns `Ok(())` because it never inspects `state_checkpoint_hash()`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`, confirming the check at [3](#0-2)  silently accepts the mismatched checkpoint root.

Note: I could not fully trace every call site of `replay_on_archive`'s use of this function within the time available (e.g., exact conditions under which `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled in production configs), so the practical exposure window for the trading-native root specifically is not fully confirmed — but the main/hot-state `state_checkpoint_hash` omission is unconditional and directly evidenced by the code and its own TODO comment.

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
