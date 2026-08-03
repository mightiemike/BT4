No vulnerability found for this question.

**Analysis:**

The `TokenEventStoreV1Resource.extension` field is `Option<AnyResource>`, where `AnyResource` has private `type_name: String` and `data: Vec<u8>` fields [1](#0-0) . The struct exposes only a getter, `extension()`, and no code path in this repository actually dispatches deserialization of the `data` bytes based on the `type_name` string [2](#0-1) .

Key points that invalidate the premise:

1. **`type_name` is not attacker-freely-settable at the Move layer.** The only way to construct an `Any`/`AnyResource` on-chain is via `aptos_std::any::pack<T>`, which derives `type_name` from `type_info::type_name<T>()` of the actual generic type `T` used — it is not a free-form string the caller can set independently of the real type of `data` [3](#0-2) . Any consumer calling `unpack<T>` re-validates `type_info::type_name<T>() == self.type_name` before decoding, so a mismatched pairing aborts rather than silently misinterpreting bytes [4](#0-3) .

2. **No indexer/API code in this repo dispatches on `type_name`.** The `event_v2_translator.rs` code that reads `TokenEventStoreV1Resource` only accesses the various `EventHandle` fields, never the `extension` field, and never branches on `type_name` [5](#0-4) . The REST API's `resource()` handler resolves and decodes resources strictly by the `StructTag` given in the request path via `try_into_resource(&tag, &bytes)`, never by any embedded `type_name` string inside resource data [6](#0-5) .

3. **The premise describes a hypothetical external/downstream consumer bug**, not a defect in aptos-core's storage, proof, executor, or authenticated-response layers. Since no in-repo authenticated response or proof-bearing path performs `type_name`-based dispatch for `AnyResource`/`TokenEventStoreV1Resource`, there is no state-commitment, proof-integrity, or ledger-object misbinding introduced by crafting a spoofed `type_name`.

Because the vulnerable dispatch pattern described in the question does not exist anywhere in the reviewed code, this does not meet the state-integrity gate for acceptance.

### Citations

**File:** types/src/account_config/resources/any.rs (L14-18)
```rust
#[derive(Debug, Clone, Eq, PartialEq, Serialize, Deserialize)]
pub struct AnyResource {
    type_name: String,
    data: Vec<u8>,
}
```

**File:** types/src/account_config/resources/token_event_store_v1.rs (L89-91)
```rust
    pub fn extension(&self) -> &Option<AnyResource> {
        &self.extension
    }
```

**File:** aptos-move/framework/aptos-stdlib/sources/any.move (L31-36)
```text
    public fun pack<T: drop + store>(x: T): Any {
        Any {
            type_name: type_info::type_name<T>(),
            data: to_bytes(&x)
        }
    }
```

**File:** aptos-move/framework/aptos-stdlib/sources/any.move (L38-42)
```text
    /// Unpack a value from the `Any` representation. This aborts if the value has not the expected type `T`.
    public fun unpack<T>(self: Any): T {
        assert!(type_info::type_name<T>() == self.type_name, error::invalid_argument(ETYPE_MISMATCH));
        from_bytes<T>(self.data)
    }
```

**File:** storage/indexer/src/event_v2_translator.rs (L1001-1015)
```rust
        let struct_tag = StructTag::from_str("0x3::token_event_store::TokenEventStoreV1")?;
        let (key, sequence_number) = if let Some(state_value_bytes) = engine
            .get_state_value_bytes_for_resource(
                collection_description_mutate.creator_addr(),
                &struct_tag,
            )? {
            let object_resource: TokenEventStoreV1Resource = bcs::from_bytes(&state_value_bytes)?;
            let key = *object_resource.collection_description_mutate_events().key();
            let sequence_number = engine.get_next_sequence_number(
                &key,
                object_resource
                    .collection_description_mutate_events()
                    .count(),
            )?;
            (key, sequence_number)
```

**File:** api/src/state.rs (L288-311)
```rust
        let (ledger_info, ledger_version, state_view) = self.context.state_view(ledger_version)?;
        let bytes = state_view
            .as_converter(self.context.db.clone(), self.context.indexer_reader.clone())
            .find_resource(&state_view, address, &tag)
            .context(format!(
                "Failed to query DB to check for {} at {}",
                tag.to_canonical_string(),
                address
            ))
            .map_err(|err| {
                BasicErrorWith404::internal_with_code(
                    err,
                    AptosErrorCode::InternalError,
                    &ledger_info,
                )
            })?
            .ok_or_else(|| resource_not_found(address, &tag, ledger_version, &ledger_info))?;

        match accept_type {
            AcceptType::Json => {
                let resource = state_view
                    .as_converter(self.context.db.clone(), self.context.indexer_reader.clone())
                    .try_into_resource(&tag, &bytes)
                    .context("Failed to deserialize resource data retrieved from DB")
```
