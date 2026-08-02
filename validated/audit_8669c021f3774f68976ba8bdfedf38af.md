### Title
Silent, unindicated dropping of write-set changes in the transaction API response - ([File: api/types/src/convert.rs])

### Summary
`into_transaction_info` in `api/types/src/convert.rs` builds the `changes` field of the API's `TransactionInfo` object by silently discarding any write op that fails to convert to a `WriteSetChange`, instead of surfacing the error. This lets a successful, committed transaction be rendered by the authenticated `/transactions` (and `wait_transaction_by_hash`) endpoints with an incomplete/incorrect `changes` list while every other field (`accumulator_root_hash`, `state_change_hash`, `success`, `version`) still reflects the real, correctly-committed write set.

### Finding Description
`into_transaction_info` builds the API's per-transaction `changes` list this way: [1](#0-0) 

```rust
accumulator_root_hash: accumulator_root_hash.into(),
// TODO: the resource value is interpreted by the type definition at the version of the converter, not the version of the tx: must be fixed before we allow module updates
changes: write_set
    .into_write_op_iter()
    .filter_map(|(sk, wo)| self.try_into_write_set_changes(sk, wo).ok())
    .flatten()
    .collect(),
```

Any write op whose conversion via `try_into_write_set_changes` returns `Err(..)` is silently dropped via `.ok()` — the function still returns `Ok(TransactionInfo{..})`, and the endpoint responds `200 OK`.

