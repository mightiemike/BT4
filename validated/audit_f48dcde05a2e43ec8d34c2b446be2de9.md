Based on the code explored, the closest reachable analog to the "no transfer ownership pattern" (a value-recipient field accepted after only a syntactic validity check, with no check that the target is a real/controlled account) is the `beneficiary_id` field of `DeleteAccountAction` in nearcore.

### Title
Unvalidated `beneficiary_id` in `DeleteAccountAction` allows permanent loss of the deleted account's balance to a non-existent account - (File: `runtime/runtime/src/action_validation.rs`, `runtime/runtime/src/actions.rs`)

### Summary
`DeleteAccountAction { beneficiary_id }` lets an account owner nominate the recipient of their remaining balance when deleting their account, analogous to `Swap.transferOwnership()` nominating a new owner. Just like the original finding, the only check performed is that the value is syntactically well-formed — there is no check that the account actually exists or is reachable/controlled by anyone.

### Finding Description
`validate_delete_action` only validates that `beneficiary_id` parses as a well-formed `AccountId`: [1](#0-0) 

At execution time, `action_delete_account` unconditionally creates a balance-refund receipt addressed to `beneficiary_id` without ever checking whether that account exists: [2](#0-1) 

The resulting `Receipt::new_balance_refund` receipt is processed later as a *refund* receipt. Per `check_account_existence`/`implicit_creation_allowed`, refund receipts are explicitly barred from creating any account, of any type (named, implicit, deterministic, or universal): [3](#0-2) 

This is also directly asserted by unit tests: a refund is never allowed to create any account kind, "however lonely the transfer is": [4](#0-3) 

So if a user sets `beneficiary_id` to a syntactically valid but non-existent named account (e.g. a typo, or an account that was never registered, or one that existed but was itself deleted between construction and execution of the transaction), the refund receipt targeting it will hit `Action::Transfer` in `check_account_existence`, find `account.is_none()`, and because `implicit_creation_allowed` returns `false` for a refund, the action fails with `ActionErrorKind::AccountDoesNotExist`: [5](#0-4) 

The requesting account has, by that point, already been irreversibly removed (`*account = None;` executed synchronously in the same action before the refund receipt is even dispatched): [6](#0-5) 

### Impact Explanation
The account deletion (removal of the account, its keys, its contract, and its state) is unconditional and irreversible at the point the `DeleteAccountAction` executes. The balance payout to `beneficiary_id` is deferred to a separately-processed refund receipt that can fail to land funds anywhere if the target account doesn't exist. Because refund receipts are barred from creating accounts of any kind, a failed refund receipt does not roll back the (already-committed) account deletion — the funds are effectively permanently lost/frozen, matching the "no transfer ownership" bug class's core harm (irreversible loss of value to an uncontrolled/invalid destination).

### Likelihood Explanation
This is directly reachable by any unprivileged transaction signer: any account owner (or a full-access key holder) can submit a `DeleteAccount` action naming an arbitrary syntactically-valid `beneficiary_id` without the protocol verifying its existence at submission time. A typo, a stale/expired sub-account, or a race where the beneficiary account is deleted between transaction construction and execution are all realistic triggers. No special privileges, validator/relayer collusion, or malicious peers are required.

### Recommendation
Either (a) validate that `beneficiary_id` refers to an existing account before allowing `DeleteAccountAction` to execute (fail the whole action, preserving the account, if it does not), or (b) make the balance-refund receipt able to create the destination account when it doesn't exist (removing the refund-can't-create-account restriction specifically for this deliberate, user-specified target), or (c) burn/return the balance to `predecessor_id` instead of silently failing when the beneficiary doesn't exist, so funds are never stranded.

### Proof of Concept
1. Attacker/user account `alice.near` holds `N` NEAR and full access key.
2. `alice.near` submits `DeleteAccount { beneficiary_id: "nonexistent.near" }` where `nonexistent.near` has never been created.
3. `action_delete_account` executes: it removes `alice.near` immediately (`*account = None`) and enqueues `Receipt::new_balance_refund("nonexistent.near", N)`.
4. When the enqueued refund receipt is processed, `check_account_existence` sees `account.is_none()` for `nonexistent.near` and `implicit_creation_allowed` returns `false` for refunds, so the transfer action fails with `AccountDoesNotExist`.
5. `alice.near` no longer exists (deletion already committed), and the `N` NEAR is not credited to any account — it is effectively lost, with no mechanism to reclaim it.

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

**File:** runtime/runtime/src/actions.rs (L387-405)
```rust
    let remove_result = remove_account(state_update, account_id)?;
    result.tokens_burnt =
        result.tokens_burnt.checked_add(gas_key_balance_to_burn).ok_or_else(|| {
            StorageError::StorageInconsistentState("tokens_burnt overflow".to_string())
        })?;
    if remove_result.gas_key_nonce_count > 0 {
        let compute = storage_removes_compute(
            &config.wasm_config.ext_costs,
            remove_result.gas_key_nonce_count,
            remove_result.gas_key_nonce_total_key_bytes,
            AccessKey::NONCE_VALUE_LEN * remove_result.gas_key_nonce_count,
        );
        result.compute_usage = safe_add_compute(result.compute_usage, compute).map_err(|_| {
            StorageError::StorageInconsistentState("compute_usage overflow".to_string())
        })?;
    }
    *actor_id = receipt.predecessor_id().clone();
    *account = None;
    Ok(())
```

**File:** runtime/runtime/src/actions.rs (L842-850)
```rust
        Action::Transfer(_) => {
            let account_type = get_account_type(account_id, config);
            if account.is_none() && !implicit_creation_allowed(account_type, receipt_shape) {
                return Err(ActionErrorKind::AccountDoesNotExist {
                    account_id: account_id.clone(),
                }
                .into());
            }
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

**File:** runtime/runtime/src/actions.rs (L2590-2597)
```rust
        // Refunds are free, and account deletion with a beneficiary makes one, so
        // no kind may be created by one however lonely the transfer is.
        for account_type in ALL {
            assert!(
                !implicit_creation_allowed(account_type, refund),
                "{account_type:?} must not be created by a refund"
            );
        }
```
