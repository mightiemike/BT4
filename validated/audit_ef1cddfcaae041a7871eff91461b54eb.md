### Title
`ValidResourceBounds` Variant Collapse in Protobuf Round-Trip Produces Wrong Transaction Hash for Synced Nodes — (`crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The protobuf serialization of `ValidResourceBounds` is not injective: both `ValidResourceBounds::L1Gas(X)` and `ValidResourceBounds::AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` serialize to identical protobuf bytes. On deserialization the heuristic always reconstructs `L1Gas(X)`. Because `get_tip_resource_bounds_hash` produces a structurally different hash for the two variants (different number of elements fed into the Poseidon sponge), a transaction whose hash was computed over the `AllResources` encoding at the gateway will hash to a different value on any node that reconstructs it from protobuf — causing a canonical hash divergence across the network.

### Finding Description

**Serialization collapses the variant.**

`From<ValidResourceBounds> for protobuf::ResourceBounds` in `crates/apollo_protobuf/src/converters/transaction.rs` serializes `L1Gas(X)` by writing `l2_gas = ResourceBounds::default()` and `l1_data_gas = ResourceBounds::default()`. It serializes `AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` by writing the same zero values for `l2_gas` and `l1_data_gas`. The two variants therefore produce byte-identical protobuf messages. [1](#0-0) 

**Deserialization always picks `L1Gas` when both fields are zero.**

`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` reconstructs the variant with the heuristic `if l1_data_gas.is_zero() && l2_gas.is_zero() { L1Gas(l1_gas) } else { AllResources(...) }`. Any `AllResources` transaction whose l2_gas and l1_data_gas bounds are zero is silently downgraded to `L1Gas`. [2](#0-1) 

**The two variants hash differently.**

`get_tip_resource_bounds_hash` feeds a different number of elements into the Poseidon sponge depending on the variant:

- `L1Gas`: `[tip, concat(l1_gas, L1_GAS), concat(0, L2_GAS)]` — **2 resource felts**
- `AllResources` with zero l2/l1_data: `[tip, concat(l1_gas, L1_GAS), concat(0, L2_GAS), concat(0, L1_DATA_GAS)]` — **3 resource felts** [3](#0-2) 

**The gateway always hashes as `AllResources`.**

`RpcInvokeTransactionV3` and `InternalRpcInvokeTransactionV3` both store `resource_bounds` as the concrete `AllResourceBounds` type. Their `InvokeTransactionV3Trait::resource_bounds()` implementation unconditionally wraps it as `ValidResourceBounds::AllResources(self.resource_bounds)`, so the hash computed at ingestion always uses the 3-element path. [4](#0-3) 

**The conversion path that loses the variant is used for block sync.**

`TryFrom<protobuf::InvokeV3> for InvokeTransactionV3` (block-sync path) calls `ValidResourceBounds::try_from(...)` on the deserialized resource bounds, triggering the collapse. [5](#0-4) 

### Impact Explanation

A syncing node that reconstructs an `InvokeTransactionV3` from protobuf and recomputes its hash (e.g., during block-hash verification via `TransactionCommitment`, or when answering `starknet_getTransactionByHash`) will obtain a hash that differs from the hash stored by the sequencer. This constitutes:

- **Wrong transaction hash returned by RPC** — the synced node serves an authoritative-looking but incorrect `tx_hash` for any affected transaction.
- **Block hash divergence** — if the synced node recomputes the transaction commitment from the deserialized transactions, the resulting `TransactionCommitment` will differ from the one committed by the sequencer, causing the block hash to mismatch and breaking consensus/proof verification.

This matches the **Critical** impact category: *Wrong state, receipt, event, class hash, storage value, or revert result from blockifier/syscall/execution logic for accepted input*, and the **High** category: *RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value.*

### Likelihood Explanation

Any unprivileged user can submit an `RpcInvokeTransactionV3` with `resource_bounds = AllResourceBounds { l1_gas: <nonzero>, l2_gas: ResourceBounds::default(), l1_data_gas: ResourceBounds::default() }`. The gateway accepts it (the gateway only rejects transactions that fail stateless/stateful validation, not ones with zero l2/l1_data bounds). The divergence is deterministic and reproducible for every such transaction.

### Recommendation

The protobuf encoding must preserve the `ValidResourceBounds` variant explicitly. Two concrete options:

1. **Add a discriminant field** to `protobuf::ResourceBounds` (e.g., `bool is_all_resources`) so the deserializer can reconstruct the exact variant without relying on a zero-value heuristic.
2. **Encode `L1Gas` and `AllResources` as separate protobuf message types** (a `oneof`), eliminating the ambiguity entirely.

The heuristic in `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` must not be used for any path that feeds into hash computation.

### Proof of Concept

```
1. Craft RpcInvokeTransactionV3 with:
     resource_bounds = AllResourceBounds {
         l1_gas:      ResourceBounds { max_amount: 1000, max_price_per_unit: 1 },
         l2_gas:      ResourceBounds { max_amount: 0,    max_price_per_unit: 0 },
         l1_data_gas: ResourceBounds { max_amount: 0,    max_price_per_unit: 0 },
     }

2. Submit to gateway.
   Gateway computes H1 = poseidon(INVOKE, v3, sender,
       poseidon(tip, concat(l1_gas,"L1_GAS"), concat(0,"L2_GAS"), concat(0,"L1_DATA")),
       ...)                                                    ← 3-element resource hash

3. Transaction is included in block B; H1 is stored as the canonical tx_hash.

4. Syncing node receives block B via P2P.
   Deserializes InvokeV3 → ValidResourceBounds::try_from sees
       l2_gas.is_zero() && l1_data_gas.is_zero() → returns L1Gas(l1_gas).

5. Syncing node recomputes hash:
   H2 = poseidon(INVOKE, v3, sender,
       poseidon(tip, concat(l1_gas,"L1_GAS"), concat(0,"L2_GAS")),
       ...)                                                    ← 2-element resource hash

6. H1 ≠ H2.
   Any RPC call to starknet_getTransactionByHash on the syncing node returns H2.
   Block-hash recomputation on the syncing node diverges from the sequencer's committed value.
```

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

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L471-489)
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
```

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L593-660)
```rust
impl TryFrom<protobuf::InvokeV3> for InvokeTransactionV3 {
    type Error = ProtobufConversionError;
    fn try_from(value: protobuf::InvokeV3) -> Result<Self, Self::Error> {
        let resource_bounds = ValidResourceBounds::try_from(
            value.resource_bounds.ok_or(missing("InvokeV3::resource_bounds"))?,
        )?;

        let tip = Tip(value.tip);

        let signature = TransactionSignature(
            value
                .signature
                .ok_or(missing("InvokeV3::signature"))?
                .parts
                .into_iter()
                .map(Felt::try_from)
                .collect::<Result<Vec<_>, _>>()?
                .into(),
        );

        let nonce = Nonce(value.nonce.ok_or(missing("InvokeV3::nonce"))?.try_into()?);

        let sender_address = value.sender.ok_or(missing("InvokeV3::sender"))?.try_into()?;

        let calldata =
            value.calldata.into_iter().map(Felt::try_from).collect::<Result<Vec<_>, _>>()?;

        let calldata = Calldata(calldata.into());

        let nonce_data_availability_mode =
            enum_int_to_volition_domain(value.nonce_data_availability_mode)?;

        let fee_data_availability_mode =
            enum_int_to_volition_domain(value.fee_data_availability_mode)?;

        let paymaster_data = PaymasterData(
            value.paymaster_data.into_iter().map(Felt::try_from).collect::<Result<Vec<_>, _>>()?,
        );

        let account_deployment_data = AccountDeploymentData(
            value
                .account_deployment_data
                .into_iter()
                .map(Felt::try_from)
                .collect::<Result<Vec<_>, _>>()?,
        );

        let proof_facts: ProofFacts = value
            .proof_facts
            .into_iter()
            .map(Felt::try_from)
            .collect::<Result<Vec<_>, _>>()?
            .into();

        Ok(Self {
            resource_bounds,
            tip,
            signature,
            nonce,
            sender_address,
            calldata,
            nonce_data_availability_mode,
            fee_data_availability_mode,
            paymaster_data,
            account_deployment_data,
            proof_facts,
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

**File:** crates/starknet_api/src/rpc_transaction.rs (L636-639)
```rust
impl InvokeTransactionV3Trait for InternalRpcInvokeTransactionV3 {
    fn resource_bounds(&self) -> ValidResourceBounds {
        ValidResourceBounds::AllResources(self.resource_bounds)
    }
```
