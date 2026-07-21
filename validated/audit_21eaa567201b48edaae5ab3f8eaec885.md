### Title
Hardcoded `paid_fee_on_l1 = Fee(1)` in Consensus-to-Executable L1 Handler Conversion Produces Wrong Execution Context and Wrong RPC Simulation Values — (`crates/apollo_transaction_converter/src/transaction_converter.rs`, `crates/apollo_rpc/src/v0_8/api/mod.rs`)

---

### Summary

Two production code paths permanently substitute the real `paid_fee_on_l1` value of every L1 handler transaction with the hard-coded sentinel `Fee(1)`. The consensus sequencing path (`convert_consensus_l1_handler_to_internal_l1_handler`) does this for every L1 handler that is executed during block production. The RPC re-execution path (`stored_txn_to_executable_txn`) does the same for every `starknet_simulateTransactions` / `starknet_traceTransaction` call that touches a stored L1 handler. Because `paid_fee_on_l1` is part of the transaction context that Cairo contracts can read via `get_execution_info`, any contract that branches on this value will observe `1` instead of the real fee, producing divergent state, events, or revert results.

---

### Finding Description

**Consensus path — wrong execution context for every sequenced L1 handler**

`convert_consensus_l1_handler_to_internal_l1_handler` is the sole conversion point from a `ConsensusTransaction::L1Handler` (the wire type, which carries no fee field) to the blockifier-executable `L1HandlerTransaction`. It unconditionally passes `Fee(1)`:

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
``` [1](#0-0) 

The `L1HandlerTransaction::create` constructor stores this value verbatim in the `paid_fee_on_l1` field of the executable struct:

```rust
// crates/starknet_api/src/executable_transaction.rs:390-396
pub fn create(
    raw_tx: crate::transaction::L1HandlerTransaction,
    chain_id: &ChainId,
    paid_fee_on_l1: Fee,
) -> Result<L1HandlerTransaction, StarknetApiError> {
    let tx_hash = raw_tx.calculate_transaction_hash(chain_id, &raw_tx.version)?;
    Ok(Self { tx: raw_tx, tx_hash, paid_fee_on_l1 })
}
``` [2](#0-1) 

The blockifier's `execute_raw` for `L1HandlerTransaction` builds the `tx_context` from `self` (which now carries `paid_fee_on_l1 = Fee(1)`) and passes it to the Cairo VM. The only guard is:

```rust
// crates/blockifier/src/transaction/l1_handler_transaction.rs:103-113
let paid_fee = self.paid_fee_on_l1;
// For now, assert only that any amount of fee was paid.
if paid_fee == Fee(0) {
    return Err(TransactionExecutionError::TransactionFeeError(...));
}
``` [3](#0-2) 

`Fee(1)` always satisfies `paid_fee != Fee(0)`, so the check is bypassed for every real L1 handler regardless of what was actually paid on Ethereum. The `tx_context` that the Cairo VM receives therefore always reports `paid_fee_on_l1 = 1`. Any Cairo contract that reads this field via `get_execution_info` (e.g., to enforce a minimum fee, to log the fee, or to gate logic on the fee amount) will observe the wrong value, producing wrong storage writes, wrong events, or a wrong revert decision.

**RPC path — wrong simulation/tracing for stored L1 handlers**

`stored_txn_to_executable_txn` in the RPC layer applies the same substitution when re-executing stored L1 handler transactions for `starknet_simulateTransactions`, `starknet_traceTransaction`, and `starknet_estimateFee`:

```rust
// crates/apollo_rpc/src/v0_8/api/mod.rs:424-428
starknet_api::transaction::Transaction::L1Handler(value) => {
    // todo(yair): This is a temporary solution until we have a better way to get the l1 fee.
    let paid_fee_on_l1 = Fee(1);
    Ok(ExecutableTransactionInput::L1Handler(value, paid_fee_on_l1, false))
}
``` [4](#0-3) 

The stored transaction already has the correct `paid_fee_on_l1` (scraped from the Ethereum `LogMessageToL2` event and stored in the DB), but it is discarded and replaced with `1`. The simulation therefore runs with a different execution context than the original on-chain execution, and any contract branch on `paid_fee_on_l1` will produce a divergent trace.

**Contrast with the correct path**

The L1 events scraper correctly reads the real fee from the Ethereum event and stores it:

```rust
// crates/papyrus_base_layer/src/eth_events.rs:31
let fee = Fee(event.fee.try_into().map_err(EthereumBaseLayerError::FeeOutOfRange)?);
``` [5](#0-4) 

The native blockifier Python wrapper also reads the real fee from the Python object:

```rust
// crates/native_blockifier/src/py_l1_handler.rs:37
let paid_fee_on_l1 = Fee(py_attr::<u128>(py_tx, "paid_fee_on_l1")?);
``` [6](#0-5) 

Only the consensus conversion and the RPC re-execution path substitute the sentinel.

---

### Impact Explanation

**Consensus path (Critical — Wrong state/event/revert from blockifier execution):**
Every L1 handler transaction sequenced through the consensus path is executed with `paid_fee_on_l1 = 1` in the Cairo VM context. A Cairo contract that reads `get_execution_info().tx_info.paid_fee_on_l1` will see `1` instead of the real Ethereum fee. This can cause:
- Wrong storage writes (e.g., a contract that records the fee paid)
- Wrong events emitted (e.g., a contract that logs the fee)
- Wrong revert decisions (e.g., a contract that requires `paid_fee_on_l1 >= threshold`)

The divergence is silent — the blockifier accepts the transaction and commits state — but the committed state is wrong relative to what the contract logic intended.

**RPC path (High — Authoritative-looking wrong simulation/trace value):**
`starknet_simulateTransactions` and `starknet_traceTransaction` return execution traces that clients use to predict on-chain behavior. For L1 handler transactions, the trace is computed with `paid_fee_on_l1 = 1` instead of the real fee. Any contract branch on this value produces a divergent trace, misleading callers about what the actual on-chain execution will do.

---

### Likelihood Explanation

The consensus path is triggered for every L1 handler transaction included in any block produced by this sequencer. The RPC path is triggered for every simulation or trace call targeting a stored L1 handler. Both are normal, unprivileged operations. The only condition for observable impact is that the L1 handler's target contract reads `paid_fee_on_l1` from the execution info — a valid and documented Starknet capability. The TODO comments confirm this is a known placeholder, not an intentional design choice.

---

### Recommendation

1. **Consensus path**: Propagate the real `paid_fee_on_l1` through the consensus message. The `ConsensusTransaction::L1Handler` variant should carry the fee (scraped from the L1 event and included by the proposer), and `convert_consensus_l1_handler_to_internal_l1_handler` should use it instead of `Fee(1)`.

2. **RPC path**: In `stored_txn_to_executable_txn`, retrieve the stored `paid_fee_on_l1` from the storage transaction (it is already persisted alongside the L1 handler transaction) and pass it to `ExecutableTransactionInput::L1Handler` instead of the hard-coded `Fee(1)`.

---

### Proof of Concept

1. Deploy a Cairo contract with an L1 handler entry point that reads `get_execution_info().tx_info.paid_fee_on_l1` and writes it to storage.
2. Send a `LogMessageToL2` event from Ethereum with `fee = 1_000_000`.
3. The L1 events scraper creates an `ExecutableL1HandlerTransaction` with `paid_fee_on_l1 = Fee(1_000_000)`.
4. The proposer includes this L1 handler in a block proposal. The consensus converter calls `convert_consensus_l1_handler_to_internal_l1_handler`, which creates the executable transaction with `paid_fee_on_l1 = Fee(1)`.
5. The blockifier executes the contract. The contract reads `paid_fee_on_l1` and writes `1` to storage instead of `1_000_000`.
6. Call `starknet_traceTransaction` on the stored L1 handler. `stored_txn_to_executable_txn` uses `Fee(1)`. The trace shows `paid_fee_on_l1 = 1`, consistent with the wrong on-chain state but divergent from the real Ethereum fee.

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

**File:** crates/starknet_api/src/executable_transaction.rs (L390-396)
```rust
    pub fn create(
        raw_tx: crate::transaction::L1HandlerTransaction,
        chain_id: &ChainId,
        paid_fee_on_l1: Fee,
    ) -> Result<L1HandlerTransaction, StarknetApiError> {
        let tx_hash = raw_tx.calculate_transaction_hash(chain_id, &raw_tx.version)?;
        Ok(Self { tx: raw_tx, tx_hash, paid_fee_on_l1 })
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

**File:** crates/apollo_rpc/src/v0_8/api/mod.rs (L424-429)
```rust
        starknet_api::transaction::Transaction::L1Handler(value) => {
            // todo(yair): This is a temporary solution until we have a better way to get the l1
            // fee.
            let paid_fee_on_l1 = Fee(1);
            Ok(ExecutableTransactionInput::L1Handler(value, paid_fee_on_l1, false))
        }
```

**File:** crates/papyrus_base_layer/src/eth_events.rs (L31-34)
```rust
            let fee = Fee(event.fee.try_into().map_err(EthereumBaseLayerError::FeeOutOfRange)?);
            let event_data = EventData::try_from(event)?;
            let tx = L1HandlerTransaction::from(event_data);
            Ok(L1Event::LogMessageToL2 { tx, fee, l1_tx_hash, block_timestamp })
```

**File:** crates/native_blockifier/src/py_l1_handler.rs (L37-39)
```rust
    let paid_fee_on_l1 = Fee(py_attr::<u128>(py_tx, "paid_fee_on_l1")?);

    Ok(L1HandlerTransaction { tx, tx_hash, paid_fee_on_l1 })
```
