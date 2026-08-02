No vulnerability found for this question.

**Rationale:** The invariant claimed to be missing is actually enforced structurally, not just by convention.

The `OnChainConfig` trait's `CONFIG_ID` is built from `Self::ADDRESS`, `Self::MODULE_IDENTIFIER`, `Self::TYPE_IDENTIFIER` strings, but this `CONFIG_ID` (and its string address component) is never used to resolve the actual storage location for fetching a config. [1](#0-0) 

The actual resource-fetch path (`fetch_config` → `fetch_config_and_bytes` → `StateKey::on_chain_config::<Self>()`) resolves the state key via `Self::resource(T::address(), &T::struct_tag())`, where `T::address()` has a hardcoded default returning `&CORE_CODE_ADDRESS` (not derived from the `ADDRESS` string constant at all), and `struct_tag()` calls `struct_tag_for_config`, which also hardcodes `address: CORE_CODE_ADDRESS` in the constructed `StructTag`. [2](#0-1) [3](#0-2) 

`ApprovedExecutionHashes` only overrides `MODULE_IDENTIFIER` and `TYPE_IDENTIFIER`, not `address()`, so its Rust-side resolution is always pinned to `CORE_CODE_ADDRESS` (0x1). [4](#0-3) 

The only place that constructs a query string using `T::ADDRESS` is `aptos-release-builder`'s helper `fetch_config`, but even there the account address parameter passed to `get_account_resource_bytes` is hardcoded to `CORE_CODE_ADDRESS`, not derived from the string — the string is merely the resource type path used against that fixed account. [5](#0-4) 

Because both `T::address()`'s default implementation and `struct_tag_for_config`/`access_path_for_config` hardcode `CORE_CODE_ADDRESS` at the Rust type level — independent of any string collision an attacker-deployed module could produce — there is no code path in this repository where `ConfigID`'s string components alone determine which account/address is queried. A module published by an unprivileged deployer at a non-0x1 address, even with an identical module/struct name, cannot be resolved by any of these fetch paths, since the address is never taken from user-controlled or string-derived data. [6](#0-5) [7](#0-6)

### Citations

**File:** types/src/on_chain_config/mod.rs (L149-157)
```rust
pub trait OnChainConfig: Send + Sync + DeserializeOwned {
    const ADDRESS: &'static str = "0x1";
    const MODULE_IDENTIFIER: &'static str;
    const TYPE_IDENTIFIER: &'static str;
    const CONFIG_ID: ConfigID = ConfigID(
        Self::ADDRESS,
        Self::MODULE_IDENTIFIER,
        Self::TYPE_IDENTIFIER,
    );
```

**File:** types/src/on_chain_config/mod.rs (L180-238)
```rust
    fn fetch_config<T>(storage: &T) -> Result<Option<Self>>
    where
        T: ConfigStorage + ?Sized,
    {
        Ok(Self::fetch_config_and_bytes(storage)?.map(|(config, _bytes)| config))
    }

    /// Same as [Self::fetch_config], but also returns the underlying bytes that were used to
    /// deserialize into config.
    fn fetch_config_and_bytes<T>(storage: &T) -> Result<Option<(Self, Bytes)>>
    where
        T: ConfigStorage + ?Sized,
    {
        let state_key = StateKey::on_chain_config::<Self>()?;
        let Some(bytes) = storage.fetch_config_bytes(&state_key)? else {
            return Ok(None);
        };
        let config = Self::deserialize_into_config(&bytes)?;
        Ok(Some((config, bytes)))
    }

    fn address() -> &'static AccountAddress {
        &CORE_CODE_ADDRESS
    }

    fn struct_tag() -> StructTag {
        struct_tag_for_config(Self::CONFIG_ID)
    }
}

impl<S: StateView> ConfigStorage for S {
    fn fetch_config_bytes(&self, state_key: &StateKey) -> Result<Option<Bytes>> {
        Ok(self.get_state_value(state_key)?.map(|s| s.bytes().clone()))
    }
}

pub fn new_epoch_event_key() -> EventKey {
    EventKey::new(2, CORE_CODE_ADDRESS)
}

pub fn new_epoch_event_type_tag() -> TypeTag {
    TypeTag::from_str("0x1::reconfiguration::NewEpoch").expect("cannot fail")
}

pub fn access_path_for_config(config_id: ConfigID) -> anyhow::Result<AccessPath> {
    let struct_tag = struct_tag_for_config(config_id);
    Ok(AccessPath::new(
        CORE_CODE_ADDRESS,
        AccessPath::resource_path_vec(struct_tag)?,
    ))
}

pub fn struct_tag_for_config(config_id: ConfigID) -> StructTag {
    StructTag {
        address: CORE_CODE_ADDRESS,
        module: Identifier::new(config_id.1).expect("fail to make identifier"),
        name: Identifier::new(config_id.2).expect("fail to make identifier"),
        type_args: vec![],
    }
```

**File:** types/src/state_store/state_key/mod.rs (L228-230)
```rust
    pub fn on_chain_config<T: OnChainConfig>() -> Result<Self> {
        Self::resource(T::address(), &T::struct_tag())
    }
```

**File:** types/src/on_chain_config/approved_execution_hashes.rs (L19-22)
```rust
impl OnChainConfig for ApprovedExecutionHashes {
    const MODULE_IDENTIFIER: &'static str = "aptos_governance";
    const TYPE_IDENTIFIER: &'static str = "ApprovedExecutionHashes";
}
```

**File:** aptos-move/aptos-release-builder/src/components/mod.rs (L611-629)
```rust
pub fn fetch_config<T: OnChainConfig>(client: &Client) -> Result<T> {
    T::deserialize_into_config(
        block_on(async {
            client
                .get_account_resource_bytes(
                    CORE_CODE_ADDRESS,
                    format!(
                        "{}::{}::{}",
                        T::ADDRESS,
                        T::MODULE_IDENTIFIER,
                        T::TYPE_IDENTIFIER
                    )
                    .as_str(),
                )
                .await
        })?
        .inner(),
    )
}
```
