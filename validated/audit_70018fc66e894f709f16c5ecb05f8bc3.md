Based on my investigation, I found a genuine, locally-provable integrity gap in the replay-verification path.

### Title
Replay-verify comparator never validates `state_checkpoint_hash`/`hot_state_checkpoint_hash` against archived `TransactionInfo`, masking state-root divergence during audits - (File: types/src/transaction/mod.rs)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the authoritative comparator used by mainnet-facing replay/audit tooling (`storage/db-tool/src/replay_on_archive.rs`, `execution/executor/src/chunk_executor/mod.rs::verify_execution`, `aptos-move/aptos-debugger`, `aptos-move/cli`) to confirm that a freshly re-executed `TransactionOutput` matches the `TransactionInfo` that was actually committed to the ledger accumulator. It checks status, gas, write-set hash, and event root hash, but never checks `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — the very fields that authenticate the post-transaction global state Merkle root.

### Finding Description [1](#0-0) 

The function validates:
- execution status
- gas used
- write-set hash vs `txn_info.state_change_hash()`
- event root hash vs `txn_info.event_root_hash()`

but ends with only a comment acknowledging the gap: [2](#0-1) 

`state_checkpoint_hash` is the root of the Sparse/Jellyfish Merkle Tree summarizing the entire world state at that transaction, and it is a field of `TransactionInfoV0`/`TransactionInfoV1` that gets hashed into the transaction accumulator leaf and ultimately into the `LedgerInfo` root signed by validators: [3](#0-2) 

This comparator is the sole check used by `storage/db-tool/src/replay_on_archive.rs`'s `Verifier`, which is the tool operators run against archived mainnet data to confirm that re-executing history reproduces the exact committed ledger state: [4](#0-3) 

It is also used inside the chunk executor's `verify_execution` path, invoked during backup/state-sync replay verification: [5](#0-4) 

Because none of the checkpoint-hash fields are compared, if a bug in the executor, storage-commit path, or JMT computation caused the freshly computed state root to diverge from the state root that was originally accepted and hashed into a signed `LedgerInfo`, none of these replay/verify tools would detect it — they would report success purely based on write-set/event/gas/status equality, silently masking a real state-commitment divergence.

### Impact Explanation
Replay-verify tooling is the primary mechanism operators and auditors use to detect state-integrity or hard-fork-only divergences after the fact (e.g., a storage/executor bug that corrupts the JMT root while still producing byte-identical write sets and events, or where an already-buggy `state_change_hash`/root computation slips through unnoticed). Since `state_checkpoint_hash` is never compared, this class of divergence — the most security-critical class the tool exists to catch — passes silently as "verified," giving false assurance about mainnet ledger integrity to anyone using `replay_on_archive` or `verify_execution` for auditing.

### Likelihood Explanation
This is not a gated/future feature: `state_checkpoint_hash` has existed on `TransactionInfoV0` since genesis, and the comparator omission applies unconditionally — it's not limited to the newer `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/`HOT_STATE_ROOT_IN_TXN_INFO` flags mentioned in the TODO. Any state-root-affecting divergence introduced by a future storage/executor bug would go undetected by this specific tool today, with no attacker action needed — it is a latent gap in the verification logic itself, self-acknowledged by the code's own TODO comment.

### Recommendation
Extend `ensure_match_transaction_info` to also compare the computed post-transaction state checkpoint hash (and, where applicable, hot-state / position-state checkpoint hashes) against `txn_info.state_checkpoint_hash()` (and the V1 equivalents), when a computed value is available to the caller. At minimum, `replay_on_archive` and `chunk_executor::verify_execution` should independently recompute and check the state root against the archived `TransactionInfo` before reporting a chunk/transaction as successfully verified.

### Proof of Concept
Not applicable as a live exploit — this is a verification-logic gap, not an executable exploit path. It can be demonstrated by unit test: construct a `TransactionOutput` whose write set/events match an archived `TransactionInfo` but whose corresponding recomputed state root differs, and observe `ensure_match_transaction_info` returns `Ok(())`.

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

**File:** types/src/transaction/mod.rs (L2405-2416)
```rust
    /// The hash value summarizing all changes caused to the world state by this transaction.
    /// i.e. hash of the output write set.
    state_change_hash: HashValue,

    /// The root hash of the Sparse Merkle Tree describing the world state at the end of this
    /// transaction. Depending on the protocol configuration, this can be generated periodical
    /// only, like per block.
    state_checkpoint_hash: Option<HashValue>,

    /// The hash value summarizing PersistedAuxiliaryInfo.
    auxiliary_info_hash: Option<HashValue>,
}
```

**File:** storage/db-tool/src/replay_on_archive.rs (L285-293)
```rust
                    let failed_txn_opt = self.execute_and_verify(
                        &executor,
                        &mut chunk_start_version,
                        &mut cur_txns,
                        &mut cur_persisted_aux_info,
                        &mut expected_txn_infos,
                        &mut expected_events,
                        &mut expected_writesets,
                    )?;
```

**File:** execution/executor/src/chunk_executor/mod.rs (L685-706)
```rust
        for (version, txn_out, txn_info, write_set, events) in multizip((
            begin_version..end_version,
            &execution_output.to_commit.transaction_outputs,
            transaction_infos.iter(),
            write_sets.iter(),
            event_vecs.iter(),
        )) {
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
        }
```
