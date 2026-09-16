## Title
Weak L1Handler fee-sufficiency check allows dust L1 fees to authorize unbounded L2 resource consumption - (File: `crates/blockifier/src/transaction/l1_handler_transaction.rs`)

### Summary
The Superfluid `provideLiquidity` bug allowed the caller-supplied "pumped" amount to be decoupled from the actual funds used to open the position, letting the attacker pre-fund the contract and pay only a dust amount at call time, bypassing the intended buy-pressure/fee mechanism. The Starknet sequencer has a structurally identical decoupling in its L1→L2 message fee accounting: the amount an L1 message sender actually pays (`paid_fee_on_l1`) is checked only for being non-zero, while the *real* resource consumption authorized by that payment is bounded only by a fixed protocol constant (`l1_handler_max_amount_bounds`), completely independent of the amount paid.

### Finding Description
In `ExecutableTransaction::execute_raw` for `L1HandlerTransaction`, resource usage is capped by a fixed bound taken from versioned constants, not by anything proportional to what was actually paid on L1: [1](#0-0) 

After the fee-check-report validates gas usage is *within the fixed bound* `l1_handler_bounds`, the only gate tied to the amount actually paid on L1 is a trivial non-zero check: [2](#0-1) 

The comment explicitly acknowledges this is a placeholder ("For now, assert only that any amount of fee was paid") and defers correctness to an off-chain assumption ("covered by the starknet core contract") that is not actually enforced anywhere in this repository — the L1 `sendMessageToL2` entry point is `payable` with no minimum value enforced, as reflected in the scraped ABI/event definitions: [3](#0-2) 

The real fee value comes only from the `LogMessageToL2` event's `fee` field, faithfully parsed by the scraper: [4](#0-3) 

This means the "pumping" analog here — the amount of ETH actually committed by the L1 sender — is only used for a binary non-zero check, while the "position size" analog — the L2 execution/storage/messaging resources the sequencer actually spends processing the handler — is bounded solely by the fixed constant `l1_handler_max_amount_bounds`, entirely decoupled from the paid amount. An L1 sender can pay a minimal fee (as little as 1 wei, exactly as used in the test/tooling helpers) and still have the sequencer execute up to the maximum allowed L1/L2/L1-data gas for that transaction type: [5](#0-4) [6](#0-5) 

Compounding this, the check is further weakened on the consensus-validation path: when a validator reconstructs an `L1HandlerTransaction` from a received `ConsensusTransaction::L1Handler` (which carries only the raw `transaction::L1HandlerTransaction`, without `paid_fee_on_l1`), the converter hardcodes a placeholder value instead of the real fee paid on L1: [7](#0-6) [8](#0-7) 

Because `ConsensusTransaction::L1Handler` only propagates `tx.tx` (dropping `paid_fee_on_l1`) as seen in the reverse conversion: [9](#0-8) 

the validating node never sees the true amount paid on L1 and always substitutes `Fee(1)`, so the already-weak non-zero check is rendered universally true on the validator's re-execution path, regardless of the true value scraped from L1.

### Impact Explanation
An unprivileged L1 message sender can trigger sequencer-side execution consuming resources up to the full `l1_handler_max_amount_bounds` (L1 gas, L2 gas, L1 data gas) while paying only a nominal/dust fee on L1, because the only enforcement tying the paid amount to the granted resource budget is a trivial non-zero check rather than a sufficiency check. This decouples the network's intended fee-for-resource economic mechanism (the "buy pressure"/fee-recovery analog to Pumponomics) from the actual cost the sequencer incurs, causing the protocol to systematically under-collect fees for L1Handler execution and enabling cheap, repeated resource consumption paid for at a fraction of its real cost — precisely mirroring the underlying bug class of H-2 (declared/side-effect amount decoupled from the amount actually used for the primary economic operation).

### Likelihood Explanation
Any L1 account can call `sendMessageToL2` with an arbitrarily small (non-zero) `msg.value`; no additional privilege, timing, or coordination is required. The condition is trivially reachable on every L1Handler transaction, since the check is unconditionally applied.

### Recommendation
Replace the trivial `paid_fee == Fee(0)` check with a sufficiency check that compares `paid_fee_on_l1` against the actually computed `receipt.fee`/resource cost of the transaction (as already computed via `FeeCheckReport`), rejecting/reverting transactions whose L1-paid fee does not cover the real resource cost. Additionally, propagate the real `paid_fee_on_l1` through `ConsensusTransaction::L1Handler` (rather than dropping it and reconstructing with a hardcoded placeholder) so that validators evaluate the same fee-sufficiency condition the proposer evaluated.

### Proof of Concept
1. An L1 account calls the Starknet core contract's `sendMessageToL2(...)` with `msg.value = 1` (or any minimal non-zero amount), as demonstrated by the test helper pattern here: [6](#0-5) .
2. The L1 events scraper records `paid_fee_on_l1 = Fee(1)`.
3. The batcher executes the resulting `L1HandlerTransaction`; execution proceeds and is checked only against the fixed `l1_handler_max_amount_bounds`, not against the 1-wei fee paid: [10](#0-9) .
4. As long as gas usage stays within `l1_handler_max_amount_bounds`, the transaction succeeds regardless of the (dust) fee paid, letting the sender consume large sequencer resources per L1 message far in excess of what was paid.

### Citations

**File:** crates/blockifier/src/transaction/l1_handler_transaction.rs (L62-73)
```rust
        let tx_context = Arc::new(block_context.to_tx_context(self));
        let limit_steps_by_resources = false;
        let l1_handler_bounds =
            block_context.versioned_constants.os_constants.l1_handler_max_amount_bounds;

        let mut remaining_gas = l1_handler_bounds.l2_gas.0;
        let mut context = EntryPointExecutionContext::new_invoke(
            tx_context.clone(),
            limit_steps_by_resources,
            SierraGasRevertTracker::new(GasAmount(remaining_gas)),
        );
        let l1_handler_payload_size = self.payload_size();
```

**File:** crates/blockifier/src/transaction/l1_handler_transaction.rs (L92-116)
```rust
                // Enforce resource bounds.
                let fee_check_report = FeeCheckReport::check_all_gas_amounts_within_bounds(
                    &l1_handler_bounds,
                    &receipt.gas,
                );
                match fee_check_report {
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
                    }
```

**File:** crates/papyrus_base_layer/resources/Starknet-0.10.3.4.json (L118-139)
```json
                {
                    "indexed": false,
                    "internalType": "uint256[]",
                    "name": "payload",
                    "type": "uint256[]"
                },
                {
                    "indexed": false,
                    "internalType": "uint256",
                    "name": "nonce",
                    "type": "uint256"
                },
                {
                    "indexed": false,
                    "internalType": "uint256",
                    "name": "fee",
                    "type": "uint256"
                }
            ],
            "name": "LogMessageToL2",
            "type": "event"
        },
```

**File:** crates/papyrus_base_layer/src/eth_events.rs (L29-35)
```rust
    match event {
        Starknet::StarknetEvents::LogMessageToL2(event) => {
            let fee = Fee(event.fee.try_into().map_err(EthereumBaseLayerError::FeeOutOfRange)?);
            let event_data = EventData::try_from(event)?;
            let tx = L1HandlerTransaction::from(event_data);
            Ok(L1Event::LogMessageToL2 { tx, fee, l1_tx_hash, block_timestamp })
        }
```

**File:** crates/blockifier/src/test_utils/l1_handler.rs (L15-29)
```rust
pub fn l1handler_tx(l1_fee: Fee, contract_address: ContractAddress) -> L1HandlerTransaction {
    let calldata = calldata![
        Felt::from(0x123), // from_address.
        Felt::from(0x876), // key.
        Felt::from(0x44)   // value.
    ];

    executable_l1_handler_tx(L1HandlerTxArgs {
        contract_address,
        entry_point_selector: *L1_HANDLER_SET_VALUE_ENTRY_POINT_SELECTOR,
        calldata,
        paid_fee_on_l1: l1_fee,
        ..Default::default()
    })
}
```

**File:** crates/apollo_l1_events/tests/utils/mod.rs (L192-197)
```rust
    let call_data = convert_call_data_to_u256(call_data);
    let fee = 1_u8;
    let message_to_l2 = contract
        .sendMessageToL2(U256::from(L1_CONTRACT_ADDRESS), U256::from(L2_ENTRY_POINT), call_data)
        .value(U256::from(fee));
    let receipt = message_to_l2.send().await.unwrap().get_receipt().await.unwrap();
```

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L178-180)
```rust
            InternalConsensusTransaction::L1Handler(tx) => {
                Ok(ConsensusTransaction::L1Handler(tx.tx))
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

**File:** crates/starknet_api/src/consensus_transaction.rs (L1-1)
```rust
use serde::{Deserialize, Serialize};
```
