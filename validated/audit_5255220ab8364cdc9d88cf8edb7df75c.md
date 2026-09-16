### Title
L1Handler transactions can under-compensate the sequencer for L1/DA and execution costs — only a non-zero `paid_fee_on_l1` is enforced, not sufficiency - (File: crates/blockifier/src/transaction/l1_handler_transaction.rs)

### Summary
Analogous to the Perennial H-6 finding, where keepers were compensated using a fixed, execution-only formula that can diverge arbitrarily from the true (dynamic) L1 cost they must pay, the Starknet sequencer's `L1HandlerTransaction::execute_raw` accepts *any* non-zero `paid_fee_on_l1` value as sufficient to cover the transaction's real, computed cost (`receipt.fee`, derived from actual L1 gas, L1 data gas, and L2 gas consumption). The comparison against the actual cost is explicitly not performed on the sequencer side.

### Finding Description
When an L1 sender calls `sendMessageToL2` on the Starknet core contract, it produces an `L1HandlerTransaction` that the sequencer must execute and, in KZG/blob DA mode, pay to publish to L1 as part of the block's data availability commitment [1](#0-0) . The fee actually incurred for processing this transaction — a mix of L1 gas, L1 data gas (DA), and L2 gas priced by `GasPriceVector` — is computed as `receipt.fee` via the standard `GasVector::cost` machinery used everywhere else in the fee system [2](#0-1) .

