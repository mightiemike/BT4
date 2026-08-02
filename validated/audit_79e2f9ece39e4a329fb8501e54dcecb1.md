## Finding: `ensure_match_transaction_info` never checks the state-checkpoint (JMT) root hash, letting `replay-verify` / `db-tool replay-on-archive` certify a divergent state root as correct

### Title
Replay/archive verification comparator omits state-checkpoint root hash checks, causing state-root divergence to be silently accepted as "verified" - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the sole comparator used by `db-tool replay-on-archive` and the CLI transaction replay tool to certify that locally re-executed transactions reproduce the historical, authenticated ledger state before a new execution/storage binary is trusted. The function checks status, gas, write-set hash, and event-root hash, but does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — the fields that actually commit to the Jellyfish Merkle (state) root. A bug that corrupts state-tree construction (e.g. incorrect key ordering/sharding in the JMT, or in the new hot-state/position-state roots) while leaving the raw write set, events, gas, and status unchanged will pass this "verification" cleanly, giving false confidence that a new execution engine is safe for mainnet.

### Finding Description
`ensure_match_transaction_info` is defined in `types/src/transaction/mod.rs`: [1](#0-0) 

It ensures: matching `TransactionStatus`, matching `gas_used`, matching `state_change_hash` (a hash of the write set only), and matching `event_root_hash`. The trailing comment is explicit about the gap:

```
// TODO(trading-native): this comparator ignores the checkpoint hashes
// (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
// tooling (e.g. db-tool's `replay_on_archive`) can report a successful
// replay even when the authenticated position state root diverges from
// local execution. Validate the checkpoint hashes here before enabling
// COMPUTE_TRADING_NATIVE_STATE_ROOTS.
``` [2](#0-1) 

This comparator is invoked directly by `db-tool`'s archive-replay verifier: [3](#0-2) 

and by the CLI's single-transaction replay/debug command: [4](#0-3) 

Both tools re-execute historical transactions against a candidate VM/storage build and rely exclusively on `ensure_match_transaction_info` to decide "match" vs "mismatch." Since `state_change_hash` is a hash of only the transaction's own write set (`CryptoHash::hash(self.write_set())`, see `state_change_hash` comparison at lines 2168-2178) it cannot detect a bug in how that write set is folded into the durable state tree (the JMT construction, hot-state root, or the newer position/trading-native state root) at commit/state-checkpoint time. The state-checkpoint hash fields on `TransactionInfo` (`state_checkpoint_hash`, `hot_state_checkpoint_hash`, `position_state_checkpoint_hash`) exist precisely to commit to that tree-construction step, but they are never compared.

By contrast, the real block/chunk-execution commit path (`do_state_checkpoint.rs`) *does* validate the computed checkpoint hash against known values when replaying during normal state sync: [5](#0-4) 

so on the live state-sync/consensus path the check exists. The gap is specific to the offline safety-net tooling (`replay-verify`, `db-tool replay-on-archive`, CLI transaction replay) whose entire purpose is to catch exactly this class of state-root divergence *before* new binaries are deployed to mainnet.

### Impact Explanation
`replay-verify`/`db-tool replay-on-archive` is the pre-deployment gate used to confirm a new Aptos node/execution binary reproduces historical mainnet state bit-for-bit. If a change (intentional refactor or an unnoticed bug) alters how the state Merkle tree, hot-state root, or trading-native/position-state root is computed from an otherwise-correct write set, this tool will report a clean pass. The corrupted state-root logic can then be shipped and only manifest as an actual mainnet consensus/state divergence (hard fork) once running live — i.e. the exact "hard-fork-only divergence during commit/replay/restore/proof verification" class called out in scope. This is a high-severity gap in a critical integrity safeguard, even though it does not by itself corrupt live consensus state.

### Likelihood Explanation
The affected code path is not conditional on privileged access — any bug in state-tree assembly (JMT sharding, hot-state, or the newly introduced `position_state_checkpoint_hash`/trading-native state root work) will trigger this exact blind spot. The in-repo TODO comment shows the Aptos team is aware and has explicitly deferred fixing it ("Validate the checkpoint hashes here before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`"), meaning the feature enabling the newest root (position/trading-native state) is expected to ship while this verification gap still exists.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` against locally computed equivalents whenever those fields are present/known (mirroring the check already done in `do_state_checkpoint.rs::get_state_checkpoint_hashes`), before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` (or any state-checkpointing feature) is enabled by default, so that `replay-verify` and `db-tool replay-on-archive` cannot certify a build whose state-tree construction has silently diverged from the authenticated chain.

### Proof of Concept
1. Take a historical transaction whose committed `WriteSet` is `W` and whose canonical `TransactionInfo` has `state_checkpoint_hash = R`.
2. Introduce (or imagine) a state-tree construction bug that, given the same `W`, computes a different checkpoint/state root `R' != R` (e.g., a key-ordering bug in JMT commit, or in the position/trading-native state root logic gated by `compute_trading_native_state_roots`), while keeping `write_set`, `events`, `gas_used`, and `status` identical.
3. Run `db-tool replay-on-archive` (`storage/db-tool/src/replay_on_archive.rs`, `execute_and_verify` at lines 388-405) or the CLI replay command (`aptos-move/cli/src/commands.rs` lines 2809-2813) against this transaction.
4. `ensure_match_transaction_info` only checks `status`, `gas_used`, `write_set` hash, and `event_root_hash` — all of which still match — so the tool reports success despite `R' != R`, i.e. a genuine state-root divergence goes undetected by the safety-net tool designed to catch it.

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

**File:** storage/db-tool/src/replay_on_archive.rs (L388-405)
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
```

**File:** aptos-move/cli/src/commands.rs (L2809-2813)
```rust
        if !skip_comparison {
            txn_output
                .ensure_match_transaction_info(self.txn_id, &txn_info, None, None)
                .map_err(|msg| CliError::UnexpectedError(msg.to_string()))?;
        }
```

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L206-221)
```rust
        if let Some(known) = known_state_checkpoints {
            ensure!(
                known.len() == num_txns,
                "Bad number of known {label} hashes. {} vs {}",
                known.len(),
                num_txns,
            );
            if let Some(idx) = last_checkpoint_index {
                ensure!(
                    known[idx] == Some(computed_last_checkpoint_hash),
                    "{label} root hash mismatch with known hashes passed in. {:?} vs {:?}",
                    known[idx],
                    Some(computed_last_checkpoint_hash),
                );
            }
            Ok(known)
```
