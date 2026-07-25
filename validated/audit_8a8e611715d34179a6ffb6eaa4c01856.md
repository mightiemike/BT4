### Title
`DeleteAccountAction` validates `beneficiary_id` format only, not existence — silent fund burn when beneficiary is non-existent - (File: `runtime/runtime/src/action_validation.rs`)

### Summary

`validate_delete_action` checks only that `beneficiary_id` is a syntactically valid account ID, mirroring the ERC20Gauges pattern of checking "not in the invalid set" without checking "in the valid/active set." When a user submits `DeleteAccount` with a `beneficiary_id` that is well-formed but does not exist on-chain, the action passes all validation, the account is deleted, and the balance-transfer refund receipt silently fails — burning the entire account balance.

### Finding Description

**Validation gap — `validate_delete_action`:**

```rust
// runtime/runtime/src/action_validation.rs:388-392
fn validate_delete_action(action: &DeleteAccountAction) -> Result<(), ActionsValidationError> {
    validate_action_account_id(&action.beneficiary_id)?;  // format only
    Ok(())
}
```

Only `validate_action_account_id` is called, which rejects syntactically invalid strings. No state-level check verifies that `beneficiary_id` corresponds to an existing account.

**Execution path — `action_delete_account`:**

```rust
// runtime/runtime/src/actions.rs:350-355
let account_balance = account_ref.amount();
if account_balance > Balance::ZERO {
    result
        .new_receipts
        .push(Receipt::new_balance_refund(&delete_account.beneficiary_id, account_balance));
}
```

A `Receipt::new_balance_refund` is unconditionally emitted to `beneficiary_id` with `predecessor_id = "system"`. No existence check is performed here either.

**Why the refund receipt burns funds:**

`Receipt::new_balance_refund` sets `predecessor_id = "system"`, making it a refund receipt. When processed, `is_refund = true`, so:

```rust
// runtime/runtime/src/lib.rs:548-550
let is_refund = receipt.predecessor_id().is_system();
let is_the_only_action = actions.len() == 1;
let implicit_account_creation_eligible = is_the_only_action && !is_refund; // false
```

`implicit_account_creation_eligible` is `false`. Inside `check_account_existence`, a `Transfer` to a non-existent account with `implicit_account_creation_eligible = false` returns `AccountDoesNotExist`:

```rust
// runtime/runtime/src/actions.rs:829-848
fn check_transfer_to_nonexisting_account(...) -> Result<(), ActionError> {
    if implicit_account_creation_eligible && account_is_implicit(...) {
        Ok(())  // not reached — is_refund=true
    } else {
        Err(ActionErrorKind::AccountDoesNotExist { ... }.into())
    }
}
```

The comment in this function explicitly acknowledges: *"Account deletion with beneficiary creates a refund, so it'll not create a new account."*

Failed refund receipts burn their deposit per the documented invariant:

> *"If the execution of a refund fails, the refund amount is burnt."* — `docs/RuntimeSpec/Refunds.md`

The runtime spec confirms: *"system-predecessor receipts (refund receipts) are free — no refund generated, and a failed refund burns its deposit into `other_burnt_amount`"* — `protocol-model/spec/runtime-execution.md`.

### Impact Explanation

An unprivileged user who submits `DeleteAccount { beneficiary_id: "typo.near" }` where `"typo.near"` does not exist will:
1. Have their account irreversibly deleted.
2. Have their entire liquid balance (`account.amount()`) burned — not refunded, not recoverable.

This is a direct, permanent loss of funds reachable from a single ordinary user transaction. The `DeleteAccount` receipt itself succeeds (outcome = `SuccessValue`), so the user sees no error at the action level; the burn is only visible in the subsequent refund-receipt outcome.

### Likelihood Explanation

Any user can trigger this via a typo or by specifying a beneficiary account that has been deleted. The validation gate (`validate_delete_action`) is the only pre-execution check and it passes silently. No warning, no error, no dry-run protection exists at the protocol level. The scenario is analogous to the ERC20Gauges finding where the judge noted "assets can directly be lost" and rated it Medium.

### Recommendation

Add an existence check for `beneficiary_id` inside `action_delete_account` before emitting the refund receipt:

