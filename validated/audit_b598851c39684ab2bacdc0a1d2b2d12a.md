### Title
Hardcoded `Fee(1)` Placeholder in `convert_consensus_l1_handler_to_internal_l1_handler` Permanently Bypasses L1Handler Fee Validation on Validator Nodes — (`File: crates/apollo_transaction_converter/src/transaction_converter.rs`)

### Summary

`convert_consensus_l1_handler_to_internal_l1_handler` unconditionally injects `paid_fee_on_l1 = Fee(1)` when converting a consensus-received `L1HandlerTransaction` into its executable form. Because the consensus protobuf (`L1HandlerV0`) carries no `paid_fee_on_l1` field, every validator node permanently loses the real L1-paid fee and substitutes a sentinel value. This breaks the fee-sufficiency check in the blockifier, causes the wrong `paid_fee_on_l1` to be committed to the central blob, and creates a divergence between the proposer's executable object and the validator's executable object for the same transaction.

### Finding Description

**Conversion boundary.** When the proposer builds a block it obtains `L1HandlerTransaction` objects from the L1 provider, each carrying the real `paid_fee_on_l1` scraped from the L1 event. Those objects are wrapped in `InternalConsensusTransaction::L1Handler` and forwarded to the batcher. When the proposer broadcasts the block to peers it serialises each L1Handler as `protobuf::L1HandlerV0`:

```rust
// crates/apollo_protobuf/src/converters/transaction.rs:987-995
impl From<L1HandlerTransaction> for protobuf::L1HandlerV0 {
    fn from(value: L1HandlerTransaction) -> Self {
        Self {
            nonce: Some(value.nonce.0.into()),
            address: Some(value.contract_address.into()),
            entry_point_selector: Some(value.entry_point_selector.0.into()),
            calldata: value.calldata.0.iter().map(|calldata| (*calldata).into()).collect(),
        }
    }
}
```

`paid_fee_on_l1` is silently dropped — the protobuf message has no field for it.

**Validator reconstruction.** On the receiving side, `convert_consensus_l1_handler_to_internal_l1_handler` must supply a value for `paid_fee_on_l1` when calling `L1HandlerTransaction::create`. It hardcodes `Fee(1)`:

```rust
// crates/apollo_transaction_converter/src/transaction_converter.rs:473-483
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

**Fee check bypassed.** The blockifier's `execute_raw` for L1Handler transactions enforces a single fee invariant — that the paid fee is non-zero:

```rust
// crates/blockifier/src/transaction/l1_handler_transaction.rs:103-113
let paid_fee = self.paid_fee_on_l1;
// For now, assert only that any amount of fee was paid.
if paid_fee == Fee(0) {
    return Err(TransactionExecutionError::TransactionFeeError(Box::new(
        TransactionFeeError::InsufficientFee {
            paid_fee,
            actual_fee: receipt.fee,
        },
    )));
}
```

Because `Fee(1) ≠ Fee(0)`, this check always passes on every validator node for every L1Handler transaction, regardless of what was actually paid on L1.

**Wrong value committed to central blob.** `CentralL1HandlerTransaction` serialises `paid_fee_on_l1` directly from the executable transaction:

```rust
// crates/apollo_consensus_orchestrator/src/cende/central_objects.rs:383-393
impl From<L1HandlerTransaction> for CentralL1HandlerTransaction {
    fn from(tx: L1HandlerTransaction) -> CentralL1HandlerTransaction {
        CentralL1HandlerTransaction {
            ...
            paid_fee_on_l1: tx.paid_fee_on_l1,   // always Fee(1) on validators
        }
    }
}
```

Any node that participates in writing the central blob will emit `paid_fee_on_l1 = 1` instead of the real L1 fee for every L1Handler transaction.

### Impact Explanation

1. **Wrong executable payload bound at conversion.** The validator's `executable_transaction::L1HandlerTransaction` carries `paid_fee_on_l1 = Fee(1)` while the proposer's carries the real value (e.g. `Fee(1_000_000)`). These are structurally different objects representing the same transaction — a broken canonicalization invariant across the public-to-internal conversion boundary.

2. **Fee check permanently bypassed on validators.** The only blockifier-level guard against zero-fee L1Handler transactions is unconditionally satisfied. A proposer that (correctly or maliciously) includes an L1Handler transaction with `paid_fee_on_l1 = 0` would have its blockifier return an error, while every validator would execute it successfully — producing divergent execution results for the same input.

3. **Wrong state written to central blob.** `paid_fee_on_l1` is a semantic field of the L1Handler receipt that downstream systems (Cende / central) use to account for L1→L2 message fees. Emitting `1` instead of the real value is a wrong state value committed under the "Wrong state … from blockifier/syscall/execution logic for accepted input" criterion.

### Likelihood Explanation

Every L1Handler transaction that passes through the consensus validator path triggers this conversion. L1Handler transactions are a normal, unprivileged part of the protocol — any user can deposit a message from L1 to L2 by calling the Starknet core contract. No special privileges or malformed bytes are required; the divergence occurs on every well-formed L1Handler transaction received by a validator.

### Recommendation

1. Add `paid_fee_on_l1` to the `L1HandlerV0` protobuf message in `consensus.proto` and propagate it through the `From`/`TryFrom` converters in `crates/apollo_protobuf/src/converters/transaction.rs`.
2. Remove the `Fee(1)` placeholder in `convert_consensus_l1_handler_to_internal_l1_handler` and use the value decoded from the protobuf message.
3. Add a round-trip test asserting that `paid_fee_on_l1` is preserved across `ConsensusTransaction → protobuf → ConsensusTransaction → InternalConsensusTransaction`.

### Proof of Concept

1. User calls the Starknet L1 core contract with `paid_fee = 500_000 wei`, producing an L1Handler event.
2. The L1 scraper stores the transaction with `paid_fee_on_l1 = Fee(500_000)`.
3. The proposer includes it in a block proposal; the protobuf serialisation drops `paid_fee_on_l1`.
4. Each validator calls `convert_consensus_l1_handler_to_internal_l1_handler`, producing `paid_fee_on_l1 = Fee(1)`.
5. The blockifier on each validator evaluates `Fee(1) == Fee(0)` → `false` → fee check passes unconditionally.
6. `CentralL1HandlerTransaction` is serialised with `paid_fee_on_l1 = 1` instead of `500_000`.
7. The proposer's executable object and every validator's executable object for the same transaction hash differ in `paid_fee_on_l1`, breaking the conversion canonicalization invariant. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** crates/blockifier/src/transaction/l1_handler_transaction.rs (L100-113)
```rust
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

**File:** crates/starknet_api/src/executable_transaction.rs (L380-397)
```rust
#[derive(Clone, Debug, Default, Deserialize, Eq, PartialEq, Serialize, Hash)]
pub struct L1HandlerTransaction {
    pub tx: crate::transaction::L1HandlerTransaction,
    pub tx_hash: TransactionHash,
    pub paid_fee_on_l1: Fee,
}

impl L1HandlerTransaction {
    pub const L1_HANDLER_TYPE_NAME: &str = "L1_HANDLER";

    pub fn create(
        raw_tx: crate::transaction::L1HandlerTransaction,
        chain_id: &ChainId,
        paid_fee_on_l1: Fee,
    ) -> Result<L1HandlerTransaction, StarknetApiError> {
        let tx_hash = raw_tx.calculate_transaction_hash(chain_id, &raw_tx.version)?;
        Ok(Self { tx: raw_tx, tx_hash, paid_fee_on_l1 })
    }
```
