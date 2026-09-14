Confirmed: `validate_delete_action` only checks that `beneficiary_id` is syntactically valid via `validate_action_account_id` (`AccountId::validate`), with no check that the account actually exists. [1](#0-0) 

### Title
Unvalidated `beneficiary_id` in `DeleteAccountAction` permanently burns transferred funds instead of allowing correction - (File: `runtime/runtime/src/actions.rs`)

### Summary
`DeleteAccountAction` lets an account owner delete their own account and redirect its full remaining balance to an arbitrary `beneficiary_id`. The only validation performed on that field is a syntactic `AccountId::validate` check; there is no check that the beneficiary account actually exists. [1](#0-0) [2](#0-1) 

### Finding Description
`action_delete_account` uses the account's current amount as the payout, unconditionally generating a balance-refund receipt to `delete_account.beneficiary_id` and then deleting the account and its state, all in the same, final, irreversible step: [3](#0-2) 

There is no mechanism analogous to a two-step "nominate then claim" pattern; the moment the transaction executes, the account is gone and the payout receipt is in flight to whatever `beneficiary_id` was specified, correct or not — the exact same "commit funds to an address before it can be corrected" pattern flagged in the reference report for Arrakis's `setManager`.

If the given `beneficiary_id` is syntactically valid but does not correspond to an existing account (e.g., a typo, or an account that was never created), the resulting balance-refund receipt fails execution with `AccountDoesNotExist`. Per documented refund semantics, when a refund receipt fails, the amount is not returned to anyone — it is burnt outright: [4](#0-3) 
This is directly exercised/asserted by `refund_may_not_create_universal_account`, which shows a refund to a non-existent id fails with `AccountDoesNotExist` and never creates the account, i.e. the deposit is lost: [5](#0-4) 

Because `DeleteAccount` must be the final action in its receipt (enforced by `DeleteActionMustBeFinal`), there is no way to bundle a "verify beneficiary exists" step or roll back if the beneficiary turns out to be wrong — the delete and the fund transfer are atomically bound together, just like `setManager`'s combined "withdraw + reassign" in the reference report. [6](#0-5) 

### Impact Explanation
An unprivileged account owner who submits a `DeleteAccount` transaction with a mistyped or otherwise non-existent (but syntactically valid) `beneficiary_id` permanently and irrecoverably loses their entire account balance — it is burnt from total supply rather than refunded to the sender or held recoverably. This matches the accepted impact category of permanently frozen/lost funds triggered by a single unprivileged transaction, with no code path allowing correction once the transaction executes (the account and its state are already deleted by the time the beneficiary-existence failure is detected).

### Likelihood Explanation
Likelihood is user-error driven but the trigger requires nothing more than a single signed `DeleteAccount` transaction from the account owner — no special privileges, no attacker cooperation, and no way to preview or dry-run the outcome for a nonexistent beneficiary before the account and its funds are gone. Given `beneficiary_id` is free-form (only syntactically validated), a copy-paste or typo error is a realistic real-world occurrence, especially since NEAR account IDs are strings entered by users/wallets.

### Recommendation
Before executing the account deletion, verify that `beneficiary_id` corresponds to an existing account in state, and fail the `DeleteAccount` action (returning an actionable error, without burning funds and without deleting the account) if it does not. Alternatively, adopt a two-phase pattern: emit the balance transfer and account deletion in a receipt that requires beneficiary account existence to be checked at receipt-creation validation time (`validate_delete_action` / `action_delete_account`) rather than deferring the failure to the refund receipt's execution, where funds are already forfeited by design.

### Proof of Concept
1. Create account `alice.near` with a positive balance.
2. Submit a signed transaction from `alice.near` with a single `DeleteAccountAction { beneficiary_id: "typo123.near" }`, where `typo123.near` has never been created.
3. `validate_delete_action` accepts the action because `"typo123.near"` parses as a valid `AccountId` (`runtime/runtime/src/action_validation.rs:447-451`).
4. `action_delete_account` deletes `alice.near` and queues a balance-refund receipt to `typo123.near` for the full balance (`runtime/runtime/src/actions.rs:380-387`).
5. The refund receipt executes against a receiver that doesn't exist, fails with `ActionErrorKind::AccountDoesNotExist`, and per `refund_unspent_gas_and_deposits`/refund semantics the deposit is burnt rather than returned to `alice.near` or anyone else (`docs/RuntimeSpec/Refunds.md:10-13`, illustrated by the analogous test `refund_may_not_create_universal_account` at `runtime/runtime/src/tests/apply.rs:6880-6926`).
6. Net effect: `alice.near`'s account and entire balance are permanently gone; no recovery path exists.

### Citations

**File:** runtime/runtime/src/action_validation.rs (L127-131)
```rust
    while let Some(action) = iter.next() {
        if let Action::DeleteAccount(_) = action {
            if iter.peek().is_some() {
                return Err(ActionsValidationError::DeleteActionMustBeFinal);
            }
```

**File:** runtime/runtime/src/action_validation.rs (L447-451)
```rust
fn validate_delete_action(action: &DeleteAccountAction) -> Result<(), ActionsValidationError> {
    validate_action_account_id(&action.beneficiary_id)?;

    Ok(())
}
```

**File:** core/primitives/src/action/mod.rs (L61-75)
```rust
#[derive(
    BorshSerialize,
    BorshDeserialize,
    PartialEq,
    Eq,
    Clone,
    Debug,
    serde::Serialize,
    serde::Deserialize,
    ProtocolSchema,
)]
#[cfg_attr(feature = "schemars", derive(schemars::JsonSchema))]
pub struct DeleteAccountAction {
    pub beneficiary_id: AccountId,
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

**File:** docs/RuntimeSpec/Refunds.md (L10-13)
```markdown
Refund receipts are identified by having `predecessor_id == "system"`. They are also special because they don't cost any gas to generate or execute. As a result, they also do not contribute to the block gas limit.

If the execution of a refund fails, the refund amount is burnt.
The refund receipt is an `ActionReceipt` that consists of a single action `Transfer` with the `deposit` amount of the refund.
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
