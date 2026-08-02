### Title
`replay-verify` (`replay_on_archive.rs` / `ensure_match_transaction_info`) never checks state/hot-state/position checkpoint hashes, so a divergent state root passes as a "successful replay" - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function the `db-tool replay-verify` and `aptos-debugger` tools call to confirm that locally re-executed transaction results match the authenticated `TransactionInfo` recorded on-chain (via the transaction accumulator/ledger info). It checks `status`, `gas_used`, `write_set` hash (`state_change_hash`), and `event_root_hash`, but — per its own code comment — deliberately skips comparing `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` against the values recomputed from local replay. [1](#0-0) 

### Finding Description
`ensure_match_transaction_info` is the sole correctness check used by the replay/verify pipeline to assert that a locally re-executed `TransactionOutput` is consistent with the trusted, ledger-committed `TransactionInfo`: [2](#0-1) 

It intentionally omits validating the checkpoint hash fields carried by `TransactionInfo`/`TransactionInfoV1`: [3](#0-2) 

These fields (`state_checkpoint_hash`, `hot_state_checkpoint_hash`, `position_state_checkpoint_hash`) are exactly the Merkle roots that bind the executed VM state (including the new trading-native/position state tree) into the accumulator-proven ledger: [4](#0-3) [5](#0-4) 

This function is invoked directly by `storage/db-tool/src/replay_on_archive.rs`'s `execute_and_verify`, which is the authoritative check that decides whether replay for a chunk of transactions "passed" or should be flagged as `TxnMismatch`: [6](#0-5) 

Because the checkpoint hashes are never compared, if the local state (main state SMT, hot-state, or the newly added native-position Merkle tree gated by `COMPUTE_TRADING_NATIVE_STATE_ROOTS`) diverges from the authenticated on-chain root at a checkpoint boundary — while write_set/events/gas/status all happen to match for the individual transaction being inspected — `execute_and_verify`/`ensure_match_transaction_info` will report success. The write set and event root only capture the delta for a single transaction; they do not capture the cumulative Merkle-tree state that `state_checkpoint_hash` (and its trading-native analog) is meant to authenticate. A bug in JMT update, hot-state bookkeeping, or (specifically called out in the source) the native-position state root computation would therefore be silently missed by the archive replay-verification tool.

### Impact Explanation
This breaks the "authenticated API/replay output bound to correct state root" invariant: `replay-verify` and `aptos-debugger`'s use of `ensure_match_transaction_info` are the primary tools operators and auditors use to confirm that historical execution against an archive DB reproduces the state committed on mainnet. A silent divergence in `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` — e.g., from a non-deterministic executor bug, a JMT/position-tree commit bug, or a consensus-vs-replay execution mismatch — would go undetected by this tool, giving false assurance that ledger state is correct when it is not. This is a High-severity gap in the correctness of the verification tooling itself, though it does not directly corrupt consensus-committed state (the accumulator/ledger info signatures are still the ultimate source of truth); its impact is limited to masking real state-divergence bugs from operators/auditors rather than a live consensus fork.

### Likelihood Explanation
The comment in the code (`// TODO(trading-native): ... so replay-verify tooling (e.g. db-tool's replay_on_archive) can report a successful replay even when the authenticated position state root diverges from local execution. Validate the checkpoint hashes here before enabling COMPUTE_TRADING_NATIVE_STATE_ROOTS.`) is an explicit, self-admitted acknowledgment from the code authors that this gap exists and is a known risk that must be closed before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled. This raises confidence that the issue is real and not a false positive, but it also indicates the feature/flag it's gated behind (`COMPUTE_TRADING_NATIVE_STATE_ROOTS`, feature id 122) may not yet be active on mainnet — I could not verify from the available code whether this flag is currently enabled in production, or how frequently `db-tool replay-verify` / `aptos-debugger` are used as the last line of defense for detecting state divergence versus other checks not covered in this search (e.g., independent JMT proof verification elsewhere in restore/state-sync paths, which do perform proper root checks per `EpochEndingRestoreController`/`AccumulatorProof::verify`).

### Recommendation
In `ensure_match_transaction_info` (`types/src/transaction/mod.rs`), add checks that recompute the local `state_checkpoint_hash` (and, when applicable, `hot_state_checkpoint_hash`/`position_state_checkpoint_hash`) at checkpoint-boundary transactions and assert equality against the corresponding fields on `txn_info`, mirroring the existing `ensure!` patterns used for `write_set_hash` and `event_root_hash`. This should be done before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled on any network, as the code comment itself specifies.

### Proof of Concept
Not applicable as a live exploit — this is a tooling/verification gap rather than an on-chain state-corruption bug. The "proof" is structural/code-level: 
1. `ensure_match_transaction_info` compares `status`, `gas_used`, `write_set_hash`, `event_root_hash` only (`types/src/transaction/mod.rs:2148-2195`).
2. It explicitly does not compare `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` (`types/src/transaction/mod.rs:2197-2203`).
3. `replay_on_archive.rs::execute_and_verify` treats an `Ok(())` from this function as proof the replayed chunk matches history and only reports `Err` from it as a mismatch (`storage/db-tool/src/replay_on_archive.rs:392-405`).
4. Therefore any scenario where the recomputed checkpoint/root hash differs from the persisted `TransactionInfo`'s value, but the per-transaction write set/events/gas/status still match, passes `replay-verify` undetected.

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

**File:** types/src/transaction/mod.rs (L2352-2364)
```rust
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
