### Title
`ensure_match_transaction_info` silently skips checkpoint-hash validation, defeating replay-verify's state-root divergence detection - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the sole per-transaction correctness gate used by replay-verify tooling (`storage/db-tool/src/replay_on_archive.rs`) to confirm that locally re-executed transaction outputs match the authenticated `TransactionInfo` pulled from a backup/archive (which itself is proof-verified against a signed `LedgerInfo`). The function checks status, gas used, write-set hash (`state_change_hash`), and event root hash, but — by its own acknowledged TODO — never compares `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`. [1](#0-0) 

### Finding Description
The function is documented to validate that a `TransactionOutput` matches a `TransactionInfo`, and is invoked with `expected_write_set`/`expected_events` parameters that suggest full-fidelity comparison: [2](#0-1) 

It checks status, gas, write-set hash, and event root hash, then contains an explicit acknowledgment that checkpoint hashes are excluded: [3](#0-2) 

This is the exact call site in the replay-verify tool, where `ensure_match_transaction_info` is the only correctness check performed per transaction against the archived `TransactionInfo` (`expected_txn_infos[idx]`), and a mismatch causes the tool to record it as a verification failure: [4](#0-3) 

Because `state_checkpoint_hash` (the Sparse/Jellyfish Merkle root of world state), `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` are never compared here, a locally re-executed chunk whose resulting state root diverges from the historical, ledger-info-authenticated root will still pass `ensure_match_transaction_info` as long as gas, status, write-set bytes hash, and events match. This is structurally analogous to the Yeti finding: a value that should gate acceptance (the state root/checkpoint hash) is silently excluded from the check, so the "amount" that matters (state-root correctness) is effectively unchecked while other correlated-but-insufficient values (write-set hash) are checked instead.

### Impact Explanation
Replay-verify is the network's principal tool for catching state-computation divergence (i.e., non-determinism or a VM/storage bug that would fork the chain) before or after mainnet upgrades, by replaying historical transactions and confirming outputs against the already-finalized, signature-authenticated ledger. Because the state-root fields are excluded from the comparator, a bug that corrupts state-tree computation (Jellyfish Merkle root, hot-state root, or the newer position/trading-native state root) without altering the write-set bytes hash, gas usage, status, or events, will not be flagged by this tool. This falls squarely in the "Hard-fork-only divergence during commit, replay, restore, or proof verification" and "Wrong accumulator root ... state proof accepted as valid" categories: the state proof/root divergence is effectively "accepted as valid" replay output because the verification path never inspects it.

### Likelihood Explanation
The gap is unconditional and always present when this comparator runs (independent of the `compute_trading_native_state_roots` / `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature gate) — it doesn't even check the baseline `state_checkpoint_hash` that exists on every `TransactionInfoV0`/`V1`. It requires no attacker action; it's an omission that manifests whenever an actual state-root-affecting bug exists elsewhere in the system, at which point this safety net fails to catch it. The comment in the code itself acknowledges the gap ("Validate the checkpoint hashes here before enabling COMPUTE_TRADING_NATIVE_STATE_ROOTS"), confirming it is a known, unaddressed TODO rather than a hypothetical.

### Recommendation
Extend `ensure_match_transaction_info` to compare `self`'s (or the caller's) computed `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` against the corresponding fields on `txn_info`, erroring out on mismatch exactly as done for `state_change_hash` and `event_root_hash`. Since `TransactionOutput` itself doesn't carry a computed state checkpoint hash (that's computed at the chunk/block level in `do_state_checkpoint.rs`), the check should be threaded through the replay-verify chunk pipeline so state-checkpoint hashes are validated per checkpoint boundary, not skipped.

### Proof of Concept
1. Assume a (hypothetical) bug in state-tree materialization that changes the computed Jellyfish/Sparse Merkle root for a block without changing any individual write op's serialized bytes hash (e.g., an ordering, hashing-scheme, or hot-state accounting bug in the checkpoint path).
2. Run `aptos-db-tool replay-on-archive` (`storage/db-tool/src/replay_on_archive.rs`) against archived mainnet history containing the affected block.
3. In `execute_and_verify`, the block is re-executed and `ensure_match_transaction_info` is invoked per transaction output.
4. Since `state_change_hash` (write-set hash) and events are unaffected, and status/gas match, the check at [5](#0-4)  passes and no divergence is reported. [6](#0-5) 
5. The operator concludes replay-verify passed, while the actual state root diverged — exactly the scenario the tool exists to prevent.

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
