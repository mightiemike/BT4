## Analysis

This isn't the assembly/ABI-decode bug class directly, but it is a structurally analogous **state-integrity comparator gap**: instead of assembly mis-decoding chunks of a byte array, here a proof/output-matching routine skips a subset of the required chunks (fields) when validating an execution output against an authenticated `TransactionInfo`.

### Title
Incomplete `TransactionOutput::ensure_match_transaction_info` check silently ignores checkpoint-hash fields, allowing divergent committed state to pass replay/output verification - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function used to validate an (untrusted / replayed / synced) `TransactionOutput` against an authenticated `TransactionInfo` (the object actually committed into the transaction accumulator and covered by consensus signatures / accumulator proofs). It checks status, gas used, write-set hash, and event root hash, but explicitly and admittedly does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`. [1](#0-0) 

### Finding Description
`ensure_match_transaction_info` computes and validates `write_set_hash` and `event_root_hash` against the trusted `txn_info`, but the trailing comment openly documents that it skips the checkpoint-hash comparisons: [2](#0-1) 

These skipped fields are exactly the fields that bind a `TransactionInfo` to the resulting **state root** (`state_checkpoint_hash`), **hot-state root** (`hot_state_checkpoint_hash`), and the new **native-position state root** (`position_state_checkpoint_hash`) introduced by the `TRANSACTION_INFO_V1`/`COMPUTE_TRADING_NATIVE_STATE_ROOTS`/`HOT_STATE_ROOT_IN_TXN_INFO` feature flags. [3](#0-2) [4](#0-3) 

These roots are consumed downstream as the authoritative expected snapshot root for state-sync/bootstrap flows — e.g. `expected_snapshot_root` in the bootstrapper reads `position_state_checkpoint_hash` directly off the (accumulator-proof-verified) target `TransactionInfo` to validate a state snapshot: [5](#0-4) 

`ensure_match_transaction_info` is called in four places that all deal with untrusted or externally-supplied transaction outputs being reconciled against authenticated `TransactionInfo`: the chunk executor (`execution/executor/src/chunk_executor/mod.rs`), `replay_on_archive` (db-tool), `aptos-debugger`, and the Move CLI. Because the function never re-derives or compares the checkpoint-hash fields, none of these call sites detect a case where the write set and events hash correctly (so `write_set_hash`/`event_root_hash` match) but the resulting state/hot-state/position tree that this write set would produce diverges from the `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` actually committed in the authenticated `TransactionInfo`.

By contrast, the block-executor's own commit path (`DoLedgerUpdate::assemble_transaction_infos`) builds every one of these checkpoint-hash fields into the `TransactionInfo` it hashes into the accumulator — proving these fields are treated as consensus-relevant, security-critical parts of the committed ledger state, not incidental metadata: [6](#0-5) 

### Impact Explanation
Any code path that relies on `ensure_match_transaction_info` (rather than a full-struct `TransactionInfo` equality check such as `ensure_transaction_infos_match` used by `StateSyncChunkVerifier`/`ReplayChunkVerifier`) to validate a supplied `TransactionOutput` will accept and persist a `TransactionOutput` whose resulting state/hot-state/position root diverges from the authenticated, accumulator-committed root, without raising an error. This is precisely the "committed state differs from correct result" / "authenticated response bound to wrong root" class called out in the State-Integrity Gate: the checked object (`TransactionOutput`/state) can silently diverge from the value actually bound to consensus signatures via the transaction accumulator, while the verifying tool reports success. The comment names `db-tool`'s `replay_on_archive` explicitly as an affected consumer, meaning a corrupted archive or execution divergence involving the new native-position/hot-state trees can pass replay verification while the underlying state is wrong.

### Likelihood Explanation
This gap only manifests once `TRANSACTION_INFO_V1` plus `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/`HOT_STATE_ROOT_IN_TXN_INFO` are enabled (these are marked "permanent" lifetime flags being rolled out, not yet default-on per `FeatureFlag::default_features()`), so today it requires those flags to be active for the divergence to be meaningful. The gap is also self-documented as a known TODO ("Validate the checkpoint hashes here before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`"), indicating the aptos-core team is aware but has not yet closed it, and enabling the feature before this fix ships would activate the gap on any path using `ensure_match_transaction_info`.

