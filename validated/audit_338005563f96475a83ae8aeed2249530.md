### Title
Protobuf `ValidResourceBounds` Deserialization Silently Downgrades `AllResources` to `L1Gas`, Producing a Different Transaction Hash - (File: `crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

When an `InvokeTransactionV3` (or `DeclareTransactionV3`) with `AllResources` resource bounds is serialized to protobuf and then deserialized back, the `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` converter in `crates/apollo_protobuf/src/converters/transaction.rs` silently downgrades the variant from `ValidResourceBounds::AllResources` to `ValidResourceBounds::L1Gas` whenever both `l2_gas` and `l1_data_gas` happen to be zero-valued. This changes the transaction hash preimage used by `get_tip_resource_bounds_hash`, because `L1Gas` omits the `L1_DATA_GAS` felt from the hash chain while `AllResources` includes it. The resulting hash diverges from the one the user signed, causing the sequencer to commit a transaction under a hash that does not match the signer's intent.

---

### Finding Description

**Step 1 – Serialization (sender → protobuf)**

`ValidResourceBounds::AllResources` is serialized to `protobuf::ResourceBounds` by `From<ValidResourceBounds> for protobuf::ResourceBounds`:

```rust
ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas }) =>
    protobuf::ResourceBounds {
        l1_gas: Some(l1_gas.into()),
        l2_gas: Some(l2_gas.into()),
        l1_data_gas: Some(l1_data_gas.into()),
    },
