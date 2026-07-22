### Title
Protobuf P2P Round-Trip Silently Downgrades `AllResources` to `L1Gas` When L2/L1DataGas Are Zero, Causing Transaction Hash Divergence — (`crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The protobuf deserializer for `ValidResourceBounds` uses a zero-value heuristic to decide which variant to reconstruct. When `l2_gas` and `l1_data_gas` are both zero it silently produces `ValidResourceBounds::L1Gas`, even if the wire value was serialized from `ValidResourceBounds::AllResources`. Because `get_tip_resource_bounds_hash` includes `l1_data_gas` as a third hash-chain element for `AllResources` but omits it entirely for `L1Gas`, the two variants produce **different Poseidon hashes** even when the numeric field values are identical. A transaction submitted via RPC with `AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` is accepted by the gateway, hashed as `AllResources` at the originating sequencer, and stored with that hash. After P2P sync the receiving node deserializes the same transaction as `L1Gas`, so any subsequent hash recomputation (re-execution, proof generation, hash validation) produces a different value, breaking state consistency between nodes.

### Finding Description

**Step 1 – Originating node hashes as `AllResources`.**

`RpcInvokeTransactionV3` carries `AllResourceBounds` (never `ValidResourceBounds`). The gateway accepts transactions where only `l1_gas` is non-zero:

```
valid_l1_gas: AllResourceBounds { l1_gas: NON_EMPTY_RESOURCE_BOUNDS, ..Default::default() }
```

The conversion to `InvokeTransactionV3` always wraps the bounds as `ValidResourceBounds::AllResources`:

```rust
// crates/starknet_api/src/rpc_transaction.rs:568-583
impl From<RpcInvokeTransactionV3> for InvokeTransactionV3 {
    fn from(tx: RpcInvokeTransactionV3) -> Self {
        Self {
            resource_bounds: ValidResourceBounds::AllResources(tx.resource_bounds),
            ...
        }
    }
}
```

`get_tip_resource_bounds_hash` then hashes **three** resource felts for `AllResources`:

```rust
// crates/starknet_api/src/transaction_hash.rs:188-210
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],                          // 2 felts total
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]  // 3 felts total
    }
});
```

Even when `l1_data_gas = 0`, the third element `concat(0, L1_DATA_GAS)` is appended, producing a distinct Poseidon digest. [1](#0-0) 

**Step 2 – Protobuf serialization emits zero fields.**

`From<ValidResourceBounds> for protobuf::ResourceBounds` serializes `L1Gas` with explicit zero `l2_gas` and `l1_data_gas`:

```rust
// crates/apollo_protobuf/src/converters/transaction.rs:471-489
ValidResourceBounds::L1Gas(l1_gas) => protobuf::ResourceBounds {
    l1_gas: Some(l1_gas.into()),
    l2_gas: Some(value.get_l2_bounds().into()),          // zero
    l1_data_gas: Some(ResourceBounds::default().into()), // zero
},
```

For `AllResources(l1_gas=X, l2_gas=0, l1_data_gas=0)` the output is byte-for-byte identical to the `L1Gas(X)` serialization. [2](#0-1) 

**Step 3 – Protobuf deserialization silently downgrades to `L1Gas`.**

```rust
// crates/apollo_protobuf/src/converters/transaction.rs:417-436
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)          // ← wrong variant
} else {
    ValidResourceBounds::AllResources(...)
})
```

A receiving node that deserializes the P2P message reconstructs `L1Gas(X)` instead of `AllResources(X, 0, 0)`. [3](#0-2) 

**Step 4 – Hash recomputation diverges.**

`InvokeTransactionV3::calculate_transaction_hash` calls `get_invoke_transaction_v3_hash`, which calls `get_tip_resource_bounds_hash`. With `L1Gas` the hash chain has two resource felts; with `AllResources` it has three. The Poseidon digests differ, so the hash the receiving node computes from its stored transaction does not match the hash committed in the block by the originating sequencer. [4](#0-3) 

**Step 5 – JSON/DB round-trip does not exhibit the bug; only the protobuf path does.**

The `Serialize` impl for `ValidResourceBounds::AllResources` always emits the `L1DataGas` key, so the DB round-trip is lossless. The `Deserialize` impl restores `AllResources` when `L1DataGas` is present. The bug is exclusive to the protobuf P2P path. [5](#0-4) 

### Impact Explanation

Any node that receives a block via P2P sync containing an Invoke V3 transaction with `AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` will store the transaction as `L1Gas(X)`. Subsequent operations that recompute the hash from the stored transaction — re-execution, proof generation (`starknet_transaction_prover`), `validate_transaction_hash` in `blockifier_reexecution` — will produce a hash that does not match the block-committed hash. This causes proof generation to fail or produce an invalid proof, and causes re-execution to diverge from the original execution. Additionally, `get_gas_vector_computation_mode()` returns `NoL2Gas` for `L1Gas` and `All` for `AllResources`, so fee computation mode also diverges between the originating and syncing nodes.

This matches: **Wrong state, receipt, or revert result from blockifier/syscall/execution logic for accepted input** (Critical) and **RPC execution, fee estimation, tracing, or simulation returns an authoritative-looking wrong value** (High).

### Likelihood Explanation

The gateway explicitly accepts `AllResourceBounds { l1_gas: NON_EMPTY, l2_gas: 0, l1_data_gas: 0 }` as a valid transaction (confirmed by the `valid_l1_gas` test case in `stateless_transaction_validator_test.rs`). Any user submitting a pre-0.13.3-style V3 transaction (only specifying l1_gas) triggers this path. No special privilege is required; the transaction is accepted by the mempool and included in a block normally. [6](#0-5) 

### Recommendation

Replace the zero-value heuristic in the protobuf deserializer with an explicit discriminant field, or always deserialize into `AllResources` when all three resource fields are present in the protobuf message (regardless of whether their values are zero). The serializer for `AllResources` should set a flag or use a distinct protobuf message type so the deserializer can unambiguously reconstruct the original variant. Alternatively, add a round-trip test that serializes `AllResources(l1_gas=X, l2_gas=0, l1_data_gas=0)` to protobuf and asserts the deserialized result is still `AllResources`, not `L1Gas`.

### Proof of Concept

```rust
use starknet_api::transaction::fields::{
    AllResourceBounds, GasAmount, GasPrice, ResourceBounds, ValidResourceBounds,
};
use apollo_protobuf::protobuf;

