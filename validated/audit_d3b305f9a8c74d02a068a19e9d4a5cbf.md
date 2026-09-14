### Title
DeleteAccount to a non-existent `beneficiary_id` permanently burns the account's entire balance instead of failing atomically - (File: `runtime/runtime/src/actions.rs`)

### Summary
`DeleteAccountAction` lets a transaction signer delete their own account and redirect its remaining balance to an arbitrary `beneficiary_id`. The action only validates that `beneficiary_id` is a syntactically well-formed account id, never that the account actually exists. The account is deleted immediately, and the balance payout is dispatched as a separate system `balance_refund` receipt. If that follow-up receipt's target account does not exist, the refund receipt fails and the entire deposit is unconditionally burned, exactly mirroring the reported bug class where a value-transfer step to an uncontrollable "receiver" is decoupled from the main operation and its failure causes irrecoverable loss instead of aborting the whole action.

### Finding Description
`validate_delete_action` only checks that `beneficiary_id` parses as a valid `AccountId`; it performs no existence check: [1](#0-0) 

`action_delete_account` then unconditionally removes the account and enqueues a `Receipt::new_balance_refund(&delete_account.beneficiary_id, account_balance)` for the entire remaining balance, again without verifying the beneficiary exists: [2](#0-1) 

The balance-refund receipt is a system-predecessor "refund" receipt. `implicit_creation_allowed` explicitly forbids refunds from creating any account, of any type: [3](#0-2) 

Consequently, `check_account_existence` rejects a `Transfer` action addressed to a missing account when the receipt is a refund: [4](#0-3) 

This exact scenario is already reproduced by an existing test that asserts a refund to a non-existent account fails with `AccountDoesNotExist` and never creates the account: [5](#0-4) 

When a refund receipt fails, the runtime burns the entire deposit instead of returning it anywhere, per the documented and implemented behavior: [6](#0-5) [7](#0-6) 

Because the `DeleteAccount` action and the balance-payout `Transfer` are two separate receipts (the account removal commits before the refund receipt is even generated/executed), the operation is not atomic: the account and its access keys are destroyed unconditionally in step one, and only in step two — processed later, possibly in a later chunk/block, and with no way to roll back step one — does the transfer to `beneficiary_id` get attempted. If `beneficiary_id` does not exist at that time, there is no receiver capable of accepting the balance and no path for the funds to return to the original owner; they are simply destroyed.

### Impact Explanation
Any unprivileged transaction signer can permanently destroy their own account's entire remaining NEAR balance by submitting a single `DeleteAccount` action with a `beneficiary_id` that does not (yet) exist — e.g., a typo, a not-yet-created named account, or an account that gets deleted/never created between transaction construction and execution. This is a direct, transaction-triggered, unrecoverable loss of user funds ("permanently frozen/burned funds"), matching the accepted impact categories for this analysis. It is also a common integration pitfall for wallets/relayers that programmatically choose `beneficiary_id` (e.g., "0u"/deterministic sub-accounts or freshly-derived implicit accounts that haven't been funded/created yet), since nothing in transaction validation or execution warns or reverts atomically — the account is gone and the money is burned rather than the whole `DeleteAccount` failing or the funds being returned to the signer.

### Likelihood Explanation
Reachable with a single ordinary transaction from any account holder — no special privileges, no cross-shard timing tricks, and no validator/network cooperation required. The only precondition is that the chosen `beneficiary_id` does not exist in state at the time the refund receipt executes, which is trivially achievable (mistyped account, unfunded implicit/deterministic id, or a beneficiary account deleted between signing and execution).

### Recommendation
Before removing the account in `action_delete_account`, verify that `beneficiary_id` refers to an existing account (this may require deferring the actual account removal until the beneficiary's existence, possibly cross-shard, is confirmed, or emitting the transfer first and only removing the account on success/via a callback). Alternatively, on refund failure for a `DeleteAccount`-originated balance refund specifically, redirect unclaimed funds back to a durable fallback (e.g., burn only truly-unclaimed protocol-owned dust, not full user balances) rather than silently burning the entire account balance, and document/loudly surface this risk to clients constructing `DeleteAccountAction`.

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

**File:** runtime/runtime/src/actions.rs (L928-933)
```rust
/// Whether a transfer to an account that does not exist yet may create it.
fn implicit_creation_allowed(account_type: AccountType, receipt_shape: ReceiptShape) -> bool {
    let ReceiptShape { is_refund, is_the_only_action } = receipt_shape;
    if is_refund {
        return false; // Refund can never create an account
    }
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
