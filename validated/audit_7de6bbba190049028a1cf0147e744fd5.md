After thorough investigation of the nearcore codebase, I have identified a structural analog to the FrankenDAO bug.

---

### Title
Gas Refund Computed with Current Fee Schedule Instead of Fee Schedule at Transaction Time — (`runtime/runtime/src/lib.rs`)

### Summary

`refund_unspent_gas_and_deposits` re-computes `prepaid_exec_gas` and `prepaid_gas` (which includes `total_prepaid_send_fees`) using the **current protocol version's fee schedule** at receipt execution time. However, the signer paid for those fees at transaction time using the fee schedule that was active then. When a protocol upgrade changes the fee schedule between transaction submission and receipt execution, the refund calculation diverges from what was actually charged, causing a balance mismatch.

### Finding Description

**At transaction time** (`runtime/runtime/src/config.rs`, `calculate_tx_cost`):

```
gas_remaining = total_prepaid_gas
              + total_prepaid_send_fees(config_OLD, actions)   // sub-receipt send fees
              + total_prepaid_exec_fees(config_OLD, actions)   // action exec fees
              + receipt_cost_OLD
```

The signer pays `gas_remaining × receipt_gas_price` and this amount is deducted from their balance. The `receipt_gas_price` is stored in the `ActionReceipt`, but **`gas_remaining` itself is not stored** — only the actions are. [1](#0-0) 

**At receipt execution time** (`runtime/runtime/src/lib.rs`, `refund_unspent_gas_and_deposits`):

```rust
let prepaid_gas = total_prepaid_gas(&action_receipt.actions())?
    .checked_add(total_prepaid_send_fees(config, &action_receipt.actions())?.gas)  // uses config_NEW
    .ok_or(IntegerOverflowError)?;
let prepaid_exec_gas =
    total_prepaid_exec_fees(config, &action_receipt

### Citations

**File:** runtime/runtime/src/config.rs (L447-456)
```rust
    let prepaid_gas = total_prepaid_gas(actions)?;
    // Send/Exec costs for actions inside the receipt
    let prepaid_send_fee = total_prepaid_send_fees(config, actions)?;
    let prepaid_exec_fee = total_prepaid_exec_fees(config, actions, receiver_id)?;
    // Exec cost for the receipt that wraps the actions
    let receipt_cost = fees.fee(ActionCosts::new_action_receipt).exec_fee();
    let gas_remaining = prepaid_gas
        .checked_add_result(prepaid_send_fee.gas)?
        .checked_add_result(receipt_cost.gas)?
        .checked_add_result(prepaid_exec_fee.gas)?;
```
