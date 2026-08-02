I found a concrete unprivileged silent-error-drop analog in the Aptos REST API's transaction-info converter, matching the same bug class as the reported `IWETH.transfer` unchecked-return-value issue: a fallible conversion whose `Result::Err` is discarded, letting the code silently proceed as if nothing happened.

### Title
Silent loss of committed write-set changes in transaction API responses via `.ok()` in `into_transaction_info` - (File: api/types/src/convert.rs)

### Summary
`MoveConverter::into_transaction_info`, used to build the authenticated `TransactionInfo.changes` field returned by the node's REST API for every committed transaction, converts each write op in the transaction's `WriteSet` into API-level `WriteSetChange` entries via `try_into_write_set_changes`. The result of that fallible conversion is discarded with `.ok()` inside a `filter_map`, so any write op that fails to convert is silently dropped from the response instead of surfacing an error. [1](#0-0) 

### Finding Description
`into_transaction_info` builds the `changes` list for the API's `TransactionInfo` object directly from the real committed `WriteSet`: [2](#0-1) 

Each entry goes through `try_into_write_set_changes`, which returns `Result<Vec<WriteSetChange>>` and can fail in several concrete ways:
- `StateKeyInner::Raw` and `StateKeyInner::TradingNative` keys always return `Err`.
- `try_access_path_into_write_set_changes` propagates decoding errors, e.g. `MoveModuleBytecode::try_parse_abi()?`, `self.try_into_resource(&typ, bytes)?`, and `self.try_into_resources_from_resource_group(bytes)?`, any of which fail if the current on-chain module layout used to interpret the historical value differs from the layout in effect when the transaction executed. [3](#0-2) 

The code's own TODO acknowledges this exact hazard — resource values are interpreted using the *current* type definitions, not the type definitions in effect *at the transaction's version*: [4](#0-3) 

Because `.ok()` swallows the error, a decode failure (e.g. following a module upgrade that changes a struct's layout) or an unsupported key kind causes that write op to vanish from `changes` with no error flag, no truncation indicator, and `success: true` still reported. The API response is therefore silently bound to an incomplete/incorrect view of the committed write set for that version, even though the actual on-chain `WriteSet`, accumulator, and proofs are untouched.

### Impact Explanation
This breaks the "authenticated API output must stay bound to the right ledger version/object" invariant: a consumer trusting `GET /transactions/by_version/{version}` (or the equivalent gRPC/JSON conversion path shared by `try_into_onchain_transaction`) to enumerate all state changes of a committed, successful transaction can silently receive a partial list. Downstream systems (indexers, bridges, auditing/compliance tooling, block explorers) that reconcile balances or state solely from this API field would under-count state changes without any signal that data was dropped, which is unprivileged and network-wide since any full node's REST API is affected identically once triggered.

### Likelihood Explanation
The trigger condition (module upgrade changing a resource/resource-group layout between the historical transaction's execution and the version of the module used by the querying node, or presence of table/raw/trading-native keys not covered by the match arms) is realistic and does not require any privileged access — it can occur from ordinary contract upgrades on mainnet, and any client querying older transactions after an upgrade can hit it.

### Recommendation
Do not use `.ok()`/`filter_map` to discard conversion errors in `into_transaction_info`. Either propagate the error (returning `Result<TransactionInfo>` and surfacing an explicit error/partial flag to the caller), or clearly mark the response as incomplete (e.g. an explicit "truncated"/"undecodable" field) so consumers cannot silently trust an incomplete `changes` list as the full committed state delta.

### Proof of Concept
1. Deploy a module with a resource type `R`.
2. Execute a transaction that writes `R`, producing a `WriteSet` entry with `StateKeyInner::AccessPath(Path::Resource(R))`.
3. Upgrade the module so `R`'s layout is incompatible with the historical bytes (e.g. change a field's type).
4. Query `GET /transactions/by_version/{version}` for the original transaction.
5. Observe that `try_into_resource(&typ, bytes)` now fails on the old bytes, `try_into_write_set_changes` returns `Err`, `.ok()` discards it in `into_transaction_info`, and the returned `TransactionInfo.changes` list omits the `WriteResource` entry for `R` entirely — while `success` is still reported as `true` — even though the entry is present, hashed, and committed in the on-chain `WriteSet`/accumulator.

### Citations

**File:** api/types/src/convert.rs (L292-319)
```rust
    pub fn into_transaction_info(
        &self,
        version: u64,
        info: &aptos_types::transaction::TransactionInfo,
        accumulator_root_hash: HashValue,
        write_set: aptos_types::write_set::WriteSet,
        txn_aux_data: Option<TransactionAuxiliaryData>,
    ) -> TransactionInfo {
        TransactionInfo {
            version: version.into(),
            hash: info.transaction_hash().into(),
            state_change_hash: info.state_change_hash().into(),
            event_root_hash: info.event_root_hash().into(),
            state_checkpoint_hash: info.state_checkpoint_hash().map(|h| h.into()),
            gas_used: info.gas_used().into(),
            success: info.status().is_success(),
            vm_status: self.explain_vm_status(info.status(), txn_aux_data),
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
    }
```

**File:** api/types/src/convert.rs (L598-674)
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

    pub fn try_access_path_into_write_set_changes(
        &self,
        state_key_hash: String,
        access_path: &AccessPath,
        op: WriteOp,
    ) -> Result<Vec<WriteSetChange>> {
        let ret = match op.bytes() {
            None => match access_path.get_path() {
                Path::Code(module_id) => vec![WriteSetChange::DeleteModule(DeleteModule {
                    address: access_path.address.into(),
                    state_key_hash,
                    module: module_id.into(),
                })],
                Path::Resource(typ) => vec![WriteSetChange::DeleteResource(DeleteResource {
                    address: access_path.address.into(),
                    state_key_hash,
                    resource: typ.into(),
                })],
                Path::ResourceGroup(typ) => vec![WriteSetChange::DeleteResource(DeleteResource {
                    address: access_path.address.into(),
                    state_key_hash,
                    resource: typ.into(),
                })],
            },
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
        };
        Ok(ret)
    }
```
