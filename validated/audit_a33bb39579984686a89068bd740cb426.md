### Title
Protobuf `ValidResourceBounds` Deserialization Silently Downgrades `AllResources` to `L1Gas` When l2_gas and l1_data_gas Are Zero, Causing P2P Rejection of Valid V3 Transactions and Transaction Hash Mismatch — (`File: crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` conversion uses a value-based heuristic to decide between the `L1Gas` and `AllResources` enum variants. When a V3 invoke transaction carries `AllResources` with zero l2_gas and l1_data_gas bounds, the protobuf round-trip silently converts it to `L1Gas`. This breaks two invariants simultaneously:

1. The subsequent conversion from `InvokeTransactionV3` back to `RpcInvokeTransactionV3` hard-rejects any `L1Gas` variant, so peers receiving the transaction via P2P consensus or mempool propagation reject it with `DEPRECATED_RESOURCE_BOUNDS_ERROR`.
2. `get_tip_resource_bounds_hash` produces a structurally different hash for `L1Gas` vs `AllResources(l2_gas=0, l1_data_gas=0)` — the `AllResources` path appends an extra `L1_DATA_GAS` element to the hash chain — so the transaction hash computed by the originating node diverges from any hash recomputed after deserialization.

---

### Finding Description

**Root cause — `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`:** [1](#0-0) 

Lines 431–435 apply a heuristic: if `l1_data_gas.is_zero() && l2_gas.is_zero()`, the result is `ValidResourceBounds::L1Gas(l1_gas)`. Otherwise it is `ValidResourceBounds::AllResources(...)`. This heuristic was introduced to support legacy 0.13.2 transactions that only carried L1 gas, but it also fires for any modern V3 transaction whose l2_gas and l1_data_gas fields happen to be zero.

**Call chain — consensus and mempool P2P paths both reach this converter:**

`TryFrom<protobuf::InvokeV3> for InvokeTransactionV3` calls `ValidResourceBounds::try_from(...)` directly: [2](#0-1) 

`TryFrom<protobuf::InvokeV3WithProof> for RpcInvokeTransactionV3` then calls `snapi_invoke.try_into()`: [3](#0-2) 

`TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3` hard-rejects any non-`AllResources` variant: [4](#0-3) 

Both the consensus P2P path and the mempool P2P path call `TryFrom<protobuf::InvokeV3WithProof> for RpcInvokeTransactionV3`: [5](#0-4) [6](#0-5) 

**Hash domain divergence — `get_tip_resource_bounds_hash`:** [7](#0-6) 

For `L1Gas(l1_gas)` the hash chain contains two resource elements: `[L1_GAS_concat, L2_GAS_concat(zero)]`. For `AllResources(l1_gas, l2_gas=0, l1_data_gas=0)` it contains three: `[L1_GAS_concat, L2_GAS_concat(zero), L1_DATA_GAS_concat(zero)]`. These produce different Poseidon digests. The originating node computes the hash with `AllResources` (via `InternalRpcInvokeTransactionV3` which always wraps `AllResourceBounds`): [8](#0-7) 

Any node that deserializes the same transaction from protobuf and recomputes the hash gets a different value.

**The RPC/mempool path uses a separate, correct converter that does not have this bug:** [9](#0-8) 

`TryFrom<protobuf::ResourceBounds> for AllResourceBounds` always produces `AllResourceBounds` without any heuristic. The bug is confined to `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`, which is used only when the intermediate `InvokeTransactionV3` type is involved.

---

### Impact Explanation

A valid V3 invoke transaction with `AllResources(l2_gas={0,0}, l1_data_gas={0,0})` is accepted by the gateway (the gateway uses `AllResourceBounds` throughout and never applies the heuristic). When the transaction is propagated to peers via P2P — either through the mempool broadcast path or through the consensus proposal path — every peer deserializes it through `TryFrom<protobuf::InvokeV3> for InvokeTransactionV3`, which silently converts the resource bounds to `L1Gas`. The subsequent `try_into()` call to `RpcInvokeTransactionV3` then returns `DEPRECATED_RESOURCE_BOUNDS_ERROR`, and the peer drops the transaction. The transaction is never sequenced by any peer node.

Additionally, the transaction hash stored by the originating node (computed with `AllResources`, 3-element resource hash) diverges from any hash recomputed after protobuf deserialization (computed with `L1Gas`, 2-element resource hash). This is a hash domain collision: two structurally distinct resource-bound representations produce the same wire bytes but different hashes.

---

### Likelihood Explanation

The trigger condition is: a V3 invoke transaction where both `l2_gas` and `l1_data_gas` satisfy `ResourceBounds::is_zero()` (both `max_amount` and `max_price_per_unit` are zero). This is a specific but reachable input. The gateway does not enforce non-zero l2_gas or l1_data_gas at the stateless validation layer. A user or operator constructing a transaction with zero-price, zero-amount l2 and l1_data bounds (e.g., for a no-fee-enforcement scenario) would trigger this silently. The TODO comment in the converter (`// TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2`) confirms the heuristic is a known transitional measure, not a permanent invariant.

---

### Recommendation

Remove the value-based heuristic from `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`. Since the protobuf `ResourceBounds` message always carries all three resource fields for V3 transactions (per the proto comment: "Starting from 0.14.0, MempoolTransaction and ConsensusTransaction shouldn't have None here"), the deserialization should always produce `AllResources` when all three fields are present, regardless of their values. The `L1Gas` variant should only be produced when the protobuf message structurally omits l2_gas and l1_data_gas (i.e., they are `None`), not when they are present but zero.

---

### Proof of Concept

1. Construct a V3 invoke transaction with `resource_bounds = AllResources { l1_gas: X, l2_gas: {0, 0}, l1_data_gas: {0, 0} }`.
2. Submit it to the gateway. The gateway accepts it and computes hash H1 using `InternalRpcInvokeTransactionV3::calculate_transaction_hash` → `get_invoke_transaction_v3_hash` → `get_tip_resource_bounds_hash` with `AllResources` (3-element chain).
3. The gateway propagates the transaction to peers as `protobuf::MempoolTransaction { txn: InvokeV3(...) }` or as part of a consensus proposal.
4. A peer deserializes: `TryFrom<protobuf::InvokeV3> for InvokeTransactionV3` calls `ValidResourceBounds::try_from(resource_bounds_proto)`. Since `l2_gas.is_zero() && l1_data_gas.is_zero()`, it returns `ValidResourceBounds::L1Gas(l1_gas)`.
5. `TryFrom<protobuf::InvokeV3WithProof> for RpcInvokeTransactionV3` calls `snapi_invoke.try_into()` → `TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3` → matches `ValidResourceBounds::L1Gas` → returns `Err(StarknetApiError::OutOfRange)` → mapped to `DEPRECATED_RESOURCE_BOUNDS_ERROR`.
6. The peer rejects the transaction. It is never sequenced by any peer node.
7. If hash recomputation is attempted on the deserialized `InvokeTransactionV3`, `get_tip_resource_bounds_hash` with `L1Gas` produces hash H2 ≠ H1 (2-element vs 3-element Poseidon chain).

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

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L593-600)
```rust
impl TryFrom<protobuf::InvokeV3> for InvokeTransactionV3 {
    type Error = ProtobufConversionError;
    fn try_from(value: protobuf::InvokeV3) -> Result<Self, Self::Error> {
        let resource_bounds = ValidResourceBounds::try_from(
            value.resource_bounds.ok_or(missing("InvokeV3::resource_bounds"))?,
        )?;

        let tip = Tip(value.tip);
```

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L1027-1052)
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
```

**File:** crates/apollo_protobuf/src/converters/rpc_transaction.rs (L32-48)
```rust
impl TryFrom<protobuf::MempoolTransaction> for RpcTransaction {
    type Error = ProtobufConversionError;
    fn try_from(value: protobuf::MempoolTransaction) -> Result<Self, Self::Error> {
        let txn = value.txn.ok_or(missing("RpcTransaction::txn"))?;
        Ok(match txn {
            protobuf::mempool_transaction::Txn::DeclareV3(txn) => {
                RpcTransaction::Declare(RpcDeclareTransaction::V3(txn.try_into()?))
            }
            protobuf::mempool_transaction::Txn::DeployAccountV3(txn) => {
                RpcTransaction::DeployAccount(RpcDeployAccountTransaction::V3(txn.try_into()?))
            }
            protobuf::mempool_transaction::Txn::InvokeV3(txn) => {
                RpcTransaction::Invoke(RpcInvokeTransaction::V3(txn.try_into()?))
            }
        })
    }
}
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

**File:** crates/apollo_protobuf/src/converters/rpc_transaction.rs (L212-224)
```rust
impl TryFrom<protobuf::ResourceBounds> for AllResourceBounds {
    type Error = ProtobufConversionError;
    fn try_from(value: protobuf::ResourceBounds) -> Result<Self, Self::Error> {
        Ok(Self {
            l1_gas: value.l1_gas.ok_or(missing("ResourceBounds::l1_gas"))?.try_into()?,
            l2_gas: value.l2_gas.ok_or(missing("ResourceBounds::l2_gas"))?.try_into()?,
            l1_data_gas: value
                .l1_data_gas
                .ok_or(missing("ResourceBounds::l1_data_gas"))?
                .try_into()?,
        })
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

**File:** crates/starknet_api/src/rpc_transaction.rs (L636-639)
```rust
impl InvokeTransactionV3Trait for InternalRpcInvokeTransactionV3 {
    fn resource_bounds(&self) -> ValidResourceBounds {
        ValidResourceBounds::AllResources(self.resource_bounds)
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
