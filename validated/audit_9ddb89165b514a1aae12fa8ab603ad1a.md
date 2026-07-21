### Title
`ValidResourceBounds::AllResources` with Zero L2/L1DataGas Silently Collapses to `L1Gas` Across Protobuf Round-Trip, Producing a Different Transaction Hash — (`File: crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The protobuf deserializer for `ValidResourceBounds` uses a value-based heuristic to reconstruct the enum variant. When an `AllResources` transaction carries `l2_gas = 0` and `l1_data_gas = 0`, the round-trip silently produces `L1Gas`. Because `get_tip_resource_bounds_hash` hashes a different number of resource fields depending on the variant (2 for `L1Gas`, 3 for `AllResources`), the transaction hash computed after deserialization diverges from the hash computed at ingestion. This is the direct sequencer analog of the BalancerMetaPoolStrategy bug: a function iterates over a *subset* of the canonical resource set (omitting `L1_DATA_GAS`) when the variant is `L1Gas`, while the original signer committed to the *full* set under `AllResources`.

### Finding Description

**Serialization side** — `From<ValidResourceBounds> for protobuf::ResourceBounds` encodes `L1Gas(l1_gas)` by setting `l2_gas` and `l1_data_gas` to `ResourceBounds::default()` (all-zero): [1](#0-0) 

**Deserialization side** — `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` reconstructs the variant purely from whether the wire values are zero: [2](#0-1) 

The condition `l1_data_gas.is_zero() && l2_gas.is_zero()` is true for *any* `AllResources` transaction whose user-supplied `l2_gas` and `l1_data_gas` bounds are both zero — a perfectly legal submission via the RPC, which always accepts `AllResourceBounds`: [3](#0-2) 

**Hash divergence** — `get_tip_resource_bounds_hash` branches on the variant. For `L1Gas` it hashes only two resource felts; for `AllResources` it appends a third (`L1_DATA_GAS`): [4](#0-3) 

A user submits `AllResources{l1_gas=X, l2_gas=0, l1_data_gas=0}` via RPC. The gateway calls `calculate_transaction_hash` on the `InternalRpcTransactionWithoutTxHash`, which uses `AllResources` and produces hash **H_all** (3-resource preimage): [5](#0-4) 

The transaction is propagated over P2P as protobuf. The receiving node deserializes it: `l1_data_gas.is_zero() && l2_gas.is_zero()` → `L1Gas`. It then recomputes the hash using `L1Gas` and produces hash **H_l1** (2-resource preimage). **H_all ≠ H_l1**.

`validate_transaction_hash` will then reject the transaction as having an invalid hash: [6](#0-5) 

### Impact Explanation

Any node that receives the transaction via P2P sync recomputes a hash that does not match the sequencer-assigned hash. The transaction is either rejected outright (valid transaction dropped before sequencing — **High**) or, if the receiving node trusts the wire hash and stores the mismatched deserialized body, the stored transaction hash diverges from the canonical hash committed to by the signer — **Critical** wrong state/receipt.

### Likelihood Explanation

The trigger requires only that a user submit a V3 `AllResources` transaction with `l2_gas = 0` and `l1_data_gas = 0`. This is a valid, unprivileged RPC submission. No special permissions or malformed bytes are needed. The condition is reachable whenever a user sets both optional gas bounds to zero (e.g., to avoid paying L2/data-gas fees on a network where those prices are zero or the user is willing to risk reverting).

### Recommendation

Remove the value-based heuristic. Preserve the variant explicitly in the wire format — either via a dedicated protobuf enum field (`L1GAS_ONLY` vs `ALL_RESOURCES`) or by always deserializing to `AllResources` and letting the hash function handle the canonical encoding. The `TODO(Shahak)` comment at line 426 already acknowledges that the `l1_data_gas` fallback is a temporary compatibility hack; the variant ambiguity it introduces must be resolved before the hash boundary is crossed. [7](#0-6) 

### Proof of Concept

1. Construct `ValidResourceBounds::AllResources(AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 })`.
2. Compute `H_all = get_tip_resource_bounds_hash(&AllResources{...}, &tip)` — result includes the `L1_DATA_GAS` concat-felt.
3. Serialize to `protobuf::ResourceBounds` via `From<ValidResourceBounds>` — wire bytes have `l2_gas = {0,0}`, `l1_data_gas = {0,0}`.
4. Deserialize via `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` — condition `l1_data_gas.is_zero() && l2_gas.is_zero()` is true → returns `L1Gas(X)`.
5. Compute `H_l1 = get_tip_resource_bounds_hash(&L1Gas(X), &tip)` — result omits the `L1_DATA_GAS` concat-felt.
6. Assert `H_all != H_l1` — the two hashes differ, confirming the transaction hash bound to the signer's intent is not preserved across the P2P serialization boundary.

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

**File:** crates/starknet_api/src/rpc_transaction.rs (L124-140)
```rust
    pub fn calculate_transaction_hash(
        &self,
        chain_id: &ChainId,
    ) -> Result<TransactionHash, StarknetApiError> {
        let transaction_version = &self.version();
        match self {
            InternalRpcTransactionWithoutTxHash::Declare(tx) => {
                tx.calculate_transaction_hash(chain_id, transaction_version)
            }
            InternalRpcTransactionWithoutTxHash::Invoke(tx) => {
                tx.calculate_transaction_hash(chain_id, transaction_version)
            }
            InternalRpcTransactionWithoutTxHash::DeployAccount(tx) => {
                tx.calculate_transaction_hash(chain_id, transaction_version)
            }
        }
    }
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L551-566)
```rust
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
