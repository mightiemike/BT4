### Title
`ValidResourceBounds::AllResources{l2_gas:0, l1_data_gas:0}` silently collapses to `L1Gas` in protobuf deserialization, producing a divergent transaction hash and execution result - (File: `crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The protobuf deserializer for `ValidResourceBounds` silently converts `AllResources{l1_gas: X, l2_gas: 0, l1_data_gas: 0}` to `L1Gas(X)` when both `l2_gas` and `l1_data_gas` are zero. This is the sequencer-native analog to M-10: the "accounted" resource bounds type (`AllResources`, which hashes 4 elements) differs from the "actual" type after P2P deserialization (`L1Gas`, which hashes 3 elements), and this discrepancy is silently accepted without error. The consequence is a divergent transaction hash and a divergent execution result (initial Sierra gas) between the originating node and any node that receives the transaction over P2P.

---

### Finding Description

**Step 1 — Hash domain split in `get_tip_resource_bounds_hash`**

`get_tip_resource_bounds_hash` in `crates/starknet_api/src/transaction_hash.rs` hashes a different number of elements depending on the `ValidResourceBounds` variant:

- `L1Gas(X)` → hashes `[tip, l1_gas_packed, l2_gas_zero_packed]` — **3 elements**
- `AllResources{l1_gas: X, l2_gas: 0, l1_data_gas: 0}` → hashes `[tip, l1_gas_packed, l2_gas_zero_packed, l1_data_gas_zero_packed]` — **4 elements** [1](#0-0) 

Because `L1_DATA_GAS = b"L1_DATA"` is non-zero, the packed felt for `l1_data_gas_zero` is non-zero, so the two Poseidon hashes are provably distinct. Call them **H_all** (AllResources) and **H_l1** (L1Gas).

**Step 2 — Silent type collapse in protobuf deserialization**

The `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` converter in `crates/apollo_protobuf/src/converters/transaction.rs` silently collapses `AllResources{X, 0, 0}` to `L1Gas(X)`:

```rust
let l1_data_gas = value.l1_data_gas.unwrap_or_default();
...
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)   // ← silent collapse
} else {
    ValidResourceBounds::AllResources(...)
})
``` [2](#0-1) 

This converter is used in `TryFrom<protobuf::InvokeV3> for InvokeTransactionV3` (the block-sync wire type): [3](#0-2) 

**Step 3 — Reachable trigger**

`RpcInvokeTransactionV3` uses `AllResourceBounds` directly (not `ValidResourceBounds`), so a user can submit a transaction with `l2_gas.max_amount = 0` and `l1_data_gas.max_amount = 0` via the public RPC endpoint. No gateway check prevents this. [4](#0-3) 

When the gateway converts this to `InternalRpcInvokeTransactionV3`, `resource_bounds()` always returns `ValidResourceBounds::AllResources(self.resource_bounds)`, so the hash is computed as **H_all**: [5](#0-4) 

When committed to a block, the transaction is stored as `InvokeTransactionV3` with `resource_bounds: ValidResourceBounds::AllResources{X, 0, 0}` (the `From<InternalRpcInvokeTransactionV3> for InvokeTransactionV3` conversion preserves `AllResources`): [6](#0-5) 

**Step 4 — Divergent hash on the receiving node**

When the block is propagated over P2P, the `InvokeV3` protobuf message is deserialized on the receiving node via `TryFrom<protobuf::InvokeV3> for InvokeTransactionV3`. The `resource_bounds` field is deserialized as `ValidResourceBounds::L1Gas(X)` (due to the zero-check collapse). If the receiving node recomputes the transaction hash (e.g., for block validation), it gets **H_l1 ≠ H_all**.

**Step 5 — Divergent execution result**

Beyond the hash, the resource bounds type directly controls the initial Sierra gas in `TransactionContext::initial_sierra_gas`:

- `L1Gas(_)` → `initial_gas_no_user_l2_bound()` (a large constant)
- `AllResources{l2_gas: GasAmount(0), ..}` → `GasAmount(0)` (immediate out-of-gas) [7](#0-6) 

The originating node executes the transaction with initial gas = 0 (reverted, out-of-gas). The receiving node executes the same transaction with a large initial gas (may succeed). This is a critical execution divergence: two nodes produce different receipts for the same transaction in the same block.

---

### Impact Explanation

Two distinct impacts:

1. **Wrong receipt/state from blockifier** (Critical): The originating node and any P2P-syncing node produce different execution results for the same `InvokeTransactionV3` with `AllResources{l2_gas: 0, l1_data_gas: 0}`. The originating node reverts the transaction (out-of-gas); the receiving node may execute it successfully. This breaks consensus and state agreement.

2. **Wrong transaction hash** (High): The stored hash H_all does not match the hash H_l1 recomputed from the P2P-deserialized transaction. Any component that recomputes and validates the hash after P2P deserialization will reject a valid block.

---

### Likelihood Explanation

- The trigger is a standard RPC `invoke_v3` transaction with `l2_gas.max_amount = 0` and `l1_data_gas.max_amount = 0`. No special privilege is required; any user can submit this.
- The `AllResourceBounds` type used in `RpcInvokeTransactionV3` does not enforce non-zero bounds.
- The protobuf collapse is unconditional and silent — no error, no log, no flag.
- The divergence is deterministic and reproducible.

---

### Recommendation

1. **Fix the protobuf deserializer**: Remove the `L1Gas` vs `AllResources` inference from `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`. Instead, add an explicit discriminant field to the protobuf `ResourceBounds` message to carry the variant tag, or always deserialize as `AllResources` when all three fields are present (even if zero).

2. **Fix `get_tip_resource_bounds_hash`**: Ensure that `AllResources{X, 0, 0}` and `L1Gas(X)` produce the same hash, or add a version gate that prevents `AllResources` transactions with zero l2_gas and l1_data_gas from being accepted (forcing them to use the `L1Gas` variant explicitly).

3. **Add a round-trip hash invariant test**: After any protobuf serialization/deserialization of `InvokeTransactionV3`, assert that `tx.calculate_transaction_hash(chain_id, version) == original_hash`.

---

### Proof of Concept

```
1. Submit via RPC:
   RpcInvokeTransactionV3 {
     resource_bounds: AllResourceBounds {
       l1_gas: ResourceBounds { max_amount: 1000, max_price_per_unit: 1 },
       l2_gas: ResourceBounds { max_amount: 0, max_price_per_unit: 0 },  // zero
       l1_data_gas: ResourceBounds { max_amount: 0, max_price_per_unit: 0 },  // zero
     },
     ...
   }

