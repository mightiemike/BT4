## Missing Position-State-Root Validation in `TransactionOutput::ensure_match_transaction_info` - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` is the authenticated cross-check used by replay/verification tooling (e.g. `storage/db-tool/src/replay_on_archive.rs`, `aptos-move/aptos-debugger/src/aptos_debugger.rs`) to confirm that a locally re-executed transaction output matches the `TransactionInfo` that is bound into the transaction accumulator via a Merkle proof. This is the exact same invariant class as the SP1Blobstream bug: a committed/authenticated value (there, block height; here, the native-position state root) is not actually checked against the locally-derived value, so a divergent value can be accepted as valid.

### Finding Description
`ensure_match_transaction_info` explicitly validates status, gas, write-set hash (`state_change_hash`), and event root hash against the `txn_info`, but it deliberately skips the checkpoint hashes: [1](#0-0) 

The comment left in the code states this directly: [2](#0-1) 

`TransactionInfoV1` carries `position_state_checkpoint_hash`, `state_checkpoint_hash`, and `hot_state_checkpoint_hash` as authenticated fields inside the accumulator leaf: [3](#0-2) [4](#0-3) 

`position_state_checkpoint_hash` is treated elsewhere in the codebase as the authenticated root of the native-position Jellyfish Merkle tree — it is what fast-sync uses to validate the position-state snapshot it downloads from an untrusted peer: [5](#0-4) [6](#0-5) 

However, `ensure_match_transaction_info` — the function used by replay-verify to confirm a locally computed output matches the historically committed/authenticated `TransactionInfo` — never checks `position_state_checkpoint_hash` (nor `state_checkpoint_hash`/`hot_state_checkpoint_hash`) against a recomputed value. Because replay tooling relies on this comparator as its sole correctness gate, a divergence between the locally computed native-position Merkle root and the previously committed/authenticated `position_state_checkpoint_hash` will not be detected: replay reports success even though the durable position ledger state diverges from the correct VM/JMT result.

I also confirmed the writer path has a conditional, feature-gated way to *compute* vs. *trust* the position summary at commit time (`position_summary_at_commit` vs. execution-supplied `chunk.position_state_summary`, gated by `COMPUTE_TRADING_NATIVE_STATE_ROOTS`): [7](#0-6) 
This shows the position-state root is a first-class, sometimes-independently-derived commitment — exactly the kind of value that needs a strong equality check in the verifier, mirroring the SP1Blobstream fix (deriving heights from the trusted data rather than trusting a separately supplied value).

### Impact Explanation
If the local recomputation of the native-position state (or of the main/hot state checkpoint) diverges from what was historically committed and hashed into the accumulator-bound `TransactionInfo`, `ensure_match_transaction_info` will not catch it. This directly undermines the "proof and storage pivot" that VM outputs must survive executor→storage handoff, and that replay/verification tooling must correctly detect divergence. A silent corruption of the native-position ledger state (or a bug in how the summary is computed for `COMPUTE_TRADING_NATIVE_STATE_ROOTS`) would go undetected by `replay_on_archive` and `aptos-debugger`, both explicitly named in the code's own TODO comment as consumers relying on this check.

### Likelihood Explanation
I could not fully verify, given the available tool budget, whether `position_state_checkpoint_hash`/`state_checkpoint_hash` are cross-checked by some *other* mechanism during normal consensus commit (e.g. `check_and_put_ledger_info` only checks the transaction-accumulator root hash, not per-transaction checkpoint hash fields against a locally recomputed value) — so this may currently only be reachable through the specific replay/debugger tooling paths, which is what the code's own comment (dated to this same TODO) already flags as an open, acknowledged gap rather than something I am asserting speculatively. The comment is authored in-repo and explicitly says the gap exists "before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`," indicating the feature that would make this gap security-relevant is not fully rolled out yet, which affects how directly exploitable it is on mainnet today.

### Recommendation
Extend `ensure_match_transaction_info` to also compare a recomputed `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when available/expected) against the corresponding fields in `txn_info`, following the same pattern already used for `state_change_hash` and `event_root_hash`, before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled.

### Proof of Concept
Not independently constructable from the indexed code alone — the gap is demonstrated directly by the source comment and the absence of any checkpoint-hash comparison in `ensure_match_transaction_info`; because I could not trace how (or whether) `position_state_checkpoint_hash` divergence is caught elsewhere in the consensus-commit path within the available tool budget, this is reported as a confirmed *local* code gap (missing invariant enforcement, explicitly acknowledged by an in-repo TODO) rather than a fully proven end-to-end mainnet exploit chain.

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

**File:** types/src/transaction/mod.rs (L2359-2364)
```rust
    pub fn position_state_checkpoint_hash(&self) -> Option<HashValue> {
        match self {
            Self::V0(_) => None,
            Self::V1(v) => v.position_state_checkpoint_hash,
        }
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

**File:** state-sync/state-sync-driver/src/bootstrapper.rs (L1005-1008)
```rust
            StateKind::Position => target_transaction_info
                .position_state_checkpoint_hash()
                .ok_or_else(|| Error::UnexpectedError("Missing position state root!".into())),
        }
```

**File:** state-sync/state-sync-driver/src/bootstrapper.rs (L1121-1144)
```rust
    /// target. Main state always does. Native-position only participates once the
    /// target's `TransactionInfo` commits a position state root: until the
    /// executor sets it there is no authenticated position state to sync, so the
    /// stage is skipped rather than trusting an unproved peer-supplied root.
    /// Requires the target transaction output to already be fetched.
    fn snapshot_kind_applies_to_target(&mut self, kind: StateKind) -> Result<bool, Error> {
        match kind {
            StateKind::MainState => Ok(true),
            StateKind::Position => {
                let transaction_output_to_sync = self.get_transaction_output_to_sync()?;
                let target_transaction_info = transaction_output_to_sync
                    .get_output_list_with_proof()
                    .proof
                    .transaction_infos
                    .first()
                    .ok_or_else(|| {
                        Error::UnexpectedError("Target transaction info does not exist!".into())
                    })?;
                Ok(target_transaction_info
                    .position_state_checkpoint_hash()
                    .is_some())
            },
        }
    }
```

**File:** storage/aptosdb/src/db/aptosdb_writer.rs (L382-399)
```rust
        // Advance the position pipeline (merklize + persist + advance the base).
        // Flag on: the summary comes from execution on the chunk; off: compute
        // it here so the tree still tracks forward (not consensus-committed).
        if let Some(store) = bundle.state_store.as_ref() {
            let new_state = match chunk.position_state_summary {
                Some(summary) => summary.clone(),
                None => self.position_summary_at_commit(chunk)?,
            };
            let estimated_items = chunk.transaction_outputs.len();
            let mut bufstate = store.buffered_state_locked();
            bufstate.update(
                new_state,
                (),
                estimated_items,
                sync_commit || chunk.is_reconfig,
            )?;
        }
        Ok(())
```
