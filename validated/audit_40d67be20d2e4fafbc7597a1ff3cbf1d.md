### Title
`TransactionOutput::ensure_match_transaction_info` never validates state-checkpoint hashes, letting replay-verify accept transaction outputs whose committed ledger state diverges from local execution - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` (the function used by replay/backup verification tooling to check a locally re-executed `TransactionOutput` against the authoritative, already-committed `TransactionInfo`) only compares status, gas used, write-set hash, and event root hash. It explicitly skips `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` — the exact fields that authenticate the post-execution *state* (JMT/hot-state/position-state roots) — with a TODO admitting the gap. [1](#0-0) 

### Finding Description
`TransactionInfo` carries several hashes: `state_change_hash` (write-set hash), `event_root_hash`, and — critically — `state_checkpoint_hash` / `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`, which bind a version to the resulting global state Merkle roots. [2](#0-1) 

`ensure_match_transaction_info` is the function that is supposed to prove a `TransactionOutput` (freshly computed by the VM) matches the authenticated `TransactionInfo` already stored/proven in the ledger accumulator. It checks status, gas, write-set hash, and event root hash, but the checkpoint hashes are never compared — the code contains a self-documented TODO stating this:

```rust
// TODO(trading-native): this comparator ignores the checkpoint hashes
// (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
// tooling (e.g. db-tool's `replay_on_archive`) can report a successful
// replay even when the authenticated position state root diverges from
// local execution. Validate the checkpoint hashes here before enabling
// COMPUTE_TRADING_NATIVE_STATE_ROOTS.
Ok(())
``` [1](#0-0) 

This function is directly used by `storage/db-tool/src/replay_on_archive.rs`, whose entire purpose is to re-execute historical transactions and detect divergence from the archived, ledger-info-anchored `TransactionInfo`: [3](#0-2) 

Because `state_checkpoint_hash` (and the trading-native `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`) are never checked, a replay whose write set, events, gas, and status all match, but whose resulting *state root* differs (e.g., due to a JMT/hot-state/position-state divergence, a non-deterministic hashing bug, a storage schema bug that reinterprets committed state, or a consensus/execution bug that only manifests in the state root and not in the per-transaction write set hash) will be reported by `replay_on_archive` as a clean, successful replay. This is analogous to the reported Solidity bug: the verifier reads/compares a struct/response using a field-set that doesn't fully match what the authoritative side has, silently accepting the wrong value for the critical field (the reserve floor there; the state root here).

### Impact Explanation
`state_checkpoint_hash` is the field that authenticates the entire post-transaction global Move state (via the Sparse/Jellyfish Merkle root), and `position_state_checkpoint_hash`/`hot_state_checkpoint_hash` authenticate newer state components. `replay_on_archive` and `db-tool replay-verify` (used in CI/`testsuite/replay-verify`) is a primary safety mechanism used to detect state-divergence bugs before/after hard forks, release upgrades, and to validate archive-node correctness. Because this checker never validates the state-checkpoint hashes, a genuine consensus-breaking, state-divergence bug (i.e., a bug that corrupts the JMT root, hot-state root, or position-state root without changing the write set bytes or event bytes it emits — e.g., a state-merge/commit ordering bug, an incorrect state-value pruning/restore interaction, or a hashing regression) could pass replay-verify undetected. This directly weakens the "committed state that differs from the correct VM result... accepted as valid" gate: the tooling meant to catch exactly this class of hard-fork/replay divergence silently reports success.

### Likelihood Explanation
This is not a remote/attacker-triggerable bug by itself; it is a verification-gap that only manifests when some other bug corrupts the state root while write-set/events/gas/status stay identical. However, because `state_checkpoint_hash` is only emitted once per checkpoint-block boundary (not per transaction) and none of the trading-native checkpoint hashes are checked at all, this is a structural, permanent hole in the verification path rather than a rare edge case — any bug in the untested surface (hot state promotion/demotion, position-state root computation, or JMT restore/merge) would go completely undetected by replay-verify regardless of how much replay-verify coverage is run. This raises likelihood for masking real high-impact state-integrity bugs that would otherwise be caught by this exact tool.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` against locally computed values whenever the corresponding feature (e.g., `COMPUTE_TRADING_NATIVE_STATE_ROOTS`) is enabled or a checkpoint is present, rather than leaving this validation gap as a TODO. At minimum, `replay_on_archive` should not report success without this check when trading-native state roots are active.

### Proof of Concept
Not applicable as a directly-triggerable exploit; this is a verification-logic gap. Conceptually:
1. Introduce (or have) any bug that corrupts the post-commit state root (JMT/hot-state/position-state) at some version `v` without altering the write-set bytes, event bytes, gas, or status recorded for `v`.
2. Run `db-tool replay-verify`/`replay_on_archive` over a range including `v`.
3. `execute_and_verify` calls `ensure_match_transaction_info`, which only checks write-set hash, event root hash, gas, and status — all of which match — and returns `Ok(())`, despite the authenticated state root diverging. [3](#0-2) [1](#0-0)

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

**File:** types/src/transaction/mod.rs (L2261-2284)
```rust
    #[builder(finish_fn = build)]
    pub fn builder_v1(
        transaction_hash: HashValue,
        state_change_hash: HashValue,
        event_root_hash: HashValue,
        state_checkpoint_hash: Option<HashValue>,
        hot_state_checkpoint_hash: Option<HashValue>,
        gas_used: u64,
        status: ExecutionStatus,
        auxiliary_info_hash: Option<HashValue>,
        position_state_checkpoint_hash: Option<HashValue>,
    ) -> Self {
        Self::V1(TransactionInfoV1::new(
            transaction_hash,
            state_change_hash,
            event_root_hash,
            state_checkpoint_hash,
            hot_state_checkpoint_hash,
            gas_used,
            status,
            auxiliary_info_hash,
            position_state_checkpoint_hash,
        ))
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
