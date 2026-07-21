### Title
`ValidResourceBounds` Protobuf Deserialization Silently Downgrades `AllResources` to `L1Gas`, Producing a Different Transaction Hash for the Same Payload — (`crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The protobuf deserializer for `ValidResourceBounds` classifies a transaction as `L1Gas` whenever `l2_gas` and `l1_data_gas` are both zero, even when the original transaction was submitted and hashed as `AllResources`. Because `get_tip_resource_bounds_hash` produces a structurally different hash preimage for the two variants (the `AllResources` path appends an extra `L1_DATA_GAS` element), the same on-chain transaction receives two distinct hash values depending on which code path processes it. A transaction submitted through the RPC gateway is always hashed as `AllResources`; after a protobuf round-trip through the P2P sync path the same transaction is hashed as `L1Gas`, yielding a different `TransactionHash`.

### Finding Description

**Hash preimage divergence — `crates/starknet_api/src/transaction_hash.rs:188-210`**

`get_tip_resource_bounds_hash` builds the resource-bounds sub-hash conditionally:

```rust
let mut resource_felts = vec![
    get_concat_resource(&l1_resource_bounds, L1_GAS)?,
    get_concat_resource(&l2_resource_bounds, L2_GAS)?,
];
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],                          // 2 elements
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]  // 3 elements
    }
});
```

`L1Gas` → `poseidon(tip, L1_GAS_felt, L2_GAS_felt)`
`AllResources` → `poseidon(tip, L1_GAS_felt, L2_GAS_felt, L1_DATA_GAS_felt)`

Even when `l2_gas = 0` and `l1_data_gas = 0`, the two variants produce **different Poseidon digests** because the input length differs.

**Protobuf deserializer silently downgrades the variant — `crates/apollo_protobuf/src/converters/transaction.rs:417-436`**

```rust
impl TryFrom<protobuf::ResourceBounds> for ValidResourceBounds {
    fn try_from(value: protobuf::ResourceBounds) -> Result<Self, Self::Error> {
        ...
        Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
            ValidResourceBounds::L1Gas(l1_gas)          // ← downgrade
        } else {
            ValidResourceBounds::AllResources(...)
        })
    }
}
```

This conversion is used by `TryFrom<protobuf::InvokeV3> for InvokeTransactionV3` (lines 593-660), which is the type used in the P2P sync path (`DataOrFin<FullTransaction>`).

**RPC gateway always hashes as `AllResources`**

`InternalRpcInvokeTransactionV3` stores `resource_bounds: AllResourceBounds` and its `InvokeTransactionV3Trait` impl always wraps it as `ValidResourceBounds::AllResources(...)`:

```rust
// crates/starknet_api/src/rpc_transaction.rs:637-638
fn resource_bounds(&self) -> ValidResourceBounds {
    ValidResourceBounds::AllResources(self.resource_bounds)
}
```

The hash is computed at `crates/apollo_transaction_converter/src/transaction_converter.rs:391`:
```rust
let tx_hash = tx_without_hash.calculate_transaction_hash(&self.chain_id)?;
```

**`InvokeTransactionV3` (sync type) returns the stored variant verbatim**

```rust
// crates/starknet_api/src/transaction_hash.rs:407-410
impl InvokeTransactionV3Trait for InvokeTransactionV3 {
    fn resource_bounds(&self) -> ValidResourceBounds {
        self.resource_bounds          // ← returns L1Gas after protobuf round-trip
    }
```

**End-to-end divergence for a user-controlled input**

1. User submits `RpcInvokeTransactionV3` with `AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }`.
2. Gateway converts → `InternalRpcInvokeTransactionV3`; hash computed as `AllResources` → **H_all** (includes `L1_DATA_GAS_felt = 0` in preimage).
3. Transaction committed with hash **H_all**.
4. P2P sync serializes as `InvokeTransactionV3` with `ValidResourceBounds::AllResources(...)`.
5. Receiving node deserializes: `l1_data_gas.is_zero() && l2_gas.is_zero()` → `ValidResourceBounds::L1Gas(l1_gas)`.
6. Hash recomputed from deserialized object → **H_l1** (omits `L1_DATA_GAS_felt`) ≠ **H_all**.

The `validate_transaction_hash` function used in the sync/storage layer will now fail for this transaction, or the syncing node will store and serve the wrong hash.

### Impact Explanation

A syncing node that receives a valid `InvokeTransactionV3` with zero `l2_gas` and `l1_data_gas` will compute a hash that differs from the committed hash. This causes:

- **Wrong hash served via RPC**: any RPC call that recomputes or re-derives the transaction hash from the stored `InvokeTransactionV3` returns an incorrect value — an authoritative-looking wrong value.
- **Hash validation failure in the sync path**: `validate_transaction_hash` will reject the transaction, preventing the node from syncing blocks that contain such transactions.
- **Consensus divergence**: validators that recompute hashes from the deserialized type will disagree with the proposer's committed hash.

This matches the allowed impact: *"High. Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload."* and *"High. RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value."*

### Likelihood Explanation

Any unprivileged user can trigger this by submitting a standard V3 invoke transaction with `l2_gas = 0` and `l1_data_gas = 0`. The gateway's stateless validator explicitly permits zero resource bounds (test cases `valid_l1_gas`, `valid_l1_data_gas` confirm this). No special permissions, malformed bytes, or privileged access are required. The condition is reachable on every node that syncs blocks containing such transactions.

### Recommendation

1. **Fix the protobuf deserializer**: `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` in `crates/apollo_protobuf/src/converters/transaction.rs` must not silently downgrade `AllResources` to `L1Gas`. Remove the value-based classification and always deserialize as `AllResources` when all three fields are present, regardless of whether their values are zero. The `TODO(Shahak)` comment already acknowledges this debt.

2. **Canonicalize the hash function**: `get_tip_resource_bounds_hash` in `crates/starknet_api/src/transaction_hash.rs` should not branch on the `ValidResourceBounds` variant for transactions that were submitted as `AllResources`. The variant should be determined by the transaction version/protocol, not by the runtime value of the bounds.

3. **Add a round-trip hash invariant test**: add a test that serializes an `InvokeTransactionV3` with `AllResources { l2_gas: 0, l1_data_gas: 0 }` to protobuf, deserializes it, and asserts that the recomputed hash equals the original.

### Proof of Concept

```rust
use starknet_api::transaction::fields::{AllResourceBounds, ResourceBounds, ValidResourceBounds};
use starknet_api::transaction_hash::get_tip_resource_bounds_hash;
use starknet_api::transaction::fields::Tip;
use starknet_api::block::GasPrice;
use starknet_api::execution_resources::GasAmount;

