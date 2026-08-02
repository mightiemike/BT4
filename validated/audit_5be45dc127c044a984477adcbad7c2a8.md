No vulnerability found for this question.

**Analysis:**

The Rust `EpochTimeoutConfig::with_grace_period` constructor [1](#0-0)  is only a plain data struct used to implement the `OnChainConfig` trait for deserializing the on-chain resource [2](#0-1) . Searching the whole repo, this constructor is invoked only from test code (`testsuite/smoke-test/src/chunky_dkg/epoch_timeout.rs`, which itself only builds a Move script string that calls the Move-side `epoch_timeout_config::new_with_grace_period` — the actual value construction and validation happen inside Move, not Rust) [3](#0-2) .

There is no production code path (genesis encoding, VM execution, executor/storage commit, or restore) that constructs an `EpochTimeoutConfig` in Rust and writes it directly to state. The only real on-chain writer of this resource is the Move module itself via `set_for_next_epoch`/`on_new_epoch`, gated by `new_with_grace_period`'s `assert!(grace_period_secs > 0, ...)` [4](#0-3)  and by `system_addresses::assert_aptos_framework` requiring the framework signer, which is only obtainable through governance-executed Move transactions [5](#0-4) .

Generic Rust helpers that can write arbitrary `OnChainConfig` bytes to a state view, such as `SimulationStateStore::set_on_chain_config` [6](#0-5)  and `replay-benchmark`'s `config_override` [7](#0-6) , exist only in test-simulation and offline-replay-benchmark tooling — they are not part of the consensus/execution commit pipeline, proof construction, or restore path, and are out of scope as they don't process unprivileged transaction/API/proof input in a way that corrupts mainnet committed state.

Because no unprivileged, production commit/proof/restore code path ever calls `EpochTimeoutConfig::with_grace_period` to write state, and the actual committing logic always routes through the Move module's `assert!` guard, there is no way for `EpochTimeoutConfig { force_end_grace_period_secs: Some(0) }` to reach durable storage through any exploitable path within this review's scope.

### Citations

**File:** types/src/on_chain_config/epoch_timeout_config.rs (L27-31)
```rust
    pub fn with_grace_period(secs: u64) -> Self {
        Self {
            force_end_grace_period_secs: Some(secs),
        }
    }
```

**File:** types/src/on_chain_config/epoch_timeout_config.rs (L38-41)
```rust
impl OnChainConfig for EpochTimeoutConfig {
    const MODULE_IDENTIFIER: &'static str = "epoch_timeout_config";
    const TYPE_IDENTIFIER: &'static str = "EpochTimeoutConfig";
}
```

**File:** testsuite/smoke-test/src/chunky_dkg/epoch_timeout.rs (L88-90)
```rust
        // Epoch watchdog: force-end after `n` seconds of stalled reconfig.
        let timeout_cfg = epoch_timeout_config::new_with_grace_period({});
        epoch_timeout_config::set_for_next_epoch(&framework_signer, timeout_cfg);
```

**File:** aptos-move/framework/aptos-framework/sources/configs/epoch_timeout_config.move (L34-38)
```text
    /// Used by on-chain governance to update the watchdog config for the next epoch.
    public fun set_for_next_epoch(framework: &signer, new_config: EpochTimeoutConfig) {
        system_addresses::assert_aptos_framework(framework);
        config_buffer::upsert(new_config);
    }
```

**File:** aptos-move/framework/aptos-framework/sources/configs/epoch_timeout_config.move (L61-69)
```text
    public fun new_with_grace_period(grace_period_secs: u64): EpochTimeoutConfig {
        assert!(
            grace_period_secs > 0,
            error::invalid_argument(E_GRACE_PERIOD_MUST_BE_POSITIVE),
        );
        EpochTimeoutConfig {
            force_end_grace_period_secs: std::option::some(grace_period_secs)
        }
    }
```

**File:** aptos-move/aptos-transaction-simulation/src/state_store.rs (L118-126)
```rust
    fn set_on_chain_config<C>(&self, config: &C) -> Result<()>
    where
        C: OnChainConfig + Serialize,
    {
        self.set_state_value(
            StateKey::on_chain_config::<C>()?,
            StateValue::new_legacy(bcs::to_bytes(&config)?.into()),
        )
    }
```

**File:** aptos-move/replay-benchmark/src/overrides.rs (L257-279)
```rust
fn config_override<T: OnChainConfig + Serialize, F: FnOnce(&mut T)>(
    state_view: &impl StateView,
    override_func: F,
) -> (StateKey, StateValue) {
    let state_key = config_state_key::<T>();
    let state_value = state_view
        .get_state_value(&state_key)
        .unwrap_or_else(|err| {
            panic!(
                "Failed to fetch on-chain config for {:?}: {:?}",
                state_key, err
            )
        })
        .unwrap_or_else(|| panic!("On-chain config for {:?} must always exist", state_key));

    let mut config = T::deserialize_into_config(state_value.bytes())
        .expect("On-chain config must be deserializable");
    override_func(&mut config);
    let config_bytes = bcs::to_bytes(&config).expect("On-chain config must be serializable");

    let new_state_value = state_value.map_bytes(|_| Ok(config_bytes.into())).unwrap();
    (state_key, new_state_value)
}
```