### Recommendation
Extend `ensure_match_transaction_info` to also verify `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` whenever those fields are present/expected on the `TransactionInfo` variant (`V1`), by recomputing them from the locally-observed state transition (or requiring the caller to pass in the locally computed values, similar to `expected_write_set`/`expected_events`), and failing the match if they diverge — mirroring the existing `write_set_hash`/`event_root_hash` checks. This should be done before `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/`HOT_STATE_ROOT_IN_TXN_INFO` are enabled network-wide.

### Proof of Concept
Not independently executable from static review alone: the exact reachability of `ensure_match_transaction_info` in `execution/executor/src/chunk_executor/mod.rs` (versus the fully-comparing `ensure_transaction_infos_match` used elsewhere in the same module) could not be fully traced within the available tool budget. The concrete, code-proven fact is limited to: the function is documented and implemented to skip checkpoint-hash validation, and is called from `db-tool`'s `replay_on_archive`, `aptos-debugger`, Move `cli`, and `chunk_executor::mod`, all of which reconcile untrusted/externally-produced `TransactionOutput`s against authenticated `TransactionInfo`. Confirming whether this specific comparator (as opposed to the full-equality comparator) is reachable on the live consensus/state-sync commit path for mainnet nodes (versus only offline replay-verification tooling) requires deeper tracing of `chunk_executor/mod.rs`'s call site than was possible here. [1](#0-0)

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

**File:** types/src/on_chain_config/aptos_features.rs (L191-209)
```rust
    HOTNESS_IN_EPILOGUE = 116,
    /// When enabled, execution assembles `TransactionInfoV1` instead of `TransactionInfoV0`.
    TRANSACTION_INFO_V1 = 117,
    /// Umbrella auth flag for the native-trading subsystem; the per-store
    /// flags below gate the actual writes. Both must be on to write.
    TRADING_NATIVE = 118,
    /// Gates native-position writes.
    NATIVE_POSITION = 119,
    /// Gates native-orderbook writes.
    NATIVE_ORDERBOOK = 120,
    /// Gates native-collateral writes.
    NATIVE_COLLATERAL = 121,
    /// When enabled, execution computes the native-position state root at the
    /// checkpoint stage and commits it to `TransactionInfoV1`, so it is
    /// consensus-verified. Requires `TRANSACTION_INFO_V1`.
    COMPUTE_TRADING_NATIVE_STATE_ROOTS = 122,
    /// When enabled, execution populates `TransactionInfoV1`'s hot state root hash, so it
    /// is committed to the ledger accumulator. Requires `TRANSACTION_INFO_V1`.
    HOT_STATE_ROOT_IN_TXN_INFO = 123,
```

**File:** state-sync/state-sync-driver/src/bootstrapper.rs (L981-1009)
```rust
    /// The expected snapshot root for the given kind at the target version, read
    /// from the target transaction info: main state's state checkpoint hash, or
    /// the committed position state root (guaranteed present once the position
    /// stage runs, per `snapshot_kind_applies_to_target`). All kinds share the
    /// target version, so this is taken from the target output, not a storage read.
    fn expected_snapshot_root(&mut self, kind: StateKind) -> Result<HashValue, Error> {
        let transaction_output_to_sync = self.get_transaction_output_to_sync()?;
        let target_transaction_info = transaction_output_to_sync
            .get_output_list_with_proof()
            .proof
            .transaction_infos
            .first()
            .ok_or_else(|| {
                Error::UnexpectedError("Target transaction info does not exist!".into())
            })?;
        match kind {
            StateKind::MainState => target_transaction_info
                .ensure_state_checkpoint_hash()
                .map_err(|error| {
                    Error::UnexpectedError(format!(
                        "State checkpoint must exist! Error: {:?}",
                        error
                    ))
                }),
            StateKind::Position => target_transaction_info
                .position_state_checkpoint_hash()
                .ok_or_else(|| Error::UnexpectedError("Missing position state root!".into())),
        }
    }
```

**File:** execution/executor/src/workflow/do_ledger_update.rs (L82-121)
```rust
                let state_checkpoint_hash = state_checkpoint_hashes[i];
                let event_hashes = txn_output
                    .events()
                    .iter()
                    .map(CryptoHash::hash)
                    .collect::<Vec<_>>();
                let event_root_hash =
                    InMemoryEventAccumulator::from_leaves(&event_hashes).root_hash();
                let write_set_hash = CryptoHash::hash(txn_output.write_set());
                let status = txn_output
                    .status()
                    .as_kept_status()
                    .expect("Already sorted.");
                let txn_info = if transaction_info_v1 {
                    TransactionInfo::builder_v1()
                        .transaction_hash(txn.committed_hash())
                        .state_change_hash(write_set_hash)
                        .event_root_hash(event_root_hash)
                        .maybe_state_checkpoint_hash(state_checkpoint_hash)
                        .maybe_hot_state_checkpoint_hash(
                            hot_state_checkpoint_hashes.and_then(|hot| hot[i]),
                        )
                        .gas_used(txn_output.gas_used())
                        .status(status)
                        .maybe_auxiliary_info_hash(auxiliary_info_hash)
                        .maybe_position_state_checkpoint_hash(
                            position_state_checkpoint_hashes.and_then(|p| p[i]),
                        )
                        .build()
                } else {
                    TransactionInfo::builder_v0()
                        .transaction_hash(txn.committed_hash())
                        .state_change_hash(write_set_hash)
                        .event_root_hash(event_root_hash)
                        .maybe_state_checkpoint_hash(state_checkpoint_hash)
                        .gas_used(txn_output.gas_used())
                        .status(status)
                        .maybe_auxiliary_info_hash(auxiliary_info_hash)
                        .build()
                };
```
