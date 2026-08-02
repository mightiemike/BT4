### Title
Replay-verify integrity check (`TransactionOutput::ensure_match_transaction_info`) does not validate the state-checkpoint hash, allowing archived replay verification to pass despite a divergent state root - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the sole comparator used by Aptos's offline replay-verification tooling (`storage/db-tool/src/replay_on_archive.rs` and `aptos-move/aptos-debugger/src/aptos_debugger.rs`) to confirm that locally re-executed transactions match the transaction info recorded in an archived/backed-up ledger. The function checks status, gas used, write-set hash (`state_change_hash`), and event root hash, but explicitly skips the state-checkpoint hash (and hot-state / position-state checkpoint hashes), as acknowledged by its own `TODO` comment. This means the state root computed by local replay can silently diverge from the authenticated on-chain state root while the tool still reports a "successful" verified replay.

### Finding Description
`ensure_match_transaction_info` in `types/src/transaction/mod.rs` compares a freshly executed `TransactionOutput` against a `TransactionInfo` retrieved from trusted/backup storage: [1](#0-0) 

It validates `status`, `gas_used`, the write-set hash against `state_change_hash`, and the event root hash: [2](#0-1) 

Critically, lines 2197-2202 contain an explicit acknowledgment that the comparator does **not** check the state/hot-state checkpoint hashes or `position_state_checkpoint_hash`, and that this allows `db-tool`'s `replay_on_archive` to report success even when "the authenticated position state root diverges from local execution": [3](#0-2) 

`TransactionInfo` (which is the object actually committed into the transaction accumulator and proven by ledger proofs) carries `state_checkpoint_hash`, and its V1 variant also carries `hot_state_checkpoint_hash` and `position_state_checkpoint_hash`: [4](#0-3) 

This comparator is invoked directly in the archive replay-verification loop, which is the exact tool operators/auditors use to confirm that a locally re-executed chain of transactions reproduces the authenticated, accumulator-proven ledger state: [5](#0-4) 

Because the state-checkpoint hash (the actual value bound into the transaction accumulator/proof system via `TransactionInfo::hash()`) is excluded from the comparison, any divergence confined to state-root computation — e.g., from the "trading native state root" or hot-state Merkle computation paths referenced by the TODO (`COMPUTE_TRADING_NATIVE_STATE_ROOTS`, `hot_state_checkpoint_hash`) — is invisible to this verification path.

### Impact Explanation
This breaks the proof/verification invariant that "committed state that differs from the correct VM result... must be detected," specifically for the archived-replay verification pivot named in scope ("replay paths... must not reinterpret committed data into a different ledger state"). An operator relying on `replay_on_archive` (or `aptos-debugger`'s equivalent path) to confirm that a full history replay reproduces the authenticated ledger state can receive a false "verified" result while the actual state root (and thus JMT/state proofs derived from it) diverges from the one proven by the transaction accumulator/ledger info. This undermines confidence in the primary tool used to detect state-corrupting bugs (including hard-fork-causing divergences) after the fact, effectively masking exactly the class of bug the Search Steps ask to find.

### Likelihood Explanation
The gap is unconditionally present in the shipped comparator — it does not depend on a race condition or attacker input, only on some other part of the state-computation pipeline (e.g., checkpoint/hot-state root logic) diverging. Given the comment's own acknowledgment that this masks divergence under `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, and that this feature area is under active development, the likelihood of the blind spot being exercised is not merely theoretical — the code authors already flagged it as a known outstanding gap to fix.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present on either side) before enabling/relying on `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, so that `replay_on_archive` and `aptos-debugger` cannot report a passing replay when the authenticated state root diverges from local execution.

### Proof of Concept
1. Run `db-tool replay-on-archive` (or `aptos-debugger`) against an archived range of transactions.
2. Introduce (or hit, via an existing latent bug) a divergence purely in state-checkpoint computation (e.g., hot-state or position-state root) while write set, events, gas, and status remain identical to the recorded `TransactionInfo`.
3. Observe `execute_and_verify` in `storage/db-tool/src/replay_on_archive.rs` calling `ensure_match_transaction_info`, which returns `Ok(())` despite the state-checkpoint hash mismatch, because the checkpoint-hash fields are never compared, as shown at [3](#0-2) .
4. The tool reports a fully successful replay-verify run even though the locally computed state root differs from the one authenticated by the ledger's transaction accumulator.

### Citations

**File:** types/src/transaction/mod.rs (L2139-2178)
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
```

**File:** types/src/transaction/mod.rs (L2180-2204)
```rust
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
