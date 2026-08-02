### Title
`replay_on_archive` verification silently ignores position/hot-state checkpoint hash divergence - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the authenticated-replay check used by `db-tool replay-on-archive` and other replay/debugger tooling to confirm that re-executed VM output matches the ledger's committed `TransactionInfo`. The function's own comment admits it "ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`)," meaning replay can report success even though the committed state/position checkpoint root diverges from what local execution produces.

### Finding Description
`ensure_match_transaction_info` compares status, gas used, write-set hash (`state_change_hash`), and event root hash against the target `TransactionInfo`, but explicitly skips the state-checkpoint hash, hot-state checkpoint hash, and `position_state_checkpoint_hash` fields: [1](#0-0) 

These skipped hashes are exactly the fields that get folded into the transaction-accumulator leaf (`TransactionInfo::hash()`) once `TRANSACTION_INFO_V1`, `HOT_STATE_ROOT_IN_TXN_INFO`, or `COMPUTE_TRADING_NATIVE_STATE_ROOTS` are enabled — i.e., the values that are supposed to be consensus-verified and durably committed to the ledger's Merkle accumulator: [2](#0-1) [3](#0-2) 

`storage/db-tool/src/replay_on_archive.rs` is the concrete caller: it re-executes archived transactions with the VM and calls `ensure_match_transaction_info` as the sole correctness gate before declaring a chunk verified: [4](#0-3) 

Because the comparator never checks `state_checkpoint_hash()`/`hot_state_checkpoint_hash()`/`position_state_checkpoint_hash()`, a bug in `DoStateCheckpoint`'s position-state or hot-state root computation (or corruption of the persisted state-summary tree feeding it) would not be caught by `replay-on-archive`, even though that same wrong root was accumulated into the ledger's `TransactionAccumulator` at commit time via `commit_transaction_accumulator`: [5](#0-4) 

The code comment in `ensure_match_transaction_info` itself flags this as a known gap ("Validate the checkpoint hashes here before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`"), confirming the root cause is a real, currently-shipped incompleteness rather than a designed limitation: [6](#0-5) 

### Impact Explanation
This breaks the "committed state must survive executor-to-storage handoff unchanged, and be verifiable via replay" invariant for the state/hot-state/position checkpoint roots. If those roots are wrong on-chain (due to any bug in `DoStateCheckpoint`/`compute_position_checkpoint`/state-summary maintenance), `replay-on-archive` — the tool operators and auditors rely on to independently confirm historical ledger correctness — will report the chunk as verified even though the authenticated accumulator leaf encodes an incorrect state root. This masks state-commitment corruption from the primary detection mechanism, undermining confidence in archival integrity checks and potentially delaying discovery of a consensus-affecting bug until it manifests elsewhere (e.g. state proofs failing for light clients, or nodes diverging on JMT roots).

### Likelihood Explanation
The condition requiring exploitation is only latent today because `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/`HOT_STATE_ROOT_IN_TXN_INFO` gating and `TRANSACTION_INFO_V1` are relatively new on-chain features; once they are enabled on any network (mainnet or otherwise) and any bug exists in position/hot-state root computation, this gap silently defeats the safety net that `replay-on-archive` is meant to provide. No malicious actor is needed — it's a coverage gap in an authenticated-verification code path, matching the "hard-fork-only divergence during commit, replay ... verification" and "proof-bearing responses must stay bound to the right root" criteria.

### Recommendation
Extend `ensure_match_transaction_info` to compare `state_checkpoint_hash()`, `hot_state_checkpoint_hash()` (when `HOT_STATE_ROOT_IN_TXN_INFO` is active), and `position_state_checkpoint_hash()` (when `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is active) against locally recomputed values before enabling those features broadly, exactly as the existing TODO comment recommends.

### Proof of Concept
Not independently reproducible as an exploit — this is a verification-completeness gap, not a state-corruption trigger by itself. To demonstrate: introduce (in a test harness) a bogus `position_state_checkpoint_hash` in a `TransactionInfo` committed to a test `AptosDB`, then run `replay_on_archive::Verifier::verify` over that version with `COMPUTE_TRADING_NATIVE_STATE_ROOTS` enabled and correct write-set/events; `execute_and_verify` will call `ensure_match_transaction_info` [7](#0-6)  and return `Ok` despite the corrupted checkpoint hash, because the comparator never inspects it [8](#0-7) .

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

**File:** execution/executor/src/workflow/do_ledger_update.rs (L30-45)
```rust
        // Assemble `TransactionInfo`s. The variant (V0 vs V1) is driven by the
        // `TRANSACTION_INFO_V1` on-chain feature, threaded via
        // `ExecutionOutput::transaction_info_v1`. The hot state root hash a V1 carries is
        // present only when `HOT_STATE_ROOT_IN_TXN_INFO` is also on (`DoStateCheckpoint`
        // produces `Some` hashes iff so); otherwise the V1 leaves it `None`.
        let (transaction_infos, transaction_info_hashes) = Self::assemble_transaction_infos(
            &execution_output.to_commit,
            execution_output.transaction_info_v1,
            &state_checkpoint_output.state_checkpoint_hashes,
            state_checkpoint_output
                .hot_state_checkpoint_hashes
                .as_deref(),
            state_checkpoint_output
                .position_state_checkpoint_hashes
                .as_deref(),
        );
```

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L42-75)
```rust
        let last_checkpoint = state_summary.last_checkpoint();

        let state_checkpoint_hashes = Self::get_state_checkpoint_hashes(
            execution_output,
            known_state_checkpoints,
            last_checkpoint.root_hash(),
            "state",
        )?;
        let hot_state_checkpoint_hashes = execution_output
            .hot_state_root_in_txn_info
            .then(|| {
                Self::get_state_checkpoint_hashes(
                    execution_output,
                    known_hot_state_checkpoints,
                    last_checkpoint.hot_root_hash()?,
                    "hot_state",
                )
            })
            .transpose()?;

        let (position_state_summary, position_state_checkpoint_hashes) =
            if execution_output.compute_trading_native_state_roots {
                let persisted = persisted_position_state_summary
                    .expect("persisted position summary required when feature on");
                let (summary, hashes) = Self::compute_position_checkpoint(
                    execution_output,
                    parent_position_state_summary,
                    persisted,
                    known_position_state_checkpoints,
                )?;
                (Some(summary), Some(hashes))
            } else {
                (None, None)
            };
```

**File:** storage/db-tool/src/replay_on_archive.rs (L392-405)
```rust
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
```

**File:** storage/aptosdb/src/db/aptosdb_writer.rs (L330-334)
```rust
            s.spawn(|_| {
                new_root_hash = self
                    .commit_transaction_accumulator(chunk.first_version, chunk.transaction_infos)
                    .unwrap()
            });
```
