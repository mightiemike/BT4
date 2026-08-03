## Analysis

This is a legitimate finding. The event-decoding path in `api/src/events.rs::list` explicitly uses the **current/latest** state view to decode historical event bytes, regardless of the version at which the event was originally emitted:

```rust
let events = self
    .context
    .latest_state_view_poem(&latest_ledger_info)?
    .as_converter(self.context.db.clone(), self.context.indexer_reader.clone())
    .try_into_versioned_events(&events)
``` [1](#0-0) 

`try_into_versioned_events` then calls `view_value` per event using the type tag stored with the event and the module layout resolved from whatever state view was passed in — here, the latest one:

```rust
pub fn try_into_versioned_events(
    &self,
    events: &[EventWithVersion],
) -> Result<Vec<VersionedEvent>> {
    let mut ret = vec![];
    for event in events {
        let data = self
            .inner
            .view_value(event.event.type_tag(), event.event.event_data())?;
        ret.push((event, MoveValue::try_from(data)?.json()?).into());
    }
    Ok(ret)
}
``` [2](#0-1) 

`AptosValueAnnotator::view_value` resolves struct layouts by loading module bytecode through `ModuleView`, which is backed by the `StateView` passed into `AptosValueAnnotator::new` — i.e., whatever state view the caller supplied: [3](#0-2) 

Since `events.rs` supplies `latest_state_view_poem` unconditionally — not a state view constructed at `event.transaction_version` — the struct layout used to interpret an event's raw BCS bytes is always the **current** module layout, not the layout that existed when the event was emitted.

### Impact
If a module owner upgrades a struct's layout (Move allows adding fields or, in constrained ways, changing internal encodings) between the time an event was emitted and the time an API consumer queries the event history, the BCS bytes of the *old* event will be deserialized against the *new* struct layout. This can silently produce a decoded JSON `data` field with incorrect field names, positions, or coerced values — with no error raised in the case where the byte layout still parses (e.g., new field appended, or field reordering that coincidentally type-checks). API consumers that infer balances or historical state transitions from the `data` field of `VersionedEvent` would silently ingest corrupted values. This is a state-view misbinding issue in the API layer.

### Scope caveat
Note that this affects **API-layer JSON decoding of already-committed event bytes** — it does not corrupt the underlying committed `ContractEvent` bytes, the write set, the accumulator, or any proof material; the raw event bytes stored on ledger remain byte-for-byte correct and verifiable via BCS/proofs. The corruption is confined to the human/JSON-readable interpretation returned by the REST API's `AcceptType::Json` path (`AcceptType::Bcs` in `events.rs` returns the raw event, unaffected). This matches "Authenticated API ... responses must stay bound to the right ledger version" in the review's Proof And Storage Pivots, since the module/layout resolution is bound to the *wrong* version (latest instead of `event.transaction_version`).

### Title
Historical event JSON decoding uses latest state view instead of the event's emission version, misinterpreting event bytes after a struct-layout-changing module upgrade - (File: api/src/events.rs)

### Summary
`EventsApi::list` in `api/src/events.rs` builds the `MoveConverter` from `latest_state_view_poem`, then calls `try_into_versioned_events`, which decodes each historical event's raw bytes using `view_value` against that latest state view's module layouts — not the module layout at `event.transaction_version`. `api/types/src/convert.rs:769-781` performs no per-event version binding.

### Finding Description
`try_into_versioned_events` iterates over `EventWithVersion` items, each carrying a `transaction_version`, but only uses `event.event.type_tag()` and `event.event.event_data()` for decoding; it never uses `event.transaction_version` to construct a state view at that historical version. The `AptosValueAnnotator` (and its inner `ModuleView`) was already fixed to the `StateView` handed to it at construction time in the caller (`api/src/events.rs`), which is always `latest_state_view_poem`. If module upgrades change struct layouts between the event's emission and query time, the decoder in `view_value` (backed by `move-resource-viewer::MoveValueAnnotator`) resolves the type layout from the current module bytecode rather than the bytecode active at the event's version.

### Impact Explanation
Consumers of the JSON events API (`/accounts/{address}/events/...`) relying on `VersionedEvent.data` to reconstruct historical balances/state (e.g., indexers, off-chain accounting, wallets) may compute wrong values whenever a struct's layout changes across a module upgrade. This is a state-misinterpretation bug at the authenticated-response layer, though it does not corrupt on-chain committed data, proofs, or the BCS-served raw event bytes.

### Likelihood Explanation
Requires only: (1) a module owner performing a compatible-per-VM-checker but field-layout-changing upgrade (Move's module compatibility checker permits certain changes such as adding fields to a struct, if `struct_layout` compatibility mode allows it, or changes to enum-like structures), and (2) any client requesting historical events via `AcceptType::Json` after the upgrade. No privileged access or malicious peer behavior is required — an unprivileged event consumer and a routine module upgrade suffice.

### Recommendation
In `try_into_versioned_events` (and `try_into_events` for similar historical use if applicable), construct a per-event state view pinned at `event.transaction_version` before decoding, or reject decoding with an explicit error if the struct layout has changed since the historical version, rather than silently reinterpreting the bytes with the current layout.

### Proof of Concept
1. Publish a module `M` with `struct Ev { a: u64 }`, emit `Ev { a: 42 }` via a handle in a transaction at version `V1`.
2. Upgrade module `M` to `struct Ev { a: u64, b: u64 }` (or otherwise change layout in a way the module verifier accepts) in a later transaction.
3. Call `GET /accounts/{addr}/events/{creation_number}` requesting `start=<seq at V1>` with `Accept: application/json`.
4. Observe that `try_into_versioned_events` decodes the V1 event bytes (`8 bytes` for `u64`) against the new 2-field layout, either producing a decode error inconsistent with the true historical value, or (if byte lengths happen to align) silently producing an incorrect/garbage `b` field or shifted values in `data`, rather than the correct single-field `{"a": "42"}` that was actually emitted.

### Citations

**File:** api/src/events.rs (L182-187)
```rust
                let events = self
                    .context
                    .latest_state_view_poem(&latest_ledger_info)?
                    .as_converter(self.context.db.clone(), self.context.indexer_reader.clone())
                    .try_into_versioned_events(&events)
                    .context("Failed to convert events from storage into response")
```

**File:** api/types/src/convert.rs (L769-781)
```rust
    pub fn try_into_versioned_events(
        &self,
        events: &[EventWithVersion],
    ) -> Result<Vec<VersionedEvent>> {
        let mut ret = vec![];
        for event in events {
            let data = self
                .inner
                .view_value(event.event.type_tag(), event.event.event_data())?;
            ret.push((event, MoveValue::try_from(data)?.json()?).into());
        }
        Ok(ret)
    }
```

**File:** aptos-move/aptos-resource-viewer/src/lib.rs (L28-46)
```rust
impl<'a, S: StateView> AptosValueAnnotator<'a, S> {
    pub fn new(state_view: &'a S) -> Self {
        let view = ModuleView::new(state_view);
        Self(MoveValueAnnotator::new(view))
    }

    /// Collect information about tables contained in the value represented by the blob.
    pub fn collect_table_info(
        &self,
        ty_tag: &TypeTag,
        blob: &[u8],
        infos: &mut Vec<MoveTableInfo>,
    ) -> anyhow::Result<()> {
        self.0.collect_table_info(ty_tag, blob, infos)
    }

    pub fn view_value(&self, ty_tag: &TypeTag, blob: &[u8]) -> anyhow::Result<AnnotatedMoveValue> {
        self.0.view_value(ty_tag, blob)
    }
```
