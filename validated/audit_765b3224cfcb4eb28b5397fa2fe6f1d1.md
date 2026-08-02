## Finding: replay-verify's `ensure_match_transaction_info` never validates the state-checkpoint / hot-state / position-state roots against the authenticated ledger

### Title
Replay-verify accepts a locally computed state root that diverges from the authenticated on-chain state root - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the sole gate used by db-tool's `replay_on_archive` (and the chunk executor / CLI callers) to decide whether a locally re-executed transaction output matches the trusted, backup-derived `TransactionInfo` that is itself proven against a signed `LedgerInfo`. The comparator checks status, gas, write-set hash, and event-root hash, but explicitly skips `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` — the very fields that bind a transaction's execution to the authenticated Sparse-Merkle/Jellyfish state root committed in the accumulator. This is a direct structural analog of the original report: a "did the trade settle correctly" check that verifies one asset (ETH) while silently ignoring another (WETH) that also moved. Here, replay-verify verifies "did the transaction execute correctly" by checking write-set/event hashes while silently ignoring whether the resulting **state root** matches the canonical one.

### Finding Description
`TransactionOutput::ensure_match_transaction_info` at [1](#0-0)  performs these checks against an authenticated `TransactionInfo`:
- status equality
- gas_used equality
- `write_set` hash equality against `txn_info.state_change_hash()`
- event root hash equality against `txn_info.event_root_hash()`

It then contains an explicit, unresolved TODO: [2](#0-1) 
which states the comparator "ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`), so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution."

This function is called from `storage/db-tool/src/replay_on_archive.rs::execute_and_verify`, the core loop that re-executes historical transactions from a backup archive and compares the freshly computed `TransactionOutput` against the expected, backup-provided `TransactionInfo` (which itself is proven against the accumulator/`LedgerInfo`): [3](#0-2) 

Because `TransactionInfo` (both V0 and V1) carries `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` as fields that are hashed into the accumulator leaf [4](#0-3) , these fields are exactly the authenticated proof-bearing state-root binding for a version. Skipping them in the equality check means a divergence between the locally-computed state root (the actual result of applying the write set to the JMT/SMT) and the canonical, backup/ledger-proven state root will not cause a comparator failure — only a write-set-hash mismatch or an event mismatch would be caught, and those are computed straight from the raw output, not from the merklized state.

### Impact Explanation
Replay-verify is Aptos's primary tool to detect state divergence between different execution engines/protocol versions (and is explicitly referenced for "trading-native"/`COMPUTE_TRADING_NATIVE_STATE_ROOTS` state-root computation work). A state-root bug — e.g., in JMT construction, hot-state snapshotting, or the new "position state" tree — that produces a wrong root but a correct write set/event set would pass `ensure_match_transaction_info` and be reported as a successful replay. This directly violates the required invariant that "accumulators, Jellyfish Merkle structures, versioned state views ... must preserve deterministic proof binding" and that "authenticated API and proof-bearing responses must stay bound to the right ledger version, root, and object." A silent state-root divergence undetected by the verification tooling used to certify historical replay correctness is a high-severity proof-integrity gap: it can mask a hard-fork-class bug in state computation that would otherwise corrupt state-proof serving or client-side state verification.

### Likelihood Explanation
The gap is not a corner case — it's unconditional and by design (per the TODO), triggering on every call to `ensure_match_transaction_info` regardless of feature flags. Any bug that changes state-tree computation (state or hot-state checkpoint hashing, or the newer `position_state_checkpoint_hash` mechanism) without altering the write-set/event hashes will systematically evade detection by this tool. The TODO itself acknowledges the gap exists specifically to unblock enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, meaning the check is currently known-incomplete in code that is being relied upon for verifying state-root computation changes.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` between the locally computed `TransactionOutput`-derived checkpoint hash(es) and the expected `txn_info`, at least whenever the transaction is a state-checkpoint boundary (consistent with how `state_checkpoint_hashes` are computed in `do_ledger_update.rs`'s `assemble_transaction_infos`, see [5](#0-4) ). This must be done before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS` in production, as the TODO itself warns.

### Proof of Concept
1. Take a backup/archive of a segment of the chain and run `db-tool replay-on-archive`, which drives `Verifier::execute_and_verify` in `storage/db-tool/src/replay_on_archive.rs`.
2. Introduce (hypothetically, as would happen with a real state-root computation bug) a change to state/hot-state/position-state root derivation that leaves the write set and events byte-identical but produces a different Merkle root for the resulting state (e.g., a bug in hot-state snapshot inclusion logic).
3. `execute_and_verify` calls `executed_outputs[idx].ensure_match_transaction_info(version, &expected_txn_infos[idx], ...)` [6](#0-5) .
4. Because the comparator only checks `status`, `gas_used`, `write_set` hash, and `event_root_hash` [7](#0-6) , the call returns `Ok(())` despite the state root diverging from the one proven by the ledger info/accumulator, and the tool reports the chunk as successfully replayed.

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
