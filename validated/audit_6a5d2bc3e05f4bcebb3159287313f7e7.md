### Title
`TryFrom<protobuf::InvokeV3> for InvokeTransactionV3` silently downgrades `AllResources` to `L1Gas` when `l2_gas=0` and `l1_data_gas=0`, causing P2P rejection of valid post-0.13.3 transactions — (`crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The protobuf deserialization path for `InvokeV3` transactions uses `ValidResourceBounds::try_from`, which silently defaults a missing `l1_data_gas` to zero and then classifies the transaction as `ValidResourceBounds::L1Gas` whenever both `l2_gas` and `l1_data_gas` are zero. A valid post-0.13.3 invoke transaction with `l2_gas=0` and `l1_data_gas=0` (submitted as `AllResources` via RPC) is therefore misclassified as `L1Gas` during P2P deserialization. The subsequent conversion to `RpcInvokeTransactionV3` unconditionally rejects any `L1Gas` variant, causing the transaction to be dropped by every receiving peer with `DEPRECATED_RESOURCE_BOUNDS_ERROR`.

---

### Finding Description

**Step 1 — Submission via RPC (correct path)**

`RpcInvokeTransactionV3` carries `resource_bounds: AllResourceBounds` (never `ValidResourceBounds`). [1](#0-0) 

The `From<RpcInvokeTransactionV3> for InvokeTransactionV3` conversion wraps it unconditionally as `ValidResourceBounds::AllResources`. [2](#0-1) 

The transaction converter then computes the hash via `InternalRpcInvokeTransactionV3::calculate_transaction_hash`, which calls `get_invoke_transaction_v3_hash` with `ValidResourceBounds::AllResources(self.resource_bounds)`. [3](#0-2) 

`get_tip_resource_bounds_hash` includes the `L1_DATA_GAS` field in the hash **only** for `AllResources`: [4](#0-3) 

So hash **H₁** = `poseidon(tip, L1_GAS, L2_GAS=0, L1_DATA_GAS=0, …)`.

---

**Step 2 — P2P serialization (correct)**

`From<ValidResourceBounds> for protobuf::ResourceBounds` always emits `l1_data_gas` (even when zero): [5](#0-4) 

So the wire message carries `l1_data_gas = 0` (present, not absent).

---

**Step 3 — P2P deserialization (broken)**

`TryFrom<protobuf::InvokeV3> for InvokeTransactionV3` (used by the mempool P2P path) calls `ValidResourceBounds::try_from`: [6](#0-5) 

`ValidResourceBounds::try_from` silently defaults a missing `l1_data_gas` to zero and then applies the version-detection heuristic: [7](#0-6) 

Because `l1_data_gas = 0` **and** `l2_gas = 0`, the result is `ValidResourceBounds::L1Gas` — the pre-0.13.3 variant — even though the transaction was originally `AllResources`.

---

**Step 4 — Conversion to `RpcInvokeTransactionV3` fails**

`TryFrom<protobuf::InvokeV3WithProof> for RpcInvokeTransactionV3` (the entry point for mempool P2P messages) converts the inner `InvokeV3` to `InvokeTransactionV3` first, then calls `snapi_invoke.try_into()`: [8](#0-7) 

`TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3` rejects any non-`AllResources` variant: [9](#0-8) 

The error is mapped to `DEPRECATED_RESOURCE_BOUNDS_ERROR`: [10](#0-9) 

The transaction is silently dropped by every peer that receives it via P2P.

---

**Step 5 — Hash divergence (secondary)**

Even if the rejection were bypassed, the hash computed on the receiving peer would be **H₂** = `poseidon(tip, L1_GAS, L2_GAS=0, …)` (no `L1_DATA_GAS` term), because `get_tip_resource_bounds_hash` omits `L1_DATA_GAS` for `L1Gas`. H₁ ≠ H₂, so any block containing this transaction would fail hash verification on syncing peers.

---

### Impact Explanation

A valid post-0.13.3 invoke transaction with `l2_gas = 0` and `l1_data_gas = 0` is accepted by the gateway and assigned hash H₁. Every peer that receives it via P2P mempool propagation rejects it with `DEPRECATED_RESOURCE_BOUNDS_ERROR` before sequencing. The transaction is effectively invisible to the rest of the network. This matches **High — Mempool/gateway/RPC admission rejects valid transactions before sequencing**.

---

### Likelihood Explanation

Any user who submits a V3 invoke transaction with both `l2_gas` and `l1_data_gas` set to zero (a valid no-fee-enforcement configuration) triggers this path. The gateway accepts the transaction; all peers reject it. No privileged access is required.

---

### Recommendation

The `TryFrom<protobuf::InvokeV3WithProof> for RpcInvokeTransactionV3` path must not go through `ValidResourceBounds::try_from`. It should use `AllResourceBounds::try_from` (already defined in `rpc_transaction.rs`, lines 212–224) directly, which requires `l1_data_gas` to be present and non-ambiguously preserves zero values as `AllResources`. The existing `TryFrom<protobuf::ResourceBounds> for AllResourceBounds` already enforces this correctly: [11](#0-10) 

The `TryFrom<protobuf::InvokeV3> for InvokeTransactionV3` in `transaction.rs` (used for historical block-sync transactions) may retain `unwrap_or_default` for 0.13.2 backward compatibility, but the mempool P2P path must be separated from it.

---

### Proof of Concept

1. Submit a V3 invoke transaction via RPC with `resource_bounds = { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }`. The gateway accepts it and stores it with hash H₁ (computed as `AllResources`).
2. The node serializes the transaction to `protobuf::InvokeV3WithProof` and broadcasts it. The wire message contains `l1_data_gas = 0` (present).
3. A receiving peer calls `TryFrom<protobuf::InvokeV3WithProof> for RpcInvokeTransactionV3`:
   - `protobuf::InvokeV3` → `InvokeTransactionV3`: `ValidResourceBounds::try_from` sees `l1_data_gas.is_zero() && l2_gas.is_zero()` → produces `ValidResourceBounds::L1Gas`.
   - `InvokeTransactionV3` → `RpcInvokeTransactionV3`: `L1Gas` arm hits `return Err(...)` → mapped to `DEPRECATED_RESOURCE_BOUNDS_ERROR`.
4. The peer drops the transaction. The transaction never reaches any other node's mempool.

### Citations

**File:** crates/starknet_api/src/rpc_transaction.rs (L550-556)
```rust
#[derive(Clone, Debug, Deserialize, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize, SizeOf)]
pub struct RpcInvokeTransactionV3 {
    pub sender_address: ContractAddress,
    pub calldata: Calldata,
    pub signature: TransactionSignature,
    pub nonce: Nonce,
    pub resource_bounds: AllResourceBounds,
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

**File:** crates/starknet_api/src/rpc_transaction.rs (L636-639)
```rust
impl InvokeTransactionV3Trait for InternalRpcInvokeTransactionV3 {
    fn resource_bounds(&self) -> ValidResourceBounds {
        ValidResourceBounds::AllResources(self.resource_bounds)
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

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L426-436)
```rust
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

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L593-598)
```rust
impl TryFrom<protobuf::InvokeV3> for InvokeTransactionV3 {
    type Error = ProtobufConversionError;
    fn try_from(value: protobuf::InvokeV3) -> Result<Self, Self::Error> {
        let resource_bounds = ValidResourceBounds::try_from(
            value.resource_bounds.ok_or(missing("InvokeV3::resource_bounds"))?,
        )?;
```

**File:** crates/apollo_protobuf/src/converters/rpc_transaction.rs (L29-30)
```rust
const DEPRECATED_RESOURCE_BOUNDS_ERROR: ProtobufConversionError =
    ProtobufConversionError::MissingField { field_description: "ResourceBounds::l1_data_gas" };
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
