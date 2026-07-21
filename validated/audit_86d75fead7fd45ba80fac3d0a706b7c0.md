### Title
Protobuf `ValidResourceBounds` Deserialization Silently Downgrades `AllResources` to `L1Gas`, Producing a Divergent Transaction Hash - (`crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The protobuf deserializer for `ValidResourceBounds` uses a zero-value heuristic to decide whether to reconstruct a transaction's resource bounds as `ValidResourceBounds::L1Gas` or `ValidResourceBounds::AllResources`. A V3 `AllResources` transaction with `l2_gas=0` and `l1_data_gas=0` — which the gateway accepts — is silently downgraded to `L1Gas` after a protobuf round-trip. Because `get_tip_resource_bounds_hash` produces structurally different Poseidon preimages for the two variants (the `AllResources` path appends a packed `l1_data_gas=0` element that the `L1Gas` path omits), the transaction hash computed after deserialization diverges from the hash computed at submission time. Any node that receives the block via P2P sync will compute and store a different hash for the same transaction, breaking hash-based verification in the OS and state commitment layers.

### Finding Description

**Serialization side** (`From<ValidResourceBounds> for protobuf::ResourceBounds`, line 471–490):

```rust
ValidResourceBounds::L1Gas(l1_gas) => protobuf::ResourceBounds {
    l1_gas: Some(l1_gas.into()),
    l2_gas: Some(value.get_l2_bounds().into()),   // zero
    l1_data_gas: Some(ResourceBounds::default().into()), // zero
},
```

Both `L1Gas` and `AllResources(l2_gas=0, l1_data_gas=0)` serialize to identical wire bytes: `l1_gas=X`, `l2_gas=0`, `l1_data_gas=0`.

**Deserialization side** (`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`, line 417–436):

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)          // ← always chosen when both are zero
} else {
    ValidResourceBounds::AllResources(...)
})
```

An `AllResources` transaction with `l2_gas=0` and `l1_data_gas=0` is reconstructed as `L1Gas`.

**Hash divergence** (`get_tip_resource_bounds_hash`, line 188–211):

```rust
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],                          // 2-element preimage
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]  // 3-element preimage
    }
});
Ok(HashChain::new().chain(&tip.0.into()).chain_iter(resource_felts.iter()).get_poseidon_hash())
```

- `L1Gas` hash: `poseidon([tip, l1_gas_packed, l2_gas_packed(0)])`
- `AllResources(l2_gas=0, l1_data_gas=0)` hash: `poseidon([tip, l1_gas_packed, l2_gas_packed(0), l1_data_gas_packed(0)])`

These are distinct Poseidon outputs. The same invariant applies in `valid_resource_bounds_as_felts` (line 333–350), which is used by the OS/Cairo execution layer.

**Gateway acceptance**: The stateless validator accepts `AllResourceBounds` with only `l1_gas` non-zero (test case `valid_l1_gas`, `crates/apollo_gateway/src/stateless_transaction_validator_test.rs:70–82`). A user can legitimately submit such a transaction.

**End-to-end path**:
1. User submits `AllResources(l1_gas=X, l2_gas=0, l1_data_gas=0)` to the gateway.
2. Gateway computes hash H₁ using the `AllResources` Poseidon preimage (3 elements after tip).
3. Block is produced; transaction stored with hash H₁.
4. Block is propagated via P2P; `InvokeTransactionV3.resource_bounds` is serialized to protobuf.
5. Receiving node deserializes `ValidResourceBounds` → `L1Gas` (heuristic fires).
6. Receiving node computes hash H₂ using the `L1Gas` Poseidon preimage (2 elements after tip).
7. H₁ ≠ H₂: the stored transaction hash diverges from the recomputed hash.

### Impact Explanation

The wrong hash is bound to the transaction at the P2P deserialization boundary. Any downstream consumer that recomputes the hash from the stored `Transaction` object — including the OS transaction hash verifier (`hash_tx_common_fields` in `transaction_hash.cairo`, line 146–178), the block hash calculator, and state commitment — will observe a mismatch. This can cause valid blocks to be rejected by syncing nodes or cause the committed state to reference a hash that cannot be reproduced from the on-chain data, breaking the integrity of the state diff and receipts.

### Likelihood Explanation

The trigger is a standard V3 invoke transaction with `l2_gas=0` and `l1_data_gas=0`, which the gateway explicitly accepts (test case `valid_l1_gas`). No privileged access is required. The condition fires automatically whenever such a transaction is propagated via P2P block sync. Any node running the P2P sync path is affected.

### Recommendation

The protobuf deserialization must preserve the original variant. Two options:

1. **Add a discriminator field** to the protobuf `ResourceBounds` message (e.g., a boolean `is_all_resources`) so the deserializer can reconstruct the correct variant without relying on zero-value heuristics.

2. **Reject the ambiguous case at the gateway**: if `l2_gas=0` and `l1_data_gas=0` in an `AllResources` submission, either reject it or canonicalize it to `L1Gas` before computing the hash, so the hash is always consistent with the `L1Gas` preimage.

The TODO comment at line 426 (`// TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2`) acknowledges that the `unwrap_or_default` fallback is a temporary compatibility measure; the discriminator ambiguity is a direct consequence of that same compatibility shim.

### Proof of Concept

```
1. Construct RpcInvokeTransactionV3 with:
     resource_bounds = AllResourceBounds {
         l1_gas:      ResourceBounds { max_amount: 1000, max_price_per_unit: 1 },
         l2_gas:      ResourceBounds::default(),   // zero
         l1_data_gas: ResourceBounds::default(),   // zero
     }

2. Compute hash H1 via get_invoke_transaction_v3_hash:
     get_tip_resource_bounds_hash dispatches to AllResources branch
     → preimage = [tip, l1_gas_packed, l2_gas_packed(0), l1_data_gas_packed(0)]
     → H1 = poseidon(preimage)   // 4-element array

3. Serialize to protobuf via From<ValidResourceBounds> for protobuf::ResourceBounds:
     l1_gas = X, l2_gas = 0, l1_data_gas = 0

4. Deserialize via TryFrom<protobuf::ResourceBounds> for ValidResourceBounds:
     l1_data_gas.is_zero() && l2_gas.is_zero() == true
     → reconstructed as ValidResourceBounds::L1Gas(l1_gas)

5. Compute hash H2 via get_invoke_transaction_v3_hash on reconstructed tx:
     get_tip_resource_bounds_hash dispatches to L1Gas branch
     → preimage = [tip, l1_gas_packed, l2_gas_packed(0)]
     → H2 = poseidon(preimage)   // 3-element array

6. Assert H1 != H2  ← hash divergence confirmed
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

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

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L471-490)
```rust
impl From<ValidResourceBounds> for protobuf::ResourceBounds {
    fn from(value: ValidResourceBounds) -> Self {
        match value {
            ValidResourceBounds::L1Gas(l1_gas) => protobuf::ResourceBounds {
                l1_gas: Some(l1_gas.into()),
                l2_gas: Some(value.get_l2_bounds().into()),
                l1_data_gas: Some(ResourceBounds::default().into()),
            },
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

**File:** crates/starknet_api/src/transaction/fields.rs (L333-350)
```rust
pub fn valid_resource_bounds_as_felts(
    resource_bounds: &ValidResourceBounds,
    exclude_l1_data_gas: bool,
) -> Result<Vec<ResourceAsFelts>, StarknetApiError> {
    let mut resource_bounds_vec: Vec<_> = vec![
        ResourceAsFelts::new(Resource::L1Gas, &resource_bounds.get_l1_bounds())?,
        ResourceAsFelts::new(Resource::L2Gas, &resource_bounds.get_l2_bounds())?,
    ];
    if exclude_l1_data_gas {
        return Ok(resource_bounds_vec);
    }
    if let ValidResourceBounds::AllResources(AllResourceBounds { l1_data_gas, .. }) =
        resource_bounds
    {
        resource_bounds_vec.push(ResourceAsFelts::new(Resource::L1DataGas, l1_data_gas)?)
    }
    Ok(resource_bounds_vec)
}
```

**File:** crates/starknet_api/src/transaction/fields.rs (L363-367)
```rust
#[derive(Clone, Copy, Debug, Eq, PartialEq, Hash, Ord, PartialOrd)]
pub enum ValidResourceBounds {
    L1Gas(ResourceBounds), // Pre 0.13.3. Only L1 gas. L2 bounds are signed but never used.
    AllResources(AllResourceBounds),
}
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator_test.rs (L70-82)
```rust
#[case::valid_l1_gas(
    StatelessTransactionValidatorConfig {
        validate_resource_bounds: true,
        ..*DEFAULT_VALIDATOR_CONFIG_FOR_TESTING
    },
    RpcTransactionArgs {
        resource_bounds: AllResourceBounds {
            l1_gas: NON_EMPTY_RESOURCE_BOUNDS,
            ..Default::default()
        },
        ..Default::default()
    }
)]
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L146-178)
```text
func hash_tx_common_fields{
    range_check_ptr, poseidon_ptr: PoseidonBuiltin*, hash_state: PoseidonHashState
}(common_fields: CommonTxFields*) {
    alloc_locals;

    // TODO(Noa, 01/01/2026): remove the following `assert` once paymaster is supported.
    assert common_fields.paymaster_data_length = 0;

    let fee_fields_hash = hash_fee_fields(
        tip=common_fields.tip,
        resource_bounds=common_fields.resource_bounds,
        n_resource_bounds=common_fields.n_resource_bounds,
    );

    // TODO(Noa, 01/01/2026):replace the two following asserts with assert_le(..., 2**32 - 1) once
    //   volition is supported.
    assert common_fields.nonce_data_availability_mode = 0;
    assert common_fields.fee_data_availability_mode = 0;
    let data_availability_modes = common_fields.nonce_data_availability_mode * 2 ** 32 +
        common_fields.fee_data_availability_mode;

    poseidon_hash_update_single(item=common_fields.tx_hash_prefix);
    poseidon_hash_update_single(item=common_fields.version);
    poseidon_hash_update_single(item=common_fields.sender_address);
    poseidon_hash_update_single(item=fee_fields_hash);
    poseidon_hash_update_with_nested_hash(
        data_ptr=common_fields.paymaster_data, data_length=common_fields.paymaster_data_length
    );
    poseidon_hash_update_single(item=common_fields.chain_id);
    poseidon_hash_update_single(item=common_fields.nonce);
    poseidon_hash_update_single(item=data_availability_modes);

    return ();
```
