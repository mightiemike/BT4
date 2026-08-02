Found the analog. The candidate root cause is in `into_transaction_info` in `api/types/src/convert.rs`, where write-set-to-API-change conversion errors are silently swallowed rather than propagated, unlike the sibling function `try_into_write_set_payload` which propagates the same errors with `?`.

### Title
Authenticated transaction API response silently drops write-set changes on conversion error, desyncing `changes` from committed ledger state - (File: api/types/src/convert.rs)

### Summary
`into_transaction_info` (used by `try_into_onchain_transaction`, the core conversion path for `GET /transactions` REST responses) builds the `changes` field of `TransactionInfo` by iterating the committed `WriteSet` and calling `try_into_write_set_changes(sk, wo)`, but discards any per-entry error with `.ok()` instead of propagating it: [1](#0-0) 

Compare this to the structurally identical conversion in `try_into_write_set_payload`, which correctly propagates errors via `.collect::<Result<Vec<Vec<_>>>>()?`: [2](#0-1) 

`try_into_write_set_changes` can genuinely fail — e.g. for `StateKeyInner::Raw` and `StateKeyInner::TradingNative` keys it unconditionally returns `Err`, and for `AccessPath` entries it can fail via `try_into_resource`/`try_into_resources_from_resource_group`/`MoveModuleBytecode::try_parse_abi` (type layout resolution, ABI/BCS decode errors, etc.): [3](#0-2) [4](#0-3) 

### Finding Description
The bug-class analog to "latestAnswer returning 0 on stale/failed price and silently corrupting downstream math" is: an authenticated, version-bound API response (`TransactionInfo.changes`) is constructed from the real committed `WriteSet`, but any decode/conversion failure for an individual write op is swallowed (`.ok()` inside `filter_map`) and that write op is simply omitted from the response, with `success: true` (or whatever the real VM status is) still reported unchanged. The response's `hash`, `accumulator_root_hash`, `state_change_hash`, and `gas_used` fields are computed from the authoritative `TransactionInfo`/accumulator root that is unaffected — but the `changes` list, which is the client-visible, authenticated statement of what state was written at that version, no longer reflects the true committed write set. This is the state/response consistency failure analogous to a silently-degraded oracle read: the caller has no signal that data is missing, and the object/version binding in the rest of the response falsely implies completeness.

### Impact Explanation
Clients, indexers, or downstream verifiers that reconstruct or reason about account/resource state purely from the REST API's `changes` field (a common pattern for light clients and off-chain services that don't run a full VM) can be given an incomplete/incorrect picture of what was written at a given, correctly-hashed version — while all surrounding proof fields (hash, root hash) appear valid and unchanged, giving false confidence in completeness. This does not corrupt the actual ledger/accumulator/JMT commitment (execution and storage commit paths are untouched), so it falls short of "wrong root accepted as valid" — it is a lower-severity authenticated-response integrity gap rather than a consensus/commit-level break.

### Likelihood Explanation
Requires a WriteOp whose `StateKey` is `Raw`/`TradingNative`, or whose module/resource bytes fail ABI parsing or type-layout-based resource decoding at the version being read (e.g., due to versioned type definition mismatches noted in the adjacent `TODO` comment, or malformed/legacy data). These are realistic, reachable conditions during normal read-API usage on mainnet fullnodes, not requiring privileged access — but they require a specific decode-failure condition to trigger, so it is not universally reproducible on arbitrary transactions.

### Recommendation
Change `into_transaction_info`'s `changes` computation to propagate conversion errors (mirroring `try_into_write_set_payload`), turning `into_transaction_info` into a `Result`-returning function whose failure surfaces as an explicit API error rather than a silently truncated `changes` list, so callers cannot mistake a partial write-set view for a complete one.

### Proof of Concept
1. Commit a transaction whose write set includes a `StateKey::Raw` entry, or a resource/resource-group write whose bytes fail `try_into_resource`/`try_into_resources_from_resource_group` decoding at read time (e.g. due to a type layout unavailable at the querying node, per the `TODO` at line 310/585).
2. Query the transaction via the REST API path that calls `try_into_onchain_transaction` → `into_transaction_info`.
3. Observe: the response returns HTTP 200 with `success: true` and valid `hash`/`accumulator_root_hash`, but the failing write op is missing from `changes`, with no error or indication of truncation — unlike `try_into_write_set_payload`, which would instead fail the whole request.

Note: I was not able to fully verify at which specific versions/states such decode failures are actually reachable in practice (this depends on runtime data not visible via static search), so likelihood should be validated against real transaction/resource data before treating this as a confirmed, exploitable-on-mainnet condition.

### Citations

**File:** api/types/src/convert.rs (L309-318)
```rust
            accumulator_root_hash: accumulator_root_hash.into(),
            // TODO: the resource value is interpreted by the type definition at the version of the converter, not the version of the tx: must be fixed before we allow module updates
            changes: write_set
                .into_write_op_iter()
                .filter_map(|(sk, wo)| self.try_into_write_set_changes(sk, wo).ok())
                .flatten()
                .collect(),
            block_height: None,
            epoch: None,
        }
```

**File:** api/types/src/convert.rs (L577-595)
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
            },
        };
        Ok(ret)
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

**File:** api/types/src/convert.rs (L649-671)
```rust
            Some(bytes) => match access_path.get_path() {
                Path::Code(_) => vec![WriteSetChange::WriteModule(WriteModule {
                    address: access_path.address.into(),
                    state_key_hash,
                    data: MoveModuleBytecode::new(bytes.to_vec()).try_parse_abi()?,
                })],
                Path::Resource(typ) => vec![WriteSetChange::WriteResource(WriteResource {
                    address: access_path.address.into(),
                    state_key_hash,
                    data: self.try_into_resource(&typ, bytes)?,
                })],
                Path::ResourceGroup(_) => self
                    .try_into_resources_from_resource_group(bytes)?
                    .into_iter()
                    .map(|data| {
                        WriteSetChange::WriteResource(WriteResource {
                            address: access_path.address.into(),
                            state_key_hash: state_key_hash.clone(),
                            data,
                        })
                    })
                    .collect::<Vec<_>>(),
            },
```
