## Analysis

The external report's core invariant is: **a security check that is supposed to guarantee two independently-derived outputs converge on the same value can be silently incomplete, letting divergent state be accepted as valid.** In Tigris, `_checkDelay()` only compared timestamps, not price bounds, so two legitimately-signed-but-divergent prices could both pass. The Aptos-native analog I found is the same class of bug but in the **execution/replay-verification path**: `TransactionOutput::ensure_match_transaction_info` in `types/src/transaction/mod.rs` is the function used by replay/debug tooling to assert that locally-recomputed execution output matches the transaction info recorded (and proven) in the ledger — but it deliberately skips comparing the state-checkpoint-related hash fields.

### Title
Replay-verification comparator omits checkpoint-hash fields, allowing divergent authenticated state roots to pass as verified - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` checks status, gas, write-set hash (`state_change_hash`), and event root hash against a `TransactionInfo`, but explicitly does **not** check `state_checkpoint_hash` / `hot_state_checkpoint_hash` (and the in-progress `position_state_checkpoint_hash`). The author's own comment documents this gap and its exact consequence for `db-tool`'s `replay_on_archive`.

### Finding Description
`ensure_match_transaction_info` (types/src/transaction/mod.rs:2139-2204) validates a locally re-executed `TransactionOutput` against the `TransactionInfo` recorded/committed in the ledger (and covered by the accumulator proof, per `TransactionInfoWithProof::verify` in `types/src/proof/definition.rs`). It checks:
- execution status
- gas used
- `state_change_hash` (write-set hash)
- `event_root_hash`

It does not check `state_checkpoint_hash` or `hot_state_checkpoint_hash`, which are precisely the fields that authenticate the **state root** produced by a transaction that is a state checkpoint (block epilogue / block boundary), as seen used elsewhere for state-proof binding (e.g. `storage/backup/backup-cli/src/backup_types/state_snapshot/restore.rs:127-136`, which does compare `state_root_hash == manifest.root_hash` from `ensure_state_checkpoint_hash()`). The code comment at lines 2197-2202 states verbatim:

> "this comparator ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`), so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution."

This function is called from `storage/db-tool/src/replay_on_archive.rs:392-397` inside `execute_and_verify`, which is the core loop of the `replay-verify` tool used to validate that a locally re-executed range of transactions matches the archived, ledger-info-signed transaction infos. It is also invoked from `aptos-move/aptos-debugger/src/aptos_debugger.rs` and `aptos-move/cli/src/commands.rs`, i.e. it is the canonical "does my re-execution match the authenticated chain history" check used by node operators/auditors.

Because the comparator omits the checkpoint hash fields, a state-checkpoint transaction (which carries `state_checkpoint_hash`/`hot_state_checkpoint_hash` as the authenticated Merkle root of post-transaction global state) can have its state root silently diverge between the archived/authenticated value and the locally recomputed value, while `ensure_match_transaction_info` still returns `Ok(())` because status/gas/write-set-hash/event-hash all still matched for that particular transaction entry. The replay tool would then report the range as successfully verified even though the state root — the most security-critical authenticated value in the whole transaction info — was never actually cross-checked.

### Impact Explanation
This breaks the core proof/replay invariant required by the Gate: "Wrong accumulator root, Merkle proof, transaction proof ... accepted as valid" and "Hard-fork-only divergence during commit, replay ... must be preserved." `state_checkpoint_hash`/`hot_state_checkpoint_hash` is the field that binds the entire JMT/state-summary root to the accumulator-proven `TransactionInfo` (as evidenced by its dedicated role in state-snapshot restore verification). If replay-verify's per-transaction comparator does not validate this field, then:
- A build/feature combination that computes a different state root (e.g. due to a future logic bug, a hot-state divergence, or the noted `COMPUTE_TRADING_NATIVE_STATE_ROOTS`-related code path referenced by the TODO) can pass `replay-verify` and `db-tool replay-on-archive` even though the actual, authenticated ledger state differs from what local execution independently derives — precisely a "hard-fork-only divergence" that verification tooling is supposed to catch and does not.
- This degrades the guarantee that replay/verification tooling used by exchanges, auditors, and node operators to confirm state integrity against the authenticated ledger history is trustworthy.

This is High severity if it manifests: the entire purpose of `ensure_match_transaction_info` is to catch state divergence, and the one field omitted is the one that actually attests to global state correctness.

### Likelihood Explanation
Moderate-to-low currently, since none of the standard code paths seem to actively exploit it today — the gap is self-acknowledged as a latent risk tied to a not-yet-enabled feature (`COMPUTE_TRADING_NATIVE_STATE_ROOTS`, referenced in `types/src/block_executor/config.rs`, `execution/executor/src/workflow/do_get_execution_output.rs`, `storage/aptosdb/src/db/aptosdb_reader.rs` and `aptosdb_writer.rs`). However, likelihood is not negligible because:
1. The gap is unconditional in the current code (it applies regardless of that feature flag), and
2. It is documented by the maintainers themselves as an active risk ("Validate the checkpoint hashes here before enabling COMPUTE_TRADING_NATIVE_STATE_ROOTS"), indicating they identified but have not yet closed the gap.

I was not able to fully trace the exact conditions under which `COMPUTE_TRADING_NATIVE_STATE_ROOTS` gets enabled or how `do_state_checkpoint.rs` derives `state_checkpoint_hash` due to iteration limits, so I cannot confirm today's exploitability with a concrete PoC that produces a diverging state root; this remains a structural/proof-integrity gap rather than a demonstrated live exploit.

### Recommendation
Add explicit checks in `ensure_match_transaction_info` comparing:
- `self` (or the locally computed) state/hot-state checkpoint hash to `txn_info.state_checkpoint_hash()` / `txn_info.hot_state_checkpoint_hash()` whenever the transaction is a checkpoint (`has_state_checkpoint_hash()`), and
- `position_state_checkpoint_hash` once introduced,

before any feature that relies on these roots (e.g. `COMPUTE_TRADING_NATIVE_STATE_ROOTS`) is enabled on mainnet, so replay-verify tooling cannot report success while the authenticated state root diverges from local execution.

### Proof of Concept
I could not construct a concrete runnable PoC within tool-call limits (would require exercising `do_state_checkpoint.rs`'s checkpoint-hash computation and forcing a controlled divergence, e.g. via a modified hot-state root, then running `replay_on_archive` against it). The structural evidence is: [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** storage/backup/backup-cli/src/backup_types/state_snapshot/restore.rs (L125-136)
```rust
        let (txn_info_with_proof, li): (TransactionInfoWithProof, LedgerInfoWithSignatures) =
            self.storage.load_bcs_file(&manifest.proof).await?;
        txn_info_with_proof.verify(li.ledger_info(), manifest.version)?;
        let state_root_hash = txn_info_with_proof
            .transaction_info()
            .ensure_state_checkpoint_hash()?;
        ensure!(
            state_root_hash == manifest.root_hash,
            "Root hash mismatch with that in proof. root hash: {}, expected: {}",
            manifest.root_hash,
            state_root_hash,
        );
```
