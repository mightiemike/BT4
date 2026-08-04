No vulnerability found for this question.

The premise doesn't hold up under review of the actual code:

1. `ValidatorSet` does not override `deserialize_into_config` — it relies purely on the default single-round `bcs::from_bytes::<Self>` via `OnChainConfig::deserialize_into_config`/`deserialize_default_impl`. [1](#0-0) [2](#0-1) 

2. Both code paths mentioned — `DbBackedOnChainConfig::get::<T>` (used by the state-sync reconfig subscriber) and the generic `OnChainConfig::fetch_config`/`fetch_config_and_bytes` (used by executor and other consumers) — call the exact same `T::deserialize_into_config(&bytes)` function on the exact same raw bytes. [3](#0-2) [4](#0-3)  Since BCS deserialization is deterministic (a byte sequence either successfully parses to a unique `ValidatorSet` value or fails outright), there is no way for the "same raw bytes" to produce two different `ValidatorSet` structs across these two call sites. There's no divergence to exploit here — both are literally the same code (`T::deserialize_into_config`), not two independently-implemented parsers.

3. The derivation of `ValidatorVerifier` from `ValidatorSet` via `From<&ValidatorSet> for ValidatorVerifier` only reads `validator_set.payload()`, which chains `active_validators` and `pending_inactive` — `pending_active` is explicitly excluded from consensus-verifier construction by design (pending-active validators are not yet active for the current epoch). [5](#0-4) [6](#0-5)  So a `ValidatorSet` with empty `active_validators`/`pending_inactive` but non-empty `pending_active` deterministically produces the same (empty) `ValidatorVerifier` in every code path that performs this conversion — there is no "size overflow" or length-dependent divergence; BCS vectors are length-prefixed and unambiguous, and the conversion logic is a single deterministic function shared by all consumers.

4. No "hard-fork-only divergence" or authenticated-response misbinding is introduced: the byte value committed to state (behind the Merkle-authenticated `StateKey::on_chain_config::<ValidatorSet>()`) is parsed identically everywhere it's consumed, since it's the same trait method invoked through the same default implementation. [7](#0-6) 

The proof-of-concept idea in the question (feed the same raw bytes to both paths and compare) would simply show identical output, because both paths literally call the same function on the same bytes — there is no alternate deserialization logic to diverge from.

### Citations

**File:** types/src/on_chain_config/validator_set.rs (L57-61)
```rust
    pub fn payload(&self) -> impl Iterator<Item = &ValidatorInfo> {
        self.active_validators
            .iter()
            .chain(self.pending_inactive.iter())
    }
```

**File:** types/src/on_chain_config/validator_set.rs (L93-97)
```rust
impl OnChainConfig for ValidatorSet {
    // validator_set_address
    const MODULE_IDENTIFIER: &'static str = "stake";
    const TYPE_IDENTIFIER: &'static str = "ValidatorSet";
}
```

**File:** types/src/on_chain_config/mod.rs (L149-177)
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

    // Single-round BCS deserialization from bytes to `Self`
    // This is the expected deserialization pattern if the Rust representation lives natively in Move.
    // but sometimes `deserialize_into_config` may need an extra customized round of deserialization
    // when the data is represented as opaque vec<u8> in Move.
    // In the override, we can reuse this default logic via this function
    // Note: we cannot directly call the default `deserialize_into_config` implementation
    // in its override - this will just refer to the override implementation itself
    fn deserialize_default_impl(bytes: &[u8]) -> Result<Self> {
        bcs::from_bytes::<Self>(bytes)
            .map_err(|e| format_err!("[on-chain config] Failed to deserialize into config: {}", e))
    }

    // Function for deserializing bytes to `Self`
    // It will by default try one round of BCS deserialization directly to `Self`
    // The implementation for the concrete type should override this function if this
    // logic needs to be customized
    fn deserialize_into_config(bytes: &[u8]) -> Result<Self> {
        Self::deserialize_default_impl(bytes)
    }
```

**File:** types/src/on_chain_config/mod.rs (L180-199)
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
```

**File:** state-sync/inter-component/event-notifications/src/lib.rs (L399-414)
```rust
impl OnChainConfigProvider for DbBackedOnChainConfig {
    fn get<T: OnChainConfig>(&self) -> Result<T> {
        let bytes = self
            .reader
            .get_state_value_by_version(&StateKey::on_chain_config::<T>()?, self.version)?
            .ok_or_else(|| {
                anyhow!(
                    "no config {} found in aptos root account state",
                    T::CONFIG_ID
                )
            })?
            .bytes()
            .clone();

        T::deserialize_into_config(&bytes)
    }
```

**File:** types/src/validator_verifier.rs (L576-599)
```rust
impl From<&ValidatorSet> for ValidatorVerifier {
    fn from(validator_set: &ValidatorSet) -> Self {
        let sorted_validator_infos: BTreeMap<u64, ValidatorConsensusInfo> = validator_set
            .payload()
            .map(|info| {
                (
                    info.config().validator_index,
                    ValidatorConsensusInfo::new(
                        info.account_address,
                        info.consensus_public_key().clone(),
                        info.consensus_voting_power(),
                    ),
                )
            })
            .collect();
        let validator_infos: Vec<_> = sorted_validator_infos.values().cloned().collect();
        for info in validator_set.payload() {
            assert_eq!(
                validator_infos[info.config().validator_index as usize].address,
                info.account_address
            );
        }
        ValidatorVerifier::new(validator_infos)
    }
```
