I've reduced this to one concrete finding rather than 4 speculative paths, since the code contains an explicit, developer-acknowledged gap in `TransactionOutput::ensure_match_transaction_info`.

### Title
`ensure_match_transaction_info` never validates the state-checkpoint hashes, allowing `db-tool replay-on-archive`/`replay-verify` to declare a state-integrity divergence as "verified" - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` is the authenticated-response invariant used by mainnet replay/verification tooling (`replay_on_archive`, `aptos-debugger`, `cli replay`) to assert that a locally re-executed `TransactionOutput` matches the `TransactionInfo` committed on an archive/mainnet ledger, which is itself bound to an accumulator-proven `LedgerInfo`. The function checks status, gas used, write-set hash (`state_change_hash`), and event root hash, but — as the in-code `TODO(trading-native)` comment states — it never compares `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` against the values recomputed during replay. [1](#0-0) 

### Finding Description
`TransactionInfo` carries `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` fields that are supposed to authenticate the post-execution state root(s) at checkpoint boundaries: [2](#0-1) . These fields are what bind a `TransactionInfo` (and thus the transaction accumulator leaf, and thus the ledger's accumulator root) to a specific state Merkle root. `ensure_match_transaction_info`, however, only verifies `status`, `gas_used`, `write_set` hash (`state_change_hash`) and `event_root_hash` [3](#0-2) , explicitly skipping the checkpoint hashes as documented in the trailing comment [4](#0-3) .

This function is the sole state-integrity gate used by:
- `storage/db-tool/src/replay_on_archive.rs`'s `execute_and_verify`, which re-executes archived transactions and calls it to decide pass/fail of the whole replay-verify run [5](#0-4) .
- `aptos-move/aptos-debugger/src/aptos_debugger.rs`'s `print_mismatches`, used to surface divergences during debugging/replay [6](#0-5) .
- `aptos-move/cli/src/commands.rs` replay command output comparison [7](#0-6) .

Because the write set is checked only via `state_change_hash` (the per-transaction write-set hash) and not the state-checkpoint root, this comparator cannot catch a scenario where the *state Merkle tree derived from applying that write set* diverges (e.g., due to a JMT/hot-state/position-state computation bug, a version-binding error in checkpoint hash assembly in `DoStateCheckpoint`, or a corrupted `state_checkpoint_hash` recomputation) while individual write-set/event hashes still match. In other words, this check validates the "per-transaction" authenticated fields but silently no-ops on the authenticated aggregate state root fields that are actually accumulated into the ledger's Merkle/accumulator structure.

### Impact Explanation
Replay-verify (`replay_on_archive`/`replay-verify`) is the mechanism operators and the Aptos Labs team rely on to detect state divergence between a locally computed ledger and an authenticated backup/mainnet archive after a code change (e.g., before/after a network upgrade, or to validate a new client implementation). Because the checkpoint-hash comparison is skipped, this tool can report a "successful replay" even when the locally computed state root differs from the authenticated on-chain state root — i.e., it fails to detect a broken state-commitment invariant that it is specifically designed to catch. This directly undermines the "authenticated API/state-view output must stay bound to the right ledger version and root" and "committed state must not diverge from correct VM result" invariants for a widely-used verification tool, though it does not itself corrupt live consensus-committed data (it is a passive verifier gap, not a live state-corruption path in the hot execution/commit pipeline).

### Likelihood Explanation
This is a real, permanent, unprivileged gap in shipped code (not test/mock code) that will manifest any time a state-checkpoint-affecting divergence occurs (e.g. HotState/position-native-state work in progress per the code's own `TODO(trading-native)` markers, or any bug in `DoStateCheckpoint`'s checkpoint hash computation) — the gap is deterministic and always present, not conditional on attacker behavior.

### Recommendation
Extend `ensure_match_transaction_info` to compare `txn_output`-derived state-checkpoint hash(es) (when a checkpoint boundary is present in the replayed output) against `txn_info.state_checkpoint_hash()`, `hot_state_checkpoint_hash()`, and `position_state_checkpoint_hash()`, as the existing TODO already flags, before any feature (e.g. `COMPUTE_TRADING_NATIVE_STATE_ROOTS`) that depends on these roots being trustworthy is enabled.

### Proof of Concept
Not applicable as an on-chain exploit — this is a verification-tool gap, demonstrable by constructing a `TransactionOutput` with a correct write set/events (matching `state_change_hash`/`event_root_hash`) but where the surrounding checkpoint/state root diverges from `txn_info.state_checkpoint_hash()`; `ensure_match_transaction_info` returns `Ok(())` in `types/src/transaction/mod.rs` lines 2139–2204 despite the state-root divergence.

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
