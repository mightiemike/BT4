## Title
`ensure_match_transaction_info` never validates `state_checkpoint_hash`, letting replay-verify accept a wrong state root - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function `db-tool`'s `replay_on_archive` uses to confirm that re-executing an archived transaction produces the same result recorded in the backup's `TransactionInfo`. The function checks status, gas used, write-set hash and event-root hash, but never compares the freshly-computed state (Sparse-Merkle) checkpoint root against `txn_info.state_checkpoint_hash()`. A divergence in state-tree computation between the executing node and the version originally committed to the accumulator will not be flagged as a replay failure.

### Finding Description
`ensure_match_transaction_info` is documented as the check used to confirm a `TransactionOutput` produced by local re-execution matches an authenticated `TransactionInfo`: [1](#0-0) 

It checks `status`, then `gas_used`: [2](#0-1) 

then `write_set_hash` (`state_change_hash`) and `event_root_hash`: [3](#0-2) 

and finally returns `Ok(())` with an explicit acknowledgement that the state-checkpoint hashes are never compared: [4](#0-3) 

`TransactionInfo::state_checkpoint_hash` is the root hash of the Sparse Merkle Tree describing world state at the end of a (checkpoint) transaction — this is precisely the value that `SparseMerkleProof`/state-proof verification, restore, and state-sync all bind to as the authoritative committed state root: [5](#0-4) 

This comparator is invoked from `db-tool`'s `replay_on_archive.rs::execute_and_verify`, which re-executes archived transactions with the local VM and asserts the result matches the backup's expected `TransactionInfo`, treating success as proof the archive replays cleanly against the executing binary: [6](#0-5) 

Since `ensure_match_transaction_info` skips `state_checkpoint_hash` entirely (not just the hot-state/position variants gated behind `COMPUTE_TRADING_NATIVE_STATE_ROOTS`), any divergence in the main global state root computed by the local node versus the state root committed on-chain and captured in the backup's `TransactionInfo`/accumulator proof will pass replay-verify silently.

### Impact Explanation
This breaks the "committed state matches VM result" and "hard-fork divergence during replay" invariants explicitly listed as in-scope. `replay_on_archive`/replay-verify is the tool operators and auditors rely on to detect state-computation bugs, hard forks, or an intentionally/accidentally corrupted archive before trusting a rebuilt/restored database. Because the comparator never checks `state_checkpoint_hash`, a bug in state-tree construction (or a maliciously/accidentally altered archived `TransactionInfo.state_checkpoint_hash`, which the tool otherwise treats as ground truth) can go completely undetected, letting an incorrect state root be accepted as valid across a full replay run.

### Likelihood Explanation
This triggers deterministically any time `replay_on_archive`/replay-verify is run against data whose `state_checkpoint_hash` differs from what local execution computes (e.g., a state-computation regression, a JMT/state-store bug, or corrupted backup data) — no attacker action is needed on a properly functioning node, but the gap means such divergences are never surfaced by this verification path.

### Recommendation
In `ensure_match_transaction_info`, compute the state summary root produced by the block executor for each checkpoint transaction and compare it against `txn_info.state_checkpoint_hash()` (and the hot-state/position variants once those features are enabled), failing the check on mismatch just as is already done for `write_set_hash` and `event_root_hash`.

### Proof of Concept
Not applicable as a runtime exploit — the issue is a missing verification step. It can be demonstrated by feeding `replay_on_archive` a `TransactionInfo` whose `state_checkpoint_hash` has been altered (or by executing against a build with a deliberately introduced state-root computation bug) and observing that `execute_and_verify` reports success because `ensure_match_transaction_info` at [4](#0-3)  never inspects the checkpoint hash field.

### Citations

**File:** types/src/transaction/mod.rs (L2139-2157)
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
```

**File:** types/src/transaction/mod.rs (L2159-2166)
```rust
        ensure!(
            self.gas_used() == txn_info.gas_used(),
            "{}: version:{}, gas_used:{:?}, expected:{:?}",
            ERR_MSG,
            version,
            self.gas_used(),
            txn_info.gas_used(),
        );
```

**File:** types/src/transaction/mod.rs (L2168-2195)
```rust
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

**File:** types/src/transaction/mod.rs (L2405-2412)
```rust
    /// The hash value summarizing all changes caused to the world state by this transaction.
    /// i.e. hash of the output write set.
    state_change_hash: HashValue,

    /// The root hash of the Sparse Merkle Tree describing the world state at the end of this
    /// transaction. Depending on the protocol configuration, this can be generated periodical
    /// only, like per block.
    state_checkpoint_hash: Option<HashValue>,
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
