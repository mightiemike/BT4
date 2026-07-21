### Title
P2P Protobuf Round-Trip Silently Converts `AllResources` to `L1Gas` for Zero-Valued Bounds, Causing Valid Transaction Rejection — (`File: crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

A `ValidResourceBounds` canonicalization invariant is broken across the protobuf serialization boundary. A V3 transaction submitted via RPC with `AllResourceBounds { l2_gas: 0, l1_data_gas: 0 }` is accepted by the gateway and hashed under the `AllResources` domain. When the same transaction is serialized to protobuf and deserialized on a P2P peer, the `ValidResourceBounds` converter silently downgrades it to `L1Gas`. The subsequent conversion back to `RpcInvokeTransactionV3` requires `AllResources` and fails with `DEPRECATED_RESOURCE_BOUNDS_ERROR`, causing the receiving node to reject the transaction. The codebase's own test suite acknowledges and works around this exact issue.

### Finding Description

**Root cause — `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`:** [1](#0-0) 

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)          // ← wrong for AllResources with zero values
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
```

The heuristic uses zero-value fields as a discriminant between the two variants. This is lossy: an `AllResources` transaction whose `l2_gas` and `l1_data_gas` are both zero is indistinguishable from an `L1Gas` transaction after a protobuf round-trip.

**Serialization path (correct):**

`ConsensusTransaction::RpcTransaction(RpcInvokeTransaction::V3(txn))` → `protobuf::InvokeV3` via: [2](#0-1) 

`RpcInvokeTransactionV3` → `InvokeTransactionV3` wraps bounds as `ValidResourceBounds::AllResources(tx.resource_bounds)`: [3](#0-2) 

All three resource fields are written to protobuf, including the zero-valued `l2_gas` and `l1_data_gas`.

**Deserialization path (broken):**

`protobuf::ConsensusTransaction` → `ConsensusTransaction` via: [4](#0-3) 

`protobuf::InvokeV3` → `InvokeTransactionV3` → `RpcInvokeTransactionV3`. The intermediate `InvokeTransactionV3` now holds `ValidResourceBounds::L1Gas` (because `l2_gas = 0` and `l1_data_gas = 0`). The final step: [5](#0-4) 

```rust
resource_bounds: match value.resource_bounds {
    ValidResourceBounds::AllResources(bounds) => bounds,
    _ => {
        return Err(StarknetApiError::OutOfRange { ... });  // ← triggered
    }
},
```

The same pattern is confirmed for `DeployAccountV3` with the explicit comment: [6](#0-5) 

> "This conversion can fail only if the resource_bounds are not AllResources."

**The codebase acknowledges this invariant break in its own test:** [7](#0-6) 

```rust
// If all the fields of `AllResources` are 0 upon serialization,
// then the deserialized value will be interpreted as the `L1Gas` variant.
fn add_gas_values_to_transaction(transactions: &mut [ConsensusTransaction]) {
    ...
    resource_bounds.l2_gas.max_amount = GasAmount(1);  // ← forced non-zero to avoid the bug
```

This workaround is applied only in tests. Production transactions are not guarded.

**Hash domain divergence (secondary):**

Even if the deserialization error were suppressed, the hash computed at the gateway uses `AllResources` (includes `L1_DATA_GAS` in `get_tip_resource_bounds_hash`), while a node that reconstructed the transaction as `L1Gas` would compute a different hash (excludes `L1_DATA_GAS`): [8](#0-7) 

### Impact Explanation

A user submitting a valid V3 invoke, declare, or deploy-account transaction with `AllResourceBounds { l2_gas: 0, l1_data_gas: 0 }` (a natural choice when only L1 gas is needed) will have the transaction:

1. **Accepted** by the local gateway and assigned a correct `tx_hash` under the `AllResources` domain.
2. **Rejected** by every P2P peer (both mempool propagation and consensus proposal paths) because the protobuf round-trip downgrades the resource bounds variant, causing `DEPRECATED_RESOURCE_BOUNDS_ERROR` on deserialization.

This matches: **High — Mempool/gateway/RPC admission accepts valid transactions that are then rejected before sequencing**, and **High — Transaction conversion binds the wrong type or executable payload**.

### Likelihood Explanation

Any V3 transaction that specifies only L1 gas (setting `l2_gas` and `l1_data_gas` to zero) triggers this. This is a common and natural usage pattern for users who do not need L2 gas or L1 data gas. The trigger requires no special privileges — any unprivileged user submitting a standard V3 transaction can hit this path.

### Recommendation

Remove the zero-value heuristic from `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`. Instead, add an explicit discriminant field (e.g., a boolean `is_all_resources`) to the `protobuf::ResourceBounds` message, or always deserialize to `AllResources` when all three fields are present in the protobuf wire format. The test workaround in `consensus_test.rs` should be removed once the root cause is fixed.

### Proof of Concept

1. Submit a V3 invoke transaction via RPC:
   ```json
   { "resource_bounds": { "l1_gas": { "max_amount": "0x3e8", "max_price_per_unit": "0x1" },
                           "l2_gas": { "max_amount": "0x0",   "max_price_per_unit": "0x0" },
                           "l1_data_gas": { "max_amount": "0x0", "max_price_per_unit": "0x0" } } }
   ```
2. Gateway accepts it; `convert_rpc_tx_to_internal` computes `tx_hash` using `AllResources` (L1_DATA_GAS included in hash preimage). [9](#0-8) 
3. Transaction enters the mempool and is included in a consensus proposal.
4. Proposer serializes `ConsensusTransaction` → `protobuf::ConsensusTransaction` → bytes. All three resource fields are written with `l2_gas = 0`, `l1_data_gas = 0`.
5. Receiving validator deserializes bytes → `protobuf::ResourceBounds` → `ValidResourceBounds::L1Gas` (zero-value heuristic fires). [10](#0-9) 
6. `TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3` returns `Err(OutOfRange)` because `L1Gas ≠ AllResources`. [11](#0-10) 
7. Receiving validator rejects the proposal; the transaction is never sequenced despite being valid.

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

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L663-699)
```rust
impl From<InvokeTransactionV3> for protobuf::InvokeV3 {
    fn from(value: InvokeTransactionV3) -> Self {
        Self {
            resource_bounds: Some(protobuf::ResourceBounds::from(value.resource_bounds)),
            tip: value.tip.0,
            signature: Some(protobuf::AccountSignature {
                parts: value.signature.0.iter().map(|signature| (*signature).into()).collect(),
            }),
            nonce: Some(value.nonce.0.into()),
            sender: Some(value.sender_address.into()),
            calldata: value.calldata.0.iter().map(|calldata| (*calldata).into()).collect(),
            nonce_data_availability_mode: volition_domain_to_enum_int(
                value.nonce_data_availability_mode,
            ),
            fee_data_availability_mode: volition_domain_to_enum_int(
                value.fee_data_availability_mode,
            ),
            paymaster_data: value
                .paymaster_data
                .0
                .iter()
                .map(|paymaster_data| (*paymaster_data).into())
                .collect(),
            account_deployment_data: value
                .account_deployment_data
                .0
                .iter()
                .map(|account_deployment_data| (*account_deployment_data).into())
                .collect(),
            proof_facts: value
                .proof_facts
                .0
                .iter()
                .map(|proof_fact| (*proof_fact).into())
                .collect(),
        }
    }
```

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L1027-1053)
```rust
impl TryFrom<protobuf::ConsensusTransaction> for ConsensusTransaction {
    type Error = ProtobufConversionError;
    fn try_from(value: protobuf::ConsensusTransaction) -> Result<Self, Self::Error> {
        let txn = value.txn.ok_or(missing("ConsensusTransaction::txn"))?;
        let txn = match txn {
            protobuf::consensus_transaction::Txn::DeclareV3(txn) => {
                ConsensusTransaction::RpcTransaction(RpcTransaction::Declare(
                    RpcDeclareTransaction::V3(txn.try_into()?),
                ))
            }
            protobuf::consensus_transaction::Txn::DeployAccountV3(txn) => {
                ConsensusTransaction::RpcTransaction(RpcTransaction::DeployAccount(
                    RpcDeployAccountTransaction::V3(txn.try_into()?),
                ))
            }
            protobuf::consensus_transaction::Txn::InvokeV3(txn) => {
                ConsensusTransaction::RpcTransaction(RpcTransaction::Invoke(
                    RpcInvokeTransaction::V3(txn.try_into()?),
                ))
            }
            protobuf::consensus_transaction::Txn::L1Handler(txn) => {
                ConsensusTransaction::L1Handler(txn.try_into()?)
            }
        };
        Ok(txn)
    }
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

**File:** crates/starknet_api/src/rpc_transaction.rs (L586-612)
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
}
```

**File:** crates/apollo_protobuf/src/converters/rpc_transaction.rs (L99-106)
```rust
impl TryFrom<protobuf::DeployAccountV3> for RpcDeployAccountTransactionV3 {
    type Error = ProtobufConversionError;
    fn try_from(value: protobuf::DeployAccountV3) -> Result<Self, Self::Error> {
        let snapi_deploy_account: DeployAccountTransactionV3 = value.try_into()?;
        // This conversion can fail only if the resource_bounds are not AllResources.
        snapi_deploy_account.try_into().map_err(|_| DEPRECATED_RESOURCE_BOUNDS_ERROR)
    }
}
```

**File:** crates/apollo_protobuf/src/converters/consensus_test.rs (L26-47)
```rust
// If all the fields of `AllResources` are 0 upon serialization,
// then the deserialized value will be interpreted as the `L1Gas` variant.
fn add_gas_values_to_transaction(transactions: &mut [ConsensusTransaction]) {
    let transaction = &mut transactions[0];
    match transaction {
        ConsensusTransaction::RpcTransaction(rpc_transaction) => match rpc_transaction {
            RpcTransaction::Declare(RpcDeclareTransaction::V3(RpcDeclareTransactionV3 {
                resource_bounds,
                ..
            }))
            | RpcTransaction::Invoke(RpcInvokeTransaction::V3(RpcInvokeTransactionV3 {
                resource_bounds,
                ..
            }))
            | RpcTransaction::DeployAccount(RpcDeployAccountTransaction::V3(
                RpcDeployAccountTransactionV3 { resource_bounds, .. },
            )) => {
                resource_bounds.l2_gas.max_amount = GasAmount(1);
            }
        },
        ConsensusTransaction::L1Handler(_) => {}
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

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L391-392)
```rust
        let tx_hash = tx_without_hash.calculate_transaction_hash(&self.chain_id)?;
        Ok((InternalRpcTransaction { tx: tx_without_hash, tx_hash }, proof_data))
```
