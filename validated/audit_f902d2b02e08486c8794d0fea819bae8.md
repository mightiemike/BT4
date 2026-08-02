No vulnerability found for this question.

**Analysis:** `OnChainConsensusConfig` is a plain Rust enum deserialized directly from on-chain resource bytes via BCS in `deserialize_into_config` [1](#0-0) . BCS enum deserialization reads the variant discriminant directly from the serialized bytes on-chain — there is no lossy or ambiguous "migration helper" in this file (or anywhere in the reviewed restore/replay code) that reinterprets a `V3` resource's bytes as `V4`/`V5`. The only code that converts between variants is `enable_validator_txns`, which is an explicit, intentional upgrade path (e.g. `V3` → `V5`) invoked only through governance-controlled config changes, not through replay/restore of historical state [2](#0-1) .

`window_size()` itself correctly matches on the actual deserialized variant tag and returns `None` for `V1`/`V2`/`V3` and `Some`/`None` per the stored field for `V4`/`V5` [3](#0-2) . Since the variant tag is embedded in the committed BCS bytes and read deterministically, a "buggy migration helper reusing this file's variants" mislabeling `V3` as `V4` would require an external tool to fabricate incorrect bytes — this is a hypothetical bug in a tool outside the reviewed repo, not a root cause here, and falls under "depends on trusted operator/tooling mistakes alone" per the decision standard. Additionally, `OnChainConsensusConfig` governs consensus/execution-pool windowing for block construction, not the VM's transaction execution/write-set computation, so even a config mismatch would not by itself cause committed state (`TransactionInfo`, write sets) to diverge from correct VM output without additional unproven mechanisms.

No actual code path in this repository was found where restore/replay/storage logic reinterprets a `V1`-`V3` on-chain consensus-config resource as `V4`/`V5`.

### Citations

**File:** types/src/on_chain_config/consensus_config.rs (L324-389)
```rust
    pub fn enable_validator_txns(&mut self) {
        let new_self = match std::mem::take(self) {
            Self::V1(config) => Self::V5 {
                alg: ConsensusAlgorithmConfig::JolteonV2 {
                    main: config,
                    quorum_store_enabled: false,
                    order_vote_enabled: false,
                },
                vtxn: ValidatorTxnConfig::default_enabled(),
                window_size: DEFAULT_WINDOW_SIZE,
                rand_check_enabled: true,
            },
            Self::V2(config) => Self::V5 {
                alg: ConsensusAlgorithmConfig::JolteonV2 {
                    main: config,
                    quorum_store_enabled: true,
                    order_vote_enabled: false,
                },
                vtxn: ValidatorTxnConfig::default_enabled(),
                window_size: DEFAULT_WINDOW_SIZE,
                rand_check_enabled: true,
            },
            Self::V3 {
                vtxn: ValidatorTxnConfig::V0,
                alg,
            } => Self::V5 {
                alg,
                vtxn: ValidatorTxnConfig::default_enabled(),
                window_size: DEFAULT_WINDOW_SIZE,
                rand_check_enabled: true,
            },
            Self::V4 {
                alg,
                vtxn: ValidatorTxnConfig::V0,
                window_size,
            } => Self::V4 {
                alg,
                vtxn: ValidatorTxnConfig::default_enabled(),
                window_size,
            },
            Self::V5 {
                alg,
                vtxn: ValidatorTxnConfig::V0,
                window_size,
                rand_check_enabled: rand_check,
            } => Self::V5 {
                alg,
                vtxn: ValidatorTxnConfig::default_enabled(),
                window_size,
                rand_check_enabled: rand_check,
            },
            item @ Self::V3 {
                vtxn: ValidatorTxnConfig::V1 { .. },
                ..
            } => item,
            item @ Self::V4 {
                vtxn: ValidatorTxnConfig::V1 { .. },
                ..
            } => item,
            item @ Self::V5 {
                vtxn: ValidatorTxnConfig::V1 { .. },
                ..
            } => item,
        };
        *self = new_self;
    }
```

**File:** types/src/on_chain_config/consensus_config.rs (L391-396)
```rust
    pub fn window_size(&self) -> Option<u64> {
        match self {
            Self::V1(_) | Self::V2(_) | Self::V3 { .. } => None,
            Self::V4 { window_size, .. } | Self::V5 { window_size, .. } => *window_size,
        }
    }
```

**File:** types/src/on_chain_config/consensus_config.rs (L445-449)
```rust
    fn deserialize_into_config(bytes: &[u8]) -> Result<Self> {
        let raw_bytes: Vec<u8> = bcs::from_bytes(bytes)?;
        bcs::from_bytes(&raw_bytes)
            .map_err(|e| format_err!("[on-chain config] Failed to deserialize into config: {}", e))
    }
```