fn main() {
    // Transaction submitted via RPC with only l1_gas set (l2_gas=0, l1_data_gas=0).
    let original = ValidResourceBounds::AllResources(AllResourceBounds {
        l1_gas: ResourceBounds {
            max_amount: GasAmount(1000),
            max_price_per_unit: GasPrice(5),
        },
        l2_gas: ResourceBounds::default(),    // zero
        l1_data_gas: ResourceBounds::default(), // zero
    });

    // Originating node computes hash — uses AllResources path (3 resource felts).
    let hash_original = get_tip_resource_bounds_hash(&original, &Tip(0)).unwrap();

    // Serialize to protobuf (P2P sync).
    let proto: protobuf::ResourceBounds = original.into();

    // Receiving node deserializes — silently becomes L1Gas.
    let deserialized = ValidResourceBounds::try_from(proto).unwrap();
    assert!(matches!(deserialized, ValidResourceBounds::L1Gas(_))); // BUG: was AllResources

    // Receiving node recomputes hash — uses L1Gas path (2 resource felts).
    let hash_deserialized = get_tip_resource_bounds_hash(&deserialized, &Tip(0)).unwrap();

    // Hashes diverge even though numeric values are identical.
    assert_ne!(hash_original, hash_deserialized); // DIVERGENCE CONFIRMED
}
```

### Citations

**File:** crates/starknet_api/src/transaction_hash.rs (L188-210)
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
```

**File:** crates/starknet_api/src/transaction_hash.rs (L370-405)
```rust
pub(crate) fn get_invoke_transaction_v3_hash<T: InvokeTransactionV3Trait>(
    transaction: &T,
    chain_id: &ChainId,
    transaction_version: &TransactionVersion,
) -> Result<TransactionHash, StarknetApiError> {
    let tip_resource_bounds_hash =
        get_tip_resource_bounds_hash(&transaction.resource_bounds(), transaction.tip())?;
    let paymaster_data_hash =
        HashChain::new().chain_iter(transaction.paymaster_data().0.iter()).get_poseidon_hash();
    let data_availability_mode = concat_data_availability_mode(
        transaction.nonce_data_availability_mode(),
        transaction.fee_data_availability_mode(),
    );
    let account_deployment_data_hash = HashChain::new()
        .chain_iter(transaction.account_deployment_data().0.iter())
        .get_poseidon_hash();
    let calldata_hash =
        HashChain::new().chain_iter(transaction.calldata().0.iter()).get_poseidon_hash();
    let mut hash_chain = HashChain::new()
        .chain(&INVOKE)
        .chain(&transaction_version.0)
        .chain(transaction.sender_address().0.key())
        .chain(&tip_resource_bounds_hash)
        .chain(&paymaster_data_hash)
        .chain(&Felt::try_from(chain_id)?)
        .chain(&transaction.nonce().0)
        .chain(&data_availability_mode)
        .chain(&account_deployment_data_hash)
        .chain(&calldata_hash);
    if !transaction.proof_facts().0.is_empty() {
        let proof_facts_hash =
            HashChain::new().chain_iter(transaction.proof_facts().0.iter()).get_poseidon_hash();
        hash_chain = hash_chain.chain(&proof_facts_hash);
    }
    Ok(TransactionHash(hash_chain.get_poseidon_hash()))
}
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

**File:** crates/starknet_api/src/transaction/fields.rs (L551-573)
```rust
impl Serialize for ValidResourceBounds {
    fn serialize<S>(&self, s: S) -> Result<S::Ok, S::Error>
    where
        S: Serializer,
    {
        let map = match self {
            ValidResourceBounds::L1Gas(l1_gas) => BTreeMap::from([
                (Resource::L1Gas, *l1_gas),
                (Resource::L2Gas, ResourceBounds::default()),
            ]),
            ValidResourceBounds::AllResources(AllResourceBounds {
                l1_gas,
                l2_gas,
                l1_data_gas,
            }) => BTreeMap::from([
                (Resource::L1Gas, *l1_gas),
                (Resource::L2Gas, *l2_gas),
                (Resource::L1DataGas, *l1_data_gas),
            ]),
        };
        DeprecatedResourceBoundsMapping(map).serialize(s)
    }
}
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator_test.rs (L69-82)
```rust
#[rstest]
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
