### Title
`DeleteAccountAction` missing `beneficiary_id != account_id` guard silently burns the entire account balance — (File: `runtime/runtime/src/action_validation.rs`)

---

### Summary

`validate_delete_action` only checks that `beneficiary_id` is a syntactically valid account ID. It does not check that `beneficiary_id` differs from the account being deleted (`receiver_id`). When a user submits `DeleteAccount` with `beneficiary_id == account_id`, the account is erased from state and a system-predecessor balance-refund receipt is emitted to the now-deleted account. Because the target account no longer exists, the transfer fails, and the runtime's documented refund-failure path burns the entire balance.

---

### Finding Description

**Validation gap.**
`validate_action_with_mode` in `runtime/runtime/src/action_validation.rs` receives `receiver: &AccountId` (the account being deleted) but does not forward it to `validate_delete_action`:

```
Action::DeleteAccount(a) => validate_delete_action(a),   // receiver is ignored
```

`validate_delete_action` itself only calls `validate_action_account_id(&action.beneficiary_id)`, which enforces syntactic rules (length 2–64, allowed chars). No semantic check prevents `beneficiary_id == receiver`. [1](#0-0) [2](#0-1) 

**Execution path.**
`action_delete_account` in `runtime/runtime/src/actions.rs`:
1. Reads `account_balance = account_ref.amount()`.
2. Pushes `Receipt::new_balance_refund(&delete_account.beneficiary_id, account_balance)` — a system-predecessor receipt targeting `beneficiary_id`.
3. Calls `remove_account(state_update, account_id)` — the account is gone. [3](#0-2) 

**Refund-failure burn.**
`apply_action_receipt` in `runtime/runtime/src/lib.rs` contains the explicit burn path for system-predecessor receipts:

```rust
if receipt.predecessor_id().is_system() {
    // If the refund fails tokens are burned.
    if result.result.is_err() {
        stats.balance.other_burnt_amount = safe_add_balance(
            stats.balance.other_burnt_amount,
            total_deposit(&action_receipt.actions())?,
        )?
    }
    ...
}
``` [4](#0-3) 

When the refund receipt executes, `receiver_id` is the deleted named account. `check_account_existence` returns `AccountDoesNotExist` for a non-implicit account that no longer exists, the transfer action fails, and the entire balance flows into `other_burnt_amount`. [5](#0-4) 

The `new_balance_refund` constructor confirms `predecessor_id = "system"`, which is the exact condition that triggers the burn-on-failure branch: [6](#0-5) 

---

### Impact Explanation

An unprivileged user who submits `DeleteAccount { beneficiary_id: <own account id> }` loses their **entire liquid balance** (`account.amount()`). The balance is permanently burned — added to `other_burnt_amount` — rather than transferred. This violates the protocol's documented invariant: *"the tokens are transferred to `beneficiary_id`"*.

The impact is bounded to the submitting account's own balance; no third-party funds are at risk. However, for accounts holding significant NEAR, the loss is total and irreversible.

---

### Likelihood Explanation

Low-to-medium. The scenario requires the user to supply their own account ID as `beneficiary_id`. Realistic triggers:

- A front-end or wallet that pre-fills the signer's account ID as the default beneficiary.
- A smart contract that programmatically constructs `DeleteAccount` and passes `env::current_account_id()` as the beneficiary (a natural but incorrect choice).
- A copy-paste error in a CLI or script.

The bug class (missing null/self-reference guard on a critical address parameter) is the direct nearcore analog of the external report.

---

### Recommendation

Pass `receiver` into `validate_delete_action` and reject the self-referential case:

```rust
// action_validation.rs
fn validate_delete_action(
    action: &DeleteAccountAction,
    receiver_id: &AccountId,
) -> Result<(), ActionsValidationError> {
    validate_action_account_id(&action.beneficiary_id)?;
    if action.beneficiary_id == *receiver_id {
        return Err(ActionsValidationError::InvalidAccountId {
            account_id: action.beneficiary_id.to_string(),
        });
    }
    Ok(())
}
```

Update the call site in `validate_action_with_mode`:

```rust
Action::DeleteAccount(a) => validate_delete_action(a, receiver),
```

This mirrors the existing pattern used by `validate_deterministic_state_init`, which already receives and checks `receiver_id`. [7](#0-6) 

---

### Proof of Concept

1. Account `"alice.near"` holds 100 NEAR (`account.amount() = 100 NEAR`).
2. Alice submits:
   ```
   Transaction {
     signer_id:   "alice.near",
     receiver_id: "alice.near",
     actions: [DeleteAccount { beneficiary_id: "alice.near" }]
   }
   ```
3. `validate_delete_action` passes — `"alice.near"` is syntactically valid.
4. `action_delete_account` executes:
   - Emits `Receipt::new_balance_refund("alice.near", 100 NEAR)` with `predecessor_id = "system"`.
   - Calls `remove_account(state_update, "alice.near")` — account deleted.
5. The balance-refund receipt is queued. When processed:
   - `receiver_id = "alice.near"` — account does not exist.
   - `check_account_existence` → `AccountDoesNotExist` (named account, not implicit).
   - `result.result = Err(...)`.
   - `predecessor_id.is_system() == true` → `other_burnt_amount += 100 NEAR`.
6. Alice's 100 NEAR is permanently burned. No error is surfaced to the original transaction outcome (the `DeleteAccount` action itself succeeded).

### Citations

**File:** runtime/runtime/src/action_validation.rs (L127-148)
```rust
/// Validates a single given action.
/// The `mode` only affects nested validation of `Action::Delegate` payloads
fn validate_action_with_mode(
    limit_config: &LimitConfig,
    action: &Action,
    receiver: &AccountId,
    current_protocol_version: ProtocolVersion,
    mode: ValidateReceiptMode,
) -> Result<(), ActionsValidationError> {
    match action {
        Action::CreateAccount(_) => Ok(()),
        Action::DeployContract(a) => validate_deploy_contract_action(limit_config, a),
        Action::DeployGlobalContract(a) => validate_deploy_global_contract_action(limit_config, a),
        Action::UseGlobalContract(a) => validate_use_global_contract_action(a),
        Action::FunctionCall(a) => {
            validate_function_call_action(limit_config, a, current_protocol_version, mode)
        }
        Action::Transfer(_) => Ok(()),
        Action::Stake(a) => validate_stake_action(a),
        Action::AddKey(a) => validate_add_key_action(limit_config, a, current_protocol_version),
        Action::DeleteKey(_) => Ok(()),
        Action::DeleteAccount(a) => validate_delete_action(a),
```

**File:** runtime/runtime/src/action_validation.rs (L388-392)
```rust
fn validate_delete_action(action: &DeleteAccountAction) -> Result<(), ActionsValidationError> {
    validate_action_account_id(&action.beneficiary_id)?;

    Ok(())
}
```

**File:** runtime/runtime/src/action_validation.rs (L424-438)
```rust
fn validate_deterministic_state_init(
    limit_config: &LimitConfig,
    action: &DeterministicStateInitAction,
    receiver_id: &AccountId,
) -> Result<(), ActionsValidationError> {
    validate_global_contract_identifier(action.state_init.code())?;

    let derived_id = derive_near_deterministic_account_id(&action.state_init);

    if derived_id != *receiver_id {
        return Err(ActionsValidationError::InvalidDeterministicStateInitReceiver {
            derived_id,
            receiver_id: receiver_id.clone(),
        });
    }
```

**File:** runtime/runtime/src/actions.rs (L349-356)
```rust
    // We use current amount as a pay out to beneficiary.
    let account_balance = account_ref.amount();
    if account_balance > Balance::ZERO {
        result
            .new_receipts
            .push(Receipt::new_balance_refund(&delete_account.beneficiary_id, account_balance));
    }
    let remove_result = remove_account(state_update, account_id)?;
```

**File:** runtime/runtime/src/actions.rs (L791-798)
```rust
        Action::Transfer(_) => {
            if account.is_none() {
                return check_transfer_to_nonexisting_account(
                    config,
                    account_id,
                    implicit_account_creation_eligible,
                );
            }
```

**File:** runtime/runtime/src/lib.rs (L926-934)
```rust
        let gas_refund_result = if receipt.predecessor_id().is_system() {
            // If the refund fails tokens are burned.
            if result.result.is_err() {
                stats.balance.other_burnt_amount = safe_add_balance(
                    stats.balance.other_burnt_amount,
                    total_deposit(&action_receipt.actions())?,
                )?
            }
            GasRefundResult::default()
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
