### Title
Unauthorized `TransferToGasKey` funding can permanently block `DeleteKey`/`DeleteAccount` via the `GasKeyBalanceTooHigh` burn cap - (File: `runtime/runtime/src/access_keys.rs`)

### Summary
`action_transfer_to_gas_key` lets **any** predecessor increase the balance of a gas key on an arbitrary receiver account, with no check that the caller is the account owner. `delete_gas_key` (and `action_delete_account`) later refuse to delete a gas key / account whose gas-key balance exceeds `GasKeyInfo::MAX_BALANCE_TO_BURN`. An attacker can therefore "blind fund" a victim's gas key past this cap and permanently prevent the victim from deleting that key or their account — the same bug class as the `LockingController.increaseUnwindingEpochs` report, where an unrestricted incoming transfer inflates a balance that a later privileged operation assumes is bounded/owned, causing that operation to revert.

### Finding Description
`action_transfer_to_gas_key` only checks that the target public key exists and is a gas key; it performs no check that the caller (`predecessor_id`) is the account owner before crediting the gas key's balance: [1](#0-0) 

This action is reachable both from a plain transaction/receipt (`Action::TransferToGasKey`) and from contract execution via the host function `promise_batch_action_transfer_to_gas_key`, which lets any contract append the action to a promise targeting an arbitrary `receiver_id`/gas key: [2](#0-1) 

Later, when the account owner tries to remove that gas key (`DeleteKey`), the runtime enforces a hard burn cap: [3](#0-2) 

The same cap is enforced in aggregate when deleting the whole account, summing all of its gas keys' balances: [4](#0-3) 

If `gas_key_info.balance` (or the account-wide sum) exceeds `GasKeyInfo::MAX_BALANCE_TO_BURN`, the action returns `ActionErrorKind::GasKeyBalanceTooHigh` and the delete operation is a no-op (returns `Ok` with an error result, i.e., it deterministically fails) rather than succeeding.

This mirrors the report's root cause exactly: `LockingController.increaseUnwindingEpochs` trusted `msg.sender`'s live balance (which included involuntary/blind transfers from a third party) when performing a privileged burn, causing the burn to revert. Here, `delete_gas_key`/`action_delete_account` trust the gas key's live balance (which anyone can inflate via `TransferToGasKey`) when enforcing the burn cap, causing the delete action to permanently fail.

### Impact Explanation
An unprivileged attacker (any account or any deployed contract, via a single transaction/receipt) can send repeated `TransferToGasKeyAction`/`promise_batch_action_transfer_to_gas_key` deposits to a victim's known gas key public key, pushing its balance above `GasKeyInfo::MAX_BALANCE_TO_BURN`. From then on:
- The victim can never successfully execute `DeleteKey` on that gas key (`GasKeyBalanceTooHigh`).
- If that pushes the account-wide gas-key balance sum over the cap, the victim can never execute `DeleteAccount` either.

This is a transaction-triggered halt of legitimate account-management operations for the victim — a permanent denial of service on `DeleteKey`/`DeleteAccount`, reachable by any single unprivileged submitted transaction against a known target account/public key.

### Likelihood Explanation
High likelihood of reachability: `TransferToGasKey` requires no special permission from the sender and no cooperation from the receiver; it can be sent by any account or any contract (via the promise host function) at negligible cost (attacker only pays the deposit, which is refunded to the victim on eventual burn, plus gas). The only precondition is knowing the victim's gas-key public key and that the key currently exists, both of which are public on-chain information once a gas key is added.

### Recommendation
Do not let an externally-supplied, attacker-inflatable balance alone determine whether a privileged deletion path succeeds. Options:
- Restrict `TransferToGasKeyAction`/`promise_batch_action_transfer_to_gas_key` so the predecessor must equal the receiver account (self-funding only), removing the "blind transfer" vector, or
- Change `delete_gas_key`/`action_delete_account` so that exceeding `MAX_BALANCE_TO_BURN` triggers a refund-then-delete (or a partial burn plus refund of the remainder) instead of an unconditional failure, ensuring the delete operation is not permanently blockable by a third party.

### Proof of Concept
1. Victim account `victim.near` has an existing gas key `gk` (added via `AddKey` with `AccessKey::gas_key_full_access(...)`), currently funded below `GasKeyInfo::MAX_BALANCE_TO_BURN`.
2. Attacker (any account, or any contract calling `promise_batch_action_transfer_to_gas_key`) submits `Action::TransferToGasKey(TransferToGasKeyAction { public_key: gk, deposit: X })` targeting `victim.near`, using `action_transfer_to_gas_key` at `runtime/runtime/src/access_keys.rs:257-288`, which performs no ownership check and simply increments `gas_key_info.balance`.
3. Attacker repeats until `gas_key_info.balance > GasKeyInfo::MAX_BALANCE_TO_BURN`.
4. Victim signs `DeleteKey(gk)`. `delete_gas_key` (`access_keys.rs:93-111`) now always returns `ActionErrorKind::GasKeyBalanceTooHigh`, so the key can never be removed by the victim.
5. If this is the account's only/decisive gas key balance, `action_delete_account` (`actions.rs:370-379`) likewise always fails via `compute_gas_key_balance_sum` exceeding the cap, permanently blocking `DeleteAccount`.

Note: I was not able to fully confirm within the available tool calls whether some other permission gate (e.g., in `action_validation.rs`) restricts `TransferToGasKeyAction`'s predecessor to equal the receiver; the code inspected (`action_transfer_to_gas_key` itself, and the promise host-function path) shows no such restriction, and the explicit doc-comment restricting *only* `WithdrawFromGasKeyAction` to transaction-only use (not `TransferToGasKeyAction`) supports that `TransferToGasKeyAction` is meant to be usable by contract-issued receipts targeting arbitrary receivers. A Devin session with full repo access should verify `action_validation.rs`'s handling of `TransferToGasKeyAction` to close out this uncertainty before treating this as fully confirmed.

### Citations

**File:** runtime/runtime/src/access_keys.rs (L93-111)
```rust
fn delete_gas_key(
    config: &RuntimeConfig,
    state_update: &mut TrieUpdate,
    account: &mut Account,
    result: &mut ActionResult,
    account_id: &AccountId,
    public_key: &PublicKey,
    access_key: &AccessKey,
    gas_key_info: &GasKeyInfo,
) -> Result<(), RuntimeError> {
    if gas_key_info.balance > GasKeyInfo::MAX_BALANCE_TO_BURN {
        result.result = Err(ActionErrorKind::GasKeyBalanceTooHigh {
            account_id: account_id.clone(),
            public_key: Some(Box::new(public_key.clone())),
            balance: gas_key_info.balance,
        }
        .into());
        return Ok(());
    }
```

**File:** runtime/runtime/src/access_keys.rs (L257-288)
```rust
pub(crate) fn action_transfer_to_gas_key(
    state_update: &mut TrieUpdate,
    result: &mut ActionResult,
    account_id: &AccountId,
    action: &TransferToGasKeyAction,
) -> Result<(), RuntimeError> {
    let Some(mut access_key) = get_access_key(state_update, account_id, &action.public_key)? else {
        result.result = Err(ActionErrorKind::GasKeyDoesNotExist {
            account_id: account_id.clone(),
            public_key: Box::new(action.public_key.clone()),
        }
        .into());
        return Ok(());
    };
    let Some(gas_key_info) = access_key.gas_key_info_mut() else {
        // Key exists but is not a gas key
        result.result = Err(ActionErrorKind::GasKeyDoesNotExist {
            account_id: account_id.clone(),
            public_key: Box::new(action.public_key.clone()),
        }
        .into());
        return Ok(());
    };

    gas_key_info.balance = gas_key_info.balance.checked_add(action.deposit).ok_or_else(|| {
        RuntimeError::StorageError(StorageError::StorageInconsistentState(
            "gas key balance integer overflow".to_string(),
        ))
    })?;
    set_access_key(state_update, account_id.clone(), action.public_key.clone(), &access_key);
    Ok(())
}
```

**File:** runtime/near-vm-runner/src/wasmtime_runner/logic.rs (L3549-3599)
```rust
pub fn promise_batch_action_transfer_to_gas_key(
    ctx: &mut Ctx,
    memory: &mut [u8],
    promise_idx: u64,
    public_key_len: u64,
    public_key_ptr: u64,
    amount_ptr: u64,
) -> Result<()> {
    ctx.result_state.gas_counter.pay_base(base)?;
    if ctx.context.is_view() {
        return Err(HostError::ProhibitedInView {
            method_name: "promise_batch_action_transfer_to_gas_key".to_string(),
        }
        .into());
    }
    let public_key_buf = get_public_key(
        &mut ctx.result_state.gas_counter,
        memory,
        &ctx.registers,
        public_key_ptr,
        public_key_len,
        ctx.ext.post_quantum_keys_enabled(),
    )?;
    let public_key_res = public_key_buf.decode();
    let pk_len = public_key_res.as_ref().map_or(0, |pk| pk.len());
    let amount =
        Balance::from_yoctonear(get_u128(&mut ctx.result_state.gas_counter, memory, amount_ptr)?);
    let (receipt_idx, sir) = promise_idx_to_receipt_idx_with_sir(ctx, promise_idx)?;
    let receiver_id = ctx.ext.get_receipt_receiver(receipt_idx);
    let send = gas_key_transfer_send_fee(&ctx.fees_config, sir, pk_len);
    let exec_pk_len = gas_key_exec_pk_len(&public_key_res, &ctx.config, pk_len);
    let exec = gas_key_transfer_exec_fee(&ctx.fees_config, receiver_id.len(), exec_pk_len);
    let burn_base = send.base;
    let use_base = burn_base.gas.checked_add(exec.base.gas).ok_or(HostError::IntegerOverflow)?;
    ctx.result_state.gas_counter.pay_action_accumulated(
        burn_base,
        use_base,
        ActionCosts::gas_key_transfer_base,
    )?;
    let burn_byte = send.per_byte;
    let use_byte =
        burn_byte.gas.checked_add(exec.per_byte.gas).ok_or(HostError::IntegerOverflow)?;
    ctx.result_state.gas_counter.pay_action_accumulated(
        burn_byte,
        use_byte,
        ActionCosts::gas_key_byte,
    )?;
    ctx.result_state.deduct_balance(amount)?;
    ctx.ext.append_action_transfer_to_gas_key(receipt_idx, public_key_res?, amount);
    Ok(())
}
```

**File:** runtime/runtime/src/actions.rs (L370-379)
```rust
    let gas_key_balance_to_burn = compute_gas_key_balance_sum(state_update, account_id)?;
    if gas_key_balance_to_burn > GasKeyInfo::MAX_BALANCE_TO_BURN {
        result.result = Err(ActionErrorKind::GasKeyBalanceTooHigh {
            account_id: account_id.clone(),
            public_key: None,
            balance: gas_key_balance_to_burn,
        }
        .into());
        return Ok(());
    }
```
