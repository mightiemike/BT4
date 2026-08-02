## Finding: `TransactionOutput::ensure_match_transaction_info` skips validating state/position checkpoint hashes, letting replay-verification accept a diverging state root

### Title
Replay-verification accepts a divergent state root because `ensure_match_transaction_info` never checks `state_checkpoint_hash` / `hot_state_checkpoint_hash` / `position_state_checkpoint_hash` - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the authenticated check used by chunk replay ("verify execution" mode) to confirm a locally re-executed transaction produced the same result as the one already committed and covered by a `TransactionInfo`/ledger-info signature. It verifies status, gas, write-set hash, and event-root hash, but explicitly does **not** verify the state checkpoint hash (nor the hot-state or position-state checkpoint hashes), as called out by its own TODO comment. [1](#0-0) 

### Finding Description
`ensure_match_transaction_info` compares the freshly computed `TransactionOutput` to the trusted, proof-covered `TransactionInfo` on four fields: status, gas used, write-set hash (`state_change_hash`), and event root hash. [2](#0-1) 

It never compares `txn_info.state_checkpoint_hash()`, `hot_state_checkpoint_hash()`, or `position_state_checkpoint_hash()` against the locally computed state summary roots, and the function's own comment states this directly:

```
// TODO(trading-native): this comparator ignores the checkpoint hashes
// (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
// tooling (e.g. db-tool's `replay_on_archive`) can report a successful
// replay even when the authenticated position state root diverges from
// local execution. Validate the checkpoint hashes here before enabling
// COMPUTE_TRADING_NATIVE_STATE_ROOTS.
``` [3](#0-2) 

This function is used by both the chunk executor's replay path (`VerifyExecutionMode`) and the `db-tool`'s `replay_on_archive` command, i.e. the two places that are supposed to authoritatively confirm a locally executed transaction stream reproduces the exact same, already-signed ledger state:


Because the state-checkpoint/position-checkpoint hash fields are the values that ultimately bind a `TransactionInfo` (and therefore the transaction accumulator / ledger info signature) to a specific state root, omitting them from this check means the verifier can accept a `TransactionOutput` whose resulting state (e.g. under the position/"trading native" state tree, gated by `compute_trading_native_state_roots`) is different from the one the validators actually signed, while every field this function does check (status/gas/write-set hash/events) still matches.

### Impact Explanation
If the `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature is (or becomes) enabled, this gap means:
- `db-tool`'s `replay_on_archive`, used by operators/auditors to independently confirm that historical execution reproduces the committed chain state, can report a clean "successful replay" even though the position/native-trading state root it computed diverges from the authenticated one recorded in the `TransactionInfo`.
- Chunk-executor replay-verify mode (state-sync / fast-sync replay path) has the same blind spot: a chunk whose write-set/events/gas/status match but whose derived state-checkpoint or position-state-checkpoint root differs would still pass `ensure_match_transaction_info`, silently accepting a wrong state root as "verified."

This is exactly the class of bug the task is targeting: an authenticated-proof/state-commitment check that is supposed to bind computed state to the signed ledger version/root, but silently skips part of that binding, allowing wrong committed/derived state to be accepted as verified.

### Likelihood Explanation
The gap is unconditionally present in the code today (not behind a flag guard) — the comment says the fix is needed "before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`", implying the feature is not yet fully turned on in production, which limits current exploitability. I could not verify the live default value of the `compute_trading_native_state_roots` feature/config flag or its exact staging status with the tools available, so I cannot confirm this is exploitable on mainnet today versus only affecting a feature still being staged.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `txn_info.state_checkpoint_hash()`, `hot_state_checkpoint_hash()`, and `position_state_checkpoint_hash()` (when applicable/known) against the locally computed values, as the existing TODO already indicates, before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` (or any future feature relying on this comparator) is enabled.

### Proof of Concept
Not independently constructible from static analysis alone: exercising this requires enabling `compute_trading_native_state_roots` and crafting a transaction whose resulting position/native state root differs while write-set/events/gas/status stay identical, then running it through `db-tool replay_on_archive` or chunk-executor replay-verify to observe the comparator return `Ok(())`. I was not able to fully trace whether `compute_trading_native_state_roots` is currently reachable/enabled in this repo snapshot within the available tool budget, so likelihood/exploitability here is not fully confirmed and should be validated by a follow-up investigation into that config's current rollout state.

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
