### Title
`paid_fee_on_l1` Silently Dropped at Consensus-to-Internal Conversion Boundary, Hardcoded Placeholder Propagates Wrong Value into Executable Payload and Central Blob — (`crates/apollo_transaction_converter/src/transaction_converter.rs`)

### Summary

The `ConsensusTransaction::L1Handler` variant carries only a bare `transaction::L1HandlerTransaction` (no `paid_fee_on_l1` field). The protobuf wire format (`L1HandlerV0`) also omits this field. When a peer converts a received `ConsensusTransaction::L1Handler` into an `InternalConsensusTransaction::L1Handler`, the converter unconditionally substitutes `Fee(1)` for the actual fee paid on L1. This hardcoded placeholder is then embedded in the executable transaction and forwarded verbatim into the central blob sent to the proving/OS layer, producing a canonically wrong `paid_fee_on_l1` for every L1Handler transaction that traverses the consensus P2P path.

### Finding Description

**Conversion boundary — the missing field:**

`ConsensusTransaction` is defined as:

```rust
pub enum ConsensusTransaction {
    RpcTransaction(RpcTransaction),
    L1Handler(transaction::L1HandlerTransaction),  // no paid_fee_on_l1
}
``` [1](#0-0) 

The protobuf representation `L1HandlerV0` carries only `nonce`, `address`, `entry_point_selector`, and `calldata` — `paid_fee_on_l1` is absent from the wire message:

```rust
impl TryFrom<protobuf::L1HandlerV0> for L1HandlerTransaction {
    fn try_from(value: protobuf::L1HandlerV0) -> Result<Self, Self::Error> {
        // ... nonce, contract_address, entry_point_selector, calldata only
        Ok(Self { version, nonce, contract_address, entry_point_selector, calldata })
    }
}
``` [2](#0-1) 

**Hardcoded placeholder injected at conversion:**

When `convert_consensus_tx_to_internal_consensus_tx` processes an `L1Handler` arm, it calls:

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
``` [3](#0-2) 

The same placeholder appears in the RPC execution path:

```rust
starknet_api::transaction::Transaction::L1Handler(value) => {
    // todo(yair): This is a temporary solution until we have a better way to get the l1 fee.
    let paid_fee_on_l1 = Fee(1);
    Ok(ExecutableTransactionInput::L1Handler(value, paid_fee_on_l1, false))
}
``` [4](#0-3) 

**Wrong value propagates into the central blob:**

`InternalConsensusTransaction::L1Handler` holds an `executable_transaction::L1HandlerTransaction` with `paid_fee_on_l1 = Fee(1)`. When the batcher serializes the finalized block for the central service, `From<L1HandlerTransaction> for CentralL1HandlerTransaction` faithfully copies this wrong value:

```rust
impl From<L1HandlerTransaction> for CentralL1HandlerTransaction {
    fn from(tx: L1HandlerTransaction) -> CentralL1HandlerTransaction {
        CentralL1HandlerTransaction {
            // ...
            paid_fee_on_l1: tx.paid_fee_on_l1,  // always Fee(1) from consensus path
            // ...
        }
    }
}
``` [5](#0-4) 

The OS re-execution layer (`echonet/os_input_builder.py`) reads `paid_fee_on_l1` directly from the central blob and passes it into the executable transaction:

```python
return {
    "tx": inner,
    "tx_hash": central_tx["hash_value"],
    "paid_fee_on_l1": central_tx["paid_fee_on_l1"],  # always 1 from consensus path
}
``` [6](#0-5) 

**Execution check that the wrong value bypasses:**

The blockifier's fee check for L1 handlers only requires `paid_fee > 0`:

```rust
let paid_fee = self.paid_fee_on_l1;
// For now, assert only that any amount of fee was paid.
if paid_fee == Fee(0) {
    return Err(TransactionExecutionError::TransactionFeeError(...InsufficientFee...));
}
``` [7](#0-6) 

`Fee(1)` satisfies this check regardless of the actual fee paid on L1, so the wrong value is silently accepted.

### Impact Explanation

Every L1Handler transaction that arrives at a validator node via the consensus P2P path is executed and committed with `paid_fee_on_l1 = Fee(1)` instead of the actual fee. This wrong value is then written into the central blob and forwarded to the OS/proving layer. The OS re-executes the transaction using this wrong `paid_fee_on_l1`, producing a receipt whose `paid_fee_on_l1` field diverges from the on-chain L1 reality. If the OS or proof verifier validates that `paid_fee_on_l1 ≥ actual_fee`, the proof will be invalid for any L1Handler whose real fee exceeds 1 wei. Even without that check, the canonical state output contains a wrong field value for every L1Handler transaction, breaking the invariant that the sequencer's committed state faithfully reflects L1 messages.

### Likelihood Explanation

Every L1Handler transaction processed through the consensus path (i.e., on any validator node) triggers this issue. L1Handler transactions are a normal part of Starknet operation (L1→L2 messages). No special attacker action is required; the bug fires automatically on every such transaction.

### Recommendation

Carry `paid_fee_on_l1` through the consensus serialization boundary:

1. Add `paid_fee_on_l1` to `ConsensusTransaction::L1Handler` (or wrap it in a dedicated struct).
2. Add a `paid_fee_on_l1` field to the protobuf `L1HandlerV0` message and update both the `From` and `TryFrom` converters.
3. Remove the `Fee(1)` placeholder in `convert_consensus_l1_handler_to_internal_l1_handler` and use the deserialized value instead.
4. Apply the same fix to `stored_txn_to_executable_txn` in `apollo_rpc/src/v0_8/api/mod.rs`.

### Proof of Concept

1. A validator node receives a `ConsensusTransaction::L1Handler` via P2P with actual `paid_fee_on_l1 = Fee(1_000_000)`.
2. The protobuf `L1HandlerV0` message carries no `paid_fee_on_l1` field.
3. `TryFrom<protobuf::L1HandlerV0> for L1HandlerTransaction` reconstructs the transaction without `paid_fee_on_l1`.
4. `convert_consensus_l1_handler_to_internal_l1_handler` calls `L1HandlerTransaction::create(..., Fee(1))`.
5. The blockifier executes the transaction; `paid_fee == Fee(1) > 0` passes the fee check.
6. The batcher serializes the block; `CentralL1HandlerTransaction.paid_fee_on_l1 = Fee(1)` is written to the central blob.
7. The OS re-executes with `paid_fee_on_l1 = 1` instead of `1_000_000` — a canonically wrong receipt value for the L1 message.

### Citations

**File:** crates/starknet_api/src/consensus_transaction.rs (L8-12)
```rust
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize, Hash)]
pub enum ConsensusTransaction {
    RpcTransaction(RpcTransaction),
    L1Handler(transaction::L1HandlerTransaction),
}
```

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L964-984)
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

**File:** crates/apollo_rpc/src/v0_8/api/mod.rs (L424-429)
```rust
        starknet_api::transaction::Transaction::L1Handler(value) => {
            // todo(yair): This is a temporary solution until we have a better way to get the l1
            // fee.
            let paid_fee_on_l1 = Fee(1);
            Ok(ExecutableTransactionInput::L1Handler(value, paid_fee_on_l1, false))
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

**File:** echonet/os_input_builder.py (L286-290)
```python
    return {
        "tx": inner,
        "tx_hash": central_tx["hash_value"],
        "paid_fee_on_l1": central_tx["paid_fee_on_l1"],
    }
```

**File:** crates/blockifier/src/transaction/l1_handler_transaction.rs (L103-113)
```rust
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