By contrast, the sibling function used for genesis write sets propagates conversion errors instead of hiding them: [2](#0-1) 

`try_into_write_set_changes` itself has multiple real (non-test-only) failure paths for real committed transactions:
- `StateKeyInner::Raw(_)` and `StateKeyInner::TradingNative(_)` unconditionally return `Err`: [3](#0-2) 

- Resource/module bytes that fail to deserialize under the *converter's* current type layout also error out (e.g. `try_into_resource`, `try_parse_abi`), which the code comment explicitly flags as a known gap ("interpreted by the type definition at the version of the converter, not the version of the tx") whenever module upgrades change a struct layout.

`StateKeyTag::Raw` and `StateKeyTag::TradingNative` are live, non-test state-key kinds used by production subsystems (e.g. the trading-native position subsystem), as seen in `types/src/state_store/state_key/inner.rs`: [4](#0-3) 

So a normal, successfully-committed transaction that writes to a `Raw` or `TradingNative` state key, or that writes a resource whose byte layout the API-serving node currently cannot decode (e.g., following a module upgrade), will have those write ops vanish from the JSON `changes` array returned by the API — with no error, warning, or indication to the caller that data was omitted.

### Impact Explanation
This breaks the invariant that "VM outputs, transaction infos, events, and write sets must survive executor-to-storage handoff [and API rendering] unchanged." The API's authenticated `TransactionInfo.changes` is presented as an authoritative reflection of what was written to the ledger for a given version, bound to `version`/`hash`/`accumulator_root_hash`; but it can silently under-represent the actual committed write set while still returning success. Any consumer that trusts this endpoint to reconstruct on-chain state changes (indexers, bridges, auditing/monitoring tools, wallets) can be misled into believing certain state changes did not happen for a transaction that, on-chain, is fully successful and durably committed. This is a state-representation integrity gap in an authenticated API surface, though it does not corrupt the underlying ledger data, roots, or proofs themselves (the DB, JMT, and accumulator remain correct) — the damage is confined to the rendered API view.

### Likelihood Explanation
Moderate. It requires either (a) a transaction touching `Raw`/`TradingNative` state keys (production subsystem-specific, not attacker-arbitrary from a generic Move script) or (b) a resource whose stored bytes can't be decoded under the node's currently loaded type layout (most plausible after a module upgrade changes a struct's fields, matching the explicit TODO in the code). Both are realistic but not trivially triggerable by an arbitrary unprivileged account on every transaction; they depend on specific system state key usage or upgrade timing.

### Recommendation
Do not use `.filter_map(...).ok()` to swallow conversion errors when building `TransactionInfo.changes`. Either:
- Propagate the error (`?`) so the API returns a clear 500 rather than a falsely-successful, incomplete response (matching the behavior already used in `try_into_write_set_payload`), or
- Explicitly render unresolvable write ops as a distinguishable "opaque"/"undecoded" change entry (raw key/bytes only) instead of omitting them, so callers can detect and account for the gap.

### Proof of Concept
1. Commit a transaction whose write set includes an entry keyed by `StateKeyInner::Raw` or `StateKeyInner::TradingNative` (produced internally by relevant subsystems), or a resource write whose on-disk bytes no longer match the type layout known to the API-serving fullnode (e.g., due to a module upgrade changing a struct's shape).
2. Query `GET /v1/transactions/by_version/{version}` (or `by_hash`) with `Accept: application/json`.
3. Observe: the response is `200 OK`, `success: true`, with correct `hash`/`accumulator_root_hash`/`state_change_hash`, but the `changes` array is missing the entry corresponding to the unconvertible write op — with no error field or warning indicating a change was dropped.
4. Compare against the `application/x-bcs` response for the same transaction (or against `TransactionOutput.write_set()` from the raw BCS transaction data), which does contain the write op, confirming the JSON rendering silently diverges from the actual committed write set.

### Citations

**File:** api/types/src/convert.rs (L309-315)
```rust
            accumulator_root_hash: accumulator_root_hash.into(),
            // TODO: the resource value is interpreted by the type definition at the version of the converter, not the version of the tx: must be fixed before we allow module updates
            changes: write_set
                .into_write_op_iter()
                .filter_map(|(sk, wo)| self.try_into_write_set_changes(sk, wo).ok())
                .flatten()
                .collect(),
```

**File:** api/types/src/convert.rs (L577-592)
```rust
            Direct(d) => {
                let (write_set, events) = d.into_inner();
                let nested_writeset_changes: Vec<Vec<WriteSetChange>> = write_set
                    .into_write_op_iter()
                    .map(|(state_key, op)| self.try_into_write_set_changes(state_key, op))
                    .collect::<Result<Vec<Vec<_>>>>()?;
                WriteSetPayload {
                    write_set: WriteSet::DirectWriteSet(DirectWriteSet {
                        // TODO: the resource value is interpreted by the type definition at the version of the converter, not the version of the tx: must be fixed before we allow module updates
                        changes: nested_writeset_changes
                            .into_iter()
                            .flatten()
                            .collect::<Vec<WriteSetChange>>(),
                        events: self.try_into_events(&events)?,
                    }),
                }
```

**File:** api/types/src/convert.rs (L598-623)
```rust
    pub fn try_into_write_set_changes(
        &self,
        state_key: StateKey,
        op: WriteOp,
    ) -> Result<Vec<WriteSetChange>> {
        let hash = state_key.hash().to_hex_literal();
        let state_key = state_key.inner();
        match state_key {
            StateKeyInner::AccessPath(access_path) => {
                self.try_access_path_into_write_set_changes(hash, access_path, op)
            },
            StateKeyInner::TableItem { handle, key } => {
                vec![self.try_table_item_into_write_set_change(hash, *handle, key.to_owned(), op)]
                    .into_iter()
                    .collect()
            },
            StateKeyInner::Raw(_) => Err(format_err!(
                "Can't convert account raw key {:?} to WriteSetChange",
                state_key
            )),
            StateKeyInner::TradingNative(_) => Err(format_err!(
                "Can't convert trading-native key {:?} to WriteSetChange",
                state_key
            )),
        }
    }
```

**File:** types/src/state_store/state_key/inner.rs (L17-41)
```rust
#[repr(u8)]
#[derive(Clone, Debug, FromPrimitive, ToPrimitive)]
pub enum StateKeyTag {
    AccessPath,
    TableItem,
    /// Umbrella for the trading-native subsystem. Sub-entities
    /// (Position, future Collateral / Order / ...) are distinguished
    /// by [`TradingNativeKeyTag`] inside the payload, not by a
    /// top-level tag. This keeps the top-level tag space focused on
    /// subsystem-level categories.
    TradingNative = 2,
    Raw = 255,
}

/// Sub-tag distinguishing entities inside the
/// [`StateKeyInner::TradingNative`] umbrella. Encoded as the first
/// byte of the payload after the top-level [`StateKeyTag::TradingNative`]
/// byte. Variant ordinals are part of the on-disk byte format —
/// **do not reorder or insert before existing entries**.
#[repr(u8)]
#[derive(Clone, Debug, FromPrimitive, ToPrimitive)]
pub enum TradingNativeKeyTag {
    Position = 0,
}

```
