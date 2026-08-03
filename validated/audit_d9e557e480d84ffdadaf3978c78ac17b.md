## Analysis Summary

The `TranslatedV1EventSchema` key is `(version, index)` where `version` is the immutable ledger version and `index` is the position of the V2 event within that transaction's (also immutable) event list, as read from `DbReader::get_events_iterator` [1](#0-0) . The write happens in `DBIndexer::process_a_batch`, which puts a `ContractEventV1` into `TranslatedV1EventSchema` under that key every time a `V2` event is translated [2](#0-1) .

However, `MintTranslator`/`MintTokenTranslator` (and other translators) do **not** derive the V1 `sequence_number` from any version-pinned state — they call `EventV2TranslationEngine::get_state_value_bytes_for_resource`/`get_state_value_bytes_for_object_group_resource`, which both read `self.main_db_reader.latest_state_checkpoint_view()` — i.e., the **current tip state**, not the state as of the version being translated [3](#0-2) . The fallback default sequence number (`get_next_sequence_number`) uses this current-tip resource's stale `EventHandle.count()` field only when neither the in-memory `DashMap` cache nor the on-disk `EventSequenceNumberSchema` has an entry for that event key [4](#0-3) .

Critically, the in-memory cache (`event_sequence_number_cache`) is updated eagerly during `process_a_batch` as translations happen [5](#0-4) , but this cache is **not** persisted to `EventSequenceNumberSchema` until the very end of the batch, and only for `version-1` progress checkpoint, in one atomic `SchemaBatch` that is asynchronously committed by a separate `DBCommitter` thread [6](#0-5) . On process restart/crash between accepting new batches into the mpsc channel and the committer thread durably flushing them, or whenever `enable_event_v2_translation` is turned on for backfill from an old version on an already-synced node, translation of a **historical** version will run against `latest_state_checkpoint_view()`, i.e. state reflecting many transactions after the one being translated (potentially issued by any unprivileged account minting/burning further tokens from the same collection). Because the on-disk `EventSequenceNumberSchema`/cache lost its progress across the restart while `TranslatedV1EventSchema` for some already-processed versions may or may not have been durably flushed (the write ordering across the two CFs is asynchronous relative to the in-memory cache state at crash time), a second processing pass over the same `(version, idx)` can compute a **different** `sequence_number`/event payload than the first pass and simply overwrite the schema entry (RocksDB `put` semantics — last writer wins, no version check).

This means an unprivileged actor who mints/burns tokens from the same 0x3::token `Collections`/`FixedSupply`/`UnlimitedSupply` resource across many transactions can cause the fallback "default" sequence number computed at different wall-clock/backfill times to diverge, and — combined with indexer crash/restart or config-driven backfill (not "trusted operator mistake", just ordinary async-commit + restart behavior) — the same `(version, idx)` key in `TranslatedV1EventSchema` can end up holding a `ContractEventV1` with a different `sequence_number` than what was computed and served before, silently corrupting the authoritative version→event mapping served through `get_translated_v1_event_by_version_and_index` to API clients [7](#0-6) .

I was not able to fully confirm the actual on-chain persistence semantics of the `mint_events`/`burn_events`/`mint_token_events` `EventHandle.count()` fields on `FixedSupplyResource`/`UnlimitedSupplyResource`/`CollectionsResource` (i.e., whether these legacy V1-style counters are still mutated by the Move framework after each V2 event, or frozen at creation) — this determines exactly how often the "default" fallback path (rather than the cache) is exercised, and thus the practical likelihood of the divergence. I could not retrieve `aptos-move/framework/aptos-token/sources/token.move`'s Move-level mutation logic for these handles or the Move `collection.move`/`token.move` accounting to verify this in the tool results returned. This is a limitation of what the indexed codebase context surfaced, not a reason to dismiss the finding — the `event_v2_translator.rs` code path itself is unambiguous about reading `latest_state_checkpoint_view()` for a historical translation.

### Title
Non-deterministic V1 event translation from tip-state reads causes `TranslatedV1EventSchema` to diverge across indexer runs - (File: `storage/indexer/src/event_v2_translator.rs`)

### Summary
`EventV2TranslationEngine::get_state_value_bytes_for_resource`/`get_state_value_bytes_for_object_group_resource` read `latest_state_checkpoint_view()` (current tip) instead of a state view pinned to the version being translated, so the default fallback sequence number used by `MintTranslator`/`MintTokenTranslator`/etc. is not a pure function of the version being processed.

### Finding Description
`process_a_batch` writes `TranslatedV1EventSchema[(version, idx)]` from whatever `sequence_number`/payload the current translation run computes [2](#0-1) . That computation depends on `get_next_sequence_number`, which falls back to reading the *live* on-chain resource state via `latest_state_checkpoint_view()` when no cached sequence number exists [8](#0-7) . Because the in-memory `DashMap` cache resets on process restart and the on-disk `EventSequenceNumberSchema`/`EventV2TranslationVersion` checkpoint is only durably committed asynchronously (batched with, but not synchronously ordered against, in-process state) [6](#0-5) , reprocessing the same version range (crash/restart, backfill on newly-enabled `enable_event_v2_translation`) can compute a different sequence number for the same historical V2 event than the original pass, silently overwriting the `TranslatedV1EventSchema` entry.

### Impact Explanation
The `(version, idx)` → `ContractEventV1` mapping is served directly to API clients as the historically-correct V1-equivalent event for a given transaction version [7](#0-6) . If it can silently change value under legitimate restart/backfill conditions triggered by unprivileged mint/burn activity elsewhere on the same collection, this breaks the "authenticated response bound to the right version" invariant for the event's `sequence_number`, which downstream indexers/consumers key on as if immutable.

### Likelihood Explanation
Requires either indexer process restart during translation (an ordinary operational event, not a "trusted operator mistake") or config-driven backfill after enabling `enable_event_v2_translation` on an already-synced node — both are standard operational occurrences, and the unprivileged trigger (repeated mint/burn on the same collection resource) is trivially achievable by any account.

### Recommendation
Pin translation reads to a `StateView` at the transaction's own version (or immediately after it) rather than `latest_state_checkpoint_view()`, and/or make the `EventSequenceNumberSchema`/cache updates synchronously durable with the corresponding `TranslatedV1EventSchema` writes so re-processing is idempotent and deterministic regardless of when/how many times a version range is retranslated.

### Proof of Concept
1. Mint tokens from collection C at version V1 while `enable_event_v2_translation` is off; let indexer backfill translate V1's `Mint` event using `latest_state_checkpoint_view()` at time T1 (after several more mints have occurred on C), assigning sequence number based on then-current handle count/cache state.
2. Restart the indexer (simulating crash before durable commit of the corresponding `EventSequenceNumberSchema` batch), causing in-memory cache to reset.
3. Reprocess the same version range; because more mints on C may have occurred between T1 and T2 (the second pass), or because on-disk cache state differs from what informed the first pass, `get_next_sequence_number` returns a different value.
4. Observe `TranslatedV1EventSchema[(V1, idx)]` now stores a `ContractEventV1` with a different `sequence_number` than what the API previously served for that version/index.

### Citations

**File:** storage/indexer/src/db_indexer.rs (L449-463)
```rust
                events.iter().enumerate().try_for_each(|(idx, event)| {
                    if let ContractEvent::V1(v1) = event {
                        batch
                            .put::<EventByKeySchema>(
                                &(*v1.key(), v1.sequence_number()),
                                &(version, idx as u64),
                            )
                            .expect("Failed to put events by key to a batch");
                        batch
                            .put::<EventByVersionSchema>(
                                &(*v1.key(), version, v1.sequence_number()),
                                &(idx as u64),
                            )
                            .expect("Failed to put events by version to a batch");
                    }
```

**File:** storage/indexer/src/db_indexer.rs (L475-478)
```rust
                                let key = *translated_v1_event.key();
                                let sequence_number = translated_v1_event.sequence_number();
                                self.event_v2_translation_engine
                                    .cache_sequence_number(&key, sequence_number);
```

**File:** storage/indexer/src/db_indexer.rs (L486-497)
```rust
                                batch
                                    .put::<EventByVersionSchema>(
                                        &(key, version, sequence_number),
                                        &(idx as u64),
                                    )
                                    .expect("Failed to put events by version to a batch");
                                batch
                                    .put::<TranslatedV1EventSchema>(
                                        &(version, idx as u64),
                                        &translated_v1_event,
                                    )
                                    .expect("Failed to put translated v1 events to a batch");
```

**File:** storage/indexer/src/db_indexer.rs (L521-565)
```rust
        if self.indexer_db.event_v2_translation_enabled() {
            batch.put::<InternalIndexerMetadataSchema>(
                &MetadataKey::EventV2TranslationVersion,
                &MetadataValue::Version(version - 1),
            )?;

            for event_key in event_keys {
                batch
                    .put::<EventSequenceNumberSchema>(
                        &event_key,
                        &self
                            .event_v2_translation_engine
                            .get_cached_sequence_number(&event_key)
                            .unwrap_or(0),
                    )
                    .expect("Failed to put events by key to a batch");
            }
        }

        if self.indexer_db.transaction_enabled() {
            batch.put::<InternalIndexerMetadataSchema>(
                &MetadataKey::TransactionVersion,
                &MetadataValue::Version(version - 1),
            )?;
        }
        if self.indexer_db.event_enabled() {
            batch.put::<InternalIndexerMetadataSchema>(
                &MetadataKey::EventVersion,
                &MetadataValue::Version(version - 1),
            )?;
        }
        if self.indexer_db.statekeys_enabled() {
            batch.put::<InternalIndexerMetadataSchema>(
                &MetadataKey::StateVersion,
                &MetadataValue::Version(version - 1),
            )?;
        }
        batch.put::<InternalIndexerMetadataSchema>(
            &MetadataKey::LatestVersion,
            &MetadataValue::Version(version - 1),
        )?;
        self.sender
            .send(CommitMessage::Write(batch))
            .map_err(|e| AptosDbError::Other(e.to_string()))?;
        Ok(version)
```

**File:** storage/indexer/src/event_v2_translator.rs (L190-235)
```rust
    pub fn get_next_sequence_number(&self, event_key: &EventKey, default: u64) -> Result<u64> {
        if let Some(seq) = self.get_cached_sequence_number(event_key) {
            Ok(seq + 1)
        } else {
            let seq = self
                .internal_indexer_db
                .get::<EventSequenceNumberSchema>(event_key)?
                .map_or(default, |seq| seq + 1);
            Ok(seq)
        }
    }

    pub fn get_state_value_bytes_for_resource(
        &self,
        address: &AccountAddress,
        struct_tag: &StructTag,
    ) -> Result<Option<Bytes>> {
        let state_view = self
            .main_db_reader
            .latest_state_checkpoint_view()
            .expect("Failed to get state view");
        let state_key = StateKey::resource(address, struct_tag)?;
        let maybe_state_value = state_view.get_state_value(&state_key)?;
        Ok(maybe_state_value.map(|state_value| state_value.bytes().clone()))
    }

    pub fn get_state_value_bytes_for_object_group_resource(
        &self,
        address: &AccountAddress,
        struct_tag: &StructTag,
    ) -> Result<Option<Bytes>> {
        let state_view = self
            .main_db_reader
            .latest_state_checkpoint_view()
            .expect("Failed to get state view");
        static OBJECT_GROUP_TAG: Lazy<StructTag> = Lazy::new(ObjectGroupResource::struct_tag);
        let state_key = StateKey::resource_group(address, &OBJECT_GROUP_TAG);
        let maybe_state_value = state_view.get_state_value(&state_key)?;
        let state_value = maybe_state_value
            .ok_or_else(|| anyhow::format_err!("ObjectGroup resource not found"))?;
        let object_group_resource: ObjectGroupResource = bcs::from_bytes(state_value.bytes())?;
        Ok(object_group_resource
            .group
            .get(struct_tag)
            .map(|bytes| Bytes::copy_from_slice(bytes)))
    }
```

**File:** storage/indexer/src/indexer_reader.rs (L168-183)
```rust
    fn get_translated_v1_event_by_version_and_index(
        &self,
        version: Version,
        index: u64,
    ) -> anyhow::Result<ContractEventV1> {
        if let Some(db_indexer_reader) = &self.db_indexer_reader {
            if db_indexer_reader.indexer_db.event_v2_translation_enabled() {
                return Ok(db_indexer_reader
                    .indexer_db
                    .get_translated_v1_event_by_version_and_index(version, index)?);
            } else {
                anyhow::bail!("Event translation is not enabled")
            }
        }
        anyhow::bail!("DB indexer reader is not available")
    }
```
