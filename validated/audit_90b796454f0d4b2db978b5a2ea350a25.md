No vulnerability found for this question.

**Analysis:**

The exploit hypothesis requires that a state-sync-restored node could decode a different `OnChainRandomnessConfig` than a live-executed node from the *same* committed state value. This doesn't hold because:

1. **Restore is proof-verified, not independently computed.** State-sync/restore paths reconstruct the JMH by replaying transactions or verifying state chunks against Merkle proofs bound to the accumulator root — the restored leaf's raw bytes are cryptographically constrained to equal the originally committed bytes, not independently re-derived. If they diverged, proof verification would fail and the restore would be rejected, not silently produce a different byte value.

2. **BCS deserialization is deterministic.** `RandomnessConfigMoveStruct` is a plain `#[derive(Deserialize)]` struct wrapping `MoveAny`, decoded via `bcs::from_bytes` in `OnChainConfig::deserialize_into_config` and `fetch_config`/`fetch_config_and_bytes` [1](#0-0) . Given identical input bytes, this decoding is deterministic across any node, restored or live.

3. **The `TryFrom<RandomnessConfigMoveStruct>` conversion is a pure match on `variant.type_name`** with no reliance on node identity, execution history, or non-deterministic state [2](#0-1) . The same bytes always produce the same `OnChainRandomnessConfig`.

4. **`payload.get::<RandomnessConfigMoveStruct>()` in `dkg/src/epoch_manager.rs::start_new_epoch`** simply calls into this same deserialization path via the `OnChainConfigPayload` abstraction [3](#0-2) , with no special-casing for restored vs. live nodes.

For this to be a real hard-fork-only divergence, one would need to find a place where restore reinterprets or re-derives the byte value rather than verifying it byte-for-byte against a proof — no such logic exists in the reviewed randomness-config path. The premise conflates "could restore produce different bytes" (a general storage/proof-integrity question, correctly guarded by Merkle proof verification during restore) with "could identical bytes decode differently" (not possible given deterministic BCS decoding). No code path was found that violates either guarantee.

### Citations

**File:** types/src/on_chain_config/mod.rs (L166-199)
```rust
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

    /// TODO: This does not work if `T`'s reflection on the Move side is using resource groups.
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

**File:** types/src/on_chain_config/randomness_config.rs (L143-164)
```rust
impl TryFrom<RandomnessConfigMoveStruct> for OnChainRandomnessConfig {
    type Error = anyhow::Error;

    fn try_from(value: RandomnessConfigMoveStruct) -> Result<Self, Self::Error> {
        let RandomnessConfigMoveStruct { variant } = value;
        let variant_type_name = variant.type_name.as_str();
        match variant_type_name {
            ConfigOff::MOVE_TYPE_NAME => Ok(Self::Off),
            ConfigV1::MOVE_TYPE_NAME => {
                let v1 = MoveAny::unpack(ConfigV1::MOVE_TYPE_NAME, variant)
                    .map_err(|e| anyhow!("unpack as v1 failed: {e}"))?;
                Ok(Self::V1(v1))
            },
            ConfigV2::MOVE_TYPE_NAME => {
                let v2 = MoveAny::unpack(ConfigV2::MOVE_TYPE_NAME, variant)
                    .map_err(|e| anyhow!("unpack as v2 failed: {e}"))?;
                Ok(Self::V2(v2))
            },
            _ => Err(anyhow!("unknown variant type")),
        }
    }
}
```

**File:** dkg/src/epoch_manager.rs (L241-261)
```rust
        let onchain_randomness_config_seq_num = payload
            .get::<RandomnessConfigSeqNum>()
            .unwrap_or_else(|_| RandomnessConfigSeqNum::default_if_missing());

        let randomness_config_move_struct = payload.get::<RandomnessConfigMoveStruct>();

        info!(
            epoch = epoch_state.epoch,
            local = self.randomness_override_seq_num,
            onchain = onchain_randomness_config_seq_num.seq_num,
            "Checking randomness config override."
        );
        if self.randomness_override_seq_num > onchain_randomness_config_seq_num.seq_num {
            warn!("Randomness will be force-disabled by local config!");
        }

        let onchain_randomness_config = OnChainRandomnessConfig::from_configs(
            self.randomness_override_seq_num,
            onchain_randomness_config_seq_num.seq_num,
            randomness_config_move_struct.ok(),
        );
```
