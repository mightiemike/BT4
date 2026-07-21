### Title
`ValidResourceBounds` Variant Divergence Between Gateway and P2P Sync Paths Produces Mismatched Transaction Hashes - (`File: crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The `get_tip_resource_bounds_hash` function conditionally includes `l1_data_gas` in the hash preimage only for `ValidResourceBounds::AllResources`. The gateway always produces `AllResources` for new V3 invoke transactions, computing hash H1 that includes `l1_data_gas=0`. The P2P sync path deserializes the same transaction from protobuf using `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`, which converts `{l1_data_gas: 0, l2_gas: 0}` to `ValidResourceBounds::L1Gas`, computing hash H2 that omits `l1_data_gas`. H1 ≠ H2, causing `validate_transaction_hash` to reject valid blocks during sync.

### Finding Description

**Step 1 — Gateway path always produces `AllResources`:**

`RpcInvokeTransactionV3` carries `resource_bounds: AllResourceBounds`. The `From<RpcInvokeTransactionV3> for InternalRpcInvokeTransactionV3` conversion preserves this as `AllResourceBounds`, and `InternalRpcInvokeTransactionV3::resource_bounds()` always returns `ValidResourceBounds::AllResources(self.resource_bounds)`. [1](#0-0) 

The hash is then computed via `get_invoke_transaction_v3_hash`, which calls `get_tip_resource_bounds_hash`. For `AllResources`, this **always appends `l1_data_gas`** to the hash preimage, even when it is zero: [2](#0-1) 

So a transaction with `AllResourceBounds{l1_gas: X, l2_gas: 0, l1_data_gas: 0}` produces hash **H1 = poseidon(... | L1_DATA_GAS(0))**.

**Step 2 — P2P sync path silently downgrades to `L1Gas`:**

When the same transaction is received via P2P block sync as a protobuf `Transaction`, the resource bounds are deserialized through: [3](#0-2) 

The condition `if l1_data_gas.is_zero() && l2_gas.is_zero()` fires, producing `ValidResourceBounds::L1Gas(l1_gas)`. The hash is then recomputed via the same `get_tip_resource_bounds_hash`, but for `L1Gas` the `l1_data_gas` branch is skipped: [4](#0-3) 

This produces hash **H2 = poseidon(...)** — without `L1_DATA_GAS(0)`. **H1 ≠ H2**.

**The same downgrade logic exists in the RPC v0.8 layer:** [5](#0-4) 

And in the JSON `Deserialize` impl via `TryFrom<DeprecatedResourceBoundsMapping>`: [6](#0-5) 

Any path that reads a stored `InvokeTransactionV3` with `{l2_gas: 0, l1_data_gas: 0}` through these converters will produce `L1Gas` and compute H2.

**The hash function that diverges:** [7](#0-6) 

### Impact Explanation

A syncing node that receives a committed block containing a V3 invoke transaction with `l2_gas=0` and `l1_data_gas=0` will deserialize the transaction as `L1Gas`, recompute H2, and fail `validate_transaction_hash` against the committed H1. This causes the node to reject a valid, already-finalized block — a wrong state/receipt result from accepted input, and a transaction conversion that binds the wrong hash to the executable payload. [8](#0-7) 

### Likelihood Explanation

Any user can submit a V3 invoke transaction with `l2_gas=0` and `l1_data_gas=0` — this is a valid, well-formed transaction (the pre-0.13.3 style). No special privilege is required. The gateway accepts it, includes it in a block, and the divergence is triggered automatically on every syncing peer.

### Recommendation

The `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` conversion (and the equivalent in `apollo_rpc/src/v0_8/transaction.rs`) must not downgrade to `L1Gas` when `l1_data_gas` is present in the protobuf message, even if its value is zero. The `L1Gas` variant should only be used when deserializing transactions that were originally signed and hashed as `L1Gas` (i.e., pre-0.13.3 transactions where `l1_data_gas` was never part of the signed preimage). For any transaction received via a path that could have been submitted as `AllResources`, the `AllResources` variant must be preserved unconditionally to guarantee hash canonicalization. [9](#0-8) 

### Proof of Concept

```
1. Submit via gateway:
   RpcInvokeTransactionV3 {
     resource_bounds: AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 },
     ...
   }

