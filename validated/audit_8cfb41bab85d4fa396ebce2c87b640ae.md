Based on my investigation, I found a genuine, code-confirmed integrity gap in Aptos's replay-verification path — the exact same bug class as the PoolTogether report (a "success" indicator emitted even though part of the verification silently failed to check a value).

### Title
`replay_on_archive` reports successful replay verification while skipping checkpoint/position state-root validation - (File: `types/src/transaction/mod.rs`, `storage/db-tool/src/replay_on_archive.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info()` is the sole per-transaction correctness check used by the `replay-verify` tool (`storage/db-tool/src/replay_on_archive.rs`) to confirm that locally re-executed transactions match the authenticated on-chain `TransactionInfo`. The function's own doc comment admits it deliberately skips validating `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash`, yet the tool treats the absence of an error as full verification success — analogous to `AwardedExternalERC721` being emitted for tokenIds that actually failed.

### Finding Description
`TransactionOutput::ensure_match_transaction_info` validates status, gas used, write-set hash, event root hash, and transaction hash, but explicitly does **not** validate the state-checkpoint-related hashes: [1](#0-0) 

The comment is unambiguous about the consequence: [2](#0-1) 

`storage/db-tool/src/replay_on_archive.rs`'s `execute_and_verify` uses exactly this function, and only this function, as its pass/fail gate per chunk of replayed transactions — there is no independent accumulator-root or ledger-info comparison performed anywhere in the verifier: [3](#0-2) 

If `execute_and_verify` returns `Ok(None)` (no error), the calling loop clears the buffers and proceeds silently, and the outer `verify`/`run` functions report the range as successfully replayed: [4](#0-3) [5](#0-4) 

This is the direct analog of the PoolTogether bug: a per-item integrity check that can silently fail to catch a real divergence (there, a failed token transfer; here, a diverged state/position checkpoint root), while the aggregate machinery reports full success regardless.

### Impact Explanation
`state_checkpoint_hash` and `position_state_checkpoint_hash` are exactly the fields committed into `TransactionInfo` and folded into the transaction accumulator root that the network's consensus/ledger-info signs — i.e., the authenticated state commitment. `TransactionInfoV1` carries these fields for the newer "trading-native" state-root scheme: [6](#0-5) 

Since `ensure_match_transaction_info` never compares these hashes, `replay_on_archive` (a tool relied upon to attest that a downloaded/backed-up ledger segment is bit-for-bit correct, e.g. for archival nodes, backup verification, or governance/security audits of historical state) can report a clean replay even when the locally recomputed state/position checkpoint root diverges from the authenticated on-chain value. This is a direct violation of the "Proof And Storage Pivots" requirement that "authenticated API and proof-bearing responses must stay bound to the right ledger version, root, and object" — a verification tool built for exactly that purpose fails to bind on one of the roots it is meant to certify.

### Likelihood Explanation
This triggers deterministically whenever `COMPUTE_TRADING_NATIVE_STATE_ROOTS` (or equivalent hot-state/position-checkpoint feature paths) is enabled and any code path — a future bug in `DoStateCheckpoint`, a storage/replay bug, a schema migration issue — produces a different checkpoint/position hash than what was originally committed. No attacker action nor privileged access is required to trigger the underlying condition; the gap is a standing code defect that will fail to alarm anyone relying on `replay_on_archive` for archival correctness the moment such a divergence exists. The bug is already flagged as a known-but-unfixed gap by the maintainers' own `TODO` comment in the code.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` against the expected `TransactionInfo` (recomputing them from local execution the way `state_change_hash`/`event_root_hash` are already validated), and make `replay_on_archive` fail loudly on any mismatch instead of only checking the fields currently covered.

### Proof of Concept
1. Enable the on-chain feature path that populates `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` on `TransactionInfoV1` (`COMPUTE_TRADING_NATIVE_STATE_ROOTS`, `hot_state_root_in_txn_info`).
2. Introduce (or rely on an existing) divergence between locally computed checkpoint/position state roots and the persisted `TransactionInfo` for a given version (e.g., via a bug in `DoStateCheckpoint`/backup restore).
3. Run `storage/db-tool/src/replay_on_archive.rs`'s `Verifier::verify` over that version range.
4. Because `execute_and_verify` at [7](#0-6)  only calls `ensure_match_transaction_info`, which skips the checkpoint hashes, the call returns `Ok(None)` and the tool reports the range as fully verified, even though the authenticated state/position root diverges from what was actually executed locally.

### Citations

**File:** types/src/transaction/mod.rs (L2196-2204)
```rust

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

**File:** storage/db-tool/src/replay_on_archive.rs (L212-240)
```rust
    pub fn run(self) -> Result<Vec<Error>> {
        if self.limit == 0 {
            info!("Nothing to verify.");
            return Ok(vec![]);
        }

        AptosVM::set_concurrency_level_once(self.replay_concurrency_level);
        let thread_pool = rayon::ThreadPoolBuilder::new()
            .num_threads(self.concurrent_replay)
            .thread_name(|i| format!("replay-verify-{}", i))
            .build()?;
        let chunk_size = self.chunk_size as u64;
        let total_chunks = self.limit.div_ceil(chunk_size);
        let res: Vec<_> = thread_pool.install(|| {
            (0..total_chunks)
                .into_par_iter()
                .map(|i| {
                    let start = self.start + i * chunk_size;
                    let end = std::cmp::min(start + chunk_size - 1, self.start + self.limit - 1);
                    self.verify(start, end - start + 1)
                })
                .collect()
        });
        let mut all_failed_txns = Vec::new();
        for iter in res.into_iter() {
            all_failed_txns.extend(iter?);
        }
        Ok(all_failed_txns)
    }
```

**File:** storage/db-tool/src/replay_on_archive.rs (L301-313)
```rust
        // verify results
        let fail_txns = self.execute_and_verify(
            &executor,
            &mut chunk_start_version,
            &mut cur_txns,
            &mut cur_persisted_aux_info,
            &mut expected_txn_infos,
            &mut expected_events,
            &mut expected_writesets,
        )?;
        total_failed_txns.extend(fail_txns);
        Ok(total_failed_txns)
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
