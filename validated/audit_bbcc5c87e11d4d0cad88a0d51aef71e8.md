### Title
Protobuf `ValidResourceBounds` Deserialization Silently Downgrades `AllResources` to `L1Gas` When L2/Data Gas Bounds Are Zero, Producing a Divergent Transaction Hash - (`crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The protobuf deserializer for `ValidResourceBounds` uses a zero-value heuristic to decide between the `L1Gas` and `AllResources` variants. When a V3 transaction carries `AllResources` bounds with both `l2_gas` and `l1_data_gas` set to zero, the deserialized variant is silently downgraded to `L1Gas`. Because `get_tip_resource_bounds_hash` includes the `L1_DATA_GAS` element only for `AllResources`, the transaction hash computed after the protobuf round-trip differs from the hash computed before it. The proposer and every validator therefore bind different hashes to the same transaction, producing wrong execution state and a broken transaction commitment.

### Finding Description

In `crates/apollo_protobuf/src/converters/transaction.rs` lines 417–436, the `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` implementation decides the variant by inspecting whether the deserialized `l2_gas` and `l1_data_gas` fields are zero:

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
``` [1](#0-0) 

This is structurally identical to the reported "decimals mismatch" pattern: two representations of the same value are assumed to be equivalent (both zero ⟹ same variant) without any explicit type tag in the wire format to confirm the intent.

The `protobuf::ResourceBounds` message carries no discriminator field to distinguish a legacy `L1Gas`-only transaction from a modern `AllResources` transaction whose L2 and data gas bounds happen to be zero:

```proto
message ResourceBounds {
    ResourceLimits l1_gas      = 1;
    optional ResourceLimits l1_data_gas = 2;   // no variant tag
    ResourceLimits l2_gas      = 3;
}
``` [2](#0-1) 

The codebase itself acknowledges the ambiguity in the consensus test helper:

```rust
// If all the fields of `AllResources` are 0 upon serialization,
// then the deserialized value will be interpreted as the `L1Gas` variant.
fn add_gas_values_to_transaction(transactions: &mut [ConsensusTransaction]) {
    ...
    resource_bounds.l2_gas.max_amount = GasAmount(1);
``` [3](#0-2) 

The test works around the bug by injecting a non-zero amount. No equivalent guard exists in the production conversion path.

The hash divergence originates in `get_tip_resource_bounds_hash`:

```rust
resource_felts.extend(match resource_bounds {
    ValidResourceBounds::L1Gas(_) => vec![],                          // 2-element hash
    ValidResourceBounds::AllResources(all_resources) => {
        vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]  // 3-element hash
    }
});
``` [4](#0-3) 

For an `AllResources` transaction with zero L2/data gas, the hash preimage is `Poseidon(tip, L1_GAS_packed, L2_GAS_packed, L1_DATA_packed)`. After the protobuf round-trip produces `L1Gas`, the hash preimage becomes `Poseidon(tip, L1_GAS_packed, L2_GAS_packed)` — a strictly different value even though all numeric gas fields are identical, because `L1_DATA_packed` encodes the 7-byte ASCII resource name `"L1_DATA"` and is non-zero even when the amount and price are zero.

The proposer computes the hash before serialization (correct, `AllResources` path). Every validator recomputes the hash after deserialization (wrong, `L1Gas` path) inside `convert_consensus_tx_to_internal_consensus_tx` → `convert_rpc_tx_to_internal` → `tx_without_hash.calculate_transaction_hash`: [5](#0-4) 

### Impact Explanation

**Wrong state / receipt / event from execution logic (Critical):** The `TRANSACTION_HASH` syscall returns the hash stored in `AccountTransaction.tx_hash`. Validators store the `L1Gas`-derived hash; the proposer stored the `AllResources`-derived hash. Any contract that reads `get_execution_info().transaction_hash` (e.g., for replay protection, event emission keyed on tx hash, or nonce-like logic) receives a different value on every validator node than on the proposer, producing divergent storage writes and events.

**Wrong transaction commitment (Critical):** The transaction commitment Merkle leaf is computed from `InternalConsensusTransaction.tx_hash`. Validators compute a different leaf than the proposer, so the `transaction_commitment` in `CommitmentParts` / `ProposalFin` will not match, breaking block finalization. [6](#0-5) 

### Likelihood Explanation

The gateway's stateful validator rejects `AllResources` transactions whose `l2_gas.max_price_per_unit` is below a threshold derived from the previous block's L2 gas price. Under normal operation this prevents zero-price L2 gas from reaching the consensus path. However:

1. If `min_gas_price_percentage` is configured to `0`, the threshold becomes `0` and any price (including zero) passes.
2. If `validate_resource_bounds` is `false` in the stateless validator config, the check is skipped entirely.
3. Transactions injected directly into the consensus P2P path (e.g., by a proposer node) bypass the gateway entirely and are not re-validated before `convert_consensus_tx_to_internal_consensus_tx` is called. [7](#0-6) 

### Recommendation

Replace the zero-value heuristic with an explicit wire-format discriminator. The cleanest fix is to add a boolean or enum field to `protobuf::ResourceBounds` (e.g., `bool all_resources = 4`) that is set unconditionally during serialization and read during deserialization to select the variant, independent of the numeric field values. Alternatively, the serializer for `ValidResourceBounds::L1Gas` should omit `l1_data_gas` entirely (leave it `None`) so that the existing `unwrap_or_default` path produces a structurally distinct wire message, and the deserializer should key on the presence/absence of `l1_data_gas` rather than its numeric value.

### Proof of Concept

1. Craft a V3 invoke transaction with `AllResourceBounds { l1_gas: {amount: X, price: Y}, l2_gas: {amount: 0, price: 0}, l1_data_gas: {amount: 0, price: 0} }`.
2. Submit it through a gateway with `min_gas_price_percentage = 0` (or `validate_resource_bounds = false`). The gateway accepts it and computes hash H₁ using the `AllResources` path (3-element resource hash).
3. The proposer serializes the transaction to `protobuf::ConsensusTransaction` and broadcasts it.
4. A validator deserializes the protobuf: `l2_gas.is_zero() && l1_data_gas.is_zero()` → variant becomes `ValidResourceBounds::L1Gas`.
5. The validator calls `calculate_transaction_hash` and computes hash H₂ using the `L1Gas` path (2-element resource hash). H₂ ≠ H₁.
6. The validator executes the transaction with hash H₂; any `get_execution_info().transaction_hash` call inside the contract returns H₂ instead of H₁. Storage writes keyed on the transaction hash diverge from the proposer's state. The transaction commitment computed by the validator does not match the proposer's `CommitmentParts`, causing `ProposalFin` verification to fail or the committed block to carry wrong receipts.

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

**File:** crates/apollo_protobuf/src/converters/consensus_test.rs (L26-44)
```rust
// If all the fields of `AllResources` are 0 upon serialization,
// then the deserialized value will be interpreted as the `L1Gas` variant.
fn add_gas_values_to_transaction(transactions: &mut [ConsensusTransaction]) {
    let transaction = &mut transactions[0];
    match transaction {
        ConsensusTransaction::RpcTransaction(rpc_transaction) => match rpc_transaction {
            RpcTransaction::Declare(RpcDeclareTransaction::V3(RpcDeclareTransactionV3 {
                resource_bounds,
                ..
            }))
            | RpcTransaction::Invoke(RpcInvokeTransaction::V3(RpcInvokeTransactionV3 {
                resource_bounds,
                ..
            }))
            | RpcTransaction::DeployAccount(RpcDeployAccountTransaction::V3(
                RpcDeployAccountTransactionV3 { resource_bounds, .. },
            )) => {
                resource_bounds.l2_gas.max_amount = GasAmount(1);
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

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L334-392)
```rust
    async fn convert_rpc_tx_to_internal(
        &self,
        tx: RpcTransaction,
    ) -> TransactionConverterResult<(InternalRpcTransaction, Option<(ProofFacts, Proof)>)> {
        let (tx_without_hash, proof_data) = match tx {
            RpcTransaction::Invoke(RpcInvokeTransaction::V3(tx)) => {
                let proof_data = if tx.proof_facts.is_empty() {
                    None
                } else {
                    Some((tx.proof_facts.clone(), tx.proof.clone()))
                };
                (InternalRpcTransactionWithoutTxHash::Invoke(tx.into()), proof_data)
            }
            RpcTransaction::Declare(RpcDeclareTransaction::V3(tx)) => {
                let ClassHashes { class_hash, executable_class_hash_v2 } =
                // TODO(Dori): Make this async and spawn a task to compile and add it to the class manager.
                    self.class_manager_client.add_class(tx.contract_class).await?;
                // TODO(Aviv): Ensure that we do not want to
                // allow declare with compiled class hash v1.
                if tx.compiled_class_hash != executable_class_hash_v2 {
                    return Err(TransactionConverterError::ValidateCompiledClassHashError(
                        ValidateCompiledClassHashError::CompiledClassHashMismatch {
                            computed_class_hash: executable_class_hash_v2,
                            supplied_class_hash: tx.compiled_class_hash,
                        },
                    ));
                }
                (
                    InternalRpcTransactionWithoutTxHash::Declare(InternalRpcDeclareTransactionV3 {
                        sender_address: tx.sender_address,
                        compiled_class_hash: tx.compiled_class_hash,
                        signature: tx.signature,
                        nonce: tx.nonce,
                        class_hash,
                        resource_bounds: tx.resource_bounds,
                        tip: tx.tip,
                        paymaster_data: tx.paymaster_data,
                        account_deployment_data: tx.account_deployment_data,
                        nonce_data_availability_mode: tx.nonce_data_availability_mode,
                        fee_data_availability_mode: tx.fee_data_availability_mode,
                    }),
                    None,
                )
            }
            RpcTransaction::DeployAccount(RpcDeployAccountTransaction::V3(tx)) => {
                let contract_address = tx.calculate_contract_address()?;
                (
                    InternalRpcTransactionWithoutTxHash::DeployAccount(
                        InternalRpcDeployAccountTransaction {
                            tx: RpcDeployAccountTransaction::V3(tx),
                            contract_address,
                        },
                    ),
                    None,
                )
            }
        };
        let tx_hash = tx_without_hash.calculate_transaction_hash(&self.chain_id)?;
        Ok((InternalRpcTransaction { tx: tx_without_hash, tx_hash }, proof_data))
```

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L260-265)
```rust
            .chain(&block_commitments.concatenated_counts)
            .chain(&block_commitments.state_diff_commitment.0.0)
            .chain(&block_commitments.transaction_commitment.0)
            .chain(&block_commitments.event_commitment.0)
            .chain(&block_commitments.receipt_commitment.0)
            .chain_iter(
```

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L358-390)
```rust
    // TODO(Arni): Consider running this validation for all gas prices.
    fn validate_tx_l2_gas_price_within_threshold(
        &self,
        tx_resource_bounds: ValidResourceBounds,
        previous_block_l2_gas_price: NonzeroGasPrice,
    ) -> StatefulTransactionValidatorResult<()> {
        match tx_resource_bounds {
            ValidResourceBounds::AllResources(tx_resource_bounds) => {
                let tx_l2_gas_price = tx_resource_bounds.l2_gas.max_price_per_unit;
                let gas_price_threshold_multiplier =
                    Ratio::new(self.config.min_gas_price_percentage.into(), 100_u128);
                let threshold = (gas_price_threshold_multiplier
                    * previous_block_l2_gas_price.get().0)
                    .to_integer();
                if tx_l2_gas_price.0 < threshold {
                    return Err(StarknetError {
                        // We didn't have this kind of an error.
                        code: StarknetErrorCode::UnknownErrorCode(
                            "StarknetErrorCode.GAS_PRICE_TOO_LOW".to_string(),
                        ),
                        message: format!(
                            "Transaction L2 gas price {tx_l2_gas_price} is below the required \
                             threshold {threshold}.",
                        ),
                    });
                }
            }
            ValidResourceBounds::L1Gas(_) => {
                // No validation required for legacy transactions.
            }
        }
        Ok(())
    }
```
