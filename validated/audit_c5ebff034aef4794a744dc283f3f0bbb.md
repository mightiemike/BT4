### Title
Silent loss of `hotness`/`extensions` write-set data on serialization causes restore/replay state divergence that proof verification cannot detect - (File: `types/src/write_set.rs`)

### Summary
`WriteSetV0`, the write-set variant used whenever the `hotness_in_epilogue` on-chain feature is disabled, marks its `hotness` and `extensions` fields with `#[serde(skip)]`. These fields carry real, applied pseudo-writes (`MakeHot` promotions and `NativePosition` entries) that are consumed directly from the in-memory `WriteSet` by the storage-commit path, but because they are skipped during BCS serialization they are absent both from the persisted `WriteSet` bytes and from the `CryptoHash` used to build `TransactionInfo::state_change_hash`. Any component that reconstructs a `WriteSet` from its persisted/serialized bytes (restore, chunk replay, or replay-verification) can never recover this data, while the ledger's proof-bearing hash (and thus the accumulator/consensus-agreed commitment) is completely blind to it, so the resulting divergence is unaccompanied by any verification failure.

### Finding Description
`WriteSetV0` is defined as: [1](#0-0) 

with `hotness` and `extensions` annotated `#[serde(skip)]`, in contrast to `WriteSetV1` which serializes both: [2](#0-1) 

`add_hotness` mutates this field in place on an existing `WriteSet` (which may still be `V0`): [3](#0-2) 

and `add_native_positions` similarly appends to `extensions`, also `#[serde(skip)]` for V0: [4](#0-3) 

Crucially, in the executor pipeline, `add_hotness` is invoked **unconditionally** for every `BlockEpilogue` transaction, regardless of whether the write sets are later converted to V1: [5](#0-4) 

Only if `onchain_config.hotness_in_epilogue()` is true are the outputs converted to `WriteSetV1` via `convert_write_sets_to_v1`; otherwise the `WriteSetV0` (with hotness already populated in memory) proceeds unchanged into `TransactionOutput`.

Downstream, `DoLedgerUpdate::assemble_transaction_infos` computes `state_change_hash` purely from `CryptoHash::hash(txn_output.write_set())`: [6](#0-5) 

Because `WriteSet` derives `BCSCryptoHash`, this hash is computed over the BCS-serialized bytes — and for `WriteSetV0` those bytes never include `hotness`/`extensions` due to `#[serde(skip)]`. The same applies to what gets persisted to disk (`WriteSetDb::put_write_set`, used both in normal commit and in backup restore): [7](#0-6) 

Yet the storage-commit path consumes the hotness/native-position data directly from the in-memory `WriteSet` object (not from its serialized form) via `base_op_iter()`/`native_position_iter()`, which merge `hotness_ref()`/`extensions_ref()` into the set of ops actually applied to the state store: [8](#0-7) [9](#0-8) 

The restore path in `storage/aptosdb/src/backup/restore_utils.rs` reconstructs state purely from persisted `write_sets` (already stripped of hotness/extensions for V0) via `StateUpdateRefs::index_write_sets`: [10](#0-9) 

So a node that originally executed and committed a block (with hotness/native-position data live in memory) applies one set of effects to its local state/cache, while any node restoring from backup, replaying from persisted transaction chunks, or reconstructing via `TransactionRestoreController`'s chunk-replay flow (`storage/backup/backup-cli/src/backup_types/transaction/restore.rs`) can only ever see the byte-identical `TransactionInfo`/write-set data that intentionally omits this information — and `TransactionInfoListWithProof::verify`/`TransactionOutputListWithProof::verify` will happily accept it, because the accumulator/proof machinery never covered these fields in the first place.

### Impact Explanation
This breaks the invariant that "VM outputs, transaction infos, events, and write sets must survive executor-to-storage handoff unchanged" and that "restore paths must not reinterpret committed data into a different ledger state." A validator's original hot-state/native-position bookkeeping is unrecoverable from the canonical, proof-verified transaction record whenever `WriteSetV0` is in effect, meaning any restore, replay-verification, or state-sync-driven state reconstruction produces a ledger whose transaction accumulator, transaction hashes, and signatures are all verified as correct yet whose actual committed hot-state/native-position data structurally diverges from what the original block producer computed. This is a durable-ledger-data-corruption class issue that survives all standard proof checks, since the compromised fields are architecturally excluded from any hash the proof machinery inspects.

### Likelihood Explanation
This triggers deterministically, without any attacker action, whenever `hotness_in_epilogue` is off (its default/legacy state, since it's an on-chain feature flag) at the time a `BlockEpilogue` transaction executes, combined with any of the standard restore/replay code paths (`storage/aptosdb/src/backup/restore_utils.rs`, `storage/backup/backup-cli/src/backup_types/transaction/restore.rs`) being used, which are routine, non-privileged operational paths (node bootstrap from backup, fast sync, disaster recovery).

### Recommendation
- Short term: remove `#[serde(skip)]` from `WriteSetV0::hotness`/`extensions`, or ensure `add_hotness`/`add_native_positions` are never called on a `WriteSetV0` (assert/convert to V1 first) so no live in-memory pseudo-write is ever silently dropped by serialization.
- Long term: fold hotness/native-position effects into a hash that is actually covered by `TransactionInfo` (or explicitly document/guard that these fields must never carry state-affecting data unless V1 is active), and add a restore/replay round-trip test that asserts committed hot-state/native-position data survives a backup-and-restore cycle bit-for-bit.

### Proof of Concept
Not independently executable within this review; the mechanism is demonstrated purely by code inspection above (the `#[serde(skip)]` annotations plus the unconditional `add_hotness` call preceding the `hotness_in_epilogue` gate). I was not able to fully trace the exact downstream consumer in `storage/aptosdb/src/db/aptosdb_writer.rs` that reads `base_op_iter()`/`native_position_iter()` during commit (tool budget exhausted), so the precise magnitude of state divergence (e.g., whether it also affects a consensus-critical root beyond the dedicated `hot_state_checkpoint_hash`) should be confirmed with a live repro (execute a block with `hotness_in_epilogue=false`, back up, restore, and diff the resulting hot-state/native-position storage) before treating this as fully confirmed high/critical severity.

### Citations

**File:** types/src/write_set.rs (L727-747)
```rust
    pub fn base_op_iter(&self) -> impl Iterator<Item = (&StateKey, &BaseStateOp)> {
        static MAKE_HOT_OP: BaseStateOp = BaseStateOp::MakeHot;

        self.value_writes()
            .write_set
            .iter()
            .map(|(key, op)| (key, op.as_base_op()))
            .merge_join_by(
                self.hotness_ref().iter().map(|key| (key, &MAKE_HOT_OP)),
                |a, b| a.0.cmp(b.0),
            )
            .map(|entry| {
                // It seems like it's possible to have a key that is both in `value` and `hotness`
                // (possibly due to inaccurate read write summary). If this happens we discard the
                // hotness change, since the recently written keys will be made hot anyway.
                match entry {
                    EitherOrBoth::Left(e) | EitherOrBoth::Right(e) => e,
                    EitherOrBoth::Both(e, _) => e,
                }
            })
    }
```

**File:** types/src/write_set.rs (L753-757)
```rust
    pub fn add_hotness(&mut self, hotness: BTreeSet<StateKey>) {
        let field = self.hotness_mut();
        assert!(field.is_empty(), "hotness should only be initialized once.");
        *field = hotness;
    }
```

**File:** types/src/write_set.rs (L759-767)
```rust
    /// Iterate the native-position bucket. Used by the storage
    /// commit applier; main-state consumers never see these entries.
    pub fn native_position_iter(&self) -> impl Iterator<Item = (&StateKey, &NativePositionOp)> {
        self.native_positions().into_iter().flat_map(|m| m.iter())
    }

    pub fn native_position_keys(&self) -> impl Iterator<Item = &StateKey> {
        self.native_positions().into_iter().flat_map(|m| m.keys())
    }
```

**File:** types/src/write_set.rs (L773-786)
```rust
    /// Install the native-position bucket. Mirrors [`add_hotness`]:
    /// expected to be called once per WriteSet, at VM-output
    /// materialization time.
    pub fn add_native_positions(&mut self, native_positions: BTreeMap<StateKey, NativePositionOp>) {
        let extensions = self.extensions_mut();
        assert!(
            !extensions
                .iter()
                .any(|e| matches!(e, Extension::NativePosition(_))),
            "native_positions should only be initialized once."
        );
        // TODO: the order here is important when there are more extensions.
        extensions.push(Extension::NativePosition(native_positions));
    }
```

**File:** types/src/write_set.rs (L792-802)
```rust
#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct WriteSetV0 {
    value_writes: WriteSetMut,
    /// Hot state promotions, non-empty only in block epilogues.
    #[serde(skip)]
    hotness: BTreeSet<StateKey>,
    /// Opt-in side-channels (see [`Extension`]). Skipped from serde so `TransactionInfo` hashes and
    /// the on-disk WriteSet format are unaffected.
    #[serde(skip)]
    extensions: Vec<Extension>,
}
```

**File:** types/src/write_set.rs (L820-827)
```rust
/// Like [`WriteSetV0`], but serializes the hotness and extension
/// buckets alongside the value write set.
#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct WriteSetV1 {
    value_writes: WriteSetMut,
    hotness: BTreeSet<StateKey>,
    extensions: Vec<Extension>,
}
```

**File:** execution/executor/src/workflow/do_get_execution_output.rs (L159-172)
```rust
        for (transaction, output) in transactions.iter().zip_eq(transaction_outputs.iter_mut()) {
            if let Transaction::BlockEpilogue(payload) = transaction {
                assert!(output.status().is_kept(), "Block epilogue must be kept");
                output.add_hotness(
                    payload
                        .try_get_keys_to_make_hot()
                        .cloned()
                        .unwrap_or_default(),
                );
            }
        }
        if onchain_config.hotness_in_epilogue() {
            Self::convert_write_sets_to_v1(&mut transaction_outputs);
        }
```

**File:** execution/executor/src/workflow/do_ledger_update.rs (L90-110)
```rust
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
```

**File:** storage/aptosdb/src/backup/restore_utils.rs (L258-265)
```rust
    // insert changes in write set schema batch
    for (idx, ws) in write_sets.iter().enumerate() {
        WriteSetDb::put_write_set(
            first_version + idx as Version,
            ws,
            &mut ledger_db_batch.write_set_db_batches,
        )?;
    }
```

**File:** storage/aptosdb/src/backup/restore_utils.rs (L267-275)
```rust
    if kv_replay && first_version > 0 && state_store.get_usage(Some(first_version - 1)).is_ok() {
        let (ledger_state, _hot_state_updates) = state_store.calculate_state_and_put_updates(
            &StateUpdateRefs::index_write_sets(first_version, write_sets, write_sets.len(), vec![]),
            &mut ledger_db_batch.ledger_metadata_db_batches, // used for storing the storage usage
            state_kv_batches,
        )?;
        // n.b. ideally this is set after the batches are committed
        state_store.set_state_ignoring_summary(ledger_state);
    }
```
