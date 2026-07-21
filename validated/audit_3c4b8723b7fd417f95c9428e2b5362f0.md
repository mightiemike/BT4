### Title
Protobuf `ValidResourceBounds` Deserialization Silently Downgrades `AllResources` to `L1Gas`, Producing a Different Transaction Hash Preimage — (`File: crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary
The protobuf deserializer for `ValidResourceBounds` classifies any transaction whose `l2_gas` and `l1_data_gas` fields are both zero as `ValidResourceBounds::L1Gas`, even when the original signer constructed and signed the transaction as `ValidResourceBounds::AllResources`. Because `get_tip_resource_bounds_hash` omits the `L1_DATA_GAS` field from the Poseidon hash preimage for the `L1Gas` variant but includes it for `AllResources`, the round-trip through protobuf produces a different transaction hash than the one the user signed, breaking the hash/signature binding.

### Finding Description

**Step 1 – Protobuf deserialization silently changes the variant.** [1](#0-0) 

When `l1_data_gas` is absent in the wire message, `unwrap_or_default()` yields a zero `ResourceBounds`. When both `l1_data_gas.is_zero() && l2_gas.is_zero()` hold, the result is `ValidResourceBounds::L1Gas(l1_gas)` — even though the original transaction was signed as `AllResources`.

**Step 2 – The hash function branches on the variant.** [2](#0-1) 

`get_tip_resource_bounds_hash` builds the Poseidon preimage as:
- `AllResources` → `[tip, L1_GAS_concat, L2_GAS_concat, L1_DATA_GAS_concat]`
- `L1Gas` → `[tip, L1_GAS_concat, L2_GAS_concat]` (**`L1_DATA_GAS` omitted**)

**Step 3 – The serializer always emits `l1_data_gas = Some(zero)` for `AllResources`.** [3](#0-2) 

So the round-trip is deterministic: `AllResources(l1_gas=X, l2_gas=0, l1_data_gas=0)` → protobuf → `L1Gas(l1_gas=X)`. The data is preserved byte-for-byte, but the variant changes, and with it the hash preimage.

**Step 4 – The hash is recomputed from the deserialized transaction.** [4](#0-3) 

`InternalRpcInvokeTransactionV3::calculate_transaction_hash` calls `get_invoke_transaction_v3_hash`, which calls `get_tip_resource_bounds_hash` with the variant returned by `resource_bounds()`. After protobuf round-trip the variant is `L1Gas`, so the hash omits `L1_DATA_GAS(0)` and diverges from the hash the user signed.

### Impact Explanation

A V3 invoke transaction submitted with `AllResources` where `l2_gas = 0` and `l1_data_gas = 0` (but `l1_gas > 0`, which passes gateway validation) is accepted by the gateway with hash H_all. When the transaction is propagated via P2P protobuf and deserialized by a receiving node, the variant becomes `L1Gas` and the recomputed hash is H_l1 ≠ H_all. Any node that recomputes the hash from the deserialized transaction will bind the wrong hash to the transaction, causing:

- Signature verification to fail (the user signed H_all, but the node verifies against H_l1).
- The executed transaction to carry a different hash than the one committed in the block header, producing a wrong receipt/event hash.
- Potential consensus split between nodes that received the transaction via RPC (hash H_all) and nodes that received it via P2P (hash H_l1).

This matches the allowed impact: **"Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload."**

### Likelihood Explanation

The trigger requires only that a user submit a standard V3 invoke transaction with non-zero `l1_gas` and zero `l2_gas`/`l1_data_gas`. The gateway's `validate_resource_bounds` check passes because `l1_gas > 0` makes `max_possible_fee > 0`. [5](#0-4) 

No special privilege is required. The protobuf round-trip is exercised on every P2P block/transaction propagation path.

### Recommendation

In `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`, do not infer the variant from the zero-ness of the fields. Instead, either:

1. Transmit an explicit discriminant field in the protobuf message that records whether the original transaction was `L1Gas` or `AllResources`, and use that to drive the conversion; or
2. Always deserialize as `AllResources` when all three resource fields are present in the wire message (even if their values are zero), reserving `L1Gas` only for legacy messages that genuinely omit `l2_gas`.

The analogous fix in the external report is: "revert when `newShare == 0`" — here the fix is: do not silently change the variant when the numeric values happen to be zero.

### Proof of Concept

```
1. Construct an InvokeTransactionV3 with:
     resource_bounds = AllResources { l1_gas: {max_amount:1, max_price:1},
                                      l2_gas: {max_amount:0, max_price:0},
                                      l1_data_gas: {max_amount:0, max_price:0} }

2. Compute H_all = get_invoke_transaction_v3_hash(tx, chain_id, version)
   → tip_resource_bounds_hash covers [tip, L1_GAS(1,1), L2_GAS(0,0), L1_DATA_GAS(0,0)]

3. Sign tx with H_all.

4. Serialize tx to protobuf::ResourceBounds:
     l1_gas = Some({max_amount:1, max_price:1})
     l2_gas = Some({max_amount:0, max_price:0})
     l1_data_gas = Some({max_amount:0, max_price:0})

5. Deserialize via TryFrom<protobuf::ResourceBounds> for ValidResourceBounds:
     l1_data_gas.is_zero() && l2_gas.is_zero() → ValidResourceBounds::L1Gas(l1_gas)

6. Compute H_l1 = get_invoke_transaction_v3_hash(deserialized_tx, chain_id, version)
   → tip_resource_bounds_hash covers [tip, L1_GAS(1,1), L2_GAS(0,0)]
     (L1_DATA_GAS omitted)

7. Assert H_all ≠ H_l1  ← hash mismatch; signature over H_all fails against H_l1.
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

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L56-88)
```rust
    fn validate_resource_bounds(
        &self,
        tx: &RpcTransaction,
    ) -> StatelessTransactionValidatorResult<()> {
        if !self.config.validate_resource_bounds {
            return Ok(());
        }

        let resource_bounds = *tx.resource_bounds();
        // The resource bounds should be positive even without the tip.
        if ValidResourceBounds::AllResources(resource_bounds).max_possible_fee(Tip::ZERO) == Fee(0)
        {
            return Err(StatelessTransactionValidatorError::ZeroResourceBounds { resource_bounds });
        }

        if resource_bounds.l2_gas.max_price_per_unit.0 < self.config.min_gas_price {
            return Err(StatelessTransactionValidatorError::MaxGasPriceTooLow {
                gas_price: resource_bounds.l2_gas.max_price_per_unit,
                min_gas_price: self.config.min_gas_price,
            });
        }

        // TODO(Arni): Consider adding a validation for max_l2_gas_amount for declare.
        if let RpcTransaction::Declare(_) = tx {
        } else if resource_bounds.l2_gas.max_amount.0 > self.config.max_l2_gas_amount {
            return Err(StatelessTransactionValidatorError::MaxGasAmountTooHigh {
                gas_amount: resource_bounds.l2_gas.max_amount,
                max_gas_amount: self.config.max_l2_gas_amount,
            });
        }

        Ok(())
    }
```
