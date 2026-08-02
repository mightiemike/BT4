## No vulnerability found for this question.

### Analysis

The proposed exploit does not hold up against the actual code. Tracing `AptosGasParameters::from_on_chain_gas_schedule` through `define_gas_parameters!`: [1](#0-0) 

1. **Key lookup never silently defaults on a missing map entry.** For every gas parameter whose `define_gas_parameters_extract_key_at_version!` invocation resolves to `Some(key)`, the code does `gas_schedule.get(&name).cloned().ok_or_else(|| format!("Gas parameter {} does not exist. Feature version: {}.", name, feature_version))?`, i.e. a missing key produces a hard `Err`, not a silently-zeroed value. [2](#0-1) 

2. **All observed version-gated key mappings use open-ended ranges (`N..`), not closed/bounded ranges.** Every match arm I found in `aptos_framework.rs`, `transaction.rs`, and `instr.rs` is of the form `{ RELEASE_VX.. => "key" }` or a chain of `RELEASE_VX..RELEASE_VY => "old_key", RELEASE_VY.. => "new_key"}`, where the final arm is always unbounded (`..`), covering `u64::MAX`. This means any `feature_version` value — including one exceeding `LATEST_GAS_FEATURE_VERSION` compiled into the binary — will always match the highest-known arm, deterministically resolving to `Some(key)` (never silently falling through to the unreachable `_ => None` branch). [3](#0-2) [4](#0-3) 

3. **The `_ => None` catch-all exists only to satisfy Rust's exhaustiveness checker** (annotated `#[allow(unreachable_patterns)]`), not as a live code path reachable by realistic version values given the observed open-ended range patterns. [5](#0-4) 

4. Consequently, if a `GasScheduleV2` on-chain resource has a `feature_version` beyond what the binary's key-mapping tables recognize:
   - If the key name for that (future) version is unchanged from the latest known mapping and the key exists in the on-chain entries, `from_on_chain_gas_schedule` succeeds and simply reuses the parameter set of the latest known version (forward-compatible behavior by design, not corruption).
   - If a key was renamed/removed in a way the compiled binary doesn't know about, the corresponding on-chain key name won't be found in the map, and the function returns a deterministic `Err`, exactly matching the "assert it errors deterministically" requirement in the proof idea.

5. This `Err` propagates as `Result<AptosGasParameters, String>` through `aptos-vm-environment/src/gas.rs::get_gas_config_from_storage` and `Environment::gas_params()`, and any use of it inside `AptosVM` goes through `get_or_vm_startup_failure`, which converts the error into `StatusCode::VM_STARTUP_FAILURE` — halting transaction execution rather than committing a corrupted write set. [6](#0-5) [7](#0-6) 

6. Separately, the only place that *silently* discards a `from_on_chain_gas_schedule` error is `api/src/context.rs::get_gas_schedule`, which is a read-only gas-estimation API helper (falls back to legacy `GasSchedule` v1 or an `InternalError` response) — it does not affect committed state, write sets, or consensus, and thus is out of scope under the State-Integrity Gate. [8](#0-7) 

7. Additionally, the on-chain Move module enforces monotonic version increases (`assert!(new_gas_schedule.feature_version >= gas_schedule.feature_version, ...)`), so an unprivileged transaction cannot arbitrarily set `feature_version` beyond what governance authorizes — that path is privileged (`aptos_governance`/`aptos_framework` signer), out of scope per the review rules (unprivileged input requirement). [9](#0-8) 

In short: the code path is fail-closed (deterministic `Err`/VM startup failure) rather than fail-open with silent partial parameter derivation, matching what the reviewer's own proof idea calls for as the *safe* outcome — so there is no committed-state corruption, no proof/write-set divergence, and no unprivileged path to trigger it in the first place.

### Citations

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

**File:** aptos-move/aptos-gas-schedule/src/gas_schedule/macros.rs (L32-46)
```rust
        impl $crate::traits::FromOnChainGasSchedule for $params_name {
            #[allow(unused)]
            fn from_on_chain_gas_schedule(gas_schedule: &std::collections::BTreeMap<String, u64>, feature_version: u64) -> Result<Self, String> {
                let mut params = $params_name::zeros();

                $(
                    if let Some(key) = $crate::gas_schedule::macros::define_gas_parameters_extract_key_at_version!($key_bindings, feature_version) {
                        let name = format!("{}.{}", $prefix, key);
                        params.$name = gas_schedule.get(&name).cloned().ok_or_else(|| format!("Gas parameter {} does not exist. Feature version: {}.", name, feature_version))?.into();
                    }
                )*

                Ok(params)
            }
        }
```

**File:** aptos-move/aptos-gas-schedule/src/gas_schedule/aptos_framework.rs (L78-80)
```rust
        [algebra_ark_bn254_g1_affine_serialize_comp: InternalGas, { 12.. => "algebra.ark_bn254_g1_affine_serialize_comp" }, 82570],
        [algebra_ark_bn254_g1_affine_serialize_uncomp: InternalGas, { 12.. => "algebra.ark_bn254_g1_affine_serialize_uncomp" }, 108110],
        [algebra_ark_bn254_g1_proj_add: InternalGas, { 12.. => "algebra.ark_bn254_g1_proj_add" }, 195740],
```

**File:** aptos-move/aptos-gas-schedule/src/gas_schedule/transaction.rs (L60-69)
```rust
            min_price_per_gas_unit: FeePerGasUnit,
            "min_price_per_gas_unit",
            aptos_global_constants::GAS_UNIT_PRICE
        ],
        [
            high_limit_txn_min_price_per_gas_unit: FeePerGasUnit,
            { RELEASE_V1_45.. => "high_limit_txn_min_price_per_gas_unit" },
            // 10x of the min_price_per_gas_unit (100).
            1000
        ],
```

**File:** aptos-move/aptos-vm-environment/src/gas.rs (L25-41)
```rust
fn get_gas_config_from_storage(
    sha3_256: &mut Sha3_256,
    state_view: &impl StateView,
) -> (Result<AptosGasParameters, String>, u64) {
    match GasScheduleV2::fetch_config_and_bytes(state_view)
        .ok()
        .flatten()
    {
        Some((gas_schedule, bytes)) => {
            sha3_256.update(&bytes);
            let feature_version = gas_schedule.feature_version;
            let map = gas_schedule.into_btree_map();
            (
                AptosGasParameters::from_on_chain_gas_schedule(&map, feature_version),
                feature_version,
            )
        },
```

**File:** aptos-move/aptos-vm/src/aptos_vm.rs (L273-282)
```rust
pub(crate) fn get_or_vm_startup_failure<'a, T>(
    gas_params: &'a Result<T, String>,
    log_context: &AdapterLogSchema,
) -> Result<&'a T, VMStatus> {
    gas_params.as_ref().map_err(|err| {
        let msg = format!("VM Startup Failed. {}", err);
        speculative_error!(log_context, msg.clone());
        VMStatus::error(StatusCode::VM_STARTUP_FAILURE, Some(msg))
    })
}
```

**File:** api/src/context.rs (L1457-1487)
```rust
            let gas_schedule_params = {
                let may_be_params = GasScheduleV2::fetch_config(&state_view)
                    .ok()
                    .flatten()
                    .and_then(|gas_schedule| {
                        let feature_version = gas_schedule.feature_version;
                        let gas_schedule = gas_schedule.into_btree_map();
                        AptosGasParameters::from_on_chain_gas_schedule(
                            &gas_schedule,
                            feature_version,
                        )
                        .ok()
                    });
                match may_be_params {
                    Some(gas_schedule) => Ok(gas_schedule),
                    None => GasSchedule::fetch_config(&state_view)
                        .ok()
                        .flatten()
                        .and_then(|gas_schedule| {
                            let gas_schedule = gas_schedule.into_btree_map();
                            AptosGasParameters::from_on_chain_gas_schedule(&gas_schedule, 0).ok()
                        })
                        .ok_or_else(|| {
                            E::internal_with_code(
                                "Failed to retrieve gas schedule",
                                AptosErrorCode::InternalError,
                                ledger_info,
                            )
                        }),
                }?
            };
```

**File:** aptos-move/framework/aptos-framework/sources/configs/gas_schedule.move (L90-102)
```text
    public fun set_for_next_epoch(aptos_framework: &signer, gas_schedule_blob: vector<u8>) acquires GasScheduleV2 {
        system_addresses::assert_aptos_framework(aptos_framework);
        assert!(!gas_schedule_blob.is_empty(), error::invalid_argument(EINVALID_GAS_SCHEDULE));
        let new_gas_schedule: GasScheduleV2 = from_bytes(gas_schedule_blob);
        if (exists<GasScheduleV2>(@aptos_framework)) {
            let cur_gas_schedule = borrow_global<GasScheduleV2>(@aptos_framework);
            assert!(
                new_gas_schedule.feature_version >= cur_gas_schedule.feature_version,
                error::invalid_argument(EINVALID_GAS_FEATURE_VERSION)
            );
        };
        config_buffer::upsert(new_gas_schedule);
    }
```
