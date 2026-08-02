### Title
`ensure_match_transaction_info` omits state/hot-state/position checkpoint hash checks, letting replay-verify and CLI transaction-replay tooling accept a divergent authenticated state root - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` is the integrity gate used by chunk-executor verification, `db-tool replay_on_archive`, the CLI transaction simulator, and `aptos-debugger` to confirm that a locally re-executed `TransactionOutput` matches the authenticated `TransactionInfo` recorded in the accumulator/backup. The function checks status, gas, write-set hash (`state_change_hash`) and event root hash, but explicitly skips `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` — the fields that actually bind a transaction to the resulting Merkle/JMT state root.

### Finding Description
`ensure_match_transaction_info` at [1](#0-0)  verifies status, gas, `write_set` hash and `event_root_hash`, but its own inline TODO documents the gap explicitly: [2](#0-1) 

This means the function never compares `txn_info.state_checkpoint_hash()`, `txn_info.hot_state_checkpoint_hash()`, or `txn_info.position_state_checkpoint_hash()` against anything computed from the replayed execution. Those fields are the only place where the derived JMT/state-summary root (as opposed to the per-transaction write set) is authenticated inside `TransactionInfo`. A write set hash match only proves that the *raw writes* produced by the VM match what was recorded — it says nothing about whether applying those writes onto the persisted state tree yields the same root that was authenticated on-chain.

This function is used as the sole per-transaction correctness oracle in several tools that are trusted to catch state divergence:
- `db-tool`'s `replay_on_archive::Verifier::execute_and_verify`, which re-executes transactions from an archive and calls it per transaction: [3](#0-2) 
- The chunk executor's execution verification path in state-sync replay: [4](#0-3) 
- The CLI's local transaction replay/simulate command: [5](#0-4) 
- `aptos-debugger`'s mismatch reporting: [6](#0-5) 

Because none of these call sites independently re-derive and compare the state-checkpoint hash, a discrepancy between the locally-computed state root (post-write-set application) and the authenticated `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` in the backed-up `TransactionInfo` would be silently accepted as "verified" by every one of these tools.

### Impact Explanation
This is scoped to the "authenticated API and state-view output... proof context" and "replay paths ... must not reinterpret committed data into a different ledger state" invariant classes called out in the task. If write-set-level hashing and actual JMT state-root computation diverge — e.g., through a bug elsewhere in state-checkpoint construction, hot-state promotion bookkeeping, or the sharded position-state feature (`compute_trading_native_state_roots`, still gated per the TODO) — `replay_on_archive` and CLI/debugger replay would report a clean, matching replay even though the actual state root used to authenticate the chain diverges from what local execution produces. This directly undermines the guarantee that replay/verify tooling is a trustworthy detector of state-commitment corruption: operators and auditors relying on `replay_on_archive` to confirm archive/backup integrity, or on `aptos-debugger`/CLI replay to confirm a transaction's effects, could be given false assurance that the state is correct when the state root has silently diverged.

Note the impact is bounded by the fact that this is a *verification-tooling* gap, not itself a live consensus/commit-path bug: it does not directly let an attacker corrupt what full nodes commit or accept in the live BFT commit path (that path is protected by validator signatures over the whole `LedgerInfo`, and consensus is out of scope per this exercise's exclusions). Its severity is realized specifically in the backup/restore/replay-verification pipeline, which is explicitly a proof-and-storage pivot in scope.

### Likelihood Explanation
The gap is unconditional in the current code — it applies today, not only once `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled, since `state_checkpoint_hash`/`hot_state_checkpoint_hash` (independent of the trading-native/position work) are also skipped, and the developer's own comment acknowledges "replay-verify tooling ... can report a successful replay even when the authenticated ... state root diverges from local execution" as a present, known risk, not merely a hypothetical for the future feature. The precondition for actual mainnet impact is a separate root-cause divergence in state-checkpoint hash construction; I could not confirm such a divergence exists elsewhere in this codebase within the scope of this investigation, so the "detector gap" itself is confirmed but a concrete triggering bug elsewhere was not independently proven here.

### Recommendation
Extend `ensure_match_transaction_info` to accept (or internally recompute, when available) the state-checkpoint / hot-state-checkpoint / position-state-checkpoint hash for the version being verified, and assert equality against `txn_info.state_checkpoint_hash()`, `txn_info.hot_state_checkpoint_hash()`, and `txn_info.position_state_checkpoint_hash()` whenever they are `Some(..)` on either side, consistent with the pattern already used for `state_change_hash`/`event_root_hash`. Do this before (not conditioned on) enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, since the state/hot-state checkpoint hash gap already exists independently of that feature flag.

### Proof of Concept
Not applicable as an exploit PoC — this is a detection-gap finding, not an exploitable state-corruption primitive by itself. The code-level demonstration is the omission itself: compare the fields checked in `ensure_match_transaction_info` (status, gas, `state_change_hash`, `event_root_hash`) [7](#0-6)  against the full set of authenticated fields on `TransactionInfoV1` (`state_checkpoint_hash`, `hot_state_checkpoint_hash`, `position_state_checkpoint_hash`) [8](#0-7) , and the explicit acknowledgement of the resulting replay-verify blind spot in the surrounding comment [2](#0-1) .

### Citations

**File:** types/src/transaction/mod.rs (L2139-2196)
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

**File:** execution/executor/src/chunk_executor/mod.rs (L692-705)
```rust
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
```

**File:** aptos-move/cli/src/commands.rs (L2809-2813)
```rust
        if !skip_comparison {
            txn_output
                .ensure_match_transaction_info(self.txn_id, &txn_info, None, None)
                .map_err(|msg| CliError::UnexpectedError(msg.to_string()))?;
        }
```

**File:** aptos-move/aptos-debugger/src/aptos_debugger.rs (L233-246)
```rust
    fn print_mismatches(
        txn_outputs: &[TransactionOutput],
        expected_txn_infos: &[TransactionInfo],
        first_version: Version,
    ) {
        for idx in 0..txn_outputs.len() {
            let txn_output = &txn_outputs[idx];
            let txn_info = &expected_txn_infos[idx];
            let version = first_version + idx as Version;
            txn_output
                .ensure_match_transaction_info(version, txn_info, None, None)
                .unwrap_or_else(|err| println!("{}", err))
        }
    }
```
