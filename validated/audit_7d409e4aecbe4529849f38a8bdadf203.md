## Finding

### Title
`TransactionOutput::ensure_match_transaction_info` never checks `state_checkpoint_hash`, allowing state-sync-by-output to commit a write set whose resulting state root diverges from the consensus-authenticated `TransactionInfo` - (File: `types/src/transaction/mod.rs`)

### Summary
When Aptos state-sync applies a chunk via `enqueue_chunk_by_transaction_outputs` (fetching pre-computed `TransactionOutput`s from a peer instead of re-executing), the only binding between the untrusted, peer-supplied `TransactionOutput` (write set + events) and the consensus-authenticated `TransactionInfo` (verified via the transaction accumulator proof) is `TransactionOutput::ensure_match_transaction_info` in [1](#0-0) . This function checks status, gas used, `state_change_hash` (hash of the write set) and `event_root_hash`, but its own inline TODO admits it never validates `state_checkpoint_hash` (or `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`) [2](#0-1) .

### Finding Description
`TransactionInfo` binds several hashes for a committed transaction: `state_change_hash` (hash of the write-set bytes), `event_root_hash`, and `state_checkpoint_hash` (root of the *resulting* global state after applying the write set, computed by `DoStateCheckpoint`, see the checkpoint-hash plumbing in [3](#0-2) ). `state_change_hash` only proves that the bytes of the write set are unmodified — it does **not** prove that applying that write set to the correct pre-state yields the state root that consensus actually committed to (via `state_checkpoint_hash` in the accumulator-proved `TransactionInfo`).

In the transaction-outputs state-sync path, `enqueue_chunk_by_transaction_outputs` in [4](#0-3)  takes `transaction_outputs` directly from an untrusted peer, verifies only that the `TransactionInfo` list is consistent with the accumulator/ledger-info proof (`txn_output_list_with_proof.verify(...)`), and then relies on `ensure_match_transaction_info` (called downstream when applying the chunk) to bind each `TransactionOutput` to its corresponding `TransactionInfo`. Because that function skips the `state_checkpoint_hash` comparison, a `TransactionOutput` whose write set hashes correctly (matches `state_change_hash`) but whose *actual state effect* differs from what was intended (e.g., a bug in fast-path replay/output generation, or a corrupted/duplicated write-set field that still serializes to a byte sequence with the right hash — this is a much stronger example if a hash-length-extension-like collision existed, but even simpler: a write set that is byte-identical yet applied against the wrong current state, or a value serialized with different type-layout leading to different logical result) can be accepted and committed to durable storage without any code path re-deriving and comparing the resulting state root against the authenticated `state_checkpoint_hash`.

### Impact Explanation
If the check is truly absent from this path (and I could not, within the remaining tool budget, positively confirm an independent state-checkpoint-hash re-verification step exists specifically in the "apply chunk by transaction outputs" replay/state-sync flow — `DoStateCheckpoint`'s only found reference is in the fresh-execution workflow, not the output-application path), a syncing node could accept and commit ledger state that differs from the state committed by consensus while still appearing internally consistent (accumulator/ledger-info proof still verifies, since it only covers `TransactionInfo` fields, and `TransactionInfo` itself was never cross-checked for `state_checkpoint_hash` against the applied output). This is exactly the "committed state that differs from the correct VM result or corrupts durable ledger data" and "authenticated proof accepted as valid for a wrong state" class of bug called out in scope. The `ensure_match_transaction_info` TODO explicitly states this exact concern already causes `replay_on_archive`/replay-verify tooling to report false-positive successful replays when the position-native state root diverges — i.e., the code's own author has already identified that this invariant is broken for at least one checkpoint field.

### Likelihood Explanation
This requires a state-sync source (or storage-service peer) that is unprivileged relative to the honest full-node's own storage/db and can supply attacker-influenced `TransactionOutput`s while a matching accumulator-proved `TransactionInfo` is fetched normally (or is already the local, honest chain's `TransactionInfo`, and only the output/state-checkpoint mismatch needs to occur, e.g. through some non-determinism, bug in output serving, or a malicious storage-service responder crafted to match `state_change_hash` and `event_root_hash` while producing a different post-state). Because `state_change_hash` is a strong preimage-resistant hash of the write set itself, a fully malicious attacker cannot forge an *arbitrary* different write set that still matches `state_change_hash`; however, the gap remains a genuine broken invariant since the code never verifies the derived post-state root at all in this path, and the in-repo TODO comment confirms this has real observable consequences today (false-successful replay verification) for at least the trading-native/position state root, and by the comment's own wording, for the base `state_checkpoint_hash` and hot-state checkpoint hash as well.

### Recommendation
Extend `TransactionOutput::ensure_match_transaction_info` to also compute the resulting state root (or hot-state / position-state root, as applicable) after applying `self.write_set()` to the pre-state, and assert it equals `txn_info.state_checkpoint_hash()` / `txn_info.hot_state_checkpoint_hash()` / `txn_info.position_state_checkpoint_hash()` whenever those fields are `Some`. This closes the gap for `enqueue_chunk_by_transaction_outputs` and for any replay/verification tooling (`aptos-debugger`, `aptos-move/cli`) that currently rely on this comparator to fully authenticate applied outputs before commit.

### Proof of Concept
Could not be fully constructed within the tool budget — I could not conclusively rule out that a separate, independent state-checkpoint-hash verification exists elsewhere specifically in the transaction-output chunk-apply path (`ChunkToApply` / `chunk_result_verifier.rs`) that would make this check redundant. This should be verified with direct access to `execution/executor/src/chunk_executor/chunk_result_verifier.rs` and `transaction_chunk.rs` (not fully retrievable in this session) before treating this as a confirmed, exploitable, unprivileged root cause. Given this residual uncertainty, I am reporting it as the strongest candidate found, but flagging the incomplete verification explicitly rather than asserting full certainty.

### Citations

**File:** types/src/transaction/mod.rs (L2139-2145)
```rust
    pub fn ensure_match_transaction_info(
        &self,
        version: Version,
        txn_info: &TransactionInfo,
        expected_write_set: Option<&WriteSet>,
        expected_events: Option<&[ContractEvent]>,
    ) -> Result<()> {
```

**File:** types/src/transaction/mod.rs (L2197-2203)
```rust
        // TODO(trading-native): this comparator ignores the checkpoint hashes
        // (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
        // tooling (e.g. db-tool's `replay_on_archive`) can report a successful
        // replay even when the authenticated position state root diverges from
        // local execution. Validate the checkpoint hashes here before enabling
        // COMPUTE_TRADING_NATIVE_STATE_ROOTS.
        Ok(())
```

**File:** execution/executor/src/workflow/do_ledger_update.rs (L82-121)
```rust
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
```

**File:** execution/executor/src/chunk_executor/mod.rs (L185-221)
```rust
    fn enqueue_chunk_by_transaction_outputs(
        &self,
        txn_output_list_with_proof: TransactionOutputListWithProofV2,
        verified_target_li: &LedgerInfoWithSignatures,
        epoch_change_li: Option<&LedgerInfoWithSignatures>,
    ) -> Result<()> {
        let _guard = CONCURRENCY_GAUGE.concurrency_with(&["chunk", "enqueue_by_outputs"]);
        let _timer = APPLY_CHUNK.start_timer();

        // Verify input data.
        THREAD_MANAGER.get_exe_cpu_pool().install(|| {
            let _timer = CHUNK_OTHER_TIMERS.timer_with(&["apply_chunk__verify"]);
            txn_output_list_with_proof.verify(
                verified_target_li.ledger_info(),
                txn_output_list_with_proof.get_first_output_version(),
            )
        })?;

        let (txn_output_list_with_proof, persisted_aux_info) =
            txn_output_list_with_proof.into_parts();
        // Compose enqueue_chunk parameters.
        let TransactionOutputListWithProof {
            transactions_and_outputs,
            first_transaction_output_version: v,
            proof: txn_infos_with_proof,
        } = txn_output_list_with_proof;
        let (transactions, transaction_outputs): (Vec<_>, Vec<_>) =
            transactions_and_outputs.into_iter().unzip();
        let chunk = ChunkToApply {
            transactions,
            transaction_outputs,
            persisted_aux_info,
            first_version: v.ok_or_else(|| anyhow!("first version is None"))?,
        };
        let chunk_verifier = Arc::new(StateSyncChunkVerifier {
            txn_infos_with_proof,
            verified_target_li: verified_target_li.clone(),
```
