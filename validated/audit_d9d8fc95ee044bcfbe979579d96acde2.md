### Title
DeleteAccount lets a user route their own account's balance to a beneficiary that does not exist, permanently burning the funds - (File: `runtime/runtime/src/action_validation.rs`, `runtime/runtime/src/actions.rs`)

### Summary
`validate_delete_action` only checks that `DeleteAccountAction.beneficiary_id` is a syntactically valid `AccountId` string, never that the account actually exists. [1](#0-0)  `action_delete_account` then unconditionally routes the deleted account's remaining balance to that beneficiary as a system-generated balance-refund receipt, regardless of whether the beneficiary account exists. [2](#0-1) 

### Finding Description
This is the same bug class as the reported `buyShares` issue: an unprivileged, user-supplied recipient address is accepted without an existence/validity check, and funds sent to it are irrecoverably lost.

In nearcore, `DeleteAccountAction` is a normal transaction action any account owner can submit against their own account (`beneficiary_id` is just an `AccountId` chosen by the caller). At validation time, `validate_delete_action` calls `validate_action_account_id(&action.beneficiary_id)`, which enforces only that the string is a well-formed account id, not that the account exists in state. [1](#0-0) 

When the delete actually executes, `action_delete_account` takes the account's current balance and pushes a new receipt via `Receipt::new_balance_refund(&delete_account.beneficiary_id, account_balance)` — i.e., it is dispatched as a refund receipt (`predecessor_id == "system"`) addressed to `beneficiary_id`. [2](#0-1) 

Refund receipts are explicitly barred from implicitly creating an account: `implicit_creation_allowed` returns `false` whenever `is_refund` is true, "Refund can never create an account." [3](#0-2)  So if `beneficiary_id` does not correspond to an existing account, `check_account_existence` rejects the transfer with `AccountDoesNotExist`, which is confirmed by the test `refund_may_not_create_universal_account` showing exactly this failure mode for a refund aimed at a nonexistent account. [4](#0-3) 

Per the refunds specification, a refund receipt that fails execution has its deposit burned rather than returned to anyone: "If the execution of a refund fails, the refund amount is burnt." [5](#0-4)  This burn path is implemented directly in `apply_action_receipt`: when `receipt.predecessor_id().is_system()` and `result.result.is_err()`, the total deposit of the failed refund is added to `stats.balance.other_burnt_amount`. [6](#0-5)  A unit test explicitly documents this precondition for a successful delete: "The beneficiary has to exist, otherwise the balance transfer the delete sends it would come straight back as a refund" and burn. [7](#0-6) 

Thus, a user who deletes their own account and (accidentally or via a typo/UI bug/relayer bug in a meta-transaction) supplies a syntactically valid but non-existent `beneficiary_id` will have their entire remaining account balance permanently destroyed (burned into validator rewards / removed from total supply) rather than returned or preserved — mirroring the `buyShares(address(0))` fund-lock bug, except here it is an outright burn.

### Impact Explanation
Any signer can permanently destroy their own account's balance with a single `DeleteAccount` transaction whose `beneficiary_id` does not exist. This is unauthorized, irreversible value destruction reachable purely from a transaction signer (no privileged role, no malicious peer/validator, no network condition needed), matching the "permanently frozen/lost funds" acceptance criterion.

### Likelihood Explanation
Likelihood is moderate: an attacker cannot steal *another* account's funds this way, but a user (or a wallet/relayer/meta-transaction application constructing this action on the user's behalf) that supplies a mistyped, unregistered, or since-deleted `beneficiary_id` will silently and irreversibly burn the deleted account's balance. There is no protocol-level guard preventing submission of such a transaction; the only safeguard is client-side UX diligence, exactly analogous to the audited Solidity issue.

### Recommendation
Add a beneficiary-existence check (or an explicit non-refund/non-burn fallback) at validation or execution time for `DeleteAccountAction`: e.g., verify `beneficiary_id` corresponds to an existing account before permitting the delete, or route the balance to a receipt shape that is allowed to implicitly create the beneficiary account instead of using the refund path that unconditionally forbids account creation and burns on failure.

### Proof of Concept
1. Account `alice.near` holds balance `B` and submits `Action::DeleteAccount(DeleteAccountAction { beneficiary_id: "nobody.near" })` where `nobody.near` is a syntactically valid but non-existent account id.
2. `validate_delete_action` accepts the action since it only validates the string format. [1](#0-0) 
3. `action_delete_account` removes `alice.near` and emits `Receipt::new_balance_refund(&"nobody.near", B)`. [2](#0-1) 
4. When that refund receipt is applied, `check_account_existence`/`implicit_creation_allowed` reject the transfer because refunds can never create an account, producing `AccountDoesNotExist`. [3](#0-2) 
5. Because the receipt's predecessor is `system` and it failed, its deposit `B` is added to `other_burnt_amount` and permanently removed from circulation. [6](#0-5) 

This is directly demonstrated by the existing regression test, which notes the beneficiary must exist "otherwise the balance transfer the delete sends it would come straight back as a refund" and be burned. [7](#0-6)

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

**File:** runtime/runtime/src/actions.rs (L928-933)
```rust
/// Whether a transfer to an account that does not exist yet may create it.
fn implicit_creation_allowed(account_type: AccountType, receipt_shape: ReceiptShape) -> bool {
    let ReceiptShape { is_refund, is_the_only_action } = receipt_shape;
    if is_refund {
        return false; // Refund can never create an account
    }
```

**File:** runtime/runtime/src/tests/apply.rs (L6354-6357)
```rust

        // The beneficiary has to exist, otherwise the balance transfer the delete
        // sends it would come straight back as a refund.
        let beneficiary: AccountId = "beneficiary.near".parse().unwrap();
```

**File:** runtime/runtime/src/tests/apply.rs (L6880-6926)
```rust
    /// The other half of the old gate, untouched by the relaxation: refunds are
    /// free, so they must not create an account, a `0u` one included.
    #[test]
    fn refund_may_not_create_universal_account() {
        init_test_logger();
        let key = SecretKey::from_seed(KeyType::ED25519, "refund-target").public_key();
        let account_id = derive_universal_account_id(&state_init_for(&[key]).to_raw());
        let (runtime, tries, root, apply_state, _signers, epoch) = setup_runtime(
            vec![alice_account()],
            Balance::from_near(100),
            Balance::ZERO,
            Gas::from_teragas(1000),
        );

        let result = runtime
            .apply(
                tries.get_trie_for_shard(ShardUId::single_shard(), root),
                &None,
                &apply_state,
                from_ref(&Receipt::new_balance_refund(&account_id, funding())),
                SignedValidPeriodTransactions::empty(),
                &epoch,
                Default::default(),
            )
            .unwrap();
        let mut store_update = tries.store_update();
        let new_root =
            tries.apply_all(&result.trie_changes, ShardUId::single_shard(), &mut store_update);
        store_update.commit();

        // Assert on the reason, not just the absence: without this the test would
        // also pass if the refund receipt were dropped instead of refused.
        let [outcome] = &result.outcomes[..] else {
            panic!("the refund receipt must produce exactly one outcome, got {:?}", result.outcomes)
        };
        assert_matches!(
            &outcome.outcome.status,
            ExecutionStatus::Failure(TxExecutionError::ActionError(err))
                if matches!(err.kind, ActionErrorKind::AccountDoesNotExist { .. }),
            "a refund to a missing `0u` id must fail with AccountDoesNotExist",
        );
        let state = tries.new_trie_update(ShardUId::single_shard(), new_root);
        assert!(
            get_account(&state, &account_id).unwrap().is_none(),
            "a refund must not bring a `0u` account into existence",
        );
    }
```

**File:** docs/RuntimeSpec/Refunds.md (L10-13)
```markdown
Refund receipts are identified by having `predecessor_id == "system"`. They are also special because they don't cost any gas to generate or execute. As a result, they also do not contribute to the block gas limit.

If the execution of a refund fails, the refund amount is burnt.
The refund receipt is an `ActionReceipt` that consists of a single action `Transfer` with the `deposit` amount of the refund.
```

**File:** runtime/runtime/src/lib.rs (L1047-1055)
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
