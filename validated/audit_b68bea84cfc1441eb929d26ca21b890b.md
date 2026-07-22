### Title
`ValidResourceBounds` Protobuf Deserialization Misclassifies `AllResources` Transactions as `L1Gas`, Producing a Divergent Transaction Hash — (`crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` converter in `transaction.rs` classifies a deserialized transaction as `ValidResourceBounds::L1Gas` whenever both `l2_gas` and `l1_data_gas` are zero. However, a valid V3 `AllResources` transaction with `l2_gas = 0` and `l1_data_gas = 0` is indistinguishable from a pre-0.13.3 `L1Gas` transaction under this check. The two variants feed different hash preimages into `get_tip_resource_bounds_hash`, so the hash computed after the protobuf round-trip diverges from the hash the sender computed and signed.

---

### Finding Description

**Hash preimage divergence by variant**

`get_tip_resource_bounds_hash` produces structurally different preimages depending on the `ValidResourceBounds` variant:

```
L1Gas      → poseidon(tip, L1_GAS, L2_GAS)              // 2 resource terms
AllResources → poseidon(tip, L1_GAS, L2_GAS, L1_DATA_GAS) // 3 resource terms
``` [1](#0-0) 

**The misclassification**

In the P2P sync path, `ValidResourceBounds` is reconstructed from a protobuf `ResourceBounds` message:

```rust
// TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2.
let l1_data_gas = value.l1_data_gas.unwrap_or_default();
...
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
``` [2](#0-1) 

The check `l1_data_gas.is_zero() && l2_gas.is_zero()` cannot distinguish two semantically different cases:

| Case | `l1_data_gas` on wire | `l2_gas` | Correct variant | Classified as |
|---|---|---|---|---|
| Pre-0.13.3 (legacy) | `None` | `0` | `L1Gas` | `L1Gas` ✓ |
| V3 `AllResources` with zero bounds | `Some(0)` | `0` | `AllResources` | `L1Gas` ✗ |

`unwrap_or_default()` collapses `None` and `Some(0)` to the same value, erasing the distinction.

**The gateway accepts `AllResources` with zero L2/L1_DATA bounds**

The gateway stateless validator explicitly accepts `AllResourceBounds { l1_gas: NON_EMPTY, l2_gas: 0, l1_data_gas: 0 }` as a valid V3 transaction: [3](#0-2) 

The `RpcTransaction` and `InternalRpcInvokeTransactionV3` types always use `AllResourceBounds` (never `L1Gas`), so the gateway computes the hash with the 3-term `AllResources` preimage. [4](#0-3) 

**Contrast with the correct RPC-path converter**

The mempool/consensus protobuf converter (`rpc_transaction.rs`) correctly requires all three fields and always produces `AllResourceBounds`, never misclassifying:

```rust
impl TryFrom<protobuf::ResourceBounds> for AllResourceBounds {
    fn try_from(value: protobuf::ResourceBounds) -> Result<Self, Self::Error> {
        Ok(Self {
            l1_gas:      value.l1_gas.ok_or(missing(...))?.try_into()?,
            l2_gas:      value.l2_gas.ok_or(missing(...))?.try_into()?,
            l1_data_gas: value.l1_data_gas.ok_or(missing(...))?.try_into()?,
        })
    }
}
``` [5](#0-4) 

Only the `transaction.rs` converter (used for the full `Transaction` type in the P2P sync path) has the broken classification.

**Serialization side also loses the distinction**

When a `ValidResourceBounds::L1Gas` value is serialized, `get_l2_bounds()` returns `ResourceBounds::default()` (zero), so the wire representation of a legacy `L1Gas` transaction and a V3 `AllResources` transaction with zero bounds are byte-for-byte identical: [6](#0-5) 

---

### Impact Explanation

A V3 `AllResources` transaction with `l2_gas = 0` and `l1_data_gas = 0` is submitted through the gateway. The gateway computes and stores hash **H_allresources** (3-term preimage). When the transaction is propagated over P2P and deserialized by a peer node, the converter produces `L1Gas`, and the peer computes hash **H_l1gas** (2-term preimage). These two hashes are different Poseidon outputs over different-length inputs.

Consequences:
- If the peer validates the hash against the provided **H_allresources**: the transaction is rejected → valid transactions are silently dropped during sync.
- If the peer does not validate and stores with **H_l1gas**: the node's transaction index diverges from the canonical chain, causing wrong receipts, wrong event lookups, and wrong RPC responses for that transaction.

This matches: **High. Transaction conversion or signature/hash logic binds the wrong hash or executable payload** and **High. Mempool/gateway/RPC admission rejects valid transactions before sequencing**.

---

### Likelihood Explanation

Any V3 transaction that specifies only L1 gas bounds (setting `l2_gas` and `l1_data_gas` to zero) triggers the misclassification. The gateway explicitly accepts this as valid. The trigger requires no privilege and is reachable by any user submitting a standard V3 invoke/declare/deploy-account transaction with only L1 gas bounds set.

---

### Recommendation

Replace the value-based classification with a presence-based check. The protobuf field `l1_data_gas` is `None` only for pre-0.13.3 transactions; for all V3 transactions it is `Some(...)` (possibly zero). Use `is_none()` rather than `is_zero()`:

```rust
// In TryFrom<protobuf::ResourceBounds> for ValidResourceBounds:
Ok(if value.l1_data_gas.is_none() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)
} else {
    let l1_data_gas = value.l1_data_gas.unwrap_or_default().try_into()?;
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
```

This preserves backward compatibility with 0.13.2 transactions (where `l1_data_gas` is absent) while correctly classifying V3 `AllResources` transactions that happen to have zero L2/L1_DATA bounds.

---

### Proof of Concept

1. Construct a V3 invoke transaction with `AllResourceBounds { l1_gas: {max_amount: 1000, max_price_per_unit: 1}, l2_gas: {0,0}, l1_data_gas: {0,0} }`.
2. Submit to the gateway. The gateway accepts it and computes:
   - `get_tip_resource_bounds_hash(AllResources{...}, tip)` → `poseidon(tip, L1_GAS_packed, L2_GAS_packed_zero, L1_DATA_GAS_packed_zero)` = **H_allresources**
3. The transaction is stored with `tx_hash = H_allresources` and propagated via P2P protobuf.
4. The receiving node deserializes the `ResourceBounds` protobuf: `l1_data_gas = Some(0).unwrap_or_default() = 0`, `l2_gas = 0` → `l1_data_gas.is_zero() && l2_gas.is_zero()` → `ValidResourceBounds::L1Gas(l1_gas)`.
5. The receiving node computes:
   - `get_tip_resource_bounds_hash(L1Gas{...}, tip)` → `poseidon(tip, L1_GAS_packed, L2_GAS_packed_zero)` = **H_l1gas** ≠ **H_allresources**
6. The transaction hash diverges. The receiving node either rejects the transaction (hash mismatch) or stores it under the wrong hash, producing an inconsistent view of the chain. [7](#0-6) [8](#0-7)

### Citations

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

**File:** crates/starknet_api/src/rpc_transaction.rs (L616-634)
```rust
pub struct InternalRpcInvokeTransactionV3 {
    pub sender_address: ContractAddress,
    pub calldata: Calldata,
    pub signature: TransactionSignature,
    pub nonce: Nonce,
    pub resource_bounds: AllResourceBounds,
    pub tip: Tip,
    pub paymaster_data: PaymasterData,
    pub account_deployment_data: AccountDeploymentData,
    pub nonce_data_availability_mode: DataAvailabilityMode,
    pub fee_data_availability_mode: DataAvailabilityMode,
    pub proof_facts: ProofFacts,
}

impl InternalRpcInvokeTransactionV3 {
    pub fn version(&self) -> TransactionVersion {
        TransactionVersion::THREE
    }
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
