### Title
`AllResourceBounds{l2_gas=0, l1_data_gas=0}` Accepted at Gateway but Collapses to `L1Gas` in Consensus Protobuf Round-Trip, Causing Peer Deserialization Failure - (`crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

A valid `RpcInvokeTransactionV3` carrying `AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` is accepted by the gateway and assigned a canonical hash. When the proposer serializes this transaction into a consensus protobuf message and broadcasts it, every validator peer fails to deserialize it: the `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` converter collapses the zero-valued `AllResources` variant into `ValidResourceBounds::L1Gas`, which is then rejected by `TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3`. The result is that any block proposal containing such a transaction is unconditionally rejected by all peers, causing a consensus round failure.

### Finding Description

**Forward path (gateway → internal):**

The gateway's stateless validator accepts `AllResourceBounds` with only `l1_gas` non-zero. The test fixture `valid_l1_gas` confirms this:

```rust
RpcTransactionArgs {
    resource_bounds: AllResourceBounds {
        l1_gas: NON_EMPTY_RESOURCE_BOUNDS,
        ..Default::default()   // l2_gas = 0, l1_data_gas = 0
    },
    ..Default::default()
}
``` [1](#0-0) 

`convert_rpc_tx_to_internal` wraps the `AllResourceBounds` as `ValidResourceBounds::AllResources` and computes the hash including `l1_data_gas = 0` in the preimage (via `get_tip_resource_bounds_hash`): [2](#0-1) 

**Reverse path (consensus protobuf → peer):**

When the proposer serializes the `ConsensusTransaction` to protobuf, `RpcInvokeTransactionV3 → InvokeTransactionV3` wraps the bounds as `ValidResourceBounds::AllResources(tx.resource_bounds)`: [3](#0-2) 

The resulting `protobuf::InvokeV3` carries `l2_gas = Some(zero)` and `l1_data_gas = Some(zero)` (both present, both zero).

On the peer side, `TryFrom<protobuf::ConsensusTransaction> for ConsensusTransaction` calls `txn.try_into()` for the `InvokeV3` arm: [4](#0-3) 

This reaches `TryFrom<protobuf::InvokeV3> for InvokeTransactionV3`, which calls `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`: [5](#0-4) 

The critical branch: when both `l2_gas` and `l1_data_gas` are zero (regardless of whether they were explicitly set to zero or absent), the converter produces `ValidResourceBounds::L1Gas(l1_gas)` — discarding the `AllResources` variant identity.

Back in `TryFrom<protobuf::InvokeV3WithProof> for RpcInvokeTransactionV3`, the now-`L1Gas`-typed `InvokeTransactionV3` is passed to `TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3`: [6](#0-5) 

The `match value.resource_bounds` arm for anything other than `AllResources` returns `Err(OutOfRange)`, which is mapped to `DEPRECATED_RESOURCE_BOUNDS_ERROR` and propagates up, causing the entire `TryFrom<protobuf::ConsensusTransaction>` to fail. [7](#0-6) 

**Hash domain asymmetry (secondary):**

`get_tip_resource_bounds_hash` hashes `l1_data_gas` only for `AllResources`, not for `L1Gas`: [8](#0-7) 

So even if the deserialization were fixed to not error, the hash computed by the proposer (with `AllResources`, including `l1_data_gas = 0`) would differ from the hash a peer would compute after the round-trip (with `L1Gas`, excluding `l1_data_gas`). This is a second, independent invariant violation: the same transaction bytes produce two different canonical hashes depending on which side of the protobuf boundary they are on.

### Impact Explanation

Any unprivile

### Citations

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

**File:** crates/starknet_api/src/rpc_transaction.rs (L568-583)
```rust
impl From<RpcInvokeTransactionV3> for InvokeTransactionV3 {
    fn from(tx: RpcInvokeTransactionV3) -> Self {
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
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L586-611)
```rust
impl TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3 {
    type Error = StarknetApiError;

    fn try_from(value: InvokeTransactionV3) -> Result<Self, Self::Error> {
        Ok(Self {
            resource_bounds: match value.resource_bounds {
                ValidResourceBounds::AllResources(bounds) => bounds,
                _ => {
                    return Err(StarknetApiError::OutOfRange {
                        string: "resource_bounds".to_string(),
                    });
                }
            },
            signature: value.signature,
            nonce: value.nonce,
            tip: value.tip,
            paymaster_data: value.paymaster_data,
            nonce_data_availability_mode: value.nonce_data_availability_mode,
            fee_data_availability_mode: value.fee_data_availability_mode,
            sender_address: value.sender_address,
            calldata: value.calldata,
            account_deployment_data: value.account_deployment_data,
            proof_facts: value.proof_facts,
            proof: Proof::default(),
        })
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

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L1042-1045)
```rust
            protobuf::consensus_transaction::Txn::InvokeV3(txn) => {
                ConsensusTransaction::RpcTransaction(RpcTransaction::Invoke(
                    RpcInvokeTransaction::V3(txn.try_into()?),
                ))
```

**File:** crates/apollo_protobuf/src/converters/rpc_transaction.rs (L115-131)
```rust
impl TryFrom<protobuf::InvokeV3WithProof> for RpcInvokeTransactionV3 {
    type Error = ProtobufConversionError;
    fn try_from(mut value: protobuf::InvokeV3WithProof) -> Result<Self, Self::Error> {
        // Extract proof first, since `starknet_api::transaction::InvokeTransactionV3` does not
        // carry a `proof` field.
        let proof = Proof::from(std::mem::take(&mut value.proof));

        let snapi_invoke: InvokeTransactionV3 = value
            .invoke
            .ok_or(ProtobufConversionError::MissingField {
                field_description: "InvokeV3WithProof::invoke",
            })?
            .try_into()?;

        // This conversion can fail only if the resource_bounds are not AllResources.
        Ok(Self { proof, ..snapi_invoke.try_into().map_err(|_| DEPRECATED_RESOURCE_BOUNDS_ERROR)? })
    }
```
