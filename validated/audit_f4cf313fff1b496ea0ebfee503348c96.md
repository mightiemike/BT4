### Title
DeleteAccount `beneficiary_id` is only syntax-checked, not existence-checked — remaining balance can be permanently burnt on an unrecoverable refund - ([File: runtime/runtime/src/actions.rs])

### Summary
The Sherlock report describes an operator/admin field (`feeRecipient`) that is validated only for basic sanity (non-zero at set time) but not re-checked at the moment funds are routed, so tokens can be sent to `address(0)` and become permanently unrecoverable. The analogous reachable pattern in nearcore is `DeleteAccountAction::beneficiary_id`: a value fully controlled by an ordinary transaction signer, which the protocol validates only for AccountId *syntax* (`validate_action_account_id`) and never for *existence* before the account's remaining balance is committed to it. If the chosen beneficiary account does not exist (and is not an implicit-account id that the refund path is allowed to auto-create), the resulting balance-refund receipt fails and — per the refund semantics — the transferred amount is burnt rather than returned to anyone, i.e. permanently lost, exactly analogous to fee flowing to the "zero address" in the original report.

### Finding Description
`validate_delete_action` in `runtime/runtime/src/action_validation.rs:447-451` only calls `validate_action_account_id(&action.beneficiary_id)`, which checks that the string is a syntactically valid `AccountId` — it does not check that the account actually exists or is reachable. [1](#0-0) 

When `action_delete_account` executes (`runtime/runtime/src/actions.rs:330-406`), the account's entire remaining balance is packaged into a `Receipt::new_balance_refund(&delete_account.beneficiary_id, account_balance)` and the account is deleted immediately: [2](#0-1) 

This new receipt is a "refund" receipt (`predecessor_id == "system"`). Per the refund model documented in the codebase: "If the execution of a refund fails, the refund amount is burnt," and a failed refund's deposit is folded into `other_burnt_amount` rather than returned anywhere. [3](#0-2) [4](#0-3) 

The refund's inner action is a `Transfer` to `beneficiary_id`. `check_account_existence` for `Action::Transfer` only permits creating the receiver implicitly if `implicit_creation_allowed` returns true for that account type/receipt shape; for a syntactically-valid, non-implicit, but *never-created* named account, the check fails with `AccountDoesNotExist`, causing the whole refund receipt to fail: [5](#0-4) 

A test in the codebase explicitly documents this exact hazard: "The beneficiary has to exist, otherwise the balance transfer the delete sends it would come straight back as a refund" — i.e., contributors are aware that an unknown/non-existent beneficiary breaks the transfer, and the test deliberately pre-creates the beneficiary account to avoid the failure path: [6](#0-5) 

No protocol-level check anywhere in `DeleteAccountAction` validation or execution verifies that `beneficiary_id` refers to an existing (or implicit-creatable) account before the source account and its balance are destroyed. The only gate is a string-format check, exactly parallel to the Surge `Factory.getFee()` bug where only `feeMantissa` was gated but `feeRecipient == address(0)` was not, letting value flow to an address that can never claim it.

### Impact Explanation
Any account holder who submits a `DeleteAccount` transaction with a beneficiary account id that is syntactically valid but does not exist (a simple typo, an account that was itself deleted between signing and execution, or one that was never created) will have their entire remaining NEAR balance permanently burnt instead of transferred — the funds are not credited to anyone and cannot be recovered by the user, the beneficiary, or the validator set beyond the already-modeled burn-goes-to-validators-via-total-supply mechanism. This is a concrete, unauthorized/unintended permanent loss of user funds triggered entirely by a single signed transaction from an unprivileged account — no attacker cooperation, validator misbehavior, or special privilege is required. This matches the required impact class ("permanently frozen/lost funds").

### Likelihood Explanation
High reachability: any account owner can trigger this merely by supplying an incorrect (but valid-format) `beneficiary_id` to `DeleteAccount` — a normal, everyday action (e.g., closing an account and specifying a refund destination). Because the protocol performs no existence check at signing time (RPC does not enforce it, and the on-chain validator only checks the string format), users have no on-chain feedback preventing this mistake until the receipt has already executed and the source account is gone. The scenario is not restricted to malicious actors, malicious validators, or network conditions — it is directly reachable from a standard user-submitted transaction.

### Recommendation
Add an existence check for `beneficiary_id` prior to (or as part of) the balance-refund path in `action_delete_account`, mirroring how `Action::Transfer` optionally allows implicit-account creation: either (a) reject the `DeleteAccount` action outright if `beneficiary_id` does not exist and is not an implicit/creatable account type, or (b) allow the balance-refund receipt itself to create the destination account (treat it consistently with the implicit-creation rules used for ordinary transfers) instead of failing and burning the funds. At minimum, surface a clear, pre-execution warning/validation error (`AccountDoesNotExist`) at the point the `DeleteAccount` action is validated, not only after the funds have already been irreversibly burnt.

### Proof of Concept
1. Create account `alice.near` with balance `B`.
2. Never create account `nobody.near` (syntactically valid `AccountId`, but no such account exists on-chain).
3. `alice.near` submits `SignedTransaction` with `Action::DeleteAccount(DeleteAccountAction { beneficiary_id: "nobody.near" })`.
4. `action_delete_account` (`runtime/runtime/src/actions.rs:380-387`) removes `alice.near` and enqueues `Receipt::new_balance_refund("nobody.near", B)`.
5. During execution of that refund receipt, `check_account_existence` for the inner `Transfer` action rejects it with `AccountDoesNotExist` because `nobody.near` is not implicit and the account is missing (`runtime/runtime/src/actions.rs:842-850`).
6. Per the refund model (`docs/RuntimeSpec/Refunds.md:10-13`), because this is a refund receipt (`predecessor_id == "system"`) whose execution failed, the deposit `B` is burnt into `other_burnt_amount` rather than returned to `alice.near` or credited to `nobody.near`.
7. Result: `B` yoctoNEAR is permanently and irrecoverably removed from circulation/from any account's control — analogous to sending the Surge protocol fee to the zero address.

### Citations

**File:** runtime/runtime/src/action_validation.rs (L447-451)
```rust
fn validate_delete_action(action: &DeleteAccountAction) -> Result<(), ActionsValidationError> {
    validate_action_account_id(&action.beneficiary_id)?;

    Ok(())
}
```

**File:** runtime/runtime/src/actions.rs (L380-387)
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

**File:** docs/RuntimeSpec/Refunds.md (L10-13)
```markdown
Refund receipts are identified by having `predecessor_id == "system"`. They are also special because they don't cost any gas to generate or execute. As a result, they also do not contribute to the block gas limit.

If the execution of a refund fails, the refund amount is burnt.
The refund receipt is an `ActionReceipt` that consists of a single action `Transfer` with the `deposit` amount of the refund.
```

**File:** protocol-model/spec/runtime-execution.md (L152-152)
```markdown
- **Refund receipts are free**: system-predecessor receipts burn zero gas; a failed refund burns its deposit into `other_burnt_amount` rather than refunding (`runtime/runtime/src/lib.rs:929`, `:972`).
```

**File:** runtime/runtime/src/tests/apply.rs (L6342-6363)
```rust
    /// `DeleteAccount` as the final action, which `DeleteActionMustBeFinal`
    /// makes the only position it can take. The init initializes the account and
    /// the delete then removes it, both inside one receipt, so the account is
    /// gone by the end of the chunk.
    #[test]
    fn delete_after_init_removes_account() {
        init_test_logger();
        let signer = signer_for("bootstrap-then-delete");
        let state_init = state_init_for(&[signer.public_key()]);
        let account_id = derive_universal_account_id(&state_init.to_raw());
        let balance = Balance::from_near(10);
        let (runtime, tries, root, apply_state, epoch) = setup(&account_id, balance);

        // The beneficiary has to exist, otherwise the balance transfer the delete
        // sends it would come straight back as a refund.
        let beneficiary: AccountId = "beneficiary.near".parse().unwrap();
        let mut state = tries.new_trie_update(ShardUId::single_shard(), root);
        set_account(
            &mut state,
            beneficiary.clone(),
            &Account::new(Balance::from_near(1), Balance::ZERO, AccountContract::None, 100),
        );
```