2. Gateway computes:
   H1 = poseidon(INVOKE | version | sender | tip_resource_bounds_hash | ... | L1_DATA_GAS(0))
   where tip_resource_bounds_hash includes L1_DATA_GAS(0) because variant = AllResources.

3. Transaction is included in block B with tx_hash = H1.

4. Syncing peer receives block B via P2P protobuf sync.
   Deserializes resource_bounds:
     l1_data_gas.is_zero() && l2_gas.is_zero() → ValidResourceBounds::L1Gas(X)

5. Peer recomputes:
   H2 = poseidon(INVOKE | version | sender | tip_resource_bounds_hash | ...)
   where tip_resource_bounds_hash omits L1_DATA_GAS because variant = L1Gas.

6. validate_transaction_hash(tx, block_number, chain_id, H1, options)
   → computes H2, checks possible_hashes.contains(&H1) → false → rejects block B.
```

### Citations

**File:** crates/starknet_api/src/rpc_transaction.rs (L636-639)
```rust
impl InvokeTransactionV3Trait for InternalRpcInvokeTransactionV3 {
    fn resource_bounds(&self) -> ValidResourceBounds {
        ValidResourceBounds::AllResources(self.resource_bounds)
    }
```

**File:** crates/starknet_api/src/transaction_hash.rs (L170-185)
```rust
pub fn validate_transaction_hash(
    transaction: &Transaction,
    block_number: &BlockNumber,
    chain_id: &ChainId,
    expected_hash: TransactionHash,
    transaction_options: &TransactionOptions,
) -> Result<bool, StarknetApiError> {
    let mut possible_hashes = get_deprecated_transaction_hashes(
        chain_id,
        block_number,
        transaction,
        transaction_options,
    )?;
    possible_hashes.push(get_transaction_hash(transaction, chain_id, transaction_options)?);
    Ok(possible_hashes.contains(&expected_hash))
}
```

**File:** crates/starknet_api/src/transaction_hash.rs (L202-210)
```rust
    // For new V3 txs, need to also hash the data gas bounds.
    resource_felts.extend(match resource_bounds {
        ValidResourceBounds::L1Gas(_) => vec![],
        ValidResourceBounds::AllResources(all_resources) => {
            vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]
        }
    });

    Ok(HashChain::new().chain(&tip.0.into()).chain_iter(resource_felts.iter()).get_poseidon_hash())
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

**File:** crates/apollo_rpc/src/v0_8/transaction.rs (L188-199)
```rust
impl From<ResourceBoundsMapping> for ValidResourceBounds {
    fn from(value: ResourceBoundsMapping) -> Self {
        if value.l1_data_gas.is_zero() && value.l2_gas.is_zero() {
            Self::L1Gas(value.l1_gas)
        } else {
            Self::AllResources(AllResourceBounds {
                l1_gas: value.l1_gas,
                l1_data_gas: value.l1_data_gas,
                l2_gas: value.l2_gas,
            })
        }
    }
```

**File:** crates/starknet_api/src/transaction/fields.rs (L575-606)
```rust
impl TryFrom<DeprecatedResourceBoundsMapping> for ValidResourceBounds {
    type Error = StarknetApiError;
    fn try_from(
        resource_bounds_mapping: DeprecatedResourceBoundsMapping,
    ) -> Result<Self, Self::Error> {
        if let (Some(l1_bounds), Some(l2_bounds)) = (
            resource_bounds_mapping.0.get(&Resource::L1Gas),
            resource_bounds_mapping.0.get(&Resource::L2Gas),
        ) {
            match resource_bounds_mapping.0.get(&Resource::L1DataGas) {
                Some(data_bounds) => Ok(Self::AllResources(AllResourceBounds {
                    l1_gas: *l1_bounds,
                    l1_data_gas: *data_bounds,
                    l2_gas: *l2_bounds,
                })),
                None => {
                    if l2_bounds.is_zero() {
                        Ok(Self::L1Gas(*l1_bounds))
                    } else {
                        Err(StarknetApiError::InvalidResourceMappingInitializer(format!(
                            "Missing data gas bounds but L2 gas bound is not zero: \
                             {resource_bounds_mapping:?}",
                        )))
                    }
                }
            }
        } else {
            Err(StarknetApiError::InvalidResourceMappingInitializer(format!(
                "{resource_bounds_mapping:?}",
            )))
        }
    }
```
