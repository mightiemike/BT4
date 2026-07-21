### Title
`ValidResourceBounds` Protobuf Deserialization Produces Wrong Transaction Hash for Zero-L2/L1_DATA_GAS V3 Transactions — (`crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The P2P sync protobuf converter for `ValidResourceBounds` silently downgrades a new `AllResources` V3 transaction to the legacy `L1Gas` variant whenever both `l2_gas` and `l1_data_gas` are zero. Because `get_tip_resource_bounds_hash` hashes a different number of resource-bound felts for each variant, the transaction hash computed after sync deserialization diverges from the hash computed at gateway ingestion, breaking hash validation for any V3 transaction whose L2 and L1_DATA_GAS bounds are zero.

### Finding Description

**Ingestion path (gateway):** `RpcInvokeTransactionV3` carries `resource_bounds: AllResourceBounds`. The conversion `From<RpcInvokeTransactionV3> for InternalRpcInvokeTransactionV3` preserves `AllResourceBounds`, and `InternalRpcInvokeTransactionV3::resource_bounds()` always returns `ValidResourceBounds::AllResources(...)`. The hash is therefore computed via the `AllResources` branch of `get_tip_resource_bounds_hash`. [1](#0-0) 

**Sync path (P2P committed-block protocol):** `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` in `transaction.rs` applies the following gate:

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
``` [2](#0-1) 

A V3 transaction with `AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` is therefore deserialized as `ValidResourceBounds::L1Gas(X)` on the receiving node.

**Hash divergence in `get_tip_resource_bounds_hash`:**

For `ValidResourceBounds::L1Gas`, the function builds:
```
resource_felts = [pack(L1_GAS, l1_gas), pack(L2_GAS, zero)]
```

For `ValidResourceBounds::AllResources` with the same zero bounds, it builds:
```
resource_felts = [pack(L1_GAS, l1_gas), pack(L2_GAS, zero), pack(L1_DATA_GAS, zero)]
``` [3](#0-2) 

The third element `pack(L1_DATA_GAS, zero)` is **not zero**: `get_concat_resource` encodes the 7-byte ASCII resource name `"L1_DATA"` into the felt, producing a non-zero value even when `max_amount` and `max_price_per_unit` are both zero. [4](#0-3) 

The Poseidon hash over a 3-element input differs from the hash over a 2-element input, so `tip_resource_bounds_hash` — and therefore the full transaction hash — diverges between the two paths.

**The TODO comment in the converter acknowledges the fragility:**
```rust
// TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2.
let l1_data_gas = value.l1_data_gas.unwrap_or_default();
``` [5](#0-4) 

The fix was deferred, leaving the ambiguity between "old 0.13.2 transaction without L1_DATA_GAS" and "new transaction with zero L1_DATA_GAS" unresolved.

### Impact Explanation

Any node that receives a committed block via P2P sync and calls `validate_transaction_hash` (or recomputes the hash for receipt/RPC serving) on a V3 transaction with zero L2 and L1_DATA_GAS bounds will compute a hash that does not match the hash stored in the block. This causes:

1. **Hash validation failure during sync** — `validate_transaction_hash` returns `false` for a valid, committed transaction, causing the syncing node to reject the block or mark the transaction as invalid.
2. **Wrong transaction representation served via RPC** — the synced node stores the transaction as `ValidResourceBounds::L1Gas`, causing `starknet_getTransactionByHash` and related endpoints to return wrong resource bounds and a wrong fee type (`Eth` instead of `STRK`), which is an authoritative-looking wrong value.

This matches: **High — Transaction conversion or signature/hash logic binds the wrong hash or executable payload** and **High — RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value**.

### Likelihood Explanation

Any user who submits a V3 invoke transaction with `l2_gas.max_amount = 0`, `l2_gas.max_price_per_unit = 0`, `l1_data_gas.max_amount = 0`, `l1_data_gas.max_price_per_unit = 0` (a valid and common pattern for transactions that only consume L1 gas) triggers this path. No special privileges are required. The gateway accepts such transactions normally; the divergence only manifests at the sync boundary.

### Recommendation

In `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` (`transaction.rs`), remove the heuristic that downgrades to `L1Gas` based on zero values. Instead, use the presence or absence of the `l1_data_gas` protobuf field as the discriminant (consistent with the existing TODO), or always deserialize post-0.13.3 transactions as `AllResources`. Concretely, implement the deferred TODO:

```rust
// After removing 0.13.2 support:
let l1_data_gas = value.l1_data_gas
    .ok_or(missing("ResourceBounds::l1_data_gas"))?
    .try_into()?;
Ok(ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas }))
```

This aligns the sync-path deserialization with the gateway-path ingestion, ensuring both sides compute the same `tip_resource_bounds_hash` and therefore the same transaction hash.

### Proof of Concept

1. Submit a V3 invoke transaction with `AllResourceBounds { l1_gas: { max_amount: 1000, max_price_per_unit: 1 }, l2_gas: { 0, 0 }, l1_data_gas: { 0, 0 } }`.
2. Gateway computes hash H_all using `AllResources` path: `Poseidon(INVOKE, 3, sender, Poseidon(tip, pack(L1_GAS,…), pack(L2_GAS,zero), pack(L1_DATA_GAS,zero)), …)`.
3. Transaction is committed to a block with hash H_all.
4. A syncing node receives the block via P2P. The protobuf `ResourceBounds` message has `l2_gas = {0,0}` and `l1_data_gas = {0,0}`. The converter fires `l1_data_gas.is_zero() && l2_gas.is_zero()` → `ValidResourceBounds::L1Gas`.
5. The syncing node recomputes the hash using `L1Gas` path: `Poseidon(INVOKE, 3, sender, Poseidon(tip, pack(L1_GAS,…), pack(L2_GAS,zero)), …)` → H_l1 ≠ H_all.
6. `validate_transaction_hash(tx, block_number, chain_id, H_all, options)` returns `false`; the block or transaction is rejected.

The exact divergent felt is `pack(L1_DATA_GAS, ResourceBounds::default())` = `Felt::from_bytes_be([0x00, 0x4C, 0x31, 0x5F, 0x44, 0x41, 0x54, 0x41, 0x00, …, 0x00])` (encodes ASCII `"L1_DATA"` in bytes 1–7), which is non-zero and absent from the `L1Gas` hash preimage.

### Citations

**File:** crates/starknet_api/src/rpc_transaction.rs (L636-639)
```rust
impl InvokeTransactionV3Trait for InternalRpcInvokeTransactionV3 {
    fn resource_bounds(&self) -> ValidResourceBounds {
        ValidResourceBounds::AllResources(self.resource_bounds)
    }
```

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L417-436)
```rust
impl TryFrom<protobuf::ResourceBounds> for ValidResourceBounds {
    type Error = ProtobufConversionError;
    fn try_from(value: protobuf::ResourceBounds) -> Result<Self, Self::Error> {
        let Some(l1_gas) = value.l1_gas else {
            return Err(missing("ResourceBounds::l1_gas"));
        };
        let Some(l2_gas) = value.l2_gas else {
            return Err(missing("ResourceBounds::l2_gas"));
        };
        // TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2.
        let l1_data_gas = value.l1_data_gas.unwrap_or_default();
        let l1_gas: ResourceBounds = l1_gas.try_into()?;
        let l2_gas: ResourceBounds = l2_gas.try_into()?;
        let l1_data_gas: ResourceBounds = l1_data_gas.try_into()?;
        Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
            ValidResourceBounds::L1Gas(l1_gas)
        } else {
            ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
        })
    }
```

**File:** crates/starknet_api/src/transaction_hash.rs (L188-211)
```rust
pub fn get_tip_resource_bounds_hash(
    resource_bounds: &ValidResourceBounds,
    tip: &Tip,
) -> Result<Felt, StarknetApiError> {
    let l1_resource_bounds = resource_bounds.get_l1_bounds();
    let l2_resource_bounds = resource_bounds.get_l2_bounds();

    // L1 and L2 gas bounds always exist.
    // Old V3 txs always have L2 gas bounds of zero, but they exist.
    let mut resource_felts = vec![
        get_concat_resource(&l1_resource_bounds, L1_GAS)?,
        get_concat_resource(&l2_resource_bounds, L2_GAS)?,
    ];

    // For new V3 txs, need to also hash the data gas bounds.
    resource_felts.extend(match resource_bounds {
        ValidResourceBounds::L1Gas(_) => vec![],
        ValidResourceBounds::AllResources(all_resources) => {
            vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]
        }
    });

    Ok(HashChain::new().chain(&tip.0.into()).chain_iter(resource_felts.iter()).get_poseidon_hash())
}
```

**File:** crates/starknet_api/src/transaction_hash.rs (L216-226)
```rust
fn get_concat_resource(
    resource_bounds: &ResourceBounds,
    resource_name: &ResourceName,
) -> Result<Felt, StarknetApiError> {
    let max_amount = resource_bounds.max_amount.0.to_be_bytes();
    let max_price = resource_bounds.max_price_per_unit.0.to_be_bytes();
    let concat_bytes =
        [[0_u8].as_slice(), resource_name.as_slice(), max_amount.as_slice(), max_price.as_slice()]
            .concat();
    Ok(Felt::from_bytes_be(&concat_bytes.try_into().expect("Expect 32 bytes")))
}
```
