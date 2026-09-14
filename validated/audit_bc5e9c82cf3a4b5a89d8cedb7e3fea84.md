### Title
`FunctionCallPermission` allowance host functions treat a computed `0` value as "unlimited allowance" instead of "zero allowance" - ([File: runtime/near-vm-runner/src/wasmtime_runner/logic.rs])

### Summary
The reported Uniswap-router bug class is: a numeric parameter value of `0` is silently reinterpreted by an external interface with a *different, more dangerous* meaning ("sell everything") instead of the literal value zero, and a caller-side computation (proportional division) can produce that `0` unintentionally, causing unauthorized asset movement. The same pattern exists in nearcore's access-key host functions: the `allowance` parameter for `FunctionCall`/`GasKeyFunctionCall` access keys is passed across the WASM ABI as a raw `u128`, and the value `0` is a *sentinel* that is silently converted into `None`, i.e. **unlimited allowance**, rather than a key with zero spending budget.

### Finding Description
`FunctionCallPermission.allowance` is documented and typed as `Option<Balance>` where `None` means unlimited allowance: [1](#0-0) 

Because the WASM host-function ABI cannot pass a Rust `Option` directly, the promise-batch host functions encode "no limit" using the sentinel value `0`, as explicitly documented: [2](#0-1) 

This sentinel conversion is implemented in the gas-key variant of the host function: [3](#0-2) 

and the equivalent conversion exists for the plain `FunctionCall` access-key variant of the same host function family (`promise_batch_action_add_key_with_function_call`), which is exercised the same way in tests, e.g.: [4](#0-3) 

The allowance is subsequently the sole gate on how much of the account's balance a `FunctionCall` key can spend on gas/fees — a finite allowance is checked and decremented on every use: [5](#0-4) 
while `None` (unlimited) skips this check entirely, letting the key spend the account's balance without limit (bounded only by the account's total balance and storage-stake reservation).

If a contract computes an allowance programmatically — e.g. splitting a fixed token/gas budget proportionally across many newly-minted access keys (the exact analog of the reported `amountToSellUnits = balance * (amountToBuyLeftUSD*1e18/collateralval)/1e18` truncating to `0`) — integer-division truncation can yield `0` for some shares. Instead of creating a key with a zero spending budget (as the contract author intended), the runtime silently grants that key **unlimited** allowance to spend the account's NEAR balance on gas and fees.

### Impact Explanation
An access key that was meant to be tightly capped (or to have no spending power at all) ends up with unlimited allowance to drain the owning account's balance via `FunctionCall` transactions (gas + fees), which is unauthorized value movement out of the account, matching the "unexpected full sale/spend of value due to an unconsidered `0` sentinel" bug class from the source report. Any factory/relayer/faucet-style contract that issues `FunctionCall` access keys with a derived allowance is exposed; the amount of NEAR at risk is the full account balance the contract is willing to fund keys from.

### Likelihood Explanation
Reaching this requires only a normal contract deployment and a single `FunctionCall` transaction batching `promise_batch_action_add_key_with_function_call` (or the gas-key equivalent) with an allowance argument computed at runtime such that it can legitimately evaluate to `0` — e.g. via integer division among many recipients, a config value that is unset, or a caller-supplied amount. This is directly reachable by any unprivileged contract deployer/caller, no validator or node-operator privilege needed.

### Recommendation
- Do not overload `0` as a semantic "unlimited" sentinel for a value that could arise from legitimate arithmetic. Either:
  - Require an explicit separate flag/parameter to request unlimited allowance instead of inferring it from `amount == 0`, or
  - Treat `0` as "zero allowance" (matching the literal numeric value) and add a distinct sentinel (e.g. `u128::MAX`) for "unlimited" if that capability must remain expressible over the WASM ABI.
- At minimum, update `docs/RuntimeSpec/Components/BindingsSpec/PromisesAPI.md` and the SDK-level bindings this host function feeds to add prominent warnings so contract authors defensively check for `allowance == 0` before calling this host function.

### Proof of Concept
1. Deploy a contract that mints `FunctionCall` access keys for N beneficiaries, splitting a fixed `budget` allowance proportionally: `share = budget * weight_i / total_weight`.
2. For a beneficiary whose `weight_i` is small enough relative to `total_weight`, integer division truncates `share` to `0`.
3. The contract calls `promise_batch_action_add_key_with_function_call(..., allowance_ptr -> 0, ...)` for that beneficiary, per: [2](#0-1) 
4. The runtime converts the raw `0` into `None` (unlimited allowance) rather than `Some(Balance::ZERO)`, per the conversion logic at: [3](#0-2) 
5. The resulting access key can now be used to send arbitrarily many `FunctionCall` transactions against the account, spending down its entire balance on gas/fees with no allowance check (`check_and_compute_new_allowance` returns `Ok(None)` for an unlimited key), per: [5](#0-4) 
even though the contract's intent was to grant that beneficiary a zero (or negligible) spending budget.

### Citations

**File:** core/primitives-core/src/account.rs (L960-966)
```rust
    /// Allowance is a balance limit to use by this access key to pay for function call gas and
    /// transaction fees. When this access key is used, both account balance and the allowance is
    /// decreased by the same value.
    /// `None` means unlimited allowance.
    /// NOTE: To change or increase the allowance, the old access key needs to be deleted and a new
    /// access key should be created.
    pub allowance: Option<Balance>,
```

**File:** docs/RuntimeSpec/Components/BindingsSpec/PromisesAPI.md (L331-336)
```markdown
Appends `AddKey` action to the batch of actions for the given promise pointed by `promise_idx`.
Details for the action: https://github.com/nearprotocol/NEPs/pull/8/files#diff-156752ec7d78e7b85b8c7de4a19cbd4R54
The access key will have `FunctionCall` permission, details: [click here](../../../DataStructures/AccessKey.md)

- If the `allowance` value (not the pointer) is `0`, the allowance is set to `None` (which means unlimited allowance). And positive value represents a `Some(...)` allowance.
- Given `method_names` is a `utf-8` string with `,` used as a separator. The vm will split the given string into a vector of strings.
```

**File:** runtime/near-vm-runner/src/wasmtime_runner/logic.rs (L3717-3722)
```rust
    let allowance = Balance::from_yoctonear(get_u128(
        &mut ctx.result_state.gas_counter,
        memory,
        allowance_ptr,
    )?);
    let allowance = if allowance > Balance::ZERO { Some(allowance) } else { None };
```

**File:** runtime/runtime/tests/test_async_calls.rs (L1397-1404)
```rust
        {"action_add_gas_key_with_function_call": {
            "promise_index": 0,
            "public_key": to_base64(&borsh::to_vec(&signer_new_account.public_key()).unwrap()),
            "num_nonces": 8,
            "allowance": "0",
            "receiver_id": "near_1",
            "method_names": "method1,method2",
        }, "id": 0 }
```

**File:** runtime/runtime/src/verifier.rs (L282-303)
```rust
fn check_and_compute_new_allowance(
    access_key: &AccessKey,
    account_id: &AccountId,
    public_key: &PublicKey,
    total_cost: Balance,
) -> Result<Option<Balance>, InvalidTxError> {
    let Some(fc) = access_key.permission.function_call_permission() else {
        return Ok(None);
    };
    let Some(allowance) = fc.allowance else {
        return Ok(None);
    };
    let new_allowance = allowance.checked_sub(total_cost).ok_or_else(|| {
        InvalidTxError::InvalidAccessKeyError(InvalidAccessKeyError::NotEnoughAllowance {
            account_id: account_id.clone(),
            public_key: public_key.clone().into(),
            allowance,
            cost: total_cost,
        })
    })?;
    Ok(Some(new_allowance))
}
```
