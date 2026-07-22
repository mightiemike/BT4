### Title
Protobuf `ValidResourceBounds` Deserialization Silently Coerces `AllResources` to `L1Gas`, Producing a Divergent Transaction Hash — (`crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The protobuf deserializer for `ValidResourceBounds` silently downgrades an `AllResources` variant (with zero `l2_gas` and zero `l1_data_gas`) to the `L1Gas` variant. Because `get_tip_resource_bounds_hash` hashes a different number of resource-bound fields depending on the variant, the transaction hash computed by the originating node (which used `AllResources`) differs from the hash recomputed by any node that deserializes the same transaction from protobuf (which reconstructs `L1Gas`). This is a canonicalization invariant violation across the P2P boundary.

---

### Finding Description

**Step 1 – The hash is variant-sensitive.**

`get_tip_resource_bounds_hash` in `crates/starknet_api/src/transaction_hash.rs` builds the resource-bounds preimage differently for the two variants:

```
L1Gas      → [tip, L1_GAS_concat, L2_GAS_concat(zero)]          // 2 resources
AllResources → [tip, L1_GAS_concat, L2_GAS_concat, L1_DATA_GAS_concat]  // 3 resources
```

Even when `l2_gas = 0` and `l1_data_gas = 0`, the `AllResources` path appends an extra `L1_DATA_GAS(zero)` field, producing a strictly different Poseidon hash. [1](#0-0) 

**Step 2 – The gateway always hashes as `AllResources`.**

`RpcInvokeTransactionV3` carries `resource_bounds: AllResourceBounds`. The converter wraps it as `ValidResourceBounds::AllResources(...)` before calling `get_invoke_transaction_v3_hash`. A user who submits `AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` therefore gets a hash that includes the zero `L1_DATA_GAS` field. [2](#0-1) [3](#0-2) 

**Step 3 – The protobuf round-trip silently changes the variant.**

When the transaction is serialized to protobuf for P2P propagation, `ValidResourceBounds::L1Gas` is serialized with an explicit zero `l1_data_gas` field. On the receiving side, the deserializer applies the following decision:

```rust
// TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2.
let l1_data_gas = value.l1_data_gas.unwrap_or_default();
...
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)   // ← variant changes here
} else {
    ValidResourceBounds::AllResources(...)
})
```

An `AllResources` transaction with `l2_gas = 0` and `l1_data_gas = 0` is reconstructed as `L1Gas` on the receiving node. [4](#0-3) 

**Step 4 – The divergent value.**

| Side | Variant used | Hash preimage |
|---|---|---|
| Originating node (gateway) | `AllResources` | `[tip, L1_GAS(X), L2_GAS(0), L1_DATA_GAS(0)]` |
| Receiving node (P2P deserialization) | `L1Gas` | `[tip, L1_GAS(X), L2_GAS(0)]` |

The two Poseidon hashes are different. Any node that recomputes the transaction hash after protobuf deserialization (e.g., during P2P block sync hash validation or consensus transaction verification) will obtain a hash that does not match the hash stored in the block.

---

### Impact Explanation

A transaction with `AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` is valid and accepted by the gateway. The originating sequencer stores it in a block with hash H₁ (computed over three resource fields). Any peer that receives the block via P2P, deserializes the transaction, and recomputes the hash obtains H₂ ≠ H₁. This causes the peer to reject a legitimately produced block, breaking consensus or P2P sync. The impact matches: **"Wrong state, receipt, event … or revert result from blockifier/syscall/execution logic for accepted input"** and **"Transaction conversion or signature/hash logic binds the wrong … hash … or executable payload."**

---

### Likelihood Explanation

Low. The trigger requires a transaction whose `l2_gas` and `l1_data_gas` are both exactly zero while `l1_gas` is non-zero. The gateway's stateless validator accepts this combination (test cases confirm `valid_l1_gas` with zero l2/data bounds). An unprivileged user can craft such a transaction deliberately or it can arise naturally from a legacy client that only sets `l1_gas`.

---

### Recommendation

1. **Preserve the variant across protobuf round-trips.** Add a discriminator field to the protobuf `ResourceBounds` message (e.g., a boolean `is_all_resources`) so the deserializer can reconstruct the correct variant regardless of whether the numeric values are zero.
2. **Remove the `unwrap_or_default` fallback** once support for protocol version 0.13.2 is dropped, and instead require `l1_data_gas` to be present, as the existing TODO comment already notes.
3. **Add a canonicalization test** asserting that `AllResources { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` survives a protobuf round-trip with the same variant and the same transaction hash.

---

### Proof of Concept

```
1. Craft RpcInvokeTransactionV3 with:
       resource_bounds = AllResourceBounds {
           l1_gas:      ResourceBounds { max_amount: 1000, max_price_per_unit: 1 },
           l2_gas:      ResourceBounds { max_amount: 0,    max_price_per_unit: 0 },
           l1_data_gas: ResourceBounds { max_amount: 0,    max_price_per_unit: 0 },
       }

2. Submit to gateway.
   → Gateway calls get_invoke_transaction_v3_hash with ValidResourceBounds::AllResources(...)
   → get_tip_resource_bounds_hash hashes [tip, L1_GAS(1000,1), L2_GAS(0,0), L1_DATA_GAS(0,0)]
   → H1 = poseidon([tip, concat_L1, concat_L2, concat_L1DATA])

3. Transaction is included in a block and propagated via P2P protobuf.
   → Serialized: l1_gas=Some(1000,1), l2_gas=Some(0,0), l1_data_gas=Some(0,0)

4. Receiving node deserializes:
   → l1_data_gas.is_zero() && l2_gas.is_zero() == true
   → Reconstructed as ValidResourceBounds::L1Gas(ResourceBounds{1000,1})

5. Receiving node recomputes hash:
   → get_tip_resource_bounds_hash hashes [tip, L1_GAS(1000,1), L2_GAS(0,0)]  ← only 2 resources
   → H2 = poseidon([tip, concat_L1, concat_L2])

6. H1 ≠ H2 → receiving node rejects the block as having an invalid transaction hash.
``` [5](#0-4) [6](#0-5)

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

**File:** crates/starknet_api/src/transaction_hash.rs (L370-405)
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
}
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L636-639)
```rust
impl InvokeTransactionV3Trait for InternalRpcInvokeTransactionV3 {
    fn resource_bounds(&self) -> ValidResourceBounds {
        ValidResourceBounds::AllResources(self.resource_bounds)
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
