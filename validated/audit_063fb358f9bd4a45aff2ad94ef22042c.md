## Finding: `ensure_match_transaction_info` skips state-checkpoint hash verification, allowing replay-verify to accept a divergent committed state

### Title
Replay/output verification omits `state_checkpoint_hash` (incl. position/hot-state roots) checks, so a corrupted checkpoint state can pass as valid - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the authenticated-response/replay invariant that binds an executed `TransactionOutput` to its committed `TransactionInfo`. It checks status, gas, write-set hash, and event-root hash, but never checks `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` against the recomputed state root, even though these fields exist specifically to authenticate the SMT/JMT state root at checkpoint boundaries.

### Finding Description [1](#0-0) 

The function checks execution status, gas used, write-set hash (`state_change_hash`), and event root hash, but has no `ensure!` for `state_checkpoint_hash` (the SMT root committed at checkpoints), nor for `hot_state_checkpoint_hash` or `position_state_checkpoint_hash`. The code's own comment acknowledges this gap: [2](#0-1) 

This comparator is consumed directly by replay/verification tooling: `aptos-debugger`, the `aptos` CLI (`db-tool`/`replay_on_archive`), and `ChunkExecutorTrait`'s replay path all call `ensure_match_transaction_info` as their correctness gate when comparing locally-recomputed `TransactionOutput`s against the authenticated `TransactionInfo` fetched from a backup/archive/peer.

Because the SMT/hot-state/position root fields are never compared, if the locally recomputed state diverges from the authenticated ledger state at a checkpoint boundary (due to a bug in the VM, a storage bug, or a malicious/corrupted archive that supplies wrong `write_set`/`events` matching hashes but the actual on-disk state ends up different), the mismatch in the actual state root is silently accepted. This directly violates the "authenticated API/state-view output bound to the wrong version, object, or proof context" invariant and the "committed state that differs from the correct VM result" gate, since the verification path that is supposed to catch such divergence for replay-verify has a hole precisely at the state-root field.

### Impact Explanation
Replay-verify (`db-tool replay-verify`, `aptos-debugger`, and chunk-executor's output-replay path) is the primary tool operators and auditors use to confirm that a node's local execution/storage matches the authenticated chain history download from backups or peers. A gap that skips state-checkpoint-hash comparison means a state divergence that manifests only in the periodic checkpoint SMT root (not in per-transaction write-set hash, which is separately verified) will not be caught by this check, undermining the guarantee that "committed state differs from the correct VM result" is detected. This is a state-integrity/proof-verification gap in a security-critical code path, even though it's a missing-check rather than an outright corruption of a currently-enabled feature.

### Likelihood Explanation
For the mainline `state_checkpoint_hash` (non-position, non-hot-state) field, this check has apparently been missing unconditionally for all replay-verify callers — this is not gated behind an experimental flag, so it applies today. The `position_state_checkpoint_hash` piece is explicitly tied to the not-yet-enabled `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature flag, which reduces likelihood for that particular sub-field until the feature ships, but the general `state_checkpoint_hash` and `hot_state_checkpoint_hash` omission is unconditional and already reachable via existing replay/debugger call sites.

### Recommendation
Add `ensure!` checks in `ensure_match_transaction_info` comparing the recomputed state-checkpoint hash (when the transaction is a checkpoint) against `txn_info.state_checkpoint_hash()`, and likewise for `hot_state_checkpoint_hash()`, before this comparator is relied upon as a correctness gate; add the `position_state_checkpoint_hash` check before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, as the existing TODO already notes.

### Proof of Concept
Not independently reproducible without runtime access to a devnet/testnet to actually diverge the state tree at a checkpoint while keeping write-set/event hashes matching (e.g., through a targeted storage-write bug). This report is based on static code inspection: [1](#0-0)  shows the omitted checks, and the callers in `execution/executor/src/chunk_executor/mod.rs`, `aptos-move/aptos-debugger/src/aptos_debugger.rs`, and `aptos-move/cli/src/commands.rs` show this is the actual verification gate used in replay-verify workflows.

---

**Caveat on confidence**: I was not able to fully trace every call site (I did not read the full bodies of `aptos_debugger.rs`, `commands.rs`, or `chunk_executor/mod.rs` around their `ensure_match_transaction_info` calls due to the iteration limit) to confirm there is no redundant state-root check performed elsewhere in those call paths that would compensate for this gap (e.g., a separate SMT-proof verification against the ledger info's accumulator root happening independently). If such a compensating check exists in those callers, the practical severity of this finding would be lower than stated above — it would then be a defense-in-depth gap rather than the sole line of defense. I'd recommend verifying this before treating it as critical.

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
