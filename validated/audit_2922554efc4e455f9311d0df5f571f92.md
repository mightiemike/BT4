Looking at the key conversion boundary in the sequencer for L1 handler transactions, specifically how `paid_fee_on_l1` is handled across the consensus wire format.### Title
Hardcoded `paid_fee_on_l1 = Fee(1)` in Consensus-to-Internal L1Handler Conversion Bypasses Fee Adequacy Check — (`File: crates/apollo_transaction_converter/src/transaction_converter.rs`)

---

### Summary

The `paid_fee_on_l1` field is structurally absent from the consensus protobuf wire format (`L1HandlerV0`) and is hardcoded to `Fee(1)` during the `ConsensusTransaction` → `InternalConsensusTransaction` conversion. The blockifier's fee check for L1 handler transactions only verifies `paid_fee != Fee(0)`, so `Fee(1)` always passes regardless of the actual fee paid on L1 or the actual execution cost. This is the sequencer-native analog of the external report's missing `msg.value == amount` check: the actual value committed on L1 is never compared against the cost incurred on L2.

---

### Finding Description

**Conversion boundary — `paid_fee_on_l1` is stripped at the protobuf layer and replaced with a constant.**

`ConsensusTransaction::L1Handler` wraps `transaction::L1HandlerTransaction`, which does **not** carry `paid_fee_on_l1`:

