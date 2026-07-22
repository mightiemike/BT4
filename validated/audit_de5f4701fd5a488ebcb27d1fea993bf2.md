### Title
Protobuf `ValidResourceBounds` Deserialization Silently Downgrades `AllResources` to `L1Gas`, Producing a Shorter Transaction Hash Preimage — (`crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The protobuf converter `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` silently reclassifies an `AllResources` transaction as `L1Gas` whenever both `l2_gas` and `l1_data_gas` are zero. Because `get_tip_resource_bounds_hash` hashes a **different number of resource felts** depending on the variant (3 for `AllResources`, 2 for `L1Gas`), any post-0.13.3 transaction whose user-supplied bounds happen to be zero for both those fields will produce a **different transaction hash** after a protobuf round-trip. The gateway computes and stores hash H₁ (3-felt preimage); a syncing peer recomputes H₂ (2-felt preimage); H₁ ≠ H₂, so the peer rejects the transaction or the block containing it.

---

### Finding Description

**Hash domain — `get_tip_resource_bounds_hash`**

`crates/starknet_api/src/transaction_hash.rs` lines 188–211 produce the fee-fields hash that is committed into every V3 transaction hash:

```rust
pub fn get_tip_resource_bounds_hash(
    resource_bounds: &ValidResourceBounds,
    tip: &Tip,
) -> Result<Felt, StarknetApiError> {
    let l1_resource_bounds = resource_bounds.get_l1_bounds();
    let l2_resource_bounds = resource_bounds.get_l2_bounds();

    let mut resource_felts = vec![
        get_concat_resource(&l1_resource_bounds, L1_GAS)?,
        get_concat_resource(&l2_resource_bounds, L2_GAS)?,
    ];

    resource_felts.extend(match resource_bounds {
        ValidResourceBounds::L1Gas(_) => vec![],                          // ← 2 felts
        ValidResourceBounds::AllResources(all_resources) => {
            vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]  // ← 3 felts
        }
    });

    Ok(HashChain::new().chain(&tip.0.into()).chain_iter(resource_felts.iter()).get_poseidon_hash())
}
```

The Poseidon hash over `[tip, l1, l2]` is structurally different from the hash over `[tip, l1, l2, l1_data]` even when `l1_data_gas_packed == 0`, because the sponge absorbs a different number of elements.

**Variant-flipping converter — `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`**

`crates/apollo_protobuf/src/converters/transaction.rs` lines 417–436 (used in the P2P state-sync path for historical `InvokeTransactionV3` objects):

```rust
impl TryFrom<protobuf::ResourceBounds> for ValidResourceBounds {
    fn try_from(value: protobuf::ResourceBounds) -> Result<Self, Self::Error> {
        // ...
        // TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2.
        let l1_data_gas = value.l1_data_gas.unwrap_or_default();   // silently defaults to zero
        // ...
        Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
            ValidResourceBounds::L1Gas(l1_gas)                      // ← variant changed
        } else {
            ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
        })
    }
}
```

The serialiser (`From<ValidResourceBounds> for protobuf::ResourceBounds`, lines 471–489) correctly emits all three fields for `AllResources`, so the wire bytes are lossless. The problem is purely in the deserialiser's classification logic: it re-derives the variant from the *values* rather than from the *original type*, and the condition `l1_data_gas.is_zero() && l2_gas.is_zero()` is true for any post-0.13.3 transaction whose user set both bounds to zero.

**Concrete divergence**

| Step | Node | Variant | Preimage length | Hash |
|------|------|---------|-----------------|------|
| Gateway ingests RPC tx | A | `AllResources` (l2=0, l1_data=0) | 4 elements | H₁ |
| Block committed, tx stored | A | `AllResources` | 4 elements | H₁ |
| Peer deserialises via protobuf | B | `L1Gas` | 3 elements | H₂ |
| Peer calls `validate_transaction_hash` | B | `L1Gas` | 3 elements | H₂ ≠ H₁ |

The `validate_transaction_hash` function in `crates/starknet_api/src/transaction_hash.rs` lines 170–184 is the gate used during state sync to confirm that a received transaction matches its stored hash. Because H₂ ≠ H₁, the peer rejects the transaction (and the block).

---

### Impact Explanation

A valid, sequenced transaction is permanently rejected by every syncing peer that receives it over P2P. The block containing it cannot be applied, causing those peers to stall or fork. This maps to:

> **High — Mempool/gateway/RPC admission accepts invalid transactions or rejects valid transactions before sequencing** (the transaction was valid at admission; the sync path rejects it due to the hash mismatch).

and potentially:

> **Critical — Wrong state, receipt, event, L1 message, class hash, storage value, or revert result from blockifier/syscall/execution logic for accepted input** (if the wrong variant is used to re-execute the transaction during re-execution or proving).

---

### Likelihood Explanation

Any user who submits a V3 `InvokeTransaction` with `AllResourceBounds` where `l2_gas.max_amount = 0`, `l2_gas.max_price_per_unit = 0`, `l1_data_gas.max_amount = 0`, `l1_data_gas.max_price_per_unit = 0` (a legal configuration — the user simply does not want to pay for L2 or data gas) triggers this path. The gateway accepts the transaction (it uses `AllResourceBounds` directly and never applies the variant-flip logic). The transaction is included in a block. Every peer that syncs the block via P2P will fail to validate it.

---

### Recommendation

Remove the variant-inference logic from the protobuf deserialiser. The variant should be preserved structurally, not re-derived from values. One approach: add a boolean or enum field to the protobuf `ResourceBounds` message that records whether the original transaction used the `L1Gas` or `AllResources` schema. Alternatively, always deserialise into `AllResources` in the sync path (matching the gateway's invariant that all post-0.13.3 transactions are `AllResources`), and only produce `L1Gas` when syncing pre-0.13.3 blocks where the `L1_DATA_GAS` key was genuinely absent.

The TODO comment at line 426 acknowledges this is a temporary workaround:

```rust
// TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2.
let l1_data_gas = value.l1_data_gas.unwrap_or_default();
```

Until that TODO is resolved, the classification condition `l1_data_gas.is_zero() && l2_gas.is_zero()` must not be used to flip a post-0.13.3 `AllResources` transaction into `L1Gas`.

---

### Proof of Concept

1. Craft a V3 Invoke transaction with `AllResourceBounds { l1_gas: {max_amount: X, max_price: P}, l2_gas: {0,0}, l1_data_gas: {0,0} }`.
2. Submit to the gateway. The gateway computes:
   ```
   H₁ = poseidon([tip=0, l1_packed, l2_packed=0, l1_data_packed=0])
   ```
   and stores `InternalRpcTransaction { tx_hash: H₁, ... }`.
3. The transaction is included in a block.
4. A syncing peer receives the block via P2P. The protobuf message carries `l1_gas=X/P`, `l2_gas=0/0`, `l1_data_gas=0/0`. The converter at `transaction.rs:431` evaluates `l1_data_gas.is_zero() && l2_gas.is_zero() == true` and produces `ValidResourceBounds::L1Gas(l1_gas)`.
5. The peer calls `get_tip_resource_bounds_hash` with `L1Gas` and computes:
   ```
   H₂ = poseidon([tip=0, l1_packed, l2_packed=0])   // only 3 elements
   ```
6. `validate_transaction_hash` compares H₁ (stored) with H₂ (recomputed): they differ. The peer rejects the transaction and cannot apply the block. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** crates/starknet_api/src/transaction_hash.rs (L187-211)
```rust
// An implementation of the SNIP: https://github.com/EvyatarO/SNIPs/blob/snip-8/SNIPS/snip-8.md
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

**File:** crates/starknet_api/src/transaction/fields.rs (L363-367)
```rust
#[derive(Clone, Copy, Debug, Eq, PartialEq, Hash, Ord, PartialOrd)]
pub enum ValidResourceBounds {
    L1Gas(ResourceBounds), // Pre 0.13.3. Only L1 gas. L2 bounds are signed but never used.
    AllResources(AllResourceBounds),
}
```
