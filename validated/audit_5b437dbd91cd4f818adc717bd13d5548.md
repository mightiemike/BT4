### Title
Protobuf `ValidResourceBounds` Deserializer Silently Narrows `AllResources` to `L1Gas`, Breaking Consensus Transaction Deserialization — (`crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The protobuf converter `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` (used on the consensus wire path) silently reclassifies an `AllResources` transaction as `L1Gas` whenever both `l2_gas` and `l1_data_gas` are zero. A transaction carrying `AllResources({l1_gas: non-zero, l2_gas: zero, l1_data_gas: zero})` can pass gateway stateless validation, be admitted to the mempool, and be included in a block proposal. When the proposer serializes that block for consensus peers, every validator that deserializes the `ConsensusTransaction` hits the `DEPRECATED_RESOURCE_BOUNDS_ERROR` path and rejects the block, causing a consensus liveness failure.

### Finding Description

**Two divergent protobuf converters for the same wire type**

The mempool P2P path uses a dedicated converter that always produces `AllResourceBounds`:

```rust
// crates/apollo_protobuf/src/converters/rpc_transaction.rs  lines 212-223
impl TryFrom<protobuf::ResourceBounds> for AllResourceBounds {
    fn try_from(value: protobuf::ResourceBounds) -> Result<Self, Self::Error> {
        Ok(Self {
            l1_gas:      value.l1_gas.ok_or(...)?.try_into()?,
            l2_gas:      value.l2_gas.ok_or(...)?.try_into()?,
            l1_data_gas: value.l1_data_gas.ok_or(...)?.try_into()?,
        })
    }
}
``` [1](#0-0) 

The consensus path uses a different converter that narrows to `L1Gas` when both secondary bounds are zero:

```rust
// crates/apollo_protobuf/src/converters/transaction.rs  lines 417-436
impl TryFrom<protobuf::ResourceBounds> for ValidResourceBounds {
    fn try_from(value: protobuf::ResourceBounds) -> Result<Self, Self::Error> {
        ...
        Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
            ValidResourceBounds::L1Gas(l1_gas)          // ← type narrowed
        } else {
            ValidResourceBounds::AllResources(...)
        })
    }
}
``` [2](#0-1) 

**How the narrowed type propagates to a hard error**

The consensus `InvokeV3` deserialization chain is:

```
protobuf::InvokeV3WithProof
  → InvokeTransactionV3  (uses ValidResourceBounds converter → may produce L1Gas)
  → RpcInvokeTransactionV3::try_from(InvokeTransactionV3)
``` [3](#0-2) 

The final step explicitly rejects anything that is not `AllResources`:

```rust
// crates/starknet_api/src/rpc_transaction.rs  lines 586-611
impl TryFrom<InvokeTransactionV3> for RpcInvokeTransactionV3 {
    fn try_from(value: InvokeTransactionV3) -> Result<Self, Self::Error> {
        Ok(Self {
            resource_bounds: match value.resource_bounds {
                ValidResourceBounds::AllResources(bounds) => bounds,
                _ => return Err(StarknetApiError::OutOfRange {
                    string: "resource_bounds".to_string(),
                }),
            },
            ...
        })
    }
}
``` [4](#0-3) 

The same pattern applies to `RpcDeclareTransactionV3` and `RpcDeployAccountTransactionV3`, both of which check for `AllResources` and return `DEPRECATED_RESOURCE_BOUNDS_ERROR` otherwise. [5](#0-4) 

**Gateway does not block the triggering input**

The stateless validator checks:

1. `max_possible_fee(Tip::ZERO) > 0` — satisfied by non-zero `l1_gas` alone.
2. `l2_gas.max_price_per_unit >= min_gas_price` — satisfied when `min_gas_price = 0` (zero price passes) or when `l2_gas.max_price_per_unit > 0` but `l2_gas.max_amount = 0` (non-zero price, zero amount; `is_zero()` may still return `true` if it checks only `max_amount`).
3. No check exists on `l1_data_gas.max_price_per_unit` at all. [6](#0-5) 

A transaction with `AllResources({l1_gas: non-zero, l2_gas: {amount:0, price:0}, l1_data_gas: {amount:0, price:0}})` therefore passes gateway admission when `min_gas_price = 0`.

**Execution path that reaches the bug**

```
User → Gateway (accepts) → Mempool → Batcher (includes in block)
  → TransactionConverter::convert_internal_consensus_tx_to_consensus_tx
  → ConsensusTransaction serialized to protobuf::ConsensusTransaction
  → Validator receives protobuf → TryFrom<protobuf::ConsensusTransaction>
  → TryFrom<protobuf::InvokeV3WithProof> for RpcInvokeTransactionV3
  → ValidResourceBounds::try_from → L1Gas  ← wrong variant
  → RpcInvokeTransactionV3::try_from → Err(DEPRECATED_RESOURCE_BOUNDS_ERROR)
  → Validator rejects block
