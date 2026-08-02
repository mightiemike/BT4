I'll investigate the areas mentioned (write-set conversion, transaction info construction, storage commit, accumulators, Merkle proofs, restore flows, authenticated API responses) to check for any genuine, provable integrity issues before concluding.This confirms the finding: `ensure_match_transaction_info()` at [1](#0-0)  is the sole per-transaction integrity check used by `replay_on_archive`'s `execute_and_verify()` at [2](#0-1) , and it explicitly skips validating `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` — a gap the code itself documents as a known TODO.

### Title
Replay-verification of authenticated position/hot-state checkpoint roots is a no-op in `TransactionOutput::ensure_match_transaction_info` - (File: `types/src/transaction/mod.rs`)

### Summary
`ensure_match_transaction_info()` is the function that `db-tool replay-on-archive` (and, per its own comment, similar replay-verify tooling) uses to confirm a locally re-executed `TransactionOutput` matches the archived, consensus-authenticated `TransactionInfo` for a given version. It checks status, gas used, write-set hash (`state_change_hash`), and event root hash, but never compares `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` [3](#0-2) . The state/hot-state/position-state Merkle roots are exactly the fields that `TransactionInfoV1` was extended to carry for the "trading-native" subsystem [4](#0-3) , gated by the `COMPUTE_TRADING_NATIVE_STATE_ROOTS` and `HOT_STATE_ROOT_IN_TXN_INFO` feature flags [5](#0-4) .

### Finding Description
The single caller of this exact API, `replay_on_archive::Verifier::execute_and_verify`, re-executes archived transactions locally and calls `ensure_match_transaction_info` as the pass/fail gate for the whole replay-verify pipeline [6](#0-5) . Because the checkpoint-hash fields are never compared, a divergence between the locally computed state root (main state, hot state, or native-position state) and the archived/consensus-signed `TransactionInfo` root will not be caught: the function returns `Ok(())` regardless. This is called out directly in the code itself as a `TODO(trading-native)`:

> "this comparator ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`), so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution. Validate the checkpoint hashes here before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`." [7](#0-6) 

Separately, `state_checkpoint_hash` root verification for main state is exercised elsewhere (e.g. `verify_snapshots` in test helpers compares stored `state_checkpoint_hash` against the JMT root via proof) [8](#0-7) , but the archive-replay integrity path that is supposed to catch execution/state divergence on a byte-for-byte basis relies specifically on this comparator, which does not check any checkpoint root.

### Impact Explanation
If the `COMPUTE_TRADING_NATIVE_STATE_ROOTS` or `HOT_STATE_ROOT_IN_TXN_INFO` features are enabled and a bug (or targeted manipulation) causes the locally computed native-position or hot-state Merkle root to diverge from the value recorded/committed at consensus time, `replay-on-archive` and equivalent replay-verify tooling built on this comparator will report success. This defeats the primary purpose of replay verification: independently proving that archived/committed ledger state matches deterministic VM re-execution. It masks state-commitment divergence exactly in the class of state (native-position/hot-state trees) that these new feature flags were added specifically to authenticate at consensus. This is a proof-integrity gap in the tooling that operators and auditors rely on to detect corruption or non-determinism in these newly authenticated roots.

### Likelihood Explanation
The gap is unconditionally present in the code (not behind a flag) — it only manifests as a security-relevant divergence-detection gap once `COMPUTE_TRADING_NATIVE_STATE_ROOTS` (feature id 122) and/or `HOT_STATE_ROOT_IN_TXN_INFO` (feature id 123) are enabled on-chain and the corresponding `TransactionInfoV1` fields are populated [9](#0-8) . The code's own inline comment documents this exact scenario as a known, unresolved precondition to enabling that feature, indicating the maintainers are aware this must be fixed first.

### Recommendation
Before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS` (and generally), extend `ensure_match_transaction_info` to compare `self`'s locally-computed `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present/expected for the version) against the corresponding fields on `txn_info`, failing the same way the other fields do on mismatch.

### Proof of Concept
Not directly exploitable as a standalone PoC without a state root divergence to trigger; the finding is that if such a divergence exists (e.g. from a native-position/hot-state execution bug), `execute_and_verify` in `replay_on_archive.rs` calling `ensure_match_transaction_info` [10](#0-9)  will not detect it, since none of the three checkpoint-hash fields are ever read or compared inside the function body [3](#0-2) .

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

**File:** storage/db-tool/src/replay_on_archive.rs (L373-405)
```rust
        let executed_outputs = executor
            .execute_block(
                &txns_provider,
                &self
                    .arc_db
                    .state_view_at_version(current_version.checked_sub(1))?,
                BlockExecutorConfigFromOnchain::new_no_block_limit(), // TODO(HotState): will need to incorporate some features.
                TransactionSliceMetadata::Chunk {
                    begin: *current_version,
                    end: *current_version + cur_txns.len() as u64,
                },
            )
            .map(BlockOutput::into_transaction_outputs_forced)?;
        assert_eq!(executed_outputs.len(), cur_txns.len());

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

**File:** aptos-move/framework/move-stdlib/sources/configs/features.move (L949-961)
```text
    /// When enabled, execution computes the trading-native state roots and commits them to
    /// `TransactionInfoV1`, so they are consensus-verified. Requires `TRANSACTION_INFO_V1`.
    /// Covers the native-position tree today and is intended to cover the other trading-native
    /// trees as they are added. Enabling it first commits the (empty-tree) roots to transaction
    /// info; the actual Move-side writes to those trees are gated by separate flags.
    /// Lifetime: permanent
    const COMPUTE_TRADING_NATIVE_STATE_ROOTS: u64 = 122;

    /// When enabled together with `TRANSACTION_INFO_V1`, execution populates
    /// `TransactionInfoV1`'s hot state root hash, so it is committed to the ledger
    /// accumulator. Requires `TRANSACTION_INFO_V1`.
    /// Lifetime: permanent
    const HOT_STATE_ROOT_IN_TXN_INFO: u64 = 123;
```

**File:** storage/aptosdb/src/db/test_helper.rs (L411-450)
```rust
fn verify_snapshots(
    db: &AptosDB,
    start_version: Version,
    snapshot_versions: Vec<Version>,
    txns_to_commit: Vec<&TransactionToCommit>,
) {
    let mut cur_version = start_version;
    let mut updates: HashMap<StateKey, Option<StateValue>> = HashMap::new();
    for snapshot_version in snapshot_versions {
        let start = (cur_version - start_version) as usize;
        let end = (snapshot_version - start_version) as usize;
        assert!(txns_to_commit[end].has_state_checkpoint_hash());
        let expected_root_hash = db
            .ledger_db
            .transaction_info_db()
            .get_transaction_info(snapshot_version)
            .unwrap()
            .state_checkpoint_hash()
            .unwrap();
        updates.extend(
            txns_to_commit[start..=end]
                .iter()
                .flat_map(|x| x.write_set().write_op_iter())
                .map(|(k, op)| (k.clone(), op.as_state_value())),
        );
        for (state_key, state_value) in &updates {
            let (state_value_in_db, proof) = db
                .get_state_value_with_proof_by_version(state_key, snapshot_version)
                .unwrap();
            assert_eq!(state_value_in_db.as_ref(), state_value.as_ref());
            proof
                .verify(
                    expected_root_hash,
                    state_key.hash(),
                    state_value_in_db.as_ref(),
                )
                .unwrap();
        }
        cur_version = snapshot_version + 1;
    }
```

**File:** types/src/on_chain_config/aptos_features.rs (L203-209)
```rust
    /// When enabled, execution computes the native-position state root at the
    /// checkpoint stage and commits it to `TransactionInfoV1`, so it is
    /// consensus-verified. Requires `TRANSACTION_INFO_V1`.
    COMPUTE_TRADING_NATIVE_STATE_ROOTS = 122,
    /// When enabled, execution populates `TransactionInfoV1`'s hot state root hash, so it
    /// is committed to the ledger accumulator. Requires `TRANSACTION_INFO_V1`.
    HOT_STATE_ROOT_IN_TXN_INFO = 123,
```
