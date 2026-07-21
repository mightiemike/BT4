### Title
`ValidResourceBounds` Protobuf Round-Trip Silently Changes Variant, Producing a Different Transaction Hash - (`File: crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The protobuf deserializer for `ValidResourceBounds` silently downgrades an `AllResources` variant to `L1Gas` whenever `l2_gas` and `l1_data_gas` are both zero. Because `get_tip_resource_bounds_hash` includes the `L1_DATA_GAS` field in the hash preimage only for `AllResources`, the same on-wire transaction data produces two different transaction hashes depending on which deserialization path is taken. A V3 transaction submitted through the gateway (which always uses `AllResourceBounds` / `AllResources`) receives hash H1. After a protobuf round-trip through the P2P sync path (which uses `ValidResourceBounds`), the same transaction is reconstructed as `L1Gas` and receives hash H2 ≠ H1.

### Finding Description

**Step 1 – Hash domain split in `get_tip_resource_bounds_hash`** [1](#0-0) 

For `ValidResourceBounds::AllResources`, the function appends a third felt `get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)` to the hash chain. For `ValidResourceBounds::L1Gas`, that felt is omitted entirely. Two inputs that differ only in which variant is used therefore produce different Poseidon digests even when the numeric field values are identical.

**Step 2 – Gateway always produces `AllResources`**

`InternalRpcInvokeTransactionV3` stores `resource_bounds: AllResourceBounds` and its `InvokeTransactionV3Trait` implementation always wraps it in `ValidResourceBounds::AllResources`: [2](#0-1) 

`convert_rpc_tx_to_internal` therefore always computes H1 with the `AllResources` path, including the zero-valued `L1_DATA_GAS` felt: [3](#0-2) 

**Step 3 – P2P sync deserializer silently downgrades to `L1Gas`**

The `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` converter used in the `Transaction` (non-RPC) path applies the following rule: [4](#0-3) 

When `l1_data_gas == 0 && l2_gas == 0`, the result is `ValidResourceBounds::L1Gas(l1_gas)`. The serializer for `AllResources` always emits explicit zero fields for both: [5](#0-4) 

So a transaction originally stored as `AllResources(l1_gas=X, l2_gas=0, l1_data_gas=0)` is deserialized as `L1Gas(l1_gas=X)` after one protobuf round-trip.

**Step 4 – Hash divergence**

`get_tip_resource_bounds_hash` called on `AllResources(X, 0, 0)` produces:
```
Poseidon(tip, concat(L1_GAS, X), concat(L2_GAS, 0), concat(L1_DATA, 0))  → H1
```
Called on `L1Gas(X)` it produces:
```
Poseidon(tip, concat(L1_GAS, X), concat(L2_GAS, 0))                       → H2
```
H1 ≠ H2.

**Step 5 – `validate_transaction_hash` has no fallback for this case**

For V3 invoke transactions, `get_deprecated_transaction_hashes` returns an empty vector: [6](#0-5) 

So `validate_transaction_hash` only checks the single current hash. A syncing node that deserializes the transaction as `L1Gas` computes H2 and rejects the block because H2 ≠ H1 (the hash stored by the proposer).

### Impact Explanation

A V3 invoke transaction with `AllResources(l1_gas > 0, l2_gas = 0, l1_data_gas = 0)` is accepted by the gateway (the resource-bounds validator explicitly allows a transaction with only `l1_gas` non-zero). The proposer stores it with hash H1. Any node that receives the block over P2P and deserializes the transaction through the `ValidResourceBounds` path recomputes H2 ≠ H1, causing `validate_transaction_hash` to fail. This produces a wrong/rejected state for the syncing node — a divergence between the proposer's committed state and every validator's view of the same block. The impact matches **"Wrong state … from blockifier/syscall/execution logic for accepted input"** (Critical) and **"Transaction conversion or signature/hash logic binds the wrong … hash … or executable payload"** (High).

### Likelihood Explanation

The trigger is a normal, gateway-accepted V3 transaction with zero L2 and data-gas bounds. Any user can submit such a transaction deliberately or inadvertently (e.g., a wallet that sets only `l1_gas`). No privileged access is required. The protobuf round-trip is on the mandatory P2P sync path, so every non-proposer node is affected.

### Recommendation

Fix the `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` converter to preserve the `AllResources` variant whenever the serialized message explicitly carries an `l1_data_gas` field, regardless of its value:

```rust
// Before
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})

// After
Ok(if value.l1_data_gas.is_none() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
```

Alternatively, add the `AllResources`-with-zero-data-gas hash as a second candidate in `get_deprecated_transaction_hashes` for V3 invoke transactions, mirroring the existing multi-hash fallback for V0 invoke and L1-handler transactions.

### Proof of Concept

```
1. Submit a V3 invoke transaction to the gateway with:
     resource_bounds = AllResourceBounds {
         l1_gas:      { max_amount: 1000, max_price_per_unit: 1 },
         l2_gas:      { max_amount: 0,    max_price_per_unit: 0 },
         l1_data_gas: { max_amount: 0,    max_price_per_unit: 0 },
     }

2. Gateway calls convert_rpc_tx_to_internal → InternalRpcInvokeTransactionV3
   with resource_bounds = AllResourceBounds { l1_gas: 1000/1, l2_gas: 0/0, l1_data_gas: 0/0 }.
   get_tip_resource_bounds_hash uses ValidResourceBounds::AllResources → includes
   concat(L1_DATA, 0) in the Poseidon chain → produces H1.

3. Proposer includes the transaction in a block with tx_hash = H1.

4. A syncing node receives the block over P2P. The transaction is deserialized via
   TryFrom<protobuf::ResourceBounds> for ValidResourceBounds:
     l1_data_gas.is_zero() && l2_gas.is_zero() → ValidResourceBounds::L1Gas(l1_gas).
   get_tip_resource_bounds_hash uses ValidResourceBounds::L1Gas → omits L1_DATA felt
   → produces H2 ≠ H1.

5. validate_transaction_hash(tx_as_L1Gas, H1) computes H2, finds H2 ∉ {H2},
   returns false → syncing node rejects the block.
```

### Citations

**File:** crates/starknet_api/src/transaction_hash.rs (L154-154)
```rust
                InvokeTransaction::V1(_) | InvokeTransaction::V3(_) => vec![],
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

**File:** crates/starknet_api/src/rpc_transaction.rs (L636-639)
```rust
impl InvokeTransactionV3Trait for InternalRpcInvokeTransactionV3 {
    fn resource_bounds(&self) -> ValidResourceBounds {
        ValidResourceBounds::AllResources(self.resource_bounds)
    }
```

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L391-392)
```rust
        let tx_hash = tx_without_hash.calculate_transaction_hash(&self.chain_id)?;
        Ok((InternalRpcTransaction { tx: tx_without_hash, tx_hash }, proof_data))
```

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L431-436)
```rust
        Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
            ValidResourceBounds::L1Gas(l1_gas)
        } else {
            ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
        })
    }
```

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L479-489)
```rust
            ValidResourceBounds::AllResources(AllResourceBounds {
                l1_gas,
                l2_gas,
                l1_data_gas,
            }) => protobuf::ResourceBounds {
                l1_gas: Some(l1_gas.into()),
                l2_gas: Some(l2_gas.into()),
                l1_data_gas: Some(l1_data_gas.into()),
            },
        }
    }
```
