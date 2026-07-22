### Title
Hardcoded `Fee(1)` Placeholder in L1Handler Consensus-to-Internal Conversion Produces Wrong `paid_fee_on_l1` in Executable Transaction and Central Serialization — (`crates/apollo_transaction_converter/src/transaction_converter.rs`)

---

### Summary

`convert_consensus_l1_handler_to_internal_l1_handler` unconditionally supplies `Fee(1)` as `paid_fee_on_l1` when converting a consensus-received `L1HandlerTransaction` into an executable one. This is the direct sequencer analog of the Derby H-7 pattern: a conversion path leaves a critical field unimplemented (empty/placeholder), silently producing a wrong value instead of the real one. Every validator node that processes an L1Handler transaction via the consensus proposal path binds the wrong fee to the executable object, which then propagates into the `CentralL1HandlerTransaction` serialization sent to the cende system.

---

### Finding Description

**Root cause — the placeholder:** [1](#0-0) 

```rust
fn convert_consensus_l1_handler_to_internal_l1_handler(
    &self,
    tx: transaction::L1HandlerTransaction,
) -> TransactionConverterResult<executable_transaction::L1HandlerTransaction> {
    Ok(executable_transaction::L1HandlerTransaction::create(
        tx,
        &self.chain_id,
        // TODO(Gilad): Change this once we put real value in paid_fee_on_l1.
        Fee(1),
    )?)
}
```

**Why the real value is unavailable:** The consensus wire type `ConsensusTransaction::L1Handler` carries only `transaction::L1HandlerTransaction`, which has no `paid_fee_on_l1` field. [2](#0-1) 

The protobuf serializer for `L1HandlerV0` likewise omits the fee entirely: [3](#0-2) 

So when a validator node deserializes a consensus proposal containing an L1Handler transaction, it has no wire-level source for the actual fee paid on L1 and the converter fills the gap with the literal constant `1`.

**Where the wrong value propagates — blockifier fee check:** [4](#0-3) 

The blockifier only checks `paid_fee_on_l1 != Fee(0)`. `Fee(1)` always satisfies this, so the check is structurally bypassed on every validator node regardless of what the L1 contract actually recorded.

**Where the wrong value propagates — central serialization:** [5](#0-4) 

`CentralL1HandlerTransaction` copies `tx.paid_fee_on_l1` verbatim. On validator nodes this field is always `Fee(1)` instead of the actual ETH fee paid on L1. The `process_transactions` function feeds `InternalConsensusTransaction` objects (which carry the `Fee(1)` value) directly into this serialization path: [6](#0-5) 

**Asymmetry between proposer and validator:**

The proposer node obtains the real `paid_fee_on_l1` from the L1 events provider and stores it in `executable_transaction::L1HandlerTransaction`. Validator nodes receive the same transaction via consensus but reconstruct it with `Fee(1)`. The two nodes therefore hold divergent executable objects for the same transaction.

---

### Impact Explanation

1. **Wrong value in authoritative serialized output.** Every `CentralL1HandlerTransaction` written by a validator node carries `paid_fee_on_l1 = 1` instead of the real L1 fee. Any downstream consumer of the cende data (fee accounting, proof verification, re-execution) that relies on this field receives a fabricated value.

2. **Divergent executable payload between proposer and validator.** The proposer's `L1HandlerTransaction` has the correct fee; every validator's copy has `Fee(1)`. This is a broken conversion invariant: the same logical transaction is bound to two different executable objects depending on which node processes it.

3. **Fee-check bypass on validators.** The blockifier's `paid_fee == Fee(0)` guard is the only in-process enforcement that L1 fee was paid. Because validators always supply `Fee(1)`, this guard can never fire on a validator, even if the actual on-chain fee was zero (e.g., due to an L1-side edge case or future protocol change). The proposer would reject such a transaction; validators would accept it, creating a consensus split.

---

### Likelihood Explanation

Every L1Handler transaction that travels through the consensus validation path (i.e., is received by a non-proposer node) triggers this code path. The trigger is unprivileged in the sense that L1Handler transactions originate from L1 events, not from user-submitted RPC calls, but any block containing an L1Handler transaction exercises the bug on all validator nodes. The TODO comment confirms this is a known, unresolved placeholder, not a temporary test stub.

---

### Recommendation

1. **Carry `paid_fee_on_l1` through the consensus wire format.** Add the field to `protobuf::L1HandlerV0` (and the corresponding `.proto` definition) so that the proposer can transmit the real fee and validators can reconstruct the correct executable object.

2. **Validate the reconstructed fee on validators.** After deserializing, validators should confirm that the received `paid_fee_on_l1` is consistent with the L1 event record they hold locally (if available) before accepting the transaction into the block.

3. **Remove the `Fee(1)` placeholder only after the wire format is updated.** Until the field is transmitted, the placeholder silently corrupts the central serialization on every validator.

---

### Proof of Concept

1. A proposer node scrapes an L1 event with `paid_fee_on_l1 = Fee(5_000_000_000)` and builds a block proposal containing the corresponding `L1HandlerTransaction`.
2. The proposer serializes the transaction via `From<ConsensusTransaction> for protobuf::ConsensusTransaction` → `From<L1HandlerTransaction> for protobuf::L1HandlerV0`. The `paid_fee_on_l1` field is **not emitted**.
3. A validator node deserializes the protobuf, reconstructs `ConsensusTransaction::L1Handler(transaction::L1HandlerTransaction { … })` with no fee field.
4. The validator calls `convert_consensus_tx_to_internal_consensus_tx` → `convert_consensus_l1_handler_to_internal_l1_handler`, which calls `L1HandlerTransaction::create(tx, &chain_id, Fee(1))`.
5. The validator's `InternalConsensusTransaction::L1Handler` now holds `paid_fee_on_l1 = Fee(1)`.
6. When `process_transactions` serializes this for the cende system, `CentralL1HandlerTransaction { paid_fee_on_l1: Fee(1), … }` is written — a factor of 5 billion off from the real value.
7. The blockifier fee check `if paid_fee == Fee(0)` passes silently on the validator, even though the real fee was never validated.

### Citations

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L473-483)
```rust
    fn convert_consensus_l1_handler_to_internal_l1_handler(
        &self,
        tx: transaction::L1HandlerTransaction,
    ) -> TransactionConverterResult<executable_transaction::L1HandlerTransaction> {
        Ok(executable_transaction::L1HandlerTransaction::create(
            tx,
            &self.chain_id,
            // TODO(Gilad): Change this once we put real value in paid_fee_on_l1.
            Fee(1),
        )?)
    }
```

**File:** crates/starknet_api/src/consensus_transaction.rs (L9-18)
```rust
pub enum ConsensusTransaction {
    RpcTransaction(RpcTransaction),
    L1Handler(transaction::L1HandlerTransaction),
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize, Hash)]
pub enum InternalConsensusTransaction {
    RpcTransaction(InternalRpcTransaction),
    L1Handler(executable_transaction::L1HandlerTransaction),
}
```

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L987-995)
```rust
impl From<L1HandlerTransaction> for protobuf::L1HandlerV0 {
    fn from(value: L1HandlerTransaction) -> Self {
        Self {
            nonce: Some(value.nonce.0.into()),
            address: Some(value.contract_address.into()),
            entry_point_selector: Some(value.entry_point_selector.0.into()),
            calldata: value.calldata.0.iter().map(|calldata| (*calldata).into()).collect(),
        }
    }
```

**File:** crates/blockifier/src/transaction/l1_handler_transaction.rs (L101-113)
```rust
                        // TODO(Arni): Consider removing this check. It is covered by the starknet
                        // core contract.
                        let paid_fee = self.paid_fee_on_l1;
                        // For now, assert only that any amount of fee was paid.
                        // The error message still indicates the required fee.
                        if paid_fee == Fee(0) {
                            return Err(TransactionExecutionError::TransactionFeeError(Box::new(
                                TransactionFeeError::InsufficientFee {
                                    paid_fee,
                                    actual_fee: receipt.fee,
                                },
                            )));
                        }
```

**File:** crates/apollo_consensus_orchestrator/src/cende/central_objects.rs (L383-394)
```rust
impl From<L1HandlerTransaction> for CentralL1HandlerTransaction {
    fn from(tx: L1HandlerTransaction) -> CentralL1HandlerTransaction {
        CentralL1HandlerTransaction {
            hash_value: tx.tx_hash,
            contract_address: tx.tx.contract_address,
            entry_point_selector: tx.tx.entry_point_selector,
            calldata: tx.tx.calldata,
            nonce: tx.tx.nonce,
            paid_fee_on_l1: tx.paid_fee_on_l1,
        }
    }
}
```

**File:** crates/apollo_consensus_orchestrator/src/cende/central_objects.rs (L527-554)
```rust
pub(crate) async fn process_transactions(
    class_manager: SharedClassManagerClient,
    txs: Vec<InternalConsensusTransaction>,
    timestamp: u64,
) -> CendeAmbassadorResult<(
    Vec<CentralTransactionWritten>,
    Vec<CentralSierraContractClassEntry>,
    Vec<CentralCasmContractClassEntry>,
)> {
    let mut contract_classes = Vec::new();
    let mut compiled_classes = Vec::new();
    let mut central_transactions = Vec::new();
    for tx in txs {
        if let Some((contract_class, compiled_class)) =
            get_contract_classes_if_declare(class_manager.clone(), &tx).await?
        {
            central_transactions.push(CentralTransactionWritten::try_from((
                tx,
                Some(&contract_class.1.contract_class),
                timestamp,
            ))?);
            contract_classes.push(contract_class);
            compiled_classes.push(compiled_class);
        } else {
            central_transactions.push(CentralTransactionWritten::try_from((tx, None, timestamp))?);
        }
    }
    Ok((central_transactions, contract_classes, compiled_classes))
```
