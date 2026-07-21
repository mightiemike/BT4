### Title
`ValidResourceBounds` Protobuf Round-Trip Silently Downgrades `AllResources` to `L1Gas`, Producing a Different Transaction Hash - (`File: crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The protobuf deserializer for `ValidResourceBounds` uses a value-based condition (`l1_data_gas.is_zero() && l2_gas.is_zero()`) to decide which variant to reconstruct. A V3 transaction submitted with `AllResourceBounds { l1_gas: non_zero, l2_gas: 0, l1_data_gas: 0 }` is accepted by the gateway, hashed under the `AllResources` domain (which includes the `L1_DATA` resource name felt in the Poseidon chain), then serialized to protobuf and deserialized back as `ValidResourceBounds::L1Gas`. The `L1Gas` hash domain omits the `l1_data_gas` element entirely, producing a structurally different hash for the same transaction bytes. Any component that recomputes the hash from the deserialized object (blockifier re-execution, P2P sync hash validation, RPC trace/simulation) will derive a hash that does not match the one committed to the block.

### Finding Description

**Step 1 – Submission and hash commitment (AllResources domain)**

`RpcInvokeTransactionV3` carries `resource_bounds: AllResourceBounds`. The gateway converts it to `InternalRpcInvokeTransactionV3` (same `AllResourceBounds` field) and calls `calculate_transaction_hash`, which dispatches to `get_invoke_transaction_v3_hash`. [1](#0-0) 

Inside `get_invoke_transaction_v3_hash`, the tip-resource-bounds sub-hash is computed via `get_tip_resource_bounds_hash`. For the `AllResources` variant the function appends **three** resource felts: `L1_GAS`, `L2_GAS`, and `L1_DATA_GAS`. [2](#0-1) 

Even when `l1_data_gas = 0`, the `get_concat_resource` call encodes the 7-byte ASCII resource name `L1_DATA` into the felt, so the resulting felt is non-zero and the Poseidon hash is distinct from the two-element chain. [3](#0-2) 

**Step 2 – Conversion to storage type always forces `AllResources`**

When the batcher converts the internal transaction to an executable `InvokeTransactionV3`, it unconditionally wraps the bounds in `ValidResourceBounds::AllResources`. [4](#0-3) 

The same forced wrapping applies to `InternalRpcDeclareTransactionV3`. [5](#0-4) 

So the stored `Transaction` object always carries `ValidResourceBounds::AllResources` for any transaction that entered through the gateway, regardless of whether `l2_gas` and `l1_data_gas` are zero.

**Step 3 – Protobuf serialization preserves all three fields**

`From<ValidResourceBounds> for protobuf::ResourceBounds` emits `l1_data_gas: Some(...)` for both variants (zero for `L1Gas`, actual value for `AllResources`). [6](#0-5) 

**Step 4 – Protobuf deserialization silently downgrades the variant**

`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` inspects the *values* rather than the presence/absence of the field to decide the variant: [7](#0-6) 

When `l2_gas = 0` and `l1_data_gas = 0` (both present but zero), the condition on line 431 is true and the result is `ValidResourceBounds::L1Gas(l1_gas)`. The original `AllResources` variant is lost.

The TODO comment on line 426 acknowledges that `l1_data_gas` being `None` (absent) is the correct sentinel for pre-0.13.3 transactions, but the branch on line 431 uses the *value* being zero instead of the field being absent.

**Step 5 – Hash recomputation produces a different value**

After deserialization the `InvokeTransactionV3` now carries `ValidResourceBounds::L1Gas`. Any call to `get_tip_resource_bounds_hash` on this object produces a two-element Poseidon chain (tip, L1_GAS, L2_GAS) instead of the original three-element chain (tip, L1_GAS, L2_GAS, L1_DATA_GAS). The final transaction hash diverges from the one committed to the block. [8](#0-7) 

### Impact Explanation

Any node that receives a synced block containing such a transaction and recomputes the transaction hash from the deserialized `Transaction` object will derive a hash that does not match the committed hash. This affects:

- **Block hash validation**: block hash derivation chains over transaction hashes; a wrong transaction hash propagates to a wrong block hash, causing the syncing node to disagree with the proposing node.
- **RPC `starknet_getTransactionByHash` / `starknet_simulateTransactions`**: the stored hash and the recomputed hash diverge, returning an authoritative-looking wrong value.
- **Blockifier re-execution** (`blockifier_reexecution`): re-executing a block from storage recomputes hashes from the deserialized `Transaction`; the re-execution hash will not match the receipt hash.

This matches the allowed impact: *Wrong state, receipt, event, L1 message, class hash, storage value, or revert result from blockifier/syscall/execution logic for accepted input* and *RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value.*

### Likelihood Explanation

The trigger is a V3 transaction with `AllResourceBounds { l1_gas: non_zero, l2_gas: 0, l1_data_gas: 0 }`. The gateway's stateless validator explicitly accepts this combination (the test suite has a `valid_l1_gas` case with exactly these zero values for the other two resources). [9](#0-8) 

No special privilege is required; any user can submit such a transaction. The divergence is triggered automatically on every P2P sync of a block containing such a transaction.

### Recommendation

Replace the value-based condition with a field-presence check. The protobuf field `l1_data_gas` being `None` (absent) is the correct indicator of a pre-0.13.3 (`L1Gas`) transaction, as the TODO comment on line 426 already notes:

```rust
// In TryFrom<protobuf::ResourceBounds> for ValidResourceBounds:
Ok(if value.l1_data_gas.is_none() {
    ValidResourceBounds::L1Gas(l1_gas)
} else {
    let l1_data_gas: ResourceBounds = value.l1_data_gas.unwrap_or_default().try_into()?;
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
```

This preserves the `AllResources` variant for any transaction that was originally submitted as a post-0.13.3 transaction, even when all non-L1-gas bounds are zero, keeping the hash domain consistent across the serialization boundary.

### Proof of Concept

1. Submit an invoke V3 transaction with `resource_bounds = AllResourceBounds { l1_gas: ResourceBounds { max_amount: 1, max_price_per_unit: 1 }, l2_gas: ResourceBounds::default(), l1_data_gas: ResourceBounds::default() }`. The gateway accepts it.

2. The gateway computes `tx_hash_A` using `get_tip_resource_bounds_hash` with `ValidResourceBounds::AllResources(...)` → Poseidon over `[tip, L1_GAS_felt, L2_GAS_felt, L1_DATA_GAS_felt]`.

3. The transaction is included in a block. The block is serialized to protobuf for P2P sync. `From<ValidResourceBounds> for protobuf::ResourceBounds` emits `l1_data_gas: Some(zero_limits)`.

4. The receiving node deserializes via `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`. Condition `l1_data_gas.is_zero() && l2_gas.is_zero()` is true → result is `ValidResourceBounds::L1Gas(l1_gas)`.

5. The receiving node recomputes `tx_hash_B` using `get_tip_resource_bounds_hash` with `ValidResourceBounds::L1Gas(...)` → Poseidon over `[tip, L1_GAS_felt, L2_GAS_felt]` (two elements, no `L1_DATA_GAS_felt`).

6. `tx_hash_A ≠ tx_hash_B`. Any hash-based lookup, block hash recomputation, or re-execution on the receiving node uses the wrong hash.

### Citations

**File:** crates/starknet_api/src/rpc_transaction.rs (L451-466)
```rust
impl From<InternalRpcDeclareTransactionV3> for DeclareTransactionV3 {
    fn from(tx: InternalRpcDeclareTransactionV3) -> Self {
        Self {
            class_hash: tx.class_hash,
            resource_bounds: ValidResourceBounds::AllResources(tx.resource_bounds),
            tip: tx.tip,
            signature: tx.signature,
            nonce: tx.nonce,
            compiled_class_hash: tx.compiled_class_hash,
            sender_address: tx.sender_address,
            nonce_data_availability_mode: tx.nonce_data_availability_mode,
            fee_data_availability_mode: tx.fee_data_availability_mode,
            paymaster_data: tx.paymaster_data,
            account_deployment_data: tx.account_deployment_data,
        }
    }
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L669-676)
```rust
impl TransactionHasher for InternalRpcInvokeTransactionV3 {
    fn calculate_transaction_hash(
        &self,
        chain_id: &ChainId,
        transaction_version: &TransactionVersion,
    ) -> Result<TransactionHash, StarknetApiError> {
        get_invoke_transaction_v3_hash(self, chain_id, transaction_version)
    }
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L679-695)
```rust
impl From<InternalRpcInvokeTransactionV3> for InvokeTransactionV3 {
    fn from(tx: InternalRpcInvokeTransactionV3) -> Self {
        Self {
            resource_bounds: ValidResourceBounds::AllResources(tx.resource_bounds),
            tip: tx.tip,
            signature: tx.signature,
            nonce: tx.nonce,
            sender_address: tx.sender_address,
            calldata: tx.calldata,
            nonce_data_availability_mode: tx.nonce_data_availability_mode,
            fee_data_availability_mode: tx.fee_data_availability_mode,
            paymaster_data: tx.paymaster_data,
            account_deployment_data: tx.account_deployment_data,
            proof_facts: tx.proof_facts,
        }
    }
}
```

**File:** crates/starknet_api/src/transaction_hash.rs (L187-211)
```rust
// An implementation of the SNIP: https://github.com/EvyatarO/SNIPs/blob/snip-8/SNIPS/snip-8.md
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

**File:** crates/starknet_api/src/transaction_hash.rs (L213-226)
```rust
// Receives resource_bounds and resource_name and returns:
// [0 | resource_name (56 bit) | max_amount (64 bit) | max_price_per_unit (128 bit)].
// An implementation of the SNIP: https://github.com/EvyatarO/SNIPs/blob/snip-8/SNIPS/snip-8.md.
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

**File:** crates/starknet_api/src/transaction_hash.rs (L370-404)
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