``` [1](#0-0) 

When `l2_gas` and `l1_data_gas` are both zero (e.g., a client-side-proving transaction where `max_price_per_unit = 0` and `max_amount = 0` for those resources), the wire bytes are indistinguishable from a pre-0.13.3 `L1Gas`-only transaction.

**Step 2 – Deserialization (protobuf → internal)**

`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` applies the following heuristic:

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
``` [2](#0-1) 

There is no version tag or discriminator in the protobuf message to distinguish the two variants. The converter infers the variant purely from whether the numeric values are zero.

**Step 3 – Hash divergence**

`get_tip_resource_bounds_hash` branches on the variant:

```rust
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],                          // omits L1_DATA_GAS felt
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]
    }
});
``` [3](#0-2) 

- **`AllResources` hash preimage**: `[tip, L1_GAS_felt, L2_GAS_felt, L1_DATA_GAS_felt]`
- **`L1Gas` hash preimage**: `[tip, L1_GAS_felt, L2_GAS_felt]`

The `L1_DATA_GAS_felt` is omitted when the variant is downgraded, producing a different `tip_resource_bounds_hash`, which propagates into the full transaction hash computed by `get_invoke_transaction_v3_hash`:

```rust
let mut hash_chain = HashChain::new()
    .chain(&INVOKE)
    .chain(&transaction_version.0)
    .chain(transaction.sender_address().0.key())
    .chain(&tip_resource_bounds_hash)   // ← diverges here
    ...
``` [4](#0-3) 

**Step 4 – Where the hash is computed after protobuf round-trip**

The hash is computed in `convert_rpc_tx_to_internal` after the `RpcTransaction` is reconstructed from protobuf:

```rust
let tx_hash = tx_without_hash.calculate_transaction_hash(&self.chain_id)?;
Ok((InternalRpcTransaction { tx: tx_without_hash, tx_hash }, proof_data))
``` [5](#0-4) 

The `InternalRpcInvokeTransactionV3` always stores `AllResourceBounds` (not `ValidResourceBounds`), so the gateway path is safe. However, the P2P/consensus path deserializes `InvokeTransactionV3` (which holds `ValidResourceBounds`) via `TryFrom<protobuf::InvokeV3> for InvokeTransactionV3`:

```rust
let resource_bounds = ValidResourceBounds::try_from(
    value.resource_bounds.ok_or(missing("InvokeV3::resource_bounds"))?,
)?;
``` [6](#0-5) 

This is the path used for consensus (`ConsensusTransaction`) and P2P sync (`FullTransaction`). After deserialization, the `InvokeTransactionV3` with the downgraded `L1Gas` variant is passed to `calculate_transaction_hash`, producing a hash that differs from the one the user signed.

---

### Impact Explanation

**Impact: High – Transaction conversion or signature/hash logic binds the wrong hash.**

A transaction submitted with `AllResources` bounds where `l2_gas` and `l1_data_gas` are numerically zero (a valid and expected configuration for client-side-proving transactions, as documented in `validate_zero_fee_resource_bounds`) will, after a protobuf round-trip through the P2P/consensus layer, be stored and executed under a different transaction hash than the one the user signed. The sequencer commits the wrong hash to state, receipts, and events. Any downstream system (explorer, wallet, prover) that recomputes the hash from the on-chain data will get a mismatch.

---

### Likelihood Explanation

The condition is reachable without any privilege. Client-side-proving transactions are explicitly required to have `max_price_per_unit = 0` for all three resources and `max_amount = 0` for `l1_gas` and `l1_data_gas`: [7](#0-6) 

Such a transaction has `l2_gas.is_zero() == false` (non-zero `max_amount`), so it would **not** be downgraded. However, any `AllResources` transaction where a user sets both `l2_gas` and `l1_data_gas` to zero (e.g., a transaction that only consumes L1 gas but was constructed with the newer `AllResources` variant) **will** be silently downgraded. The protobuf comment itself acknowledges the ambiguity: `"This can be None only in transactions that don't support l2 gas. Starting from 0.14.0, MempoolTransaction and ConsensusTransaction shouldn't have None here."` — yet the deserializer still applies the zero-value heuristic. [8](#0-7) 

---

### Recommendation

1. **Add a discriminator field** to `protobuf::ResourceBounds` (e.g., a boolean `is_all_resources` or an enum `bounds_type`) so the variant can be round-tripped faithfully without relying on zero-value inference.

2. **Until the protobuf schema is updated**, the deserializer should default to `AllResources` when `l1_data_gas` is present (even if zero), reserving `L1Gas` only for the legacy case where `l1_data_gas` is `None` (the `optional` field is absent from the wire):

```rust
// Proposed fix: use presence of l1_data_gas field, not its value
let l1_data_gas_opt = value.l1_data_gas;
Ok(match l1_data_gas_opt {
    None => ValidResourceBounds::L1Gas(l1_gas),
    Some(l1_data_gas_proto) => ValidResourceBounds::AllResources(AllResourceBounds {
        l1_gas, l2_gas, l1_data_gas: l1_data_gas_proto.try_into()?
    }),
})
```

3. **Add a round-trip test** that serializes `AllResources` with zero `l2_gas` and `l1_data_gas`, deserializes it, and asserts the variant is still `AllResources` and the transaction hash is unchanged.

---

### Proof of Concept

```
1. Construct an InvokeTransactionV3 with:
   resource_bounds = AllResources {
       l1_gas:      { max_amount: 1000, max_price_per_unit: 1 },
       l2_gas:      { max_amount: 0,    max_price_per_unit: 0 },  // zero
       l1_data_gas: { max_amount: 0,    max_price_per_unit: 0 },  // zero
   }

2. Compute hash_A = get_invoke_transaction_v3_hash(tx, chain_id, version)
   → get_tip_resource_bounds_hash uses AllResources branch
   → preimage includes L1_DATA_GAS felt

3. Serialize tx → protobuf::InvokeV3 (via From<InvokeTransactionV3>)
   → l2_gas and l1_data_gas are present but zero on the wire

4. Deserialize protobuf::InvokeV3 → InvokeTransactionV3
   → TryFrom<protobuf::ResourceBounds> for ValidResourceBounds
   → l1_data_gas.is_zero() && l2_gas.is_zero() == true
   → returns ValidResourceBounds::L1Gas(l1_gas)   ← DOWNGRADED

5. Compute hash_B = get_invoke_transaction_v3_hash(tx_deserialized, chain_id, version)
   → get_tip_resource_bounds_hash uses L1Gas branch
   → preimage OMITS L1_DATA_GAS felt

6. Assert hash_A != hash_B  ← divergence confirmed
```

The divergence is in `get_tip_resource_bounds_hash` at `crates/starknet_api/src/transaction_hash.rs` lines 202–208, triggered by the variant downgrade in `crates/apollo_protobuf/src/converters/transaction.rs` lines 431–432.

### Citations

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

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L596-598)
```rust
        let resource_bounds = ValidResourceBounds::try_from(
            value.resource_bounds.ok_or(missing("InvokeV3::resource_bounds"))?,
        )?;
```

**File:** crates/starknet_api/src/transaction_hash.rs (L202-208)
```rust
    // For new V3 txs, need to also hash the data gas bounds.
    resource_felts.extend(match resource_bounds {
        ValidResourceBounds::L1Gas(_) => vec![],
        ValidResourceBounds::AllResources(all_resources) => {
            vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]
        }
    });
```

**File:** crates/starknet_api/src/transaction_hash.rs (L388-404)
```rust
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

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L391-392)
```rust
        let tx_hash = tx_without_hash.calculate_transaction_hash(&self.chain_id)?;
        Ok((InternalRpcTransaction { tx: tx_without_hash, tx_hash }, proof_data))
```

**File:** crates/starknet_transaction_prover/src/proving/virtual_snos_prover.rs (L401-443)
```rust
fn validate_zero_fee_resource_bounds(
    tx: &RpcInvokeTransactionV3,
) -> Result<(), VirtualSnosProverError> {
    let bounds = &tx.resource_bounds;
    let mut violations = Vec::new();

    if bounds.l1_gas.max_price_per_unit != GasPrice(0) {
        violations
            .push(format!("l1_gas.max_price_per_unit = {}", bounds.l1_gas.max_price_per_unit.0));
    }
    if bounds.l2_gas.max_price_per_unit != GasPrice(0) {
        violations
            .push(format!("l2_gas.max_price_per_unit = {}", bounds.l2_gas.max_price_per_unit.0));
    }
    if bounds.l1_data_gas.max_price_per_unit != GasPrice(0) {
        violations.push(format!(
            "l1_data_gas.max_price_per_unit = {}",
            bounds.l1_data_gas.max_price_per_unit.0
        ));
    }
    if tx.tip != Tip(0) {
        violations.push(format!("tip = {}", tx.tip.0));
    }

    if !violations.is_empty() {
        return Err(VirtualSnosProverError::InvalidTransactionInput(format!(
            "Proving is client-side — no fees are charged. The following fields must be zero but \
             were not: [{}]. Set all max_price_per_unit fields and tip to 0x0. Note: max_amount \
             fields are fine to set — l2_gas.max_amount controls the gas limit enforced by the OS \
             (use the value from starknet_estimateFee, or 100000000 as a safe upper bound). \
             l1_gas.max_amount and l1_data_gas.max_amount do not affect OS execution.",
            violations.join(", ")
        )));
    }

    if bounds.l2_gas.max_amount == GasAmount(0) {
        return Err(VirtualSnosProverError::InvalidTransactionInput(
            "l2_gas.max_amount must be non-zero — it is the gas limit enforced by the OS on the \
             transaction. Set this to the value returned by starknet_estimateFee, or use \
             100000000 (0x5f5e100) as a safe upper bound (sufficient for ~1 million Cairo steps)."
                .to_string(),
        ));
    }
```

**File:** crates/apollo_protobuf/src/proto/p2p/proto/transaction.proto (L13-19)
```text
message ResourceBounds {
    ResourceLimits l1_gas = 1;
    // This can be None only in transactions that don't support l2 gas.
    // Starting from 0.14.0, MempoolTransaction and ConsensusTransaction shouldn't have None here.
    optional ResourceLimits l1_data_gas = 2;
    ResourceLimits l2_gas = 3;
}
```
