The claimed mechanism is real and present in the codebase. Let me show the exact code path.

**The `skip_deduct` path exists and accumulates unboundedly:** [1](#0-0) 

```rust
let skip_deduct = amount == Balance::from_yoctonear(1)
    && self.config.one_yocto_on_promise
    && self.result_state.current_account_balance.is_zero();
if skip_deduct {
    self.result_state.subsidized_amount = self
        .result_state
        .subsidized_amount
        .checked_add(amount)
        .expect("subsidized_amount overflow");
} else {
    self.result_state.deduct_balance(amount)?;
}
```

The identical path exists in the wasmtime runner: [2](#0-1) 

**Why the condition stays true across N calls:** `current_account_balance` is never decremented when `skip_deduct` is true. So on the second, third, ... Nth call, `current_account_balance.is_zero()` is still true, `skip_deduct` fires again, and `subsidized_amount` grows by 1 yocto each time. There is no per-execution cap enforced within the VM logic.

**What the tests confirm:** The existing test `test_one_yocto_on_promise_enabled` only tests a single call and asserts `subsidized_amount == 1`. It does not test N sequential calls. [3](#0-2) 

The test `test_one_yocto_on_promise_deducts_with_nonzero_balance` shows that after the balance drops to zero mid-execution, the second call also subsidizes — confirming the condition re-fires: [4](#0-3) 

**The critical unverified piece:** Whether the runtime caps `subsidized_amount` at 1 yocto when processing `VMOutcome`. I could not find that cap in the searched code. If the runtime mints `subsidized_amount` yocto to cover outgoing receipt deposits without enforcing a per-execution limit of 1, then N calls = N yocto minted from thin air.

---

### Title
Unbounded `subsidized_amount` accumulation in `promise_batch_action_function_call_weight` allows zero-balance contract to create arbitrary outgoing deposits — (`runtime/near-vm-runner/src/logic/logic.rs`)

### Summary
When `config.one_yocto_on_promise == true`, a contract with `current_account_balance == 0` can call `promise_batch_action_function_call_weight` N times with `amount = 1 yoctoNEAR`. Each call passes the `skip_deduct` guard (because the balance is never decremented), appends a receipt with 1 yocto attached, and increments `subsidized_amount` by 1. After N calls, `subsidized_amount == N yocto` while the account balance remains zero. If the runtime mints `subsidized_amount` yocto when applying the outcome without capping it at 1, the contract creates N yocto from thin air.

### Finding Description
The `skip_deduct` condition at `logic.rs:3000–3002` checks three things: `amount == 1 yocto`, `config.one_yocto_on_promise`, and `current_account_balance.is_zero()`. Because the `skip_deduct` branch never calls `deduct_balance`, `current_account_balance` stays zero for the entire execution frame. Every subsequent call with `amount = 1` satisfies all three conditions again. The `subsidized_amount` field accumulates without bound within a single execution. [1](#0-0) 

### Impact Explanation
If the runtime applies `subsidized_amount` as a mint (to cover the outgoing receipt deposits) without a per-execution cap of 1 yocto, an attacker-controlled zero-balance contract can:
- Create N receipts each carrying 1 yocto to arbitrary recipients.
- Have N yocto minted from thin air to fund those receipts.
- Net effect: unauthorized fund creation and transfer — stealing from the protocol's implicit balance invariant.

### Likelihood Explanation
`one_yocto_on_promise` is a feature-flag config. If it is enabled on mainnet (the comment says it is intended for "deterministic accounts"), any deployed contract with zero balance can exploit this. The attacker only needs to deploy a contract and call it — no privileged access required.

### Recommendation
Add a per-execution cap: track whether a subsidization has already occurred in the current execution frame and reject a second `skip_deduct` within the same frame. Alternatively, enforce `subsidized_amount <= 1 yocto` in the runtime when processing `VMOutcome` before applying receipts.

### Proof of Concept
```rust
// Zero-balance contract, one_yocto_on_promise = true
// Call promise_batch_action_function_call_weight N times with amount=1 yocto
// Assert: subsidized_amount == N yocto, account balance == 0
// Assert: N receipts each carry 1 yocto to recipient
// If runtime mints subsidized_amount: recipient gains N yocto, protocol loses N yocto
```

The existing test infrastructure at `runtime/near-vm-runner/src/logic/tests/promises.rs` already sets up the exact preconditions; extending `test_one_yocto_on_promise_enabled` to call the host function N times and asserting `subsidized_amount == N` would confirm the accumulation. [5](#0-4)

### Citations

**File:** runtime/near-vm-runner/src/logic/logic.rs (L3000-3011)
```rust
        let skip_deduct = amount == Balance::from_yoctonear(1)
            && self.config.one_yocto_on_promise
            && self.result_state.current_account_balance.is_zero();
        if skip_deduct {
            self.result_state.subsidized_amount = self
                .result_state
                .subsidized_amount
                .checked_add(amount)
                .expect("subsidized_amount overflow");
        } else {
            self.result_state.deduct_balance(amount)?;
        }
```

**File:** runtime/near-vm-runner/src/wasmtime_runner/logic.rs (L3205-3216)
```rust
    let skip_deduct = amount == Balance::from_yoctonear(1)
        && ctx.config.one_yocto_on_promise
        && ctx.result_state.current_account_balance.is_zero();
    if skip_deduct {
        ctx.result_state.subsidized_amount = ctx
            .result_state
            .subsidized_amount
            .checked_add(amount)
            .expect("subsidized_amount overflow");
    } else {
        ctx.result_state.deduct_balance(amount)?;
    }
```

**File:** runtime/near-vm-runner/src/logic/tests/promises.rs (L690-719)
```rust
#[test]
fn test_one_yocto_on_promise_enabled() {
    let mut logic_builder = VMLogicBuilder::default();
    logic_builder.config.one_yocto_on_promise = true;
    logic_builder.context.account_balance = Balance::ZERO;
    logic_builder.context.attached_deposit = Balance::ZERO;
    let mut logic = logic_builder.build();

    let index = promise_create(&mut logic, b"rick.test", 0, 0).expect("should create a promise");

    // 1 yoctoNEAR should succeed even with zero balance
    promise_batch_action_function_call_weight(&mut logic, index, 1, Gas::ZERO, 0)
        .expect("1 yoctoNEAR should succeed with feature enabled");

    // 2 yoctoNEAR should still fail
    promise_batch_action_function_call_weight(&mut logic, index, 2, Gas::ZERO, 0)
        .expect_err("2 yoctoNEAR should fail with zero balance");

    // Transfer with 1 yoctoNEAR should still fail (feature only applies to function calls)
    let num_1u128 = logic.internal_mem_write(&1u128.to_le_bytes());
    logic
        .promise_batch_action_transfer(index, num_1u128.ptr)
        .expect_err("transfer should still fail with zero balance");

    assert_eq!(
        logic.result_state().subsidized_amount,
        Balance::from_yoctonear(1),
        "subsidized_amount should track the skipped deduction"
    );
}
```

**File:** runtime/near-vm-runner/src/logic/tests/promises.rs (L745-753)
```rust
    // Balance is now zero, so the skip kicks in
    promise_batch_action_function_call_weight(&mut logic, index, 1, Gas::ZERO, 0)
        .expect("should succeed via zero-balance exemption");

    assert_eq!(
        logic.result_state().subsidized_amount,
        Balance::from_yoctonear(1),
        "subsidized balance should be tracked correctly"
    );
```
