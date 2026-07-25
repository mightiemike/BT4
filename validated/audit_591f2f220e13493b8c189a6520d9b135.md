### Title
`distribute_gas` Floor Division Produces Zero-Gas `FunctionCallAction` That Fails Receipt Validation — (`runtime/runtime/src/receipt_manager.rs`)

### Summary

When a contract uses `promise_batch_action_function_call_weight` with a non-zero `gas_weight` but the floor-division gas distribution rounds the allocated gas to zero for a non-last entry, the resulting `FunctionCallAction` carries `gas = 0`. When `apply_action_receipt` subsequently validates the newly created receipt via `validate_receipt(..., NewReceipt)`, `validate_function_call_action` unconditionally rejects any action with `gas == Gas::ZERO` with `FunctionCallZeroAttachedGas`. This causes the entire function call execution to fail and all state changes to be rolled back, even though the contract supplied a valid non-zero `gas_weight` and the static gas parameter was legitimately set to zero (the documented way to rely purely on weight-based distribution).

### Finding Description

**Step 1 — Gas weight distribution with floor division**

In `distribute_gas`, the gas allocated to each non-last weighted function call is:

```rust
let to_assign =
    (u128::from(unused_gas.as_gas()) * weight.0 as u128 / gas_weight_sum) as u64;
``` [1](#0-0) 

This is integer floor division. When `weight` is small relative to `gas_weight_sum`, `to_assign` rounds to zero. The existing test explicitly documents this case:

```rust
// Weight over gas limit with three function calls
function_call_weight_check(&[
    (Gas::ZERO, 10_000_000_000, Gas::from_gas(4_999_999_999)),
    (Gas::ZERO, 1, Gas::ZERO),   // ← gets 0 gas after distribution
    (Gas::ZERO, 10_000_000_000, Gas::from_gas(5_000_000_001)),
]);
``` [2](#0-1) 

The last entry always receives the remainder, so it is safe. Only non-last entries are affected.

**Step 2 — The action is stored with `gas = 0`**

`append_action_function_call_weight` stores the action with `gas: prepaid_gas` (the static gas argument). When the static gas is `0` (the documented way to rely purely on weight) and `to_assign` is also `0`, the stored action has `gas = Gas::ZERO`:

```rust
Action::FunctionCall(Box::new(FunctionCallAction {
    ...
    gas: prepaid_gas,   // Gas::ZERO when static gas omitted
    ...
})),
``` [3](#0-2) 

**Step 3 — Receipt validation unconditionally rejects zero-gas function calls**

After the WASM execution completes, `apply_action_receipt` validates every newly created receipt with `validate_receipt(..., NewReceipt)`. This calls `validate_function_call_action`, which hard-rejects any `FunctionCallAction` with `gas == Gas::ZERO`:

```rust
if action.gas == Gas::ZERO {
    return Err(ActionsValidationError::FunctionCallZeroAttachedGas);
}
``` [4](#0-3) 

The spec confirms new receipts are validated at line 871 of `lib.rs`:

> "for each action compute an `action_hash`, call `apply_action`, and on success validate every newly created receipt with `validate_receipt(..., NewReceipt)`" [5](#0-4) 

**Step 4 — Execution fails and state is rolled back**

The `FunctionCallZeroAttachedGas` error propagates as an `ActionError`, causing `apply_action_receipt` to roll back all state changes from the function call. The caller's gas is burnt. The contract's intended cross-contract call is silently dropped.

### Impact Explanation

This is **contract execution flow breakage** reachable from ordinary user-submitted transactions. Any user who calls a contract that uses `promise_batch_action_function_call_weight` with multiple weighted calls can trigger this failure if the gas distribution rounds to zero for a non-last entry. The contract's state changes are rolled back, the cross-contract call is never sent, and the caller loses their gas. This matches the allowed impact gate: "contract execution flow breakage."

### Likelihood Explanation

The `promise_batch_action_function_call_weight` host function is the standard NEP-264 mechanism for distributing remaining gas across multiple outgoing calls. Any contract that schedules more than one weighted call where the weights are unequal and the total weight sum is large relative to the available gas can hit this. The nearcore documentation itself warns that "the amount of distributed gas to each action can be 0," but does not warn that this will cause a validation failure. The existing test suite confirms the 0-gas case is reachable but does not test the downstream validation path. [6](#0-5) 

### Recommendation

In `distribute_gas`, when `to_assign == 0` for a non-last entry that has a non-zero `gas_weight`, either:

1. **Assign at least 1 gas unit** to any non-last entry whose weight is non-zero (adjusting the remainder accordingly), or
2. **Skip the zero-gas action** from the receipt (do not emit a `FunctionCallAction` with `gas = 0`), or
3. **Relax `validate_function_call_action`** to permit `gas = 0` when the action was produced by weight-based distribution (requires a protocol-version gate).

Option 1 is the least invasive and mirrors the Velocimeter fix: "if the calculated fee is 0, do not send fees."

### Proof of Concept

A contract that triggers the bug:

```rust
// Contract method that schedules two weighted calls where the second gets 0 gas
pub fn trigger_zero_gas_bug(&mut self) {
    let promise = env::promise_batch_create("receiver.near");
    
    // First call: weight = u64::MAX (dominates the sum)
    env::promise_batch_action_function_call_weight(
        promise,
        "method_a",
        b"",
        0,          // static gas = 0
        0,          // deposit
        u64::MAX,   // gas_weight: huge, takes nearly all gas
    );
    
    // Second call: weight = 1 (gets floor(unused_gas * 1 / (u64::MAX + 1)) = 0)
    env::promise_batch_action_function_call_weight(
        promise,
        "method_b",
        b"",
        0,          // static gas = 0
        0,          // deposit
        1,          // gas_weight: tiny relative to sum
    );
    // After distribute_gas: method_b gets gas = 0
    // validate_receipt then returns FunctionCallZeroAttachedGas
    // → entire function call fails, state rolled back
}
```

The floor division at `receipt_manager.rs:679` produces `to_assign = 0` for the second entry. The resulting `FunctionCallAction` has `gas = Gas::ZERO`. The validation at `action_validation.rs:258` then rejects it, causing the function call to fail with `ActionError { kind: NewReceiptValidationError(ActionsValidation(FunctionCallZeroAttachedGas)) }`. [7](#0-6) [8](#0-7)

### Citations

**File:** runtime/runtime/src/receipt_manager.rs (L378-396)
```rust
        let action_index = self.append_action(
            receipt_index,
            Action::FunctionCall(Box::new(FunctionCallAction {
                method_name: String::from_utf8(method_name)
                    .map_err(|_| HostError::InvalidMethodName)?,
                args,
                gas: prepaid_gas,
                deposit: attached_deposit,
            })),
        );

        if gas_weight.0 > 0 {
            self.gas_weights.push((
                FunctionCallActionIndex { receipt_index: receipt_index as usize, action_index },
                gas_weight,
            ));
        }

        Ok(())
```

**File:** runtime/runtime/src/receipt_manager.rs (L654-695)
```rust
    pub(super) fn distribute_gas(&mut self, unused_gas: Gas) -> Result<Gas, RuntimeError> {
        let ReceiptManager {
            action_receipts,
            data_receipts: _,
            gas_weights,
            promise_yield_receipt_index: _,
        } = self;
        let gas_weight_sum: u128 = gas_weights.iter().map(|(_, gv)| u128::from(gv.0)).sum();
        if gas_weight_sum == 0 || unused_gas == Gas::ZERO {
            return Ok(Gas::ZERO);
        }
        let mut distributed = 0u64;
        let mut gas_weight_iterator = gas_weights.iter().peekable();
        loop {
            let Some((index, weight)) = gas_weight_iterator.next() else { break };
            let FunctionCallActionIndex { receipt_index, action_index } = *index;
            let Some(Action::FunctionCall(action)) = action_receipts
                .get_mut(receipt_index)
                .and_then(|receipt| receipt.actions.get_mut(action_index))
            else {
                panic!(
                    "Invalid function call index (promise_index={receipt_index}, action_index={action_index})",
                );
            };
            let to_assign =
                (u128::from(unused_gas.as_gas()) * weight.0 as u128 / gas_weight_sum) as u64;
            action.gas =
                action.gas.checked_add(Gas::from_gas(to_assign)).ok_or(IntegerOverflowError)?;
            distributed = distributed
                .checked_add(to_assign)
                .unwrap_or_else(|| panic!("gas computation overflowed"));
            if gas_weight_iterator.peek().is_none() {
                let remainder = unused_gas.as_gas().wrapping_sub(distributed);
                distributed = distributed
                    .checked_add(remainder)
                    .unwrap_or_else(|| panic!("gas computation overflowed"));
                action.gas =
                    action.gas.checked_add(Gas::from_gas(remainder)).ok_or(IntegerOverflowError)?;
            }
        }
        assert_eq!(unused_gas.as_gas(), distributed);
        Ok(Gas::from_gas(distributed))
```

**File:** runtime/runtime/src/receipt_manager.rs (L810-815)
```rust
        // Weight over gas limit with three function calls
        function_call_weight_check(&[
            (Gas::ZERO, 10_000_000_000, Gas::from_gas(4_999_999_999)),
            (Gas::ZERO, 1, Gas::ZERO),
            (Gas::ZERO, 10_000_000_000, Gas::from_gas(5_000_000_001)),
        ]);
```

**File:** runtime/runtime/src/action_validation.rs (L252-260)
```rust
fn validate_function_call_action(
    limit_config: &LimitConfig,
    action: &FunctionCallAction,
    current_protocol_version: ProtocolVersion,
    mode: ValidateReceiptMode,
) -> Result<(), ActionsValidationError> {
    if action.gas == Gas::ZERO {
        return Err(ActionsValidationError::FunctionCallZeroAttachedGas);
    }
```

**File:** protocol-model/spec/runtime-execution.md (L67-68)
```markdown
4. **Execute actions in order** (`runtime/runtime/src/lib.rs:848`): for each action compute an `action_hash`, call `apply_action`, and on success validate every newly created receipt with `validate_receipt(..., NewReceipt)` (`:871`). `merge` folds the result; on the first `Err` the loop records the action index and breaks (`runtime/runtime/src/lib.rs:884`).
5. If the receipt still succeeded, re-check receiver storage staking; a shortfall sets `LackBalanceForState` (`runtime/runtime/src/lib.rs:891`-`912`).
```

**File:** runtime/near-vm-runner/src/logic/logic.rs (L2942-2944)
```rust
    /// If the `gas_weight` parameter is set as a large value, the amount of distributed gas
    /// to each action can be 0 or a very low value because the amount of gas per weight is
    /// based on the floor division of the amount of gas by the sum of weights.
```
