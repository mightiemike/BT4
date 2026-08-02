## Title
`ensure_match_transaction_info()` never validates the state/hot-state/position checkpoint hashes, letting `replay_on_archive`/CLI verification accept a divergent state root as a "successful" replay — (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info()` is the sole consistency check used by replay/debugger tooling (`storage/db-tool/src/replay_on_archive.rs`, `aptos-move/aptos-debugger/src/aptos_debugger.rs`, `aptos-move/cli/src/commands.rs`) to confirm that a locally re-executed transaction matches the authenticated `TransactionInfo` recorded on-chain. It checks status, gas used, write-set hash (`state_change_hash`), and event root hash, but explicitly does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`.

### Finding Description [1](#0-0) 

The function's own comment documents the gap: [2](#0-1) 

`ensure_match_transaction_info` verifies four fields (`status`, `gas_used`, `write_set_hash` vs `state_change_hash`, and `event_root_hash`) but stops there — no comparison of `state_checkpoint_hash()`/`hot_state_checkpoint_hash()`/`position_state_checkpoint_hash()` on `TransactionInfo` is performed, even though those fields exist on `TransactionInfoV0`/`TransactionInfoV1` (see fields at [3](#0-2)  and [4](#0-3) ).

This function is used, unconditionally, as the pass/fail gate for offline archive replay-verification: [5](#0-4) 

The state checkpoint hash is the root of the Sparse/Jellyfish Merkle Tree describing world state at a checkpoint — it is the strongest state-commitment invariant a full replay can check (write-set hash alone proves per-transaction outputs are internally consistent, not that applying them onto the state tree produces the tree root that the chain actually committed to). Skipping it means `replay_on_archive`/CLI transaction-replay can report success even when the locally recomputed state root diverges from the authenticated on-chain checkpoint root, e.g. due to state-application bugs, or (as the comment states) once trading-native/position-state roots are computed and could differ silently.

### Impact Explanation
This breaks the "Storage schemas, replay paths, and restore helpers must not reinterpret committed data into a different ledger state" and "committed state that differs from the correct VM result... must be caught" integrity gates for the offline verification tooling path. A silent divergence between the locally-replayed state root and the authenticated `state_checkpoint_hash`/`position_state_checkpoint_hash` in the backup/archive would go undetected by `replay_on_archive` and the debugger/CLI replay-and-compare flow, undermining the primary safety mechanism operators rely on to detect state-computation bugs or corrupted backups. It does not itself corrupt mainnet consensus state (no accumulator/Merkle proof is falsely "accepted as valid" during normal consensus commit — the real commit path in the executor separately computes and commits the state root), so the impact is scoped to detection/verification correctness of replay tooling rather than to consensus-critical state commitment itself.

### Likelihood Explanation
The gap is deterministic and always present for every call to `ensure_match_transaction_info` — it's not conditional on adversarial input. It only manifests as an actual missed-detection bug when there is an underlying divergence between local execution and the authenticated checkpoint hash, and per the code's own TODO the risk is explicitly acknowledged as increasing once `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/position-state roots are enabled. Today, absent such a divergence, the check being missing is a latent gap rather than an active exploit; I could not confirm (with available tools) whether other layers (e.g., primary in-consensus commit-time verification) independently prevent this divergence from occurring in mainnet-committed data, so I cannot assert this reaches "High/Critical" mainnet impact — it is a verification-tooling correctness bug documented by the authors themselves as needing a fix.

### Recommendation
Extend `ensure_match_transaction_info` to compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` between the locally computed values (once available on `TransactionOutput`) and `txn_info`, at least once `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled, per the existing TODO. Until then, callers of the replay-verification tooling should be made aware that the checkpoint/state root is not part of the automated comparison.

### Proof of Concept
Not applicable as an exploit PoC — this is a code-level gap directly evidenced by the function body and its own TODO comment. Demonstrating the practical effect would require constructing a divergent state-checkpoint hash for a real transaction and showing `ensure_match_transaction_info`/`replay_on_archive` reports success, which is beyond what can be confirmed via static code reading alone.

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

**File:** types/src/transaction/mod.rs (L2409-2416)
```rust
    /// The root hash of the Sparse Merkle Tree describing the world state at the end of this
    /// transaction. Depending on the protocol configuration, this can be generated periodical
    /// only, like per block.
    state_checkpoint_hash: Option<HashValue>,

    /// The hash value summarizing PersistedAuxiliaryInfo.
    auxiliary_info_hash: Option<HashValue>,
}
```

**File:** types/src/transaction/mod.rs (L2440-2461)
```rust
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[cfg_attr(any(test, feature = "fuzzing"), derive(Arbitrary))]
pub struct TransactionInfoV1 {
    gas_used: u64,
    status: ExecutionStatus,
    transaction_hash: HashValue,
    event_root_hash: HashValue,
    state_change_hash: HashValue,
    state_checkpoint_hash: Option<HashValue>,
    hot_state_checkpoint_hash: Option<HashValue>,
    auxiliary_info_hash: Option<HashValue>,

    /// Repurposed reserved field; `None` matches the prior BCS encoding.
    position_state_checkpoint_hash: Option<HashValue>,
    placeholder1: Option<HashValue>,
    placeholder2: Option<HashValue>,
    placeholder3: Option<HashValue>,
    placeholder4: Option<HashValue>,
    placeholder5: Option<HashValue>,
    placeholder6: Option<HashValue>,
    placeholder7: Option<HashValue>,
}
```

**File:** aptos-move/cli/src/commands.rs (L2651-2655)
```rust
                if !self.skip_comparison {
                    txn_output
                        .ensure_match_transaction_info(self.txn_id, &txn_info, None, None)
                        .map_err(|msg| CliError::UnexpectedError(msg.to_string()))?;
                }
```
