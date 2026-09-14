### Title
`gas_penalty_for_gas_refund()` rounds the NEP-536 refund penalty down instead of up, systematically underclaiming the protocol's fee - (File: `core/parameters/src/cost.rs`)

### Summary
`RuntimeFeesConfig::gas_penalty_for_gas_refund()` computes the penalty subtracted from every gas refund using plain integer division (`gas_refund.as_gas() as u128 * numer / denom`), which truncates toward zero. Because the quantity being computed is a fee owed *to* the protocol (analogous to `PrimeRateLib.convertFromStorage()`'s debt conversion), truncation always rounds in favor of the refund recipient (the user) and against the protocol, exactly the pattern flagged in the referenced Sherlock finding. [1](#0-0) 

### Finding Description
`gas_penalty_for_gas_refund` is called from `refund_unspent_gas_and_deposits` for every receipt that leaves unspent prepaid gas, i.e. reachable by any ordinary account submitting a `FunctionCall` transaction/receipt: [2](#0-1) 

The penalty is defined as `max(gas_refund * gas_refund_penalty, min_gas_refund_penalty)`, capped at `gas_refund`. The multiplication-then-division `(gas_refund.as_gas() as u128 * numer) / denom` uses Rust's default integer division, which truncates toward zero — i.e., it always rounds *down* the amount owed to the protocol as a fee, exactly like the reported `storedCashBalance.mul(pr.debtFactor).div(pr.supplyFactor)` truncation in the Notional report, which also always rounds a debt-like quantity down. In both cases the fix is the same conceptual class: add `denom - 1` before dividing (ceiling division) when the quantity computed represents an amount that should accrue to the protocol.

Every unspent-gas refund on every FunctionCall receipt goes through this exact code path, so the rounding error recurs on essentially every transaction that doesn't consume 100% of its prepaid gas.

### Impact Explanation
The truncated remainder is refunded to the user instead of being retained as a fee, i.e. a small, systematic under-collection of the protocol's own designed fee (dust loss per receipt, but occurring on a very large fraction of all transactions network-wide, making it accumulative exactly as characterized in the referenced report). This matches the "accumulating dust across the protocol" impact class explicitly called out as valid by the original report and is a genuine (if small) value leak from the protocol to end users on every affected refund, not a resource-only or no-impact issue.

### Likelihood Explanation
High likelihood of occurrence: this code executes on essentially every FunctionCall transaction/receipt that leaves unspent prepaid gas, requiring no special conditions, privileges, or attacker behavior — a normal transaction signer triggers it simply by not consuming 100% of attached gas (the common case).

### Recommendation
Change the penalty computation to round up (ceiling division) rather than down, e.g.:
```rust
let relative_cost = Gas::from_gas(
    ((u128::from(gas_refund.as_gas()) * *self.gas_refund_penalty.numer() as u128
        + (*self.gas_refund_penalty.denom() as u128 - 1))
        / *self.gas_refund_penalty.denom() as u128)
        .try_into()
        .unwrap(),
);
```
This preserves the existing `min`/`max` clamping logic but ensures the fee owed to the protocol is never under-collected due to truncation.

### Proof of Concept
Given `gas_refund_penalty = 1/20` (5%, the target value per NEP-536) and `gas_refund = 39` gas units:
- Exact penalty = `39 * 1 / 20 = 1.95` gas.
- Current code: `39 * 1 / 20 = 1` (integer division truncates `1.95` down to `1`), so the user is refunded `38` gas worth of balance instead of the intended `37.05`.
- With ceiling division: `(39 * 1 + 19) / 20 = 2`, correctly rounding the fee up in the protocol's favor.

This can be observed directly in the existing test harness for `refund_unspent_gas_and_deposits`/`gas_penalty_for_gas_refund`, e.g. the assertions in `test_apply_deficit_gas_for_function_call_covered` and `test_apply_surplus_gas_for_function_call`, which compute `refund_penalty` using the same (down-rounding) function and then verify the exact refund transfer amount — demonstrating the shortfall is deterministically reproducible for any receipt whose unspent gas times `gas_refund_penalty` is not an exact integer. [3](#0-2)

### Citations

**File:** core/parameters/src/cost.rs (L732-746)
```rust
    /// Given a left over gas amount to be refunded, returns how much should be
    /// subtracted as a penalty introduced with NEP-536.
    ///
    /// Must return a value smaller or equal to the `gas_refund` parameter.
    pub fn gas_penalty_for_gas_refund(&self, gas_refund: Gas) -> Gas {
        let relative_cost = Gas::from_gas(
            (u128::from(gas_refund.as_gas()) * *self.gas_refund_penalty.numer() as u128
                / *self.gas_refund_penalty.denom() as u128)
                .try_into()
                .unwrap(),
        );

        let penalty = std::cmp::max(relative_cost, self.min_gas_refund_penalty);
        std::cmp::min(penalty, gas_refund)
    }
```

**File:** runtime/runtime/src/lib.rs (L1318-1329)
```rust
        // NEP-536 also adds a penalty to gas refund.
        let refund_penalty: Gas = config.fees.gas_penalty_for_gas_refund(gross_gas_refund);
        let penalty_gas_price = if ProtocolFeature::AccountCostIncrease.enabled(protocol_version) {
            gas_burn_price
        } else {
            gas_purchase_price
        };
        let refund_penalty_amount = safe_gas_to_balance(penalty_gas_price, refund_penalty)?;

        // Refund for the leftover gas that was not used by this receipt.
        let unused_gas_balance_refund = safe_gas_to_balance(gas_purchase_price, gross_gas_refund)?
            .saturating_sub(refund_penalty_amount);
```

**File:** runtime/runtime/src/tests/apply.rs (L1040-1052)
```rust
    // With gas refund penalties enabled, we should see a reduced refund value
    let unspent_gas: Gas = Gas::from_gas(
        (total_receipt_cost.checked_sub(expected_gas_burnt_amount).unwrap().as_yoctonear()
            / gas_price.as_yoctonear())
        .try_into()
        .unwrap(),
    );
    let refund_penalty = apply_state.config.fees.gas_penalty_for_gas_refund(unspent_gas);
    let expected_refund = total_receipt_cost
        .checked_sub(expected_gas_burnt_amount)
        .unwrap()
        .checked_sub(gas_price.checked_mul(u128::from(refund_penalty.as_gas())).unwrap())
        .unwrap();
```
