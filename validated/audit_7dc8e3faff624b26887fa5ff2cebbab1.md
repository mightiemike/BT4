No vulnerability found for this question.

**Analysis:**

The claim assumes that `feature_version` values greater than `LATEST_GAS_FEATURE_VERSION` could fall "outside the range ever checked," causing `define_gas_parameters_extract_key_at_version` to silently duplicate or drop a gas parameter key. This does not hold up against the actual macro design:

1. **Version guards are open-ended ranges, not closed intervals.** Every multi-version key binding in the codebase uses a `RangeFrom` pattern (`X..`), e.g. `{ RELEASE_V1_45.. => "high_limit_txn_min_price_per_gas_unit" }` [1](#0-0)  or `{ RELEASE_V1_38.. => "i8" }` [2](#0-1) . Because the upper bound is unbounded, any `feature_version` value at or above the highest declared threshold resolves to the *same* match arm as `LATEST_GAS_FEATURE_VERSION` itself [3](#0-2) . There is no "boundary" beyond the last threshold where behavior could change, so a version one past `LATEST_GAS_FEATURE_VERSION` produces an identical key resolution — not a divergent or corrupted one.

2. **The uniqueness test's bound is therefore sufficient by construction.** `keys_should_be_unique_for_all_versions` iterates `0..=LATEST_GAS_FEATURE_VERSION` [4](#0-3) , which covers every version-threshold transition that could ever change which key is selected. Since all thresholds are `RangeFrom` and monotonic, checking beyond the last declared threshold cannot reveal a new divergence — the resolved key is provably constant for all versions ≥ the largest threshold used across all `define_gas_parameters!` invocations.

3. **`feature_version` is not unprivileged input.** It comes from the on-chain `GasScheduleV2` config, which is only mutated through Aptos governance [5](#0-4) , and is also cross-checked at deployment time against the expected bundle by `verify_framework_deployment` tooling [6](#0-5) . Governance can only realistically set `feature_version` to a value produced by some binary's `LATEST_GAS_FEATURE_VERSION`; it cannot be driven by an unprivileged transaction, package, or view input. The review scope explicitly excludes "privileged governance or admin assumptions."

4. **Mixed-binary rollout is a trusted-operator/rollout condition, not an unprivileged-input path.** Even under a hypothetical mixed-binary window, a validator running an older binary with a lower `LATEST_GAS_FEATURE_VERSION` would still resolve `feature_version` values via the same open-ended range logic to the identical key set as a newer binary, since no closed range ever excludes higher values. This eliminates the "hard-fork-only divergence" concern raised in the question.

Since the mechanism the question describes (values above `LATEST_GAS_FEATURE_VERSION` falling into an unchecked, differently-resolved region) does not exist in the actual `RangeFrom`-based macro design, and the only way to set `feature_version` is through privileged governance (explicitly out of scope), this does not meet the state-integrity gate for acceptance.

### Citations

**File:** aptos-move/aptos-gas-schedule/src/gas_schedule/transaction.rs (L64-69)
```rust
        [
            high_limit_txn_min_price_per_gas_unit: FeePerGasUnit,
            { RELEASE_V1_45.. => "high_limit_txn_min_price_per_gas_unit" },
            // 10x of the min_price_per_gas_unit (100).
            1000
        ],
```

**File:** aptos-move/aptos-gas-schedule/src/gas_schedule/misc.rs (L39-44)
```rust
        [i8: AbstractValueSize, { RELEASE_V1_38.. => "i8" }, 40],
        [i16: AbstractValueSize, { RELEASE_V1_38.. => "i16" }, 40],
        [i32: AbstractValueSize, { RELEASE_V1_38.. => "i32" }, 40],
        [i64: AbstractValueSize, { RELEASE_V1_38.. => "i64" }, 40],
        [i128: AbstractValueSize, { RELEASE_V1_38.. => "i128" }, 40],
        [i256: AbstractValueSize, { RELEASE_V1_38.. => "i256" }, 40],
```

**File:** aptos-move/aptos-gas-schedule/src/gas_schedule/macros.rs (L9-15)
```rust
    ({ $($ver: pat => $key: literal),+ }, $cur_ver: expr) => {
        match $cur_ver {
            $($ver => Some($key)),+,
            #[allow(unreachable_patterns)]
            _ => None,
        }
    }
```

**File:** aptos-move/aptos-gas-schedule/src/gas_schedule/macros.rs (L168-181)
```rust
        #[test]
        fn keys_should_be_unique_for_all_versions() {
            for ver in 0..=$crate::LATEST_GAS_FEATURE_VERSION {
                let mut map = std::collections::BTreeMap::<&str, ()>::new();

                $(
                    if let Some(key) = $crate::gas_schedule::macros::define_gas_parameters_extract_key_at_version!($key_bindings, ver) {
                        if map.insert(key, ()).is_some() {
                            panic!("duplicated key {} at version {}", key, ver);
                        }
                    }
                )*
            }
        }
```

**File:** aptos-move/aptos-vm-environment/src/gas.rs (L13-21)
```rust
/// Returns the gas feature version stored in [GasScheduleV2]. If the gas schedule does not exist,
/// returns 0 gas feature version.
pub fn get_gas_feature_version(state_view: &impl StateView) -> u64 {
    GasScheduleV2::fetch_config(state_view)
        .ok()
        .flatten()
        .map(|gas_schedule| gas_schedule.feature_version)
        .unwrap_or(0)
}
```

**File:** aptos-move/aptos-release-tool/src/commands/verify_framework_deployment.rs (L56-63)
```rust

    if on_chain.feature_version != expected.feature_version {
        bail!(
            "gas feature version on-chain ({}) != bundle ({})",
            on_chain.feature_version,
            expected.feature_version
        );
    }
```