```rust
// crates/starknet_api/src/consensus_transaction.rs
pub enum ConsensusTransaction {
    RpcTransaction(RpcTransaction),
    L1Handler(transaction::L1HandlerTransaction),   // no paid_fee_on_l1
}
``` [1](#0-0) 

The protobuf `L1HandlerV0` message (the consensus wire format) also has no `paid_fee_on_l1` field — it carries only `nonce`, `address`, `entry_point_selector`, and `calldata`:

```proto
// crates/apollo_protobuf/src/proto/p2p/proto/consensus/consensus.proto
message ConsensusTransaction {
    oneof txn {
        L1HandlerV0 l1_handler = 4;   // no paid_fee_on_l1
    }
}
``` [2](#0-1) 

The Rust deserializer for `L1HandlerV0` reconstructs only those four fields and produces a `transaction::L1HandlerTransaction` with no fee information: [3](#0-2) 

When `TransactionConverter::convert_consensus_tx_to_internal_consensus_tx` processes a `ConsensusTransaction::L1Handler`, it calls `convert_consensus_l1_handler_to_internal_l1_handler`, which **hardcodes** `Fee(1)`:

```rust
// crates/apollo_transaction_converter/src/transaction_converter.rs
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
``` [4](#0-3) 

**Fee check — only `!= 0` is tested, never `>= actual_fee`.**

The blockifier's `execute_raw` for `L1HandlerTransaction` performs the following post-execution fee check:

```rust
let paid_fee = self.paid_fee_on_l1;
// For now, assert only that any amount of fee was paid.
// The error message still indicates the required fee.
if paid_fee == Fee(0) {
    return Err(TransactionExecutionError::TransactionFeeError(...));
}
``` [5](#0-4) 

Because `Fee(1) != Fee(0)`, the check unconditionally passes for every L1 handler transaction that arrives through the consensus path. The actual execution cost (`receipt.fee`) is computed and stored in the receipt, but it is **never compared** against `paid_fee_on_l1`. The comment on the check itself acknowledges the weakness: *"For now, assert only that any amount of fee was paid."*

**Contrast with the proposer path.**

On the proposer side, the L1 provider supplies the real `paid_fee_on_l1` scraped from the L1 event. The `CentralL1HandlerTransaction` serializer correctly propagates it:

```rust
impl From<L1HandlerTransaction> for CentralL1HandlerTransaction {
    fn from(tx: L1HandlerTransaction) -> CentralL1HandlerTransaction {
        CentralL1HandlerTransaction {
            paid_fee_on_l1: tx.paid_fee_on_l1,   // real value
            ...
        }
    }
}
``` [6](#0-5) 

Validator nodes that receive the same transaction through the consensus protobuf path get `paid_fee_on_l1 = Fee(1)` instead. The two representations of the same transaction diverge on this field across the conversion boundary.

---

### Impact Explanation

Every L1 handler transaction that a validator node receives through the consensus wire format is executed with `paid_fee_on_l1 = Fee(1)`, regardless of the actual fee committed on L1. The blockifier's fee adequacy gate (`paid_fee >= actual_fee`) is never evaluated; only the trivial non-zero check is applied. This means:

1. **Wrong executable payload bound to the wrong fee value.** The `InternalConsensusTransaction::L1Handler` produced by the validator carries a fabricated `paid_fee_on_l1` that does not reflect the on-chain commitment. This is a broken conversion invariant in the public-to-internal conversion path.

2. **Fee adequacy check bypassed.** An L1 handler transaction whose actual execution cost exceeds the fee paid on L1 will still be accepted and committed by every validator, because `Fee(1)` always satisfies the only check performed.

3. **Divergent state between proposer and validators.** The proposer records the real `paid_fee_on_l1` in its cende/central objects; validators record `Fee(1)`. Any downstream system (e.g., the cende layer, receipts, or future fee-refund logic) that reads `paid_fee_on_l1` from the executable transaction will observe different values depending on which node produced the record.

This matches the "High — Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload" impact tier, and touches "Critical — Incorrect fee, gas, bouncer, resource accounting … with economic impact" if the `paid_fee_on_l1` field is used for any settlement or refund accounting.

---

### Likelihood Explanation

The condition is triggered for **every** L1 handler transaction processed by a validator node, because the consensus protobuf path is the only way validators receive L1 handler transactions from the proposer. The hardcoded `Fee(1)` is unconditional; no special input is required. The TODO comment confirms this is a known placeholder, not a deliberate design choice.

---

### Recommendation

1. **Add `paid_fee_on_l1` to the consensus wire format.** Extend `L1HandlerV0` in `consensus.proto` with a `paid_fee_on_l1` field and update the Rust serializer/deserializer in `crates/apollo_protobuf/src/converters/transaction.rs` to round-trip it.

2. **Propagate the real value through `ConsensusTransaction`.** Change `ConsensusTransaction::L1Handler` to wrap `executable_transaction::L1HandlerTransaction` (which carries `paid_fee_on_l1`) instead of the raw `transaction::L1HandlerTransaction`.

3. **Strengthen the blockifier fee check.** Replace the `paid_fee == Fee(0)` guard with `paid_fee < receipt.fee` so that the sequencer enforces fee adequacy independently of the L1 contract, as the comment at line 101–102 of `l1_handler_transaction.rs` already contemplates.

---

### Proof of Concept

```
1. An L1 user calls sendMessageToL2 on the Starknet core contract, paying a
   minimal fee (e.g., 1 wei) that passes the L1 contract's minimum threshold
   but is far below the actual L2 execution cost of the target handler.

2. The proposer's L1 provider scrapes the event and creates an
   executable_transaction::L1HandlerTransaction with paid_fee_on_l1 = Fee(1_wei).

3. The proposer serialises the transaction into a ConsensusTransaction::L1Handler
   (transaction::L1HandlerTransaction) and broadcasts it via the protobuf
   consensus wire format (L1HandlerV0), which carries no paid_fee_on_l1 field.

4. Each validator receives the protobuf message and calls
   convert_consensus_l1_handler_to_internal_l1_handler, which constructs
   executable_transaction::L1HandlerTransaction { paid_fee_on_l1: Fee(1) }.

5. The blockifier executes the handler. Suppose the actual execution cost is
   Fee(1_000_000). The post-execution check evaluates:
       if Fee(1) == Fee(0) { ... }   // false → no error raised

6. The transaction is committed. The receipt records actual_fee = Fee(1_000_000),
   but paid_fee_on_l1 = Fee(1) is what the validator stored — a 999,999-unit
   discrepancy that is never caught by the sequencer.
``` [7](#0-6) [8](#0-7) [9](#0-8)

### Citations

**File:** crates/starknet_api/src/consensus_transaction.rs (L9-12)
```rust
pub enum ConsensusTransaction {
    RpcTransaction(RpcTransaction),
    L1Handler(transaction::L1HandlerTransaction),
}
```

**File:** crates/apollo_protobuf/src/proto/p2p/proto/consensus/consensus.proto (L10-18)
```text
message ConsensusTransaction {
    oneof txn {
        DeclareV3WithClass declare_v3     = 1;
        DeployAccountV3 deploy_account_v3 = 2;
        InvokeV3WithProof invoke_v3       = 3;
        L1HandlerV0 l1_handler            = 4;
    }
    Hash transaction_hash = 5;
}
```

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L964-985)
```rust
impl TryFrom<protobuf::L1HandlerV0> for L1HandlerTransaction {
    type Error = ProtobufConversionError;
    fn try_from(value: protobuf::L1HandlerV0) -> Result<Self, Self::Error> {
        let version = L1HandlerTransaction::VERSION;

        let nonce = Nonce(value.nonce.ok_or(missing("L1HandlerV0::nonce"))?.try_into()?);

        let contract_address = value.address.ok_or(missing("L1HandlerV0::address"))?.try_into()?;

        let entry_point_selector_felt = Felt::try_from(
            value.entry_point_selector.ok_or(missing("L1HandlerV0::entry_point_selector"))?,
        )?;
        let entry_point_selector = EntryPointSelector(entry_point_selector_felt);

        let calldata =
            value.calldata.into_iter().map(Felt::try_from).collect::<Result<Vec<_>, _>>()?;

        let calldata = Calldata(calldata.into());

        Ok(Self { version, nonce, contract_address, entry_point_selector, calldata })
    }
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

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L197-201)
```rust
            ConsensusTransaction::L1Handler(tx) => {
                let internal_tx = self.convert_consensus_l1_handler_to_internal_l1_handler(tx)?;
                Ok((InternalConsensusTransaction::L1Handler(internal_tx), None))
            }
        }
```

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

**File:** crates/blockifier/src/transaction/l1_handler_transaction.rs (L98-115)
```rust
                    Ok(()) => {
                        // Post-execution check passed, commit the execution.
                        execution_state.commit();
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

                        Ok(l1_handler_tx_execution_info(execute_call_info, receipt, None))
```

**File:** crates/apollo_consensus_orchestrator/src/cende/central_objects.rs (L383-393)
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
```