2. Gateway computes hash H_all using ValidResourceBounds::AllResources → 4-element Poseidon.
   Transaction is committed to block N with hash H_all.

3. Block N is propagated over P2P as protobuf InvokeV3.

4. Receiving node deserializes:
   ValidResourceBounds::try_from(resource_bounds)
   → l1_data_gas.is_zero() && l2_gas.is_zero() == true
   → ValidResourceBounds::L1Gas(ResourceBounds { max_amount: 1000, max_price_per_unit: 1 })

5. Receiving node recomputes hash H_l1 using ValidResourceBounds::L1Gas → 3-element Poseidon.
   H_l1 ≠ H_all → hash validation fails, block rejected.

6. Alternatively, if hash is not revalidated:
   Originating node: initial_sierra_gas = GasAmount(0) → out-of-gas → REVERTED
   Receiving node:   initial_sierra_gas = initial_gas_no_user_l2_bound() → may SUCCEED
   → Divergent receipts, divergent state roots, consensus failure.
``` [8](#0-7) [9](#0-8) [7](#0-6)

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

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L593-599)
```rust
impl TryFrom<protobuf::InvokeV3> for InvokeTransactionV3 {
    type Error = ProtobufConversionError;
    fn try_from(value: protobuf::InvokeV3) -> Result<Self, Self::Error> {
        let resource_bounds = ValidResourceBounds::try_from(
            value.resource_bounds.ok_or(missing("InvokeV3::resource_bounds"))?,
        )?;

```

**File:** crates/starknet_api/src/rpc_transaction.rs (L550-566)
```rust
#[derive(Clone, Debug, Deserialize, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize, SizeOf)]
pub struct RpcInvokeTransactionV3 {
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
    #[serde(default, skip_serializing_if = "ProofFacts::is_empty")]
    pub proof_facts: ProofFacts,
    #[serde(default, skip_serializing_if = "Proof::is_empty")]
    pub proof: Proof,
}
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L636-639)
```rust
impl InvokeTransactionV3Trait for InternalRpcInvokeTransactionV3 {
    fn resource_bounds(&self) -> ValidResourceBounds {
        ValidResourceBounds::AllResources(self.resource_bounds)
    }
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L679-694)
```rust
impl From<InternalRpcInvokeTransactionV3> for InvokeTransactionV3 {
    fn from(tx: InternalRpcInvokeTransactionV3) -> Self {
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

**File:** crates/blockifier/src/context.rs (L55-73)
```rust
    pub fn initial_sierra_gas(&self) -> GasAmount {
        match &self.tx_info {
            TransactionInfo::Deprecated(_)
            | TransactionInfo::Current(CurrentTransactionInfo {
                resource_bounds: ValidResourceBounds::L1Gas(_),
                ..
            }) => self.block_context.versioned_constants.initial_gas_no_user_l2_bound(),
            TransactionInfo::Current(CurrentTransactionInfo {
                resource_bounds: ValidResourceBounds::AllResources(AllResourceBounds { l2_gas, .. }),
                ..
            }) => {
                #[cfg(feature = "reexecution")]
                if self.block_context.versioned_constants.ignore_user_l2_gas_bound {
                    return self.block_context.versioned_constants.initial_gas_no_user_l2_bound();
                }
                l2_gas.max_amount
            }
        }
    }
```
