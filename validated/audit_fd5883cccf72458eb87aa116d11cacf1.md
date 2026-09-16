Based on my research, I found a legitimate structural analog in the sequencer's `L1HandlerTransaction` execution path, which mirrors the report's core bug class: a **hardcoded, non-user-configurable gas/resource buffer used both as the execution budget and the post-execution acceptance threshold for finalizing an L1→L2 message**, without margin proportional to the actual worst-case execution cost — and unlike ordinary transactions, an L1 handler failing this check causes a **state rollback of an already-irrevocable L1 message consumption**, i.e., potential permanent loss of bridged funds.

### Title
Insufficient / Hardcoded `l1_handler_max_amount_bounds` Can Cause Legitimate L1→L2 Message Finalization To Revert, Permanently Freezing Bridged Funds - (File: `crates/blockifier/src/transaction/l1_handler_transaction.rs`)

### Summary
`L1HandlerTransaction::execute_raw` uses a single, protocol-fixed `l1_handler_max_amount_bounds` (from `VersionedConstants`) both as the initial gas budget for executing the L1-triggered call and as the post-execution ceiling that the actual resources consumed (`l1_gas`, `l1_data_gas`, `l2_gas`) must not exceed. This buffer is not derived from, or scalable by, the L1 sender in any way (unlike normal V3 transactions where the sender sets their own `resource_bounds`). If actual resource usage exceeds these fixed bounds, the execution is aborted/reverted [1](#0-0) , yet the corresponding L1→L2 message was already consumed on L1 before this L2 transaction runs — Starknet L1→L2 messaging has no automatic re-delivery mechanism, so a revert here can permanently strand the funds/state update the message was meant to deliver (e.g., a bridge deposit credit). This is directly analogous to the reported issue where a fixed gas buffer (`RELAY_RESERVED_GAS`/`RELAY_GAS_CHECK_BUFFER`) added for the original Optimism `relayMessage` logic was not adjusted for extra added instructions, causing legitimate cross-domain messages to fail unexpectedly and preventing deposit/withdrawal finalization.

### Finding Description
`execute_raw` sets the entry-point execution's initial gas directly from the fixed bound: `let mut remaining_gas = l1_handler_bounds.l2_gas.0;` [2](#0-1) . After the call executes and a receipt is computed, `FeeCheckReport::check_all_gas_amounts_within_bounds` is applied against that same fixed bound for all three resources (`l1_gas`, `l2_gas`, `l1_data_gas`) [3](#0-2) . If any resource exceeds the bound, the state changes are aborted and the transaction is recorded with a `RevertError::PostExecution(FeeCheckError::MaxGasAmountExceeded)` [4](#0-3) , confirmed by the existing test `test_l1_handler_resource_bounds` [5](#0-4) .

Critically, this bound has been tightened by multiple orders of magnitude across protocol versions: from `l1_gas: 10000000000, l1_data_gas: 10000000000, l2_gas: 10000000000` in versions up to `0.13.6` [6](#0-5)  down to `l1_gas: 40000, l1_data_gas: 20000, l2_gas: 100000000` starting in `0.14.0` [7](#0-6) . In particular, the `l1_data_gas` bound of `20000` is extremely tight — the DA/state-diff gas cost model charges `GAS_PER_MEMORY_WORD` (128) or blob-based DA costs per changed storage word plus fixed per-message overhead (`GAS_PER_ZERO_TO_NONZERO_STORAGE_SET` = 20000, `GAS_PER_COUNTER_DECREASE` per consumed message) [8](#0-7) [9](#0-8) , so a bridge/message handler that updates even a couple of storage slots under KZG DA accounting can plausibly exceed this bound depending on state-diff size. There is no mechanism for the L1 sender or the handler contract to raise this ceiling for a specific message; it is a single global protocol constant applied uniformly to every `l1_handler` transaction network-wide, just as `RELAY_RESERVED_GAS`/`RELAY_GAS_CHECK_BUFFER` was a single global constant applied uniformly to every relayed message in the Mantle report, without being re-derived after additional logic (approve calls) was layered on top.

### Impact Explanation
Because the L1 message is already irrevocably marked "consumed" on L1 before the corresponding `l1_handler` transaction executes on L2 (Starknet messaging model has no re-queue/retry for a message once consumed), a post-execution revert due to exceeding the fixed `l1_handler_max_amount_bounds` results in the L2-side effect of the message (e.g., crediting a token deposit, applying a governance action) being permanently lost while the L1-side message can never be resent. This is a concrete, permanent freezing/loss-of-funds scenario reachable purely by a message originating from L1 (an allowed unprivileged entity per this analysis' scope) whose handler happens to touch slightly more state or emit calldata than the network's fixed, non-adjustable buffer allows.

### Likelihood Explanation
Likelihood is moderate: it requires an `l1_handler` entry point whose resource usage is close to or exceeds the fixed bound (more plausible for `l1_data_gas` given its comparatively small 20000 ceiling introduced in 0.14.0, and for handlers with larger payloads/calldata or multiple state writes). It does not require any malicious actor — a legitimate bridge/message with a marginally larger-than-typical payload or state footprint is sufficient to trigger it, and the versioned-constants history shows this bound has already been reduced drastically (10¹⁰ → 4×10⁴/2×10⁴/10⁸), increasing the chance that some legitimate handler logic now falls outside the new bound.

### Recommendation
Re-derive `l1_handler_max_amount_bounds` (particularly `l1_data_gas`) with an explicit safety margin that accounts for realistic worst-case DA/state-diff costs of `l1_handler` entry points (multiple storage writes, larger payloads), similarly to how the report recommends revisiting `RELAY_RESERVED_GAS`/`RELAY_GAS_CHECK_BUFFER` to account for the actual worst-case cost of the added logic. Consider decoupling the L1_data_gas bound from a single global constant, or providing telemetry/alerts when `l1_handler` transactions are close to/exceed the bound so protocol constants can be tuned proactively before real deposits are affected, and document/verify that no legitimate current handler logic (e.g., StarkGate deposit handlers) can realistically exceed the new tighter bounds under KZG DA.

### Proof of Concept
1. Deploy an `l1_handler` entry point that writes to several distinct storage keys (e.g., updating a mapping/balance plus an aggregate counter) and accepts a moderately sized payload, similar to `test_l1_handler_resource_bounds`'s pattern of setting a custom (lower) bound and observing the built-in test contract exceed it [5](#0-4) .
2. Send the corresponding message from L1 so the message is consumed on L1 (irrevocable) and the `l1_handler` transaction is created with `l1_handler_max_amount_bounds` from `0.14.x` versioned constants (`l1_data_gas: 20000`) [7](#0-6) .
3. Observe that `check_all_gas_amounts_within_bounds` rejects the receipt because the actual `l1_data_gas` (driven by the state diff from the storage writes) exceeds `20000` [3](#0-2) , the state change is aborted [4](#0-3) , and the funds/effect that the L1 message intended to deliver are permanently lost since the L1 message cannot be resent.

### Citations

**File:** crates/blockifier/src/transaction/l1_handler_transaction.rs (L64-72)
```rust
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

**File:** crates/blockifier/src/fee/fee_checks.rs (L128-149)
```rust
    pub fn check_all_gas_amounts_within_bounds(
        max_amount_bounds: &GasVector,
        gas_vector: &GasVector,
    ) -> FeeCheckResult<()> {
        // TODO(Arni): Consider refactoring the returned error. The first failed check will hide
        // future checks.
        for (resource, max_amount, actual_amount) in [
            (L1Gas, max_amount_bounds.l1_gas, gas_vector.l1_gas),
            (L2Gas, max_amount_bounds.l2_gas, gas_vector.l2_gas),
            (L1DataGas, max_amount_bounds.l1_data_gas, gas_vector.l1_data_gas),
        ] {
            if max_amount < actual_amount {
                return Err(FeeCheckError::MaxGasAmountExceeded {
                    resource,
                    max_amount,
                    actual_amount,
                });
            }
        }

        Ok(())
    }
```

**File:** crates/blockifier/src/transaction/transactions_test.rs (L2964-3008)
```rust
#[rstest]
#[case(L1Gas, GasAmount(1))]
// Sufficient to pass execution (enough gas to run the transaction), but fails post-execution
// resource bounds check.
#[case(L2Gas, GasAmount(200000))]
#[case(L1DataGas, GasAmount(1))]
fn test_l1_handler_resource_bounds(#[case] resource: Resource, #[case] new_bound: GasAmount) {
    let test_contract = FeatureContract::TestContract(CairoVersion::Cairo1(RunnableCairo1::Casm));

    // Set to true to ensure L1 data gas is non-zero.
    let use_kzg_da = true;

    let mut block_context = BlockContext::create_for_account_testing_with_kzg(use_kzg_da);
    let chain_info = block_context.chain_info.clone();
    let mut state = test_state(&chain_info, BALANCE, &[(test_contract, 1)]);
    let contract_address = test_contract.get_instance_address(0);

    // Modify the resource bound for the tested resource.
    let os_constants = Arc::make_mut(&mut block_context.versioned_constants.os_constants);
    match resource {
        L1Gas => os_constants.l1_handler_max_amount_bounds.l1_gas = new_bound,
        L2Gas => os_constants.l1_handler_max_amount_bounds.l2_gas = new_bound,
        L1DataGas => os_constants.l1_handler_max_amount_bounds.l1_data_gas = new_bound,
    }

    let tx = l1handler_tx(Fee(1), contract_address);

    let execution_info = tx.execute(&mut state, &block_context).unwrap();

    assert_matches!(
        execution_info,
        TransactionExecutionInfo {
            validate_call_info: None,
            execute_call_info: None,
            fee_transfer_call_info: None,
            revert_error: Some(RevertError::PostExecution(FeeCheckError::MaxGasAmountExceeded {
                resource: r,
                max_amount,
                actual_amount
            })),
            // TODO(Arni): consider checking other fields of the receipt.
            receipt: TransactionReceipt { fee, .. },
        } if r == resource && new_bound == max_amount && actual_amount > max_amount && fee == Fee(0)
    );
}
```

**File:** crates/blockifier/resources/blockifier_versioned_constants_0_13_6.json (L184-189)
```json
        "l1_handler_version": 0,
        "l1_handler_max_amount_bounds": {
            "l1_gas": 10000000000,
            "l1_data_gas": 10000000000,
            "l2_gas": 10000000000
        },
```

**File:** crates/blockifier/resources/blockifier_versioned_constants_0_14_0.json (L184-189)
```json
        "l1_handler_version": 0,
        "l1_handler_max_amount_bounds": {
            "l1_gas": 40000,
            "l1_data_gas": 20000,
            "l2_gas": 100000000
        },
```

**File:** crates/blockifier/src/fee/eth_gas_constants.rs (L12-17)
```rust
// Storage.
pub const GAS_PER_ZERO_TO_NONZERO_STORAGE_SET: usize = 20000;
pub const GAS_PER_COLD_STORAGE_ACCESS: usize = 2100;
pub const GAS_PER_NONZERO_TO_INT_STORAGE_SET: usize = 2900;
pub const GAS_PER_COUNTER_DECREASE: usize =
    GAS_PER_COLD_STORAGE_ACCESS + GAS_PER_NONZERO_TO_INT_STORAGE_SET;
```

**File:** crates/blockifier/src/fee/resources.rs (L404-422)
```rust
    pub fn get_starknet_gas_cost(&self) -> GasVector {
        let n_l2_to_l1_messages = self.l2_to_l1_payload_lengths.len();
        let n_l1_to_l2_messages = usize::from(self.l1_handler_payload_size.is_some());

        [
            GasVector::from_l1_gas(
                // Starknet's updateState gets the message segment as an argument.
                u64_from_usize(
                    self.message_segment_length * eth_gas_constants::GAS_PER_MEMORY_WORD
                // Starknet's updateState increases a (storage) counter for each L2-to-L1 message.
                + n_l2_to_l1_messages * eth_gas_constants::GAS_PER_ZERO_TO_NONZERO_STORAGE_SET
                // Starknet's updateState decreases a (storage) counter for each L1-to-L2 consumed
                // message (note that we will probably get a refund of 15,000 gas for each consumed
                // message but we ignore it since refunded gas cannot be used for the current
                // transaction execution).
                + n_l1_to_l2_messages * eth_gas_constants::GAS_PER_COUNTER_DECREASE,
                )
                .into(),
            ),
```
