## Finding: Replay-verification comparator skips state-checkpoint / position-state root fields

### Title
`ensure_match_transaction_info` never verifies `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`, letting replay tooling accept a diverged state root - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info`, the function used by replay/debug tooling to confirm that a locally re-executed transaction matches the authenticated on-chain `TransactionInfo`, only compares `status`, `gas_used`, the write-set hash against `state_change_hash`, and the event root hash. It never compares the computed Merkle **state root** (`state_checkpoint_hash`), the hot-state root, or the new trading-native `position_state_checkpoint_hash` fields carried in `TransactionInfo` against what local execution/state-checkpoint computation produced.

### Finding Description
`ensure_match_transaction_info` is the authoritative "did my local replay match the chain" check: [1](#0-0) 

It validates `status`, `gas_used`, and `write_set_hash == state_change_hash`, plus the event root hash, but stops there. The function's own trailing comment documents the gap explicitly: [2](#0-1) 

Critically, `write_set_hash` is a hash of the *write set itself* (the intended operations), not of the resulting Jellyfish Merkle root after those operations are applied to a (possibly different) base state. The actual committed/authenticated root that downstream light clients, state proofs, and API responses rely on is `state_checkpoint_hash` (and, for the new trading-native feature, `position_state_checkpoint_hash`), which are computed by `DoStateCheckpoint::run` / `compute_position_checkpoint` from the parent state summary plus the write set: [3](#0-2) 

Neither of these checkpoint hashes is compared against the chain's authenticated `TransactionInfo` in `ensure_match_transaction_info`. This function is used as the correctness gate by `db-tool`'s `replay_on_archive`, `aptos-debugger`, and the CLI: [4](#0-3) 

Because `ensure_match_transaction_info` never checks `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash`, any divergence between the locally computed state root and the authenticated on-chain root — caused by a base-state corruption, a JMT update bug, or the newly introduced trading-native position-state root computation — will **not** be detected by this verification path, even though the write set itself matches byte-for-byte.

This is the direct structural analog of the reported bug class: two values that must be kept in lock-step (here: write-set-derived hash vs. actual state root hash) are silently allowed to diverge because the code that is supposed to enforce their consistency simply omits the check for one side, exactly as `depositAndAllocateForPartyB` silently mismatched native-precision vs. 18-decimal-precision `amount`.

### Impact Explanation
Replay-verify and debugger tooling is the mechanism operators and auditors use to confirm a node (or an alternate execution path, e.g. the sharded executor, or MonoMove) reproduces the exact ledger state committed on mainnet. Because the checkpoint/state-root fields are excluded from the comparison, a state-root divergence — e.g. from a bug in JMT leaf updates, from the trading-native position-state merklization path (`compute_position_checkpoint`), or from base-state corruption during restore — would pass replay verification as "matching," while the durable committed state root actually differs from the correct VM result. This directly violates the required invariant that "committed state that differs from the correct VM result" and "wrong accumulator/state proof accepted as valid" must be caught, and is exactly the kind of hard-fork-only divergence during replay/restore/proof-verification that the gate calls out as in-scope.

### Likelihood Explanation
The gap is unconditional and always present in `ensure_match_transaction_info` — it doesn't require the trading-native feature to be enabled; the state/hot-state checkpoint hash omission exists regardless. The trading-native `position_state_checkpoint_hash` gap is explicitly flagged by the code's own TODO as needing to be closed "before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`," which increases confidence this is a known-but-unfixed hole, not a false positive. The realistic trigger is any state-root-affecting bug elsewhere in the JMT/state-checkpoint pipeline that this verification function is supposed to catch but silently won't.

### Recommendation
Extend `ensure_match_transaction_info` to also compare the locally computed `state_checkpoint_hash`, `hot_state_checkpoint_hash` (when applicable), and `position_state_checkpoint_hash` (when trading-native roots are computed) against the corresponding fields on the authenticated `TransactionInfo`, failing loudly on mismatch, before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` or any other feature relying on this replay-verification path is enabled.

### Proof of Concept
Conceptual (no runnable PoC constructed): supply `ensure_match_transaction_info` a `TransactionOutput` whose `write_set` is byte-identical to the on-chain one (so `state_change_hash` matches) but whose base state (`parent_state`) differs, causing `DoStateCheckpoint::run` to derive a different `state_checkpoint_hash` / `position_state_checkpoint_hash` than the authenticated `TransactionInfo`. `ensure_match_transaction_info` returns `Ok(())` because it never inspects those fields, so `replay_on_archive`/`aptos-debugger` reports a successful, matching replay despite the durable committed state root being wrong. [5](#0-4) [6](#0-5)

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

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L36-83)
```rust
        let state_summary = parent_state_summary.update(
            persisted_state_summary,
            &execution_output.hot_state_updates,
            execution_output.to_commit.state_update_refs(),
        )?;

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

        Ok(StateCheckpointOutput::builder()
            .state_summary(state_summary)
            .state_checkpoint_hashes(state_checkpoint_hashes)
            .maybe_hot_state_checkpoint_hashes(hot_state_checkpoint_hashes)
            .maybe_position_state_summary(position_state_summary)
            .maybe_position_state_checkpoint_hashes(position_state_checkpoint_hashes)
            .build())
```

**File:** storage/db-tool/src/lib.rs (L36-43)
```rust
    ReplayVerify(replay_verify::Opt),

    GenReplayVerifyJobs(gen_replay_verify_jobs::Opt),

    #[clap(subcommand)]
    Restore(restore::Command),

    ReplayOnArchive(replay_on_archive::Opt),
```
