Based on my investigation, I found a genuine, locally provable integrity gap analogous to the Mento bug: a validation function whose job is to prove that re-executed/replayed transaction data matches the authenticated on-chain commitment (`TransactionInfo`) actually skips validating part of that commitment — exactly like `BancorExchangeProvider` applying logic that ignores an edge condition it should check.

### Title
`TransactionOutput::ensure_match_transaction_info` skips verifying state/hot-state/position checkpoint hashes, allowing replay-verify and chunk-apply flows to accept state divergent from the authenticated `TransactionInfo` - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function used by state-sync's chunk executor, the Aptos debugger, and the CLI to verify that a re-executed/replayed `TransactionOutput` matches the authenticated `TransactionInfo` that was signed into the ledger by validators (via the transaction accumulator / `LedgerInfoWithSignatures`). It validates status, gas, write-set hash (`state_change_hash`), and event root hash, but by its own documented admission never validates the `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` fields. [1](#0-0) 

### Finding Description
The function checks `status`, `gas_used`, `write_set` hash against `state_change_hash`, and event root hash: [2](#0-1)  — but then returns `Ok(())` immediately after a comment explicitly stating the checkpoint hashes are not compared: [3](#0-2) 

These checkpoint-hash fields are the authenticated commitments to the actual Merkle/JMT-derived state roots (regular state, hot state, and — under the newly introduced `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature — the native-position state tree) that ride inside `TransactionInfoV1` and are covered by the transaction accumulator that validators sign in `LedgerInfoWithSignatures`. This is the same class of state-commitment field the task's "Proof And Storage Pivots" section calls out: "Accumulators, Jellyfish Merkle structures, versioned state views, and restore paths must preserve deterministic proof binding."

`ensure_match_transaction_info` is called from the chunk executor (state-sync / restore path) as well as `aptos-debugger` and the CLI replay tooling — i.e., exactly the "replay, restore, or proof verification" contexts called out as in-scope. This mirrors the Mento root cause: a check that is supposed to hold under all conditions is silently incomplete for a specific state (here, whenever V1 checkpoint fields / trading-native roots are populated), so a real integrity violation can pass unnoticed, analogous to how the exit-contribution logic didn't account for the zero-supply case.

### Impact Explanation
If a locally re-executed/replayed chunk produces the correct write-set/event hashes but a different actual state root (e.g. due to a bug in state-checkpoint construction, hot-state root computation, or the newly added native-position tree logic under `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/`HOT_STATE_ROOT_IN_TXN_INFO`), this verification function will report success even though the locally committed/persisted state diverges from the authenticated ledger state. This is precisely the "committed state that differs from the correct VM result" and "hard-fork-only divergence during commit, replay, restore, or proof verification" impact categories: a state-syncing or replaying node can silently persist and serve state (and answer authenticated API queries) that does not match what validators actually committed, with no error raised by this integrity gate.

The code comment itself flags this as a known, unresolved gap that must be fixed "before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`" [4](#0-3) , confirming the maintainers recognize this as a real correctness hole, not a stylistic one.

### Likelihood Explanation
This is not a hypothetical attacker-triggered exploit but a structural gap in the codebase's own consistency-verification logic, exercised on every chunk-apply/replay/debugger invocation of `ensure_match_transaction_info`. Any divergence in state/hot-state/position-state root computation (bugs in `DoStateCheckpoint`, the position-tree extension logic, or JMT restore paths) would go undetected by this specific gate, which is otherwise the primary mechanism to enforce ledger consistency during state sync and replay-verify.

### Recommendation
Extend `ensure_match_transaction_info` to compare `self`'s locally computed `state_checkpoint_hash`, `hot_state_checkpoint_hash` (when `hot_state_root_in_txn_info` is on), and `position_state_checkpoint_hash` (when `compute_trading_native_state_roots` is on) against the corresponding fields on `txn_info`, failing loudly (as the other checks do) on mismatch, before any dependent feature flag (`COMPUTE_TRADING_NATIVE_STATE_ROOTS`, `HOT_STATE_ROOT_IN_TXN_INFO`) is enabled on mainnet.

### Proof of Concept
Not independently reproducible as an end-to-end exploit from the index alone — the concrete PoC would require constructing a `TransactionOutput`/`TransactionInfo` pair where the write-set/event hashes match but the state/position checkpoint hash differs (e.g., via a hot-state or native-position root computation divergence) and confirming `ensure_match_transaction_info` returns `Ok(())`. This can be directly derived from the code shown at `types/src/transaction/mod.rs:2139-2204`, where no comparison of checkpoint hash fields exists. I was unable to further trace whether `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/`HOT_STATE_ROOT_IN_TXN_INFO` are currently enabled on mainnet (the feature-flag definitions I found mark them "Lifetime: permanent" but I could not confirm activation status), which affects real-world exploitability today versus being a landmine for when the feature is turned on.

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