```rust
// runtime/runtime/src/actions.rs — inside action_delete_account, before emitting the receipt
if get_account(state_update, &delete_account.beneficiary_id)?.is_none() {
    result.result = Err(ActionErrorKind::AccountDoesNotExist {
        account_id: delete_account.beneficiary_id.clone(),
    }.into());
    return Ok(());
}
```

This mirrors the ERC20Gauges recommended fix (`!_gauges.contains(gauge)`) — checking membership in the active set, not just absence from the invalid set. The account is not deleted if the beneficiary does not exist, giving the user a recoverable error.

### Proof of Concept

1. Alice holds `alice.near` with 100 NEAR.
2. Alice submits: `DeleteAccount { beneficiary_id: "bobb.near" }` (typo; `"bobb.near"` does not exist).
3. `validate_delete_action` passes — `"bobb.near"` is syntactically valid. [1](#0-0) 
4. `action_delete_account` runs: `alice.near` is removed from state; a `Receipt::new_balance_refund("bobb.near", 100 NEAR)` is pushed to `result.new_receipts`. [2](#0-1) 
5. The refund receipt is processed. `is_refund = true` → `implicit_account_creation_eligible = false`. [3](#0-2) 
6. `check_account_existence` calls `check_transfer_to_nonexisting_account` with `implicit_account_creation_eligible = false` → returns `AccountDoesNotExist`. [4](#0-3) 
7. The refund receipt fails; 100 NEAR is added to `other_burnt_amount` and permanently destroyed. [5](#0-4) 
8. Alice's account is gone and her 100 NEAR is burned. No recovery path exists.

### Citations

**File:** runtime/runtime/src/action_validation.rs (L388-392)
```rust
fn validate_delete_action(action: &DeleteAccountAction) -> Result<(), ActionsValidationError> {
    validate_action_account_id(&action.beneficiary_id)?;

    Ok(())
}
```

**File:** runtime/runtime/src/actions.rs (L349-355)
```rust
    // We use current amount as a pay out to beneficiary.
    let account_balance = account_ref.amount();
    if account_balance > Balance::ZERO {
        result
            .new_receipts
            .push(Receipt::new_balance_refund(&delete_account.beneficiary_id, account_balance));
    }
```

**File:** runtime/runtime/src/actions.rs (L829-848)
```rust
fn check_transfer_to_nonexisting_account(
    config: &RuntimeConfig,
    account_id: &AccountId,
    implicit_account_creation_eligible: bool,
) -> Result<(), ActionError> {
    if implicit_account_creation_eligible
        && account_is_implicit(account_id, config.wasm_config.eth_implicit_accounts)
    {
        // OK. It's implicit account creation.
        // Notes:
        // - Transfer action has to be the only action in the transaction to avoid
        // abuse by hijacking this account with other public keys or contracts.
        // - Refunds don't automatically create accounts, because refunds are free and
        // we don't want some type of abuse.
        // - Account deletion with beneficiary creates a refund, so it'll not create a
        // new account.
        Ok(())
    } else {
        Err(ActionErrorKind::AccountDoesNotExist { account_id: account_id.clone() }.into())
    }
```

**File:** runtime/runtime/src/lib.rs (L548-550)
```rust
        let is_refund = receipt.predecessor_id().is_system();
        let is_the_only_action = actions.len() == 1;
        let implicit_account_creation_eligible = is_the_only_action && !is_refund;
```

**File:** core/primitives/src/receipt.rs (L496-510)
```rust
    pub fn new_balance_refund(receiver_id: &AccountId, refund: Balance) -> Self {
        Receipt::V0(ReceiptV0 {
            predecessor_id: "system".parse().unwrap(),
            receiver_id: receiver_id.clone(),
            receipt_id: CryptoHash::default(),
            receipt: ReceiptEnum::Action(ActionReceipt {
                signer_id: "system".parse().unwrap(),
                signer_public_key: PublicKey::empty(KeyType::ED25519),
                gas_price: Balance::ZERO,
                output_data_receivers: vec![],
                input_data_ids: vec![],
                actions: vec![Action::Transfer(TransferAction { deposit: refund })],
            }),
        })
    }
```
