### Title
Hardcoded `paid_fee_on_l1 = Fee(1)` in Consensus-Path L1Handler Conversion Bypasses Fee Check and Corrupts OS Input - (`File: crates/apollo_transaction_converter/src/transaction_converter.rs`)

### Summary

When a validator node receives an `L1Handler` transaction via the consensus P2P path, the `paid_fee_on_l1` field is not carried in the protobuf wire format and is silently replaced with the hardcoded constant `Fee(1)` during conversion. This is the sequencer analog of the DCA `takerInteraction` zero-validation bug: a critical economic field is not validated or preserved across a conversion boundary, causing the blockifier's fee-sufficiency check to always pass and the `CentralL1HandlerTransaction` object forwarded to the OS/proving layer to carry a wrong fee value.

### Finding Description

**Step 1 – Field absent from protobuf wire format.**

`ConsensusTransaction::L1Handler` wraps a `transaction::L1HandlerTransaction`, which has no `paid_fee_on_l1` field. The protobuf serializer confirms this: [1](#0-0) 

Only `nonce`, `address`, `entry_point_selector`, and `calldata` are serialized. `paid_fee_on_l1` is never transmitted over consensus P2P.

**Step 2 – Converter hardcodes `Fee(1)` as a placeholder.**

When `convert_consensus_tx_to_internal_consensus_tx` processes an `L1Handler` variant it calls: [2](#0-1) 

The `// TODO(Gilad): Change this once we put real value in paid_fee_on_l1.` comment confirms this is a known placeholder, but it is live production code executed on every validator for every L1Handler transaction.

**Step 3 – Blockifier fee check is bypassed.**

After execution the blockifier checks: [3](#0-2) 

The guard is `paid_fee == Fee(0)`. Because the converter always injects `Fee(1)`, this check unconditionally passes for every L1Handler transaction arriving via consensus, regardless of what was actually paid on L1.

**Step 4 – Wrong value propagates to the OS central object.**

The `CentralL1HandlerTransaction` serialized and forwarded to Cende/OS includes `paid_fee_on_l1`: [4](#0-3) 

On the proposer node, `paid_fee_on_l1` is read from the L1 events scraper (real value). On every validator node it is `Fee(1)`. The two nodes therefore produce structurally different `CentralL1HandlerTransaction` objects for the same transaction.

### Impact Explanation

- **Wrong receipt/state from execution logic (Critical scope):** The blockifier's `paid_fee == Fee(0)` guard is the only in-process check that an L1Handler transaction carried a non-zero fee. Hardcoding `Fee(1)` makes this check a no-op for the entire consensus validation path. Any L1Handler transaction—including one whose actual `paid_fee_on_l1` is `0`—will be accepted and committed by validators.

- **Wrong OS input (Critical scope):** The `CentralL1HandlerTransaction` sent to the OS/prover carries `paid_fee_on_l1 = 1` on all validator nodes instead of the real fee. If the OS uses this field for fee accounting, block-level fee aggregation, or any hash that feeds into the block commitment, the validator's OS run diverges from the proposer's, producing a wrong class hash, storage value, or revert result.

### Likelihood Explanation

This is triggered automatically for every `L1Handler` transaction that passes through the consensus validation path. No special attacker action is required; the divergence is structural and deterministic. Any block containing an L1Handler transaction will exhibit the wrong `paid_fee_on_l1` on all validator nodes.

### Recommendation

1. Include `paid_fee_on_l1` in the `protobuf::L1HandlerV0` message so it is transmitted over consensus P2P and can be reconstructed faithfully on validators.
2. Remove the `Fee(1)` placeholder in `convert_consensus_l1_handler_to_internal_l1_handler` and use the deserialized value.
3. Add a validation gate analogous to the DCA fix: assert `paid_fee_on_l1 > Fee(0)` before accepting the converted transaction, mirroring the blockifier's intent.

### Proof of Concept

1. Proposer node scrapes an L1→L2 message with `paid_fee_on_l1 = 500_000_000_000` (real ETH fee).
2. Proposer serializes the transaction into a `protobuf::ConsensusTransaction` via `From<ConsensusTransaction>` — `paid_fee_on_l1` is absent from the wire bytes.
3. Validator deserializes the protobuf and calls `convert_consensus_tx_to_internal_consensus_tx` → `convert_consensus_l1_handler_to_internal_l1_handler` → `L1HandlerTransaction::create(tx, &chain_id, Fee(1))`.
4. Blockifier executes the transaction; `paid_fee == Fee(1) != Fee(0)` → check passes.
5. `CentralL1HandlerTransaction { paid_fee_on_l1: Fee(1), … }` is forwarded to Cende/OS.
6. Proposer's central object has `paid_fee_on_l1: Fee(500_000_000_000)`; validator's has `Fee(1)` — exact divergent value confirmed.

### Citations

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
