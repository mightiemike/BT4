## Finding

### Title
Silent loss of `WriteSet` extensions (native-position writes) on BCS round-trip for `WriteSetV0` causes storage-commit divergence — (File: `types/src/write_set.rs`)

### Summary
The external report describes a class of bug where one code path consumes the *full* input while a sibling path only consumes a *partial* slice of the same input, producing state that silently diverges from what callers assume was used. The Aptos-native analog is in `WriteSet`'s two-variant representation (`WriteSetV0` / `WriteSetV1`): the `extensions` field (which carries `Extension::NativePosition` write-ops) is marked `#[serde(skip)]` on `WriteSetV0` but is a normal serialized field on `WriteSetV1`. Any `WriteSet` that stays in `V0` form and is round-tripped through BCS serialization (disk persistence, state-sync/backup transfer) silently loses its `extensions` bucket, while consumers that read the *original in-memory* `WriteSet` (immediately after execution) still see the data.

### Finding Description
`WriteSet::add_native_positions` pushes an `Extension::NativePosition(..)` write-op map into a `WriteSet`'s `extensions` field: [1](#0-0) 

Both write-set variants store this in an `extensions: Vec<Extension>` field, but only `WriteSetV0` marks it `#[serde(skip)]`: [2](#0-1) 

`WriteSet` derives `BCSCryptoHash`/`Serialize`/`Deserialize` directly on the enum, so: [3](#0-2) 
- Hashing (`state_change_hash` in `TransactionInfo`) and BCS (de)serialization use the *same* `#[serde(skip)]` annotation, so the hash of a `V0` write set is self-consistent with its serialized bytes — extensions never affect the accumulator/proof root. This means the accumulator/proof itself is not directly corrupted.
- However, the **committed durable data** (the native-position write-ops) is only preserved if the write set is a `V1`, which happens only when `convert_write_sets_to_v1` is explicitly invoked, and that call is gated by an unrelated on-chain feature flag: [4](#0-3) 
`Self::convert_write_sets_to_v1(&mut transaction_outputs)` only runs `if onchain_config.hotness_in_epilogue()`. This flag governs the *hot-state* feature, not `compute_trading_native_state_roots` (the native-position feature) — so a chain with `compute_trading_native_state_roots` enabled but `hotness_in_epilogue` disabled keeps `WriteSetV0`, whose `extensions` never survive a BCS encode/decode.

The `WriteSetSchema` codec used to persist write sets to RocksDB performs exactly such a round trip using plain `bcs::to_bytes`/`bcs::from_bytes`: [5](#0-4) 

Downstream, the storage commit path derives native-position writes from `TransactionOutput::write_set().native_position_iter()`, which only returns data recovered from the `extensions` bucket: [6](#0-5) [7](#0-6) 

This `commit_native_position` routine is invoked from the generic chunk-commit path (`calculate_and_commit_ledger_and_state_kv`), which is used both for freshly-executed blocks and for chunks whose `TransactionOutput`s were reconstructed from a BCS-deserialized source (e.g. the fast-sync/state-snapshot bootstrap path): [8](#0-7) [9](#0-8) 

In `finalize_state_snapshot`, `transactions_and_outputs` are unzipped directly from `output_with_proof`, i.e., data that arrived over the wire/backup storage and was BCS-deserialized — exactly the code path where a `WriteSetV0`'s `extensions` (and hence any `NativePosition` write-ops) would already have been dropped before this function even sees them.

### Impact Explanation
A validator that executes a block live keeps the in-memory `TransactionOutput`/`WriteSet` with intact `extensions`, so its `commit_native_position` correctly persists native-position writes into `position_merkle_db`. A validator/full node that instead receives the same committed data via state sync (fast-sync bootstrap) or backup restore receives `TransactionOutput`s that already went through a BCS round trip while still `WriteSetV0` — losing the `NativePosition` extension silently (no error, no panic, because `serde(skip)` just defaults to empty). That node's native-position store — and any state root/proof later derived from `compute_trading_native_state_roots` — permanently diverges from nodes that executed the block directly, without any consistency check catching the discrepancy, since the accumulator/transaction-info hash never covered `extensions` in the first place. This is a durable, undetectable divergence in committed ledger data for the native-position/trading subsystem, matching the "committed state differs from correct VM result / corrupts durable ledger data" and "hard-fork-only divergence during commit/replay/restore" gate criteria.

### Likelihood Explanation
Trigger conditions are narrow but realistic in production topology: the native-position/trading feature (`compute_trading_native_state_roots`) must be enabled while the unrelated `hotness_in_epilogue` flag is not (or, more generally, whenever a `WriteSetV0` carrying `extensions` is persisted/transmitted without conversion to `V1`), and a node must obtain the chunk via state-sync/backup restore rather than local execution. No malicious actor is required — a normal, honest fast-sync/backup-restoring node is silently affected, purely from the interaction between `#[serde(skip)]` on `WriteSetV0::extensions` and the unrelated feature-flag gate on `convert_write_sets_to_v1`.

### Recommendation
Decouple the `WriteSet` V0→V1 promotion from `hotness_in_epilogue` and instead gate it on "does this write set carry any non-empty extensions" (i.e., call `convert_write_sets_to_v1` whenever `output.write_set().has_native_positions()` or any extension is non-empty, in addition to the current hotness-based trigger). Alternatively, remove `#[serde(skip)]` from `WriteSetV0::extensions` (or ban/assert that extensions must be empty before a `WriteSetV0` is serialized to storage/wire), so a BCS round-trip can never silently discard `Extension::NativePosition` data.

### Proof of Concept
Local, code-level PoC path (no live network required to demonstrate the data-loss root cause):
1. Construct a `WriteSet::V0` and call `add_native_positions` with a non-empty `BTreeMap<StateKey, NativePositionOp>` (as VM execution does when `compute_trading_native_state_roots` is enabled but `hotness_in_epilogue` is disabled, since `convert_write_sets_to_v1` is skipped per `do_get_execution_output.rs:170-172`).
2. Serialize it with `bcs::to_bytes(&write_set)` (exactly what `WriteSetSchema::encode_value` in `storage/aptosdb/src/schema/write_set/mod.rs:55-57` does), then `bcs::from_bytes::<WriteSet>(&bytes)`.
3. Observe `write_set.native_position_iter().next()` is `None` after the round trip, even though it was `Some(..)` before — because `WriteSetV0::extensions` is `#[serde(skip)]` (`types/src/write_set.rs:800-801`) and defaults to an empty `Vec` on deserialize.
4. This same round trip occurs implicitly whenever `TransactionOutput`s are shipped through state sync / backup and then fed into `commit_native_position` (`storage/aptosdb/src/db/aptosdb_writer.rs:343-377`), causing that receiving node's native-position store to permanently omit writes that the originating (executing) node persisted.

### Citations

**File:** types/src/write_set.rs (L555-565)
```rust
#[derive(BCSCryptoHash, Clone, CryptoHasher, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum WriteSet {
    V0(WriteSetV0),
    V1(WriteSetV1),
}

impl Default for WriteSet {
    fn default() -> Self {
        Self::V0(WriteSetV0::default())
    }
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

**File:** types/src/write_set.rs (L792-827)
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

impl WriteSetV0 {
    #[inline]
    pub fn iter(&self) -> btree_map::Iter<'_, StateKey, WriteOp> {
        self.value_writes.write_set.iter()
    }

    #[inline]
    pub fn into_write_op_iter(self) -> btree_map::IntoIter<StateKey, WriteOp> {
        self.value_writes.write_set.into_iter()
    }

    pub fn get(&self, key: &StateKey) -> Option<&WriteOp> {
        self.value_writes.get(key)
    }
}

/// Like [`WriteSetV0`], but serializes the hotness and extension
/// buckets alongside the value write set.
#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct WriteSetV1 {
    value_writes: WriteSetMut,
    hotness: BTreeSet<StateKey>,
    extensions: Vec<Extension>,
}
```

**File:** execution/executor/src/workflow/do_get_execution_output.rs (L148-172)
```rust
        // Manually create hotness write sets for block epilogue transaction(s), based on the block
        // end info saved. Note that even if we are re-executing transactions during a state sync,
        // the block end info is not re-computed and has to come from the previous execution.
        //
        // If the input transactions are from a normal block, the last one should be the epilogue.
        // If they are from a chunk (i.e. we are re-executing transactions during state sync), then
        // there could be zero or more block epilogue transactions, and we need to handle all of
        // them.
        //
        // TODO(HotState): it might be better to do this in AptosVM::execute_single_transaction,
        // but we need to figure out how to properly construct `VMOutput` from block end info.
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

**File:** storage/aptosdb/src/schema/write_set/mod.rs (L54-79)
```rust
impl ValueCodec<WriteSetSchema> for WriteSet {
    fn encode_value(&self) -> Result<Vec<u8>> {
        bcs::to_bytes(self).map_err(Into::into)
    }

    fn decode_value(data: &[u8]) -> Result<Self> {
        // TODO(HotState): we could simply use `bcs::from_bytes` for everything once the latest
        // release branch does not write LegacyWriteSetV1Payload and forge-compat test would not
        // fail in CI.
        match data.first() {
            Some(&1) => match bcs::from_bytes::<WriteSet>(data) {
                Ok(ws) => Ok(ws),
                // Legacy V1 payload lacks the trailing `extensions` field, so the new decoder
                // runs out of bytes while reading it. Any other error indicates real corruption
                // and should propagate.
                Err(bcs::Error::Eof) => {
                    let legacy: LegacyWriteSetV1Payload = bcs::from_bytes(&data[1..])?;
                    let mut ws = WriteSet::V0(legacy.value);
                    ws.add_hotness(legacy.hotness);
                    Ok(ws)
                },
                Err(e) => Err(e.into()),
            },
            _ => bcs::from_bytes::<WriteSet>(data).map_err(Into::into),
        }
    }
```

**File:** storage/aptosdb/src/db/aptosdb_writer.rs (L187-211)
```rust
            let (transactions, outputs): (Vec<Transaction>, Vec<TransactionOutput>) =
                output_with_proof
                    .transactions_and_outputs
                    .into_iter()
                    .unzip();
            let events = outputs
                .clone()
                .into_iter()
                .map(|output| output.events().to_vec())
                .collect::<Vec<_>>();
            let wsets: Vec<WriteSet> = outputs
                .into_iter()
                .map(|output| output.write_set().clone())
                .collect();
            let transaction_infos = output_with_proof.proof.transaction_infos;
            // We should not save the key value since the value is already recovered for this version
            restore_utils::save_transactions(
                self.state_store.clone(),
                self.ledger_db.clone(),
                version,
                &transactions,
                &persisted_aux_info,
                &transaction_infos,
                &events,
                wsets,
```

**File:** storage/aptosdb/src/db/aptosdb_writer.rs (L286-341)
```rust
    fn calculate_and_commit_ledger_and_state_kv(
        &self,
        chunk: &ChunkToCommit,
        sync_commit: bool,
    ) -> Result<HashValue> {
        let _timer = OTHER_TIMERS_SECONDS.timer_with(&["save_transactions__work"]);

        let mut new_root_hash = HashValue::zero();
        THREAD_MANAGER.get_non_exe_cpu_pool().scope(|s| {
            // TODO(grao): Write progress for each of the following databases, and handle the
            // inconsistency at the startup time.
            //
            // TODO(grao): Consider propagating the error instead of panic, if necessary.
            s.spawn(|_| {
                self.commit_events(chunk.first_version, chunk.transaction_outputs)
                    .unwrap()
            });
            s.spawn(|_| {
                self.ledger_db
                    .write_set_db()
                    .commit_write_sets(chunk.first_version, chunk.transaction_outputs)
                    .unwrap()
            });
            s.spawn(|_| {
                self.ledger_db
                    .transaction_db()
                    .commit_transactions(
                        chunk.first_version,
                        chunk.transactions,
                        true, /* skip_index */
                    )
                    .unwrap()
            });
            s.spawn(|_| {
                self.ledger_db
                    .persisted_auxiliary_info_db()
                    .commit_auxiliary_info(chunk.first_version, chunk.persisted_auxiliary_infos)
                    .unwrap()
            });
            s.spawn(|_| self.commit_state_kv_and_ledger_metadata(chunk).unwrap());
            s.spawn(|_| {
                self.commit_transaction_infos(chunk.first_version, chunk.transaction_infos)
                    .unwrap()
            });
            s.spawn(|_| {
                new_root_hash = self
                    .commit_transaction_accumulator(chunk.first_version, chunk.transaction_infos)
                    .unwrap()
            });
            if self.position.is_some() {
                s.spawn(|_| self.commit_native_position(chunk, sync_commit).unwrap());
            }
        });

        Ok(new_root_hash)
    }
```

**File:** storage/aptosdb/src/db/aptosdb_writer.rs (L343-377)
```rust
    fn commit_native_position(&self, chunk: &ChunkToCommit, sync_commit: bool) -> Result<()> {
        let _timer = OTHER_TIMERS_SECONDS.timer_with(&["commit_native_position"]);
        let Some(bundle) = self.position.as_ref() else {
            return Ok(());
        };
        if chunk.transaction_outputs.is_empty() {
            return Ok(());
        }
        let committer = NativeStateCommitter::new(bundle.kv_db.clone());

        let chunk_first = chunk.first_version;
        let chunk_last_inclusive = chunk_first + chunk.transaction_outputs.len() as Version - 1;

        // Persist the position KV values + stale index.
        let mut sharded_kv_batches = new_sharded_kv_batches();
        let mut in_chunk_prior = InChunkPriorVersions::new();
        for (i, output) in chunk.transaction_outputs.iter().enumerate() {
            let version = chunk_first + i as Version;
            let position_writes: Vec<_> = output
                .write_set()
                .native_position_iter()
                .map(|(k, op)| (k.clone(), op.as_write_op().clone()))
                .collect();
            if !position_writes.is_empty() {
                committer
                    .apply(
                        version,
                        position_writes,
                        &mut sharded_kv_batches,
                        &mut in_chunk_prior,
                    )
                    .map_err(|e| AptosDbError::Other(format!("native commit: {e}")))?;
            }
        }
        bundle
```
