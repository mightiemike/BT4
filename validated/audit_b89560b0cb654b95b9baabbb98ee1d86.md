### Title
Hardcoded `l1_handler_max_amount_bounds` can cause consumed-but-reverted L1→L2 messages, permanently freezing bridged funds - ([File: crates/blockifier/src/transaction/l1_handler_transaction.rs])

### Summary
`InfraredBERAConstants.INITIAL_DEPOSIT` in the external report is a hardcoded, protocol-adjacent constant that silently drifted out of sync with the actually-enforced minimum on the consensus layer, causing all deposits to permanently revert. The analogous pattern exists in this sequencer's L1-handler execution path: `os_constants.l1_handler_max_amount_bounds` is a hardcoded, versioned constant (not user- or sender-configurable) that caps the L1/L2/L1-data gas an L1-handler transaction may consume. Because the L1→L2 message is marked "consumed" unconditionally, before the bound check is evaluated, any message whose required execution gas exceeds this fixed cap will be included, its L1 message consumed, and its state changes rolled back — with no retry path, since the sender (an L1 message sender, not the Starknet account layer) cannot alter the gas bound after the message was sent.

### Finding Description
In `execute_l1_handler_transaction` (Cairo OS), the L1→L2 message is recorded as consumed via `consume_l1_to_l2_message` unconditionally, before the entry point runs and before any resource-bound enforcement: [1](#0-0) 

In the Rust execution path, `l1_handler_bounds` is read directly from the hardcoded, versioned `os_constants.l1_handler_max_amount_bounds` (not derived from the sender-paid L1 fee, and not adjustable per-message): [2](#0-1) 

After execution, resource usage is checked post-hoc against this fixed bound; if it's exceeded, the execution is aborted/reverted, yet the transaction is still committed to the block (as a reverted L1 handler) and the message was already consumed in the OS trace: [3](#0-2) 

The bound itself is a hardcoded versioned constant, e.g. in `blockifier_versioned_constants_0_14_4.json`: [4](#0-3) 

This mirrors the Berachain finding precisely: a static, hardcoded ceiling (`l1_handler_max_amount_bounds`, analogous to `INITIAL_DEPOSIT`) is checked against a dynamically evolving requirement (actual gas consumption of increasingly complex L1-handler/bridge contract logic, analogous to the changed `MIN_DEPOSIT_AMOUNT_IN_GWEI`). If the two drift out of sync — e.g., a bridge/messaging contract's `l1_handler` entrypoint grows in gas cost across upgrades, or future protocol versions raise real-world gas costs faster than this constant is updated — then legitimate L1→L2 messages (e.g., token bridge deposits) will systematically exceed the bound, be marked consumed, and revert, with the underlying L1-side funds/state permanently unrecoverable since there is no sender-side mechanism to raise `l1_handler_max_amount_bounds` for a given message (unlike account transactions, which specify their own resource bounds).

### Impact Explanation
Medium/High: this results in permanent freezing of user funds sent via L1→L2 messaging (e.g., bridge deposits) whenever the actual required gas for the `l1_handler` entry point exceeds the hardcoded, non-per-message-adjustable bound. The message is irreversibly consumed on L1 while execution effects are rolled back on L2, and there is no sender-controlled way to retry with a higher bound — an L1 message sender has no lever to increase `l1_handler_max_amount_bounds`. This is the same class of failure the external report describes for BeaconKit/Infrared: a static local constant silently becoming inconsistent with real-world/protocol requirements, producing systematic, unrecoverable failures for legitimate operations.

### Likelihood Explanation
Low-to-Medium: `l1_handler_max_amount_bounds` is a versioned constant maintained by the Starknet core team and is presumably validated against known contracts at each version bump (similar caution as recommended in the report). However, it is a static, protocol-wide cap that does not scale with individual contracts' evolving `l1_handler` gas requirements (e.g., contract upgrades, added logic, larger calldata/payloads), so any future growth in legitimate L1-handler gas usage that outpaces a versioned-constant update reproduces exactly the "in-sync but in-flux" risk flagged in the report.

### Recommendation
- Treat `l1_handler_max_amount_bounds` as a governance/version-controlled parameter with an explicit process ensuring it is validated against known L1-handler contracts' evolving gas needs before each version release (analogous to the report's recommendation to convert `InfraredBERAConstants` into governance-adjustable state).
- Consider deriving/allowing a per-message gas bound tied to the fee actually paid on L1 (`paid_fee_on_l1`) rather than a single hardcoded ceiling, so senders who pay more can obtain higher bounds, similar to how account transactions specify their own resource bounds.
- Ensure `consume_l1_to_l2_message` semantics and the resulting "message consumed but reverted" outcome are clearly documented and monitored, so operators can detect when a bridge contract's L1-handler is nearing the hardcoded bound before it causes systematic freezing of user funds.

### Proof of Concept
1. An L1 bridge/messaging contract's `l1_handler` entry point is upgraded or grows in complexity such that its execution now legitimately requires more than `l1_handler_max_amount_bounds.l2_gas` (e.g., 100,000,000 in `blockifier_versioned_constants_0_14_4.json`).
2. A user calls the L1 bridge contract to send a message to L2 (e.g., depositing tokens), paying the required L1 fee.
3. The sequencer includes the resulting L1Handler transaction; `consume_l1_to_l2_message` records the message as consumed in the OS output [5](#0-4) .
4. Execution exceeds `l1_handler_max_amount_bounds`; `FeeCheckReport::check_all_gas_amounts_within_bounds` fails, the state changes are aborted, and the transaction is recorded as reverted [6](#0-5) .
5. The L1 message is nonetheless marked consumed (per step 3); the user's deposit/funds are lost with no path to re-trigger the L2-side mint/logic for that nonce, since resending requires a new L1 call and additional fee, and the underlying bound is not adjustable per message.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L443-448)
```text
    // Consume L1-to-L2 message.
    consume_l1_to_l2_message(execution_context=tx_execution_context, nonce=nonce);
    let remaining_gas = L1_HANDLER_L2_GAS_MAX_AMOUNT;
    non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
        block_context=block_context, execution_context=tx_execution_context
    );
```

**File:** crates/blockifier/src/transaction/l1_handler_transaction.rs (L63-72)
```rust
        let limit_steps_by_resources = false;
        let l1_handler_bounds =
            block_context.versioned_constants.os_constants.l1_handler_max_amount_bounds;

        let mut remaining_gas = l1_handler_bounds.l2_gas.0;
        let mut context = EntryPointExecutionContext::new_invoke(
            tx_context.clone(),
            limit_steps_by_resources,
            SierraGasRevertTracker::new(GasAmount(remaining_gas)),
        );
```

**File:** crates/blockifier/src/transaction/l1_handler_transaction.rs (L92-130)
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
                    Err(fee_check_error) => {
                        // Post-execution check failed. Revert the execution.
                        execution_state.abort();
                        let receipt = TransactionReceipt::reverted_l1_handler(
                            &tx_context,
                            l1_handler_payload_size,
                        );
                        Ok(l1_handler_tx_execution_info(
                            None,
                            receipt,
                            Some(fee_check_error.into()),
                        ))
                    }
                }
```

**File:** crates/blockifier/resources/blockifier_versioned_constants_0_14_4.json (L189-194)
```json
        "l1_handler_version": 0,
        "l1_handler_max_amount_bounds": {
            "l1_gas": 40000,
            "l1_data_gas": 20000,
            "l2_gas": 100000000
        },
```
