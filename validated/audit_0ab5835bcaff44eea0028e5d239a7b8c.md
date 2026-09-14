### Title
DeleteAccount action permits self-transfer of the deleted balance to an unreachable or misconfigured beneficiary_id, permanently burning the funds instead of protecting them - (File: `runtime/runtime/src/actions.rs`)

### Summary
The `SdtBuffer.pullRewards` report flags a class of bug where a reward/fund-distribution routine transfers tokens to a receiver address without validating that the receiver can actually accept the funds, causing permanent loss. The nearcore analog is `action_delete_account` in `runtime/runtime/src/actions.rs`, which pays out a deleted account's full balance to a `beneficiary_id` that is validated only for *syntactic* well-formedness, not for whether it can actually receive the refund.

### Finding Description
`validate_delete_action` (`runtime/runtime/src/action_validation.rs:447-451`) only checks that `beneficiary_id` is a syntactically valid account id: [1](#0-0) 
It never checks that the account exists, is not a named/non-implicit account, or is otherwise able to accept a `Transfer`.

`action_delete_account` then unconditionally pushes the entire account balance as a system-generated balance-refund receipt to that `beneficiary_id`: [2](#0-1) 

This refund receipt is executed later as a `Transfer` action whose predecessor is the system account (`is_refund = true`). `check_account_existence` for `Action::Transfer` requires the target account to already exist unless it is one of the implicit-creatable account types (`runtime/runtime/src/actions.rs:842-850`), and `implicit_creation_allowed` explicitly returns `false` when `is_refund` is true (`runtime/runtime/src/actions.rs:928-947`): [3](#0-2) 

So if `beneficiary_id` names a non-existent named account (or any account that is not registered), the refund `Transfer` action fails with `AccountDoesNotExist`. Critically, the runtime treats any receipt whose predecessor is the system account specially: on failure, instead of generating a second-level refund (refunds of refunds are not produced), the deposited amount is simply burned: [4](#0-3) 
```
let gas_refund_result = if receipt.predecessor_id().is_system() {
    // If the refund fails tokens are burned.
    if result.result.is_err() {
        stats.balance.other_burnt_amount = safe_add_balance(...)
    }
    ...
```
This is by design for legitimate refunds (e.g. gas refunds to a deleted signer account), but `DeleteAccountAction`'s `beneficiary_id` is fully attacker/user-controlled at submission time, and there is no requirement that the beneficiary exist, be registered, or even be different from the account being deleted. A comment in the test suite (`runtime/runtime/src/tests/apply.rs:6355-6356`) confirms this is a known, intentional gap rather than a defended invariant: [5](#0-4) 
"The beneficiary has to exist, otherwise the balance transfer the delete sends it would come straight back as a refund" — and that refund-of-a-refund is precisely the path that gets burned per `lib.rs:1047-1054`, rather than credited to the treasury/validators or returned to the account owner.

### Impact Explanation
Any account owner (or, more importantly, any relayer/contract executing a batched `DeleteAccount` promise action, or a meta-transaction) can cause an account's entire balance to be irrecoverably destroyed by naming a beneficiary that does not exist (a typo'd or unregistered named account). Unlike ordinary transfers, which are rejected up front by `check_account_existence` at submission if the target account doesn't exist and isn't implicitly creatable, `DeleteAccountAction`'s target validation is deferred to execution time on a *system-refund* receipt, whose failure path is defined to burn funds rather than fail the whole action atomically or fall back to a safe default (e.g. the account owner or the protocol treasury). This is a concrete unauthorized/unintended value destruction: user funds are permanently and irreversibly removed from supply due to an unchecked receiver address, which is functionally identical to the reported `SdtBuffer.pullRewards` zero-address loss-of-funds class (funds sent to an unusable recipient are lost).

### Likelihood Explanation
Likelihood is high for accidental loss (a single mistyped `beneficiary_id` in a `DeleteAccount` action or a `promise_batch_action_delete_account` host call, which has no existence check either — `runtime/near-vm-runner/src/wasmtime_runner/logic.rs:4038-4072`) and moderate for intentional grief/DoS by a relayer executing meta-transactions or a contract issuing a promise-batch delete on behalf of a user. The transaction/action structure is fully reachable by any ordinary transaction signer or contract call; no privileged, validator-only, or network-layer access is required.

### Recommendation
Before executing the deletion or before creating the balance-refund receipt, validate (at execution time, inside `action_delete_account`) that `beneficiary_id`:
- either already exists in state, or
- is an implicit-creatable account type such that the ensuing transfer will succeed.

If the beneficiary account cannot be confirmed to accept the transfer, the `DeleteAccountAction` should fail with an explicit `ActionErrorKind` (e.g. a new `BeneficiaryDoesNotExist` variant) rather than proceeding and later burning the funds through the generic system-refund failure path. Alternatively, disallow burning for this specific refund reason and instead redirect undeliverable delete-account balances to the protocol treasury account, consistent with how other undeliverable-value paths (e.g. contract-reward-without-live-account) are documented to redirect to validators rather than vanish (`protocol-model/spec/economics.md:99`).

### Proof of Concept
1. Create account `victim.near` with a positive balance and no locked stake.
2. Submit (or have `victim.near` submit) a transaction/receipt containing `Action::DeleteAccount(DeleteAccountAction { beneficiary_id: "nonexistent123.near".parse().unwrap() })`, where `nonexistent123.near` is a syntactically valid but never-created named account.
3. `validate_delete_action` accepts the action (only checks `validate_action_account_id`) — `runtime/runtime/src/action_validation.rs:447-451`.
4. `action_delete_account` deletes `victim.near` and enqueues `Receipt::new_balance_refund(&"nonexistent123.near", account_balance)` — `runtime/runtime/src/actions.rs:380-386`.
5. When that refund receipt executes, `check_account_existence` rejects the `Transfer` action because the target does not exist and `implicit_creation_allowed` returns `false` for `is_refund == true` — `runtime/runtime/src/actions.rs:842-850`, `928-947`.
6. Because `receipt.predecessor_id().is_system()` is true and the result is an `Err`, the deposited amount is added to `stats.balance.other_burnt_amount` and permanently removed from circulation — `runtime/runtime/src/lib.rs:1047-1054`.
7. End state: `victim.near`'s entire balance is unrecoverably burned; no account (owner, beneficiary, treasury, or validators) receives it.

### Citations

**File:** runtime/runtime/src/action_validation.rs (L447-451)
```rust
fn validate_delete_action(action: &DeleteAccountAction) -> Result<(), ActionsValidationError> {
    validate_action_account_id(&action.beneficiary_id)?;

    Ok(())
}
```

**File:** runtime/runtime/src/actions.rs (L380-386)
```rust
    // We use current amount as a pay out to beneficiary.
    let account_balance = account_ref.amount();
    if account_balance > Balance::ZERO {
        result
            .new_receipts
            .push(Receipt::new_balance_refund(&delete_account.beneficiary_id, account_balance));
    }
```

**File:** runtime/runtime/src/actions.rs (L928-947)
```rust
/// Whether a transfer to an account that does not exist yet may create it.
fn implicit_creation_allowed(account_type: AccountType, receipt_shape: ReceiptShape) -> bool {
    let ReceiptShape { is_refund, is_the_only_action } = receipt_shape;
    if is_refund {
        return false; // Refund can never create an account
    }

    match account_type {
        // Named accounts can never be implicitly created by transfer
        AccountType::NamedAccount => false,
        // Near-implicit, Eth-implicit, and deterministic accounts can only be created
        // if transfer is the only action, to avoid account hijacking.
        AccountType::NearImplicitAccount
        | AccountType::EthImplicitAccount
        | AccountType::NearDeterministicAccount => is_the_only_action,
        // Universal account creation does NOT require transfer to be the only action.
        // It cannot be hijacked by other actions batched with the transfer.
        AccountType::UniversalAccount => true,
    }
}
```

**File:** runtime/runtime/src/lib.rs (L1047-1054)
```rust
        let gas_refund_result = if receipt.predecessor_id().is_system() {
            // If the refund fails tokens are burned.
            if result.result.is_err() {
                stats.balance.other_burnt_amount = safe_add_balance(
                    stats.balance.other_burnt_amount,
                    total_deposit(&action_receipt.actions())?,
                )?
            }
```

**File:** runtime/runtime/src/tests/apply.rs (L6354-6357)
```rust

        // The beneficiary has to exist, otherwise the balance transfer the delete
        // sends it would come straight back as a refund.
        let beneficiary: AccountId = "beneficiary.near".parse().unwrap();
```