let l1_gas = ResourceBounds {
    max_amount: GasAmount(100),
    max_price_per_unit: GasPrice(1),
};
let tip = Tip(0);

// Hash as AllResources (gateway path)
let all_resources = ValidResourceBounds::AllResources(AllResourceBounds {
    l1_gas,
    l2_gas: ResourceBounds::default(),      // zero
    l1_data_gas: ResourceBounds::default(), // zero
});
let hash_all = get_tip_resource_bounds_hash(&all_resources, &tip).unwrap();

// Hash as L1Gas (protobuf deserialization path, after downgrade)
let l1_gas_only = ValidResourceBounds::L1Gas(l1_gas);
let hash_l1 = get_tip_resource_bounds_hash(&l1_gas_only, &tip).unwrap();

// These are different — same effective resource bounds, different hashes
assert_ne!(hash_all, hash_l1);
// hash_all includes L1_DATA_GAS_felt=0 in the Poseidon input; hash_l1 does not.
```

The divergence is rooted in:
- [1](#0-0) 
- [2](#0-1) 
- [3](#0-2) 
- [4](#0-3) 
- [5](#0-4)

### Citations

**File:** crates/starknet_api/src/transaction_hash.rs (L188-210)
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
```

**File:** crates/starknet_api/src/transaction_hash.rs (L407-410)
```rust
impl InvokeTransactionV3Trait for InvokeTransactionV3 {
    fn resource_bounds(&self) -> ValidResourceBounds {
        self.resource_bounds
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

**File:** crates/starknet_api/src/rpc_transaction.rs (L636-639)
```rust
impl InvokeTransactionV3Trait for InternalRpcInvokeTransactionV3 {
    fn resource_bounds(&self) -> ValidResourceBounds {
        ValidResourceBounds::AllResources(self.resource_bounds)
    }
```