However, in `execute_raw` for `L1HandlerTransaction`, after computing the receipt and passing bounds checks, the code only verifies that the amount paid on L1 is non-zero, not that it covers `receipt.fee`: [3](#0-2) 

The comment explicitly documents this as an intentional relaxation ("For now, assert only that any amount of fee was paid... TODO(Arni): Consider removing this check. It is covered by the starknet core contract."), meaning the sequencer/blockifier code path in this repo does not itself guarantee compensation matches cost — it defers that guarantee to an off-repo L1 contract. There is no other comparison between `paid_fee_on_l1` and `receipt.fee` anywhere in the transaction lifecycle in this repo (only `InsufficientFee` is raised when `paid_fee == Fee(0)`) [4](#0-3) .

Execution size (and thus the true L1/DA/L2 cost the sequencer bears) is only capped by the generous `l1_handler_max_amount_bounds` constant (a fixed per-resource ceiling, not tied to the fee actually paid), so an L1 sender can pay a minimal (e.g. 1-wei) fee while consuming up to the full bound in L2 execution steps and L1 data-availability bytes, exactly mirroring the Perennial pattern where a fixed/mismatched compensation parameter (`buffer`) could not track the true, independently varying cost.

### Impact Explanation
This is the mirror image of the keeper-compensation bug: instead of the *executor* being under-compensated for variable off-chain costs by a fixed on-chain formula, here the *sequencer* (the entity that must actually pay for L1 data-availability/blob publication of the resulting state diff) can be forced to absorb execution and DA costs that are not covered by the fee paid on L1, because the sufficiency check was deliberately weakened to "non-zero only." Repeated or automated abuse (many L1 handler messages each paying 1 wei) causes the sequencer to under-recover the real L1 posting cost for those transactions, a direct economic loss to the network operator, analogous to the "no incentive to submit/execute" and "drain funds via minimal self-payment" scenarios highlighted in the H-6 escalation.

### Likelihood Explanation
This is trivially reachable by any L1 account: `sendMessageToL2` is a normal, permissionless L1 contract call, and paying a nominal non-zero fee (any wei amount) is sufficient per this code path. No sequencer, prover, or node-operator collusion is required — a single unprivileged L1 message sender can trigger it. The relaxation is also explicitly acknowledged in the source comment as pending re-evaluation, indicating the authors are aware the invariant is not enforced on the L2/sequencer side and currently rely entirely on an L1 contract (outside this repo) — which this analysis cannot verify enforces adequate fee sufficiency, only non-zero payment presence.

### Recommendation
Enforce `paid_fee_on_l1 >= receipt.fee` (or a documented safety margin) directly in `L1HandlerTransaction::execute_raw`, reverting/rejecting the transaction (or charging the difference against a protocol-controlled account) when the L1-side prepayment does not cover the actual, dynamically-priced L1/DA/L2 cost — removing sequencer-side reliance on an unverifiable L1-side invariant, consistent with how V3 account transactions validate resource-bound sufficiency (`check_fee_bounds`) before execution [5](#0-4) .

### Proof of Concept
1. An L1 contract calls `sendMessageToL2` with `msg.value` set to `1 wei` (minimal, but non-zero) and a payload sized to approach `l1_handler_max_amount_bounds` (e.g., large calldata triggering many storage writes and a sizable DA segment) [6](#0-5) .
2. The resulting `L1HandlerTransaction` is executed; `receipt.fee` is computed from the real L1 gas / L1 data gas / L2 gas prices at execution time and can be arbitrarily larger than the 1-wei payment [7](#0-6) .
3. `execute_raw` only checks `paid_fee_on_l1 != Fee(0)`, so the transaction is accepted and committed despite `receipt.fee` far exceeding `paid_fee` [3](#0-2) .
4. Repeating this from many L1 accounts imposes unrecovered L1 data-availability/execution costs on the sequencer, with no on-repo mechanism preventing or penalizing it.

**Caveat**: Whether the referenced "starknet core contract" (mentioned in the code comment, external to this repo) actually enforces fee sufficiency at message-send time could not be verified from this codebase; if it does not, this finding is directly exploitable as described. If it does, this is at minimum a defense-in-depth gap explicitly flagged as a TODO by the code owners.

### Citations

**File:** crates/apollo_base_layer_tests/src/anvil_base_layer.rs (L281-308)
```rust
/// Converts a given [L1 handler transaction](starknet_api::transaction::L1HandlerTransaction)
/// to match the interface of the given [starknet l1 contract](StarknetL1Contract), and
/// triggers the L1 entry point, which sends the message to L2.
pub async fn send_message_to_l2(
    starknet_core_contract: &StarknetL1Contract,
    l1_handler: &L1HandlerTransaction,
) -> TransactionReceipt {
    const PAID_FEE_ON_L1: U256 = U256::from_be_slice(b"paid"); // Arbitrary value.

    let l2_contract_address = l1_handler.contract_address.0.key().to_hex_string().parse().unwrap();
    let l2_entry_point = l1_handler.entry_point_selector.0.to_hex_string().parse().unwrap();

    // The calldata of an L1 handler transaction consists of the L1 sender address followed by
    // the transaction payload. We remove the sender address to extract the message
    // payload.
    let payload =
        l1_handler.calldata.0[1..].iter().map(|x| x.to_hex_string().parse().unwrap()).collect();
    let msg = starknet_core_contract.sendMessageToL2(l2_contract_address, l2_entry_point, payload);

    msg
        // Sets a non-zero fee to be paid on L1.
        .value(PAID_FEE_ON_L1)
        // Sends the transaction to the Starknet L1 contract. For debugging purposes, replace
        // `.send()` with `.call_raw()` to retrieve detailed error messages from L1.
        .send().await.expect("Transaction submission to Starknet L1 contract failed.")
        // Waits until the transaction is received on L1 and then fetches its receipt.
        .get_receipt().await.expect("Transaction was not received on L1 or receipt retrieval failed.")
}
```

**File:** crates/starknet_api/src/execution_resources.rs (L155-186)
```rust
    /// Computes the cost (in fee token units) of the gas vector (panicking on overflow).
    pub fn cost(&self, gas_prices: &GasPriceVector, tip: Tip) -> Fee {
        let tipped_l2_gas_price =
            gas_prices.l2_gas_price.checked_add(tip.into()).unwrap_or_else(|| {
                panic!(
                    "Tip overflowed: addition of L2 gas price ({}) and tip ({}) resulted in \
                     overflow.",
                    gas_prices.l2_gas_price, tip
                )
            });

        let mut sum = Fee(0);
        for (gas, price, resource) in [
            (self.l1_gas, gas_prices.l1_gas_price, Resource::L1Gas),
            (self.l1_data_gas, gas_prices.l1_data_gas_price, Resource::L1DataGas),
            (self.l2_gas, tipped_l2_gas_price, Resource::L2Gas),
        ] {
            let cost = gas.checked_mul(price.get()).unwrap_or_else(|| {
                panic!(
                    "{resource} cost overflowed: multiplication of gas amount ({gas}) by price \
                     per unit ({price}) resulted in overflow."
                )
            });
            sum = sum.checked_add(cost).unwrap_or_else(|| {
                panic!(
                    "Total cost overflowed: addition of current sum ({sum}) and cost of \
                     {resource} ({cost}) resulted in overflow."
                )
            });
        }
        sum
    }
```

**File:** crates/blockifier/src/transaction/l1_handler_transaction.rs (L82-96)
```rust
                let receipt = TransactionReceipt::from_l1_handler(
                    &tx_context,
                    l1_handler_payload_size,
                    CallInfo::summarize_many(
                        execute_call_info.iter(),
                        &block_context.versioned_constants,
                    ),
                    &execution_state.to_state_diff()?,
                );

                // Enforce resource bounds.
                let fee_check_report = FeeCheckReport::check_all_gas_amounts_within_bounds(
                    &l1_handler_bounds,
                    &receipt.gas,
                );
```

**File:** crates/blockifier/src/transaction/l1_handler_transaction.rs (L98-116)
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
                    }
```

**File:** crates/blockifier/src/transaction/errors.rs (L1-20)
```rust
use cairo_vm::types::errors::program_errors::ProgramError;
use num_bigint::BigUint;
use starknet_api::block::GasPrice;
use starknet_api::core::{ClassHash, ContractAddress, EntryPointSelector, Nonce};
use starknet_api::execution_resources::GasAmount;
use starknet_api::transaction::fields::{AllResourceBounds, Fee, Resource};
use starknet_api::transaction::TransactionVersion;
use starknet_api::StarknetApiError;
use starknet_types_core::felt::FromStrError;
use thiserror::Error;

use crate::bouncer::BouncerWeights;
use crate::execution::call_info::Retdata;
use crate::execution::errors::{
    AnnotatedEntryPointExecutionError,
    ConstructorEntryPointExecutionError,
};
use crate::execution::stack_trace::{gen_tx_execution_error_trace, Cairo1RevertSummary};
use crate::fee::fee_checks::FeeCheckError;
use crate::state::errors::StateError;
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L374-397)
```rust
    fn check_fee_bounds(
        &self,
        tx_context: &TransactionContext,
    ) -> TransactionPreValidationResult<()> {
        let minimal_gas_amount_vector = estimate_minimal_gas_vector(
            &tx_context.block_context,
            self,
            &tx_context.get_gas_vector_computation_mode(),
        );
        let TransactionContext { block_context, tx_info } = tx_context;
        let block_info = &block_context.block_info;
        let fee_type = &tx_info.fee_type();
        match tx_info {
            TransactionInfo::Current(context) => {
                let resources_amount_tuple = match &context.resource_bounds {
                    ValidResourceBounds::L1Gas(l1_gas_resource_bounds) => vec![(
                        L1Gas,
                        l1_gas_resource_bounds,
                        minimal_gas_amount_vector.to_l1_gas_for_fee(
                            tx_context.get_gas_prices(),
                            &tx_context.block_context.versioned_constants,
                        ),
                        block_info.gas_prices.l1_gas_price(fee_type),
                    )],
```
