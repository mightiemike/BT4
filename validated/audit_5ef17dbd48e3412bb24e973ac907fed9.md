[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** runtime/near-vm-runner/src/logic/logic.rs (L3000-3002)
```rust
        let skip_deduct = amount == Balance::from_yoctonear(1)
            && self.config.one_yocto_on_promise
            && self.result_state.current_account_balance.is_zero();
```

**File:** runtime/near-vm-runner/src/logic/logic.rs (L3003-3008)
```rust
        if skip_deduct {
            self.result_state.subsidized_amount = self
                .result_state
                .subsidized_amount
                .checked_add(amount)
                .expect("subsidized_amount overflow");
```

**File:** runtime/near-vm-runner/src/logic/logic.rs (L3685-3693)
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
```

**File:** runtime/near-vm-runner/src/logic/logic.rs (L4596-4598)
```rust
    /// Amount of balance subsidized (minted) by skipping deduction for
    /// 1 yoctoNEAR attached deposits on zero-balance contracts.
    pub subsidized_amount: Balance,
```

**File:** core/primitives/src/chunk_apply_stats.rs (L251-254)
```rust
    /// Amount of balance subsidized (effectively minted) for zero-balance contracts
    /// attaching 1 yoctoNEAR to promise function calls. This amount must be
    /// subtracted from total_balance_burnt to keep total supply correct.
    pub subsidized_amount: Balance,
```

**File:** integration-tests/src/tests/runtime/test_yield_resume.rs (L682-703)
```rust
    let args = drain_then_yield_args(&alice_id, balance_before, &[20u8; 32], &[6u8; 16], 1);
    let args_len = args.len() as u64;
    let res = node
        .user()
        .function_call(alice_id, zba_id.clone(), "call_promise", args, MAX_GAS, Balance::ZERO)
        .unwrap();

    assert_eq!(
        res.status,
        FinalExecutionStatus::SuccessValue(vec![]),
        "drain + 1-yocto via the exemption should succeed on a ZBA; got {res:?}",
    );

    // The exemption fires exactly once — confirm the runtime tracked it.
    let subsidized_after = node.client.read().cumulative_subsidized;
    let subsidy = subsidized_after.checked_sub(subsidized_before).unwrap();
    assert_eq!(
        subsidy,
        Balance::from_yoctonear(1),
        "expected exactly 1 yoctoNEAR of subsidy (the exemption), got {} yoctoNEAR",
        subsidy.as_yoctonear(),
    );
```

**File:** runtime/runtime/src/tests/apply.rs (L4370-4384)
```rust
    // Step 3: call max_self_recursion_delay on both accounts in the same chunk.
    // Each call attaches 1 yoctoNEAR via promise_batch_action_function_call_weight
    // on a zero-balance account, so the subsidized amount should accumulate to
    // 2 yoctoNEAR. Using separate accounts avoids the gas rebate from the first
    // receipt making the account non-zero for the second.
    let call_alice = create_receipt_with_actions(
        alice_account(),
        signers[0].clone(),
        vec![Action::FunctionCall(Box::new(FunctionCallAction {
            method_name: "max_self_recursion_delay".to_string(),
            args: 0u32.to_be_bytes().to_vec(),
            gas: Gas::from_teragas(100),
            deposit: Balance::ZERO,
        }))],
    );
```
