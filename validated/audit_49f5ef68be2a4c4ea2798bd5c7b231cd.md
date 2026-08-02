[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** types/src/state_store/state_key/mod.rs (L262-274)
```rust
    pub fn table_item(handle: &TableHandle, key: &[u8]) -> Self {
        Self(
            REGISTRY
                .table_item(handle, key)
                .get_or_add(handle, key, || {
                    Ok(StateKeyInner::TableItem {
                        handle: *handle,
                        key: key.to_vec(),
                    })
                })
                .expect("only possible error is resource path serialization"),
        )
    }
```

**File:** types/src/state_store/state_key/inner.rs (L112-116)
```rust
            StateKeyInner::TableItem { handle, key } => {
                writer.write_all(&[StateKeyTag::TableItem as u8])?;
                bcs::serialize_into(&mut writer, &handle)?;
                writer.write_all(key)?;
            },
```

**File:** api/src/state.rs (L178-187)
```rust
    /// Get raw table item
    ///
    /// Get a table item at a specific ledger version from the table identified by {table_handle}
    /// in the path and the "key" (RawTableItemRequest) provided in the request body.
    ///
    /// The `get_raw_table_item` requires only a serialized key comparing to the full move type information
    /// comparing to the `get_table_item` api, and can only return the query in the bcs format.
    ///
    /// The Aptos nodes prune account state history, via a configurable time window.
    /// If the requested ledger version has been pruned, the server responds with a 410.
```

**File:** api/src/state.rs (L429-446)
```rust
        // Retrieve value from the state key
        let state_key = StateKey::table_item(&TableHandle(table_handle.into()), &raw_key);
        let bytes = state_view
            .get_state_value_bytes(&state_key)
            .context(format!(
                "Failed when trying to retrieve table item from the DB with key: {}",
                key
            ))
            .map_err(|err| {
                BasicErrorWith404::internal_with_code(
                    err,
                    AptosErrorCode::InternalError,
                    &ledger_info,
                )
            })?
            .ok_or_else(|| {
                table_item_not_found(table_handle, &key, ledger_version, &ledger_info)
            })?;
```
