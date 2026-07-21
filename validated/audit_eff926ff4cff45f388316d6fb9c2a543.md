### Title
Protobuf `ValidResourceBounds` Deserialization Silently Downgrades `AllResources` to `L1Gas`, Producing a Different Transaction Hash Preimage — (File: `crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary
The protobuf converter for `ValidResourceBounds` silently changes the variant from `AllResources` to `L1Gas` whenever both `l2_gas` and `l1_data_gas` are zero. Because `get_tip_resource_bounds_hash` includes `L1_DATA_GAS` in the Poseidon preimage only for the `AllResources` variant, the same logical transaction produces two distinct hash values depending on which deserialization path is taken. A user-submitted `AllResources` transaction with `l2_gas = 0, l1_data_gas = 0` is hashed with three resource felts at the gateway, but after a P2P round-trip it is deserialized as `L1Gas` and hashed with only two resource felts, breaking the hash-canonicalization invariant across the public-to-internal conversion boundary.

### Finding Description

**Root cause — protobuf converter (`crates/apollo_protobuf/src/converters/transaction.rs`, lines 417–436):**

```rust
impl TryFrom<protobuf::ResourceBounds> for ValidResourceBounds {
    fn try_from(value: protobuf::ResourceBounds) -> Result<Self, Self::Error> {
        // ...
        // TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2.
        let l1_data_gas = value.l1_data_gas.unwrap_or_default();
        // ...
        Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
            ValidResourceBounds::L1Gas(l1_gas)          // ← variant silently changed
        } else {
            ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
        })
    }
}
```

The guard was introduced to tolerate missing `l1_data_gas` from pre-0.13.3 peers (the `unwrap_or_default()` path). However, it also fires when `l1_data_gas` is explicitly present but zero, which is a valid state for a new `AllResources` transaction. The result is that any `AllResources` transaction with `l2_gas = 0, l1_data_gas = 0` is silently re-typed as `L1Gas` on the receiving side.

**Hash divergence — `get_tip_resource_bounds_hash` (`crates/starknet_api/src/transaction_hash.rs`, lines 187–211):**

```rust
pub fn get_tip_resource_bounds_hash(
    resource_bounds: &ValidResourceBounds,
    tip: &Tip,
) -> Result<Felt, StarknetApiError> {
    let mut resource_felts = vec![
        get_concat_resource(&l1_resource_bounds, L1_GAS)?,
        get_concat_resource(&l2_resource_bounds, L2_GAS)?,
    ];
    resource_felts.extend(match resource_bounds {
        ValidResourceBounds::L1Gas(_) => vec![],                          // 2 felts
        ValidResourceBounds::AllResources(all_resources) => {
            vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]  // 3 felts
        }
    });
    Ok(HashChain::new().chain(&tip.0.into()).chain_iter(resource_felts.iter()).get_poseidon_hash())
}
```

- `AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` → `Poseidon(tip, L1_GAS_packed(X), L2_GAS_packed(0), L1_DATA_GAS_packed(0))` — **4 elements**
- `L1Gas(X)` → `Poseidon(tip, L1_GAS_packed(X), L2_GAS_packed(0))` — **3 elements**

These are distinct Poseidon outputs. The full transaction hash (`get_invoke_transaction_v3_hash`, lines 370–404) embeds `tip_resource_bounds_hash` as a single chained element, so the outer hash also diverges.

**Reachability:** All RPC V3 transactions arrive as `AllResourceBounds` (the `RpcInvokeTransactionV3.resource_bounds` field is typed `AllResourceBounds`, not `ValidResourceBounds`). The gateway validator explicitly accepts transactions where only `l1_gas` is non-zero and both `l2_gas` and `l1_data_gas` are zero (confirmed by `stateless_transaction_validator_test.rs`, `valid_l1_gas` case). Such a transaction is accepted, hashed as `AllResources` (H1), stored, and then propagated over P2P. The receiving node deserializes it as `L1Gas` and computes H2 ≠ H1.

**Conditional `proof_facts` inclusion (`get_invoke_transaction_v3_hash`, lines