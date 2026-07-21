### Title
Protobuf `ValidResourceBounds` Deserializer Conflates `AllResources` with `L1Gas`, Producing a Different Transaction Hash for the Same Bytes — (File: `crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The protobuf converter for `ValidResourceBounds` silently downgrades an `AllResources` transaction to `L1Gas` whenever `l2_gas` and `l1_data_gas` are both zero. Because `get_tip_resource_bounds_hash` hashes a different number of resource felts for `L1Gas` vs `AllResources`, the transaction hash computed on the P2P/state-sync path diverges from the hash computed on the RPC/gateway path for the same transaction. A syncing node that recomputes the hash from the deserialized transaction will obtain a value that does not match the canonical hash stored in the block, causing hash validation to fail and the block to be rejected.

### Finding Description

**Step 1 – RPC path always uses `AllResources`.**

`RpcInvokeTransactionV3` stores `resource_bounds: AllResourceBounds` (never `ValidResourceBounds`). [1](#0-0) 

When `convert_rpc_tx_to_internal` converts an incoming RPC transaction it calls `tx_without_hash.calculate_transaction_hash(&self.chain_id)`, which dispatches to `get_invoke_transaction_v3_hash` via `InvokeTransactionV3Trait for InternalRpcInvokeTransactionV3`, where `resource_bounds()` always returns `ValidResourceBounds::AllResources(self.resource_bounds)`. [2](#0-1) [3](#0-2) 

**Step 2 – `get_tip_resource_bounds_hash` hashes a different number of felts for `L1Gas` vs `AllResources`.**

For `AllResources` the hash preimage contains three resource felts: `L1_GAS`, `L2_GAS`, and `L1_DATA_GAS`. For `L1Gas` it contains only two (`L1_GAS`, `L2_GAS`). Even when `l2_gas = 0` and `l1_data_gas = 0`, the Poseidon output differs because the input length differs. [4](#0-3) 

**Step 3 – The protobuf converter silently downgrades `AllResources` to `L1Gas`.**

`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` treats an absent or zero `l1_data_gas` field combined with a zero `l2_gas` field as `ValidResourceBounds::L1Gas`:

```rust
// TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2.
let l1_data_gas = value.l1_data_gas.unwrap_or_default();
...
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
``` [5](#0-4) 

This converter is invoked directly when deserializing a V3 invoke transaction from a P2P sync message: [6](#0-5) 

**Step 4 – The consensus path also passes through this converter.**

`TryFrom<protobuf::ConsensusTransaction> for ConsensusTransaction` converts `InvokeV3` protobuf messages using the same `TryFrom<protobuf::InvokeV3> for InvokeTransactionV3` path, which calls the broken `ValidResourceBounds` converter. [7](#0-6) 

**Step 5 – `TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3` rejects `L1Gas`.**

If the consensus path converts `InvokeTransactionV3 { resource_bounds: L1Gas(_) }` to `RpcInvokeTransactionV3`, the conversion returns `Err(StarknetApiError::OutOfRange)`, causing the consensus node to reject a transaction that the gateway already accepted. [8](#0-7) 

### Impact Explanation

Two concrete impacts:

1. **Hash divergence / wrong state (Critical):** A syncing node that recomputes the transaction hash from the protobuf-deserialized `InvokeTransactionV3` (with `L1Gas`) obtains a hash that does not match the canonical hash (computed with `AllResources`). `validate_transaction_hash` returns `false`, causing the node to reject a valid block or store an incorrect state. [9](#0-8) 

2. **Consensus admission rejects valid transactions (High):** The consensus orchestrator calls `convert_consensus_tx_to_internal_consensus_tx`, which internally calls `convert_rpc_tx_to_internal`. If the intermediate `InvokeTransactionV3` carries `L1Gas`, the subsequent `TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3` conversion fails, causing the consensus node to drop a transaction that was already accepted and sequenced by the gateway. [10](#0-9) 

### Likelihood Explanation

Any V3 invoke transaction submitted via the RPC gateway with `AllResourceBounds { l1_gas: X, l2_gas: 0, l1_data_gas: 0 }` triggers this path. The gateway's stateless validator explicitly accepts such transactions (test cases `valid_l1_gas` with `l2_gas=0` and `l1_data_gas=0` are marked valid). [11](#0-10) 

The `l1_data_gas` field is intentionally optional in the protobuf schema to maintain backward compatibility with protocol version 0.13.2, so any peer running an older node will omit it, triggering the downgrade. [12](#0-11) 

### Recommendation

- **Short term:** In `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`, always produce `AllResources` when `l1_data_gas` is absent but `l2_gas` is present (even if zero), because the RPC path never produces `L1Gas` for V3 transactions. The `L1Gas` variant should only be used when deserializing pre-0.13.3 transactions that were originally signed as `L1Gas`.
- **Long term:** Add a discriminator field to the protobuf `ResourceBounds` message (or use a `oneof`) so that the `L1Gas` vs `AllResources` distinction is explicit and cannot be inferred from zero values. Add a round-trip hash test that verifies `hash(RPC-submitted tx) == hash(protobuf-deserialized tx)` for the `l2_gas=0, l1_data_gas=0` case.

### Proof of Concept

```
1. Craft a V3 invoke transaction with:
     resource_bounds = AllResourceBounds { l1_gas: {max_amount: 1000, max_price: 1}, l2_gas: {0,0}, l1_data_gas: {0,0} }
   Submit via RPC gateway. Gateway accepts it and computes hash H_all using AllResources
   (3-felt resource preimage: L1_GAS_concat || L2_GAS_concat || L1_DATA_GAS_concat).

2. The transaction is included in block B with hash H_all.

3. A syncing peer receives block B via P2P. The protobuf InvokeV3 message omits l1_data_gas
   (or sets it to None). The converter fires:
     l1_data_gas = None.unwrap_or_default() = zero
     l2_gas = 0
     → ValidResourceBounds::L1Gas(l1_gas)

4. The syncing node recomputes the hash using L1Gas
   (2-felt resource preimage: L1_GAS_concat || L2_GAS_concat).
   This produces H_l1 ≠ H_all.

5. validate_transaction_hash(tx, block_number, chain_id, H_all, options)
   computes possible_hashes = [H_l1] (plus deprecated variants, none of which equal H_all).
   Returns false → block rejected / wrong state stored.
``` [5](#0-4) [4](#0-3) [9](#0-8)

### Citations

**File:** crates/starknet_api/src/rpc_transaction.rs (L549-566)
```rust
/// An invoke account transaction that can be added to Starknet through the RPC.
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

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L184-202)
```rust
    async fn convert_consensus_tx_to_internal_consensus_tx(
        &self,
        tx: ConsensusTransaction,
    ) -> TransactionConverterResult<(InternalConsensusTransaction, Option<VerifyAndStoreProofTask>)>
    {
        match tx {
            ConsensusTransaction::RpcTransaction(tx) => {
                let (internal_tx, proof_data) = self.convert_rpc_tx_to_internal(tx).await?;
                let task = proof_data.map(|(proof_facts, proof)| {
                    self.spawn_verify_and_store_proof(proof_facts, proof)
                });
                Ok((InternalConsensusTransaction::RpcTransaction(internal_tx), task))
            }
            ConsensusTransaction::L1Handler(tx) => {
                let internal_tx = self.convert_consensus_l1_handler_to_internal_l1_handler(tx)?;
                Ok((InternalConsensusTransaction::L1Handler(internal_tx), None))
            }
        }
    }
```

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L388-392)
```rust
                )
            }
        };
        let tx_hash = tx_without_hash.calculate_transaction_hash(&self.chain_id)?;
        Ok((InternalRpcTransaction { tx: tx_without_hash, tx_hash }, proof_data))
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

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L593-600)
```rust
impl TryFrom<protobuf::InvokeV3> for InvokeTransactionV3 {
    type Error = ProtobufConversionError;
    fn try_from(value: protobuf::InvokeV3) -> Result<Self, Self::Error> {
        let resource_bounds = ValidResourceBounds::try_from(
            value.resource_bounds.ok_or(missing("InvokeV3::resource_bounds"))?,
        )?;

        let tip = Tip(value.tip);
```

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L1027-1053)
```rust
impl TryFrom<protobuf::ConsensusTransaction> for ConsensusTransaction {
    type Error = ProtobufConversionError;
    fn try_from(value: protobuf::ConsensusTransaction) -> Result<Self, Self::Error> {
        let txn = value.txn.ok_or(missing("ConsensusTransaction::txn"))?;
        let txn = match txn {
            protobuf::consensus_transaction::Txn::DeclareV3(txn) => {
                ConsensusTransaction::RpcTransaction(RpcTransaction::Declare(
                    RpcDeclareTransaction::V3(txn.try_into()?),
                ))
            }
            protobuf::consensus_transaction::Txn::DeployAccountV3(txn) => {
                ConsensusTransaction::RpcTransaction(RpcTransaction::DeployAccount(
                    RpcDeployAccountTransaction::V3(txn.try_into()?),
                ))
            }
            protobuf::consensus_transaction::Txn::InvokeV3(txn) => {
                ConsensusTransaction::RpcTransaction(RpcTransaction::Invoke(
                    RpcInvokeTransaction::V3(txn.try_into()?),
                ))
            }
            protobuf::consensus_transaction::Txn::L1Handler(txn) => {
                ConsensusTransaction::L1Handler(txn.try_into()?)
            }
        };
        Ok(txn)
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