``` [7](#0-6) [8](#0-7) 

### Impact Explanation

Every validator that receives the consensus proposal containing such a transaction will fail to deserialize it and reject the block. The proposer's block is never committed. This is a **High** impact: transaction conversion logic binds the wrong type to the executable payload, causing authoritative-looking wrong behavior (block rejection) at the RPC/consensus execution layer.

### Likelihood Explanation

Exploitability requires `min_gas_price = 0` in the gateway config (or `is_zero()` checking only `max_amount`, allowing a non-zero price with zero amount to slip through). This is a configuration-dependent condition. In a default production deployment with `min_gas_price > 0`, the gateway rejects the triggering transaction before it reaches the mempool. However, the root cause — two divergent converters for the same wire type with no invariant enforcing their equivalence — is unconditionally present in the code.

### Recommendation

1. **Unify the converters**: Remove `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` from the consensus path and replace it with `TryFrom<protobuf::ResourceBounds> for AllResourceBounds` (already correct in `rpc_transaction.rs`), then wrap the result in `ValidResourceBounds::AllResources`. The `L1Gas` narrowing logic belongs only in the historical sync/re-execution path, not in the live consensus path.

2. **Add a gateway invariant**: Explicitly reject any `AllResources` transaction where both `l2_gas` and `l1_data_gas` are zero (amount and price), regardless of `min_gas_price`, since such a transaction cannot survive the consensus deserialization round-trip.

### Proof of Concept

```
1. Craft RpcInvokeTransactionV3 with:
     resource_bounds = AllResourceBounds {
       l1_gas:      { max_amount: 1000, max_price_per_unit: 1 },
       l2_gas:      { max_amount: 0,    max_price_per_unit: 0 },
       l1_data_gas: { max_amount: 0,    max_price_per_unit: 0 },
     }
   (valid signature, nonce, sender_address)

2. Submit to gateway with min_gas_price = 0.
   → max_possible_fee = 1000 > 0  ✓
   → l2_gas.max_price_per_unit (0) >= min_gas_price (0)  ✓
   → Transaction accepted, enters mempool.

3. Batcher includes transaction in block proposal.
   TransactionConverter converts InternalRpcTransaction → ConsensusTransaction
   → serialized to protobuf::ConsensusTransaction::InvokeV3.

4. Validator receives protobuf:
   TryFrom<protobuf::InvokeV3WithProof> for RpcInvokeTransactionV3
     → InvokeTransactionV3::try_from(protobuf::InvokeV3)
       → ValidResourceBounds::try_from(protobuf::ResourceBounds)
         l2_gas.is_zero() = true, l1_data_gas.is_zero() = true
         → returns ValidResourceBounds::L1Gas(l1_gas)   ← WRONG
     → RpcInvokeTransactionV3::try_from(InvokeTransactionV3)
         match value.resource_bounds { AllResources => ..., _ => Err(...) }
         → Err(DEPRECATED_RESOURCE_BOUNDS_ERROR)

5. Validator rejects block. Consensus stalls.
```

### Citations

**File:** crates/apollo_protobuf/src/converters/rpc_transaction.rs (L115-131)
```rust
impl TryFrom<protobuf::InvokeV3WithProof> for RpcInvokeTransactionV3 {
    type Error = ProtobufConversionError;
    fn try_from(mut value: protobuf::InvokeV3WithProof) -> Result<Self, Self::Error> {
        // Extract proof first, since `starknet_api::transaction::InvokeTransactionV3` does not
        // carry a `proof` field.
        let proof = Proof::from(std::mem::take(&mut value.proof));

        let snapi_invoke: InvokeTransactionV3 = value
            .invoke
            .ok_or(ProtobufConversionError::MissingField {
                field_description: "InvokeV3WithProof::invoke",
            })?
            .try_into()?;

        // This conversion can fail only if the resource_bounds are not AllResources.
        Ok(Self { proof, ..snapi_invoke.try_into().map_err(|_| DEPRECATED_RESOURCE_BOUNDS_ERROR)? })
    }
```

**File:** crates/apollo_protobuf/src/converters/rpc_transaction.rs (L169-191)
```rust
impl TryFrom<protobuf::DeclareV3WithClass> for RpcDeclareTransactionV3 {
    type Error = ProtobufConversionError;
    fn try_from(value: protobuf::DeclareV3WithClass) -> Result<Self, Self::Error> {
        let (common, class) = value.try_into()?;
        Ok(Self {
            resource_bounds: match common.resource_bounds {
                ValidResourceBounds::AllResources(resource_bounds) => resource_bounds,
                _ => {
                    return Err(DEPRECATED_RESOURCE_BOUNDS_ERROR);
                }
            },
            sender_address: common.sender_address,
            signature: common.signature,
            nonce: common.nonce,
            compiled_class_hash: common.compiled_class_hash,
            contract_class: class,
            tip: common.tip,
            paymaster_data: common.paymaster_data,
            account_deployment_data: common.account_deployment_data,
            nonce_data_availability_mode: common.nonce_data_availability_mode,
            fee_data_availability_mode: common.fee_data_availability_mode,
        })
    }
```

**File:** crates/apollo_protobuf/src/converters/rpc_transaction.rs (L212-223)
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

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L169-202)
```rust
    async fn convert_internal_consensus_tx_to_consensus_tx(
        &self,
        tx: InternalConsensusTransaction,
    ) -> TransactionConverterResult<ConsensusTransaction> {
        match tx {
            InternalConsensusTransaction::RpcTransaction(tx) => self
                .convert_internal_rpc_tx_to_rpc_tx(tx)
                .await
                .map(ConsensusTransaction::RpcTransaction),
            InternalConsensusTransaction::L1Handler(tx) => {
                Ok(ConsensusTransaction::L1Handler(tx.tx))
            }
        }
    }

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
