Based on local code, I found a genuine, self-acknowledged proof-integrity gap in the transaction-output verification comparator, distinct from (though a plausible "integrity analog" of) the seed report's theme of a check that silently fails to enforce the invariant it's meant to guard.

### Title
Replay/output verification comparator skips state-checkpoint root hashes, allowing divergent state to pass as a match - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the canonical function used across the codebase to verify that a locally-produced `TransactionOutput` matches an authenticated `TransactionInfo` (the object committed to the transaction accumulator and covered by ledger-info signatures). It checks status, gas used, write-set hash, and event-root hash, but it explicitly omits comparing `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` — the fields that authenticate the Sparse-Merkle/Jellyfish state root at that version.

### Finding Description
The comparator's own comment documents the gap: [1](#0-0) 

```
// TODO(trading-native): this comparator ignores the checkpoint hashes
// (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
// tooling (e.g. db-tool's `replay_on_archive`) can report a successful
// replay even when the authenticated position state root diverges from
// local execution. Validate the checkpoint hashes here before enabling
// COMPUTE_TRADING_NATIVE_STATE_ROOTS.
```

The function body only asserts status, gas, write-set hash, and event-root hash before returning `Ok(())`: [2](#0-1) 

`TransactionInfoV0`/`TransactionInfoV1` carry `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` as first-class, hashed fields of the authenticated `TransactionInfo` that is itself a leaf in the transaction accumulator: [3](#0-2) 

This function is the sole verification gate used by multiple tools that are supposed to catch state divergence between locally re-executed transactions and the authenticated on-chain record:
- `storage/db-tool/src/replay_on_archive.rs`'s `Verifier::execute_and_verify`, which drives the `replay-verify` / `replay-on-archive` tooling used to validate archive nodes and detect state divergence, calls it directly: [4](#0-3) 
- `aptos-move/aptos-debugger/src/aptos_debugger.rs` and `aptos-move/cli/src/commands.rs` (transaction replay/debugging) also rely on it as the pass/fail gate: [5](#0-4) 

Because the checkpoint-hash fields are skipped, if local re-execution produces a different state root (e.g., due to a bug in state-checkpoint computation, hot-state materialization, or the new "position state" tree feature gated by `COMPUTE_TRADING_NATIVE_STATE_ROOTS`), the verifier still reports success as long as the write-set/event/gas/status match. The write-set hash check alone does not imply the resulting Merkle/JMT root is correct — it only demonstrates that the raw write operations serialize identically, not that they were correctly applied/committed into the versioned state tree that downstream proofs (`SparseMerkleProof`, restore/consistency, `raw_value`/`raw_table_item` API reads bound to a ledger version) rely on.

### Impact Explanation
This breaks the intended proof/verification invariant that replay-verify and debugger tooling exist to enforce: that a node's locally-computed ledger state is bound to, and consistent with, the authenticated (ledger-info-signed) state at every version. A latent bug in state-checkpoint/hot-state/position-state root computation — introduced in a hard fork, a new feature (`HOT_STATE_ROOT_IN_TXN_INFO`, `COMPUTE_TRADING_NATIVE_STATE_ROOTS`), or a storage schema change — would silently pass `replay-verify`/`replay_on_archive`, the exact safety-net whose job is to detect this class of divergence before it propagates. Any subsequent proof served from the corrupted state tree (state proofs, JMT range/consistency proofs, snapshot restore verification) would be built on an unverified root, i.e., an authenticated response bound to the wrong state root without any tooling catching it. This matches the "Hard-fork-only divergence during commit, replay, restore, or proof verification" and "wrong accumulator root ... proof accepted as valid" impact categories.

### Likelihood Explanation
The gap is not theoretical — it is a documented TODO left in shipped code precisely because a new fork-gated feature (`COMPUTE_TRADING_NATIVE_STATE_ROOTS`, position-state trees) is planned to be enabled while this comparator has not yet been updated to check the corresponding checkpoint hash. Any code path that computes `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` incorrectly (a realistic class of bug during active feature development, e.g., in `execution/executor/src/workflow/do_ledger_update.rs`'s `assemble_transaction_infos`, which builds these fields per transaction) would go undetected by every consumer of `ensure_match_transaction_info` until the divergence causes a hard, unrecoverable failure later (e.g. state-proof verification failures for external light clients, or corrupted restore).

### Recommendation
Extend `ensure_match_transaction_info` to compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` against locally-computed equivalents whenever they are present in the authenticated `TransactionInfo` (matching the `Option` semantics already used for periodic checkpointing), before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS` or any other feature that depends on these fields for correctness. At minimum, `replay_on_archive` and the CLI/debugger replay paths should fail loudly on a checkpoint-hash mismatch rather than silently treating it as a passing replay.

### Proof of Concept
Not applicable as an exploit PoC — the flaw is a verification omission provable by static inspection: `ensure_match_transaction_info` (types/src/transaction/mod.rs:2139-2204) never reads `txn_info.state_checkpoint_hash()`, `.hot_state_checkpoint_hash()`, or `.position_state_checkpoint_hash()`, so no test input can make it fail on those fields regardless of how badly a local state-checkpoint computation diverges from the authenticated value.

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

**File:** aptos-move/cli/src/commands.rs (L2797-2813)
```rust
        // Materialize into transaction output and check if the outputs match.
        let txn_output = vm_output.into_transaction_output().map_err(|err| {
            CliError::UnexpectedError(format!(
                "Failed to materialize into transaction output: {}",
                err
            ))
        })?;

        // When local package overrides are in use the replayed code diverges from
        // what was originally executed on-chain (different instructions, gas, etc.),
        // so output comparison is meaningless and is automatically skipped.
        let skip_comparison = self.skip_comparison || !self.use_local_package.is_empty();
        if !skip_comparison {
            txn_output
                .ensure_match_transaction_info(self.txn_id, &txn_info, None, None)
                .map_err(|msg| CliError::UnexpectedError(msg.to_string()))?;
        }
```
