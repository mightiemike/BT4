### Title
DeleteAccount action allows funds to be permanently burnt by specifying a non-existent `beneficiary_id`, with no existence validation - ([File: runtime/runtime/src/actions.rs])

### Summary
`DeleteAccountAction.beneficiary_id` is only syntactically validated (that it is a well-formed `AccountId`), never checked for existence, before the account's entire balance is routed to it as a refund receipt. If the target account does not exist, the refund receipt fails and, per protocol, its balance is unconditionally burnt rather than returned or refused.

### Finding Description
`action_delete_account` computes the deleted account's balance and unconditionally pushes a `Receipt::new_balance_refund(&delete_account.beneficiary_id, account_balance)` whenever `account_balance > Balance::ZERO`, with no check that `beneficiary_id` corresponds to an existing account: [1](#0-0) 

Validation of the `DeleteAccount` action only enforces that `beneficiary_id` is a *syntactically* valid account id (via `validate_delete_action`/`validate_action_account_id`), analogous to `AccountId::validate`, but never checks that the account actually exists on-chain: [2](#0-1) 

Once the resulting balance-refund receipt executes against a non-existent `beneficiary_id`, the transfer action fails with `AccountDoesNotExist`, and because it is a system-predecessor refund receipt, the protocol documents and implements that "If the execution of a refund fails, the refund amount is burnt": the deposit is added to `other_burnt_amount` rather than returned to anyone: [3](#0-2) [4](#0-3) 

This is confirmed by an existing regression test that shows a balance-refund receipt sent to a non-existent (undeliverable) account fails with `AccountDoesNotExist` and does not create the account — i.e., the value is not routed anywhere and is lost: [5](#0-4) 

The documentation for `DeleteAccountAction` also only lists `InvalidAccountId` as a validation error for `beneficiary_id`, confirming existence is never checked at validation time: [6](#0-5) 

This closely mirrors the audit report's root cause: a caller-controlled address/account parameter (`beneficiary_id`, analogous to the report's zero-address constructor parameter) is accepted without existence/reachability validation, and once that unvalidated value is later used to move value, the funds cannot be withdrawn/recovered.

### Impact Explanation
Any account owner (or a relayer executing a `DeleteAccount` action via a batched receipt or delegate action) can supply a syntactically valid but non-existent `beneficiary_id`. The entire remaining balance of the deleted account is then permanently and irrecoverably burnt instead of being transferred to any account — a direct, concrete, protocol-level destruction/loss of user funds triggered entirely by a single unprivileged transaction, with no way to reverse or recover the tokens. This matches the "permanently frozen funds" / "unauthorized value movement (destruction)" acceptance criteria: the funds are neither delivered to the intended beneficiary nor kept safe for the original owner.

### Likelihood Explanation
Likelihood is high in the sense that it is trivially reachable: any account holder (or a contract acting on their behalf via a batched action / meta-transaction) can trigger this by mistyping, mis-scripting, or maliciously choosing a beneficiary account id (e.g., a typo'd account, an account that was never created, or one that has since been deleted). No special privileges, races, or validator collusion are required — a single `DeleteAccount` action with a bad `beneficiary_id` is sufficient.

### Recommendation
Before executing `action_delete_account`, verify that `beneficiary_id` corresponds to an existing account (similar to how `Transfer`/regular actions fail with `AccountDoesNotExist` at execution but *before* committing to delete the source account), and reject the `DeleteAccount` action (return an `ActionErrorKind` such as `BeneficiaryAccountDoesNotExist`) rather than deleting the source account and letting the refund silently burn. Alternatively, do not treat a failed post-delete refund as an unconditional burn — instead refuse the whole `DeleteAccount` action up front if the beneficiary account does not exist.

### Proof of Concept
1. Alice owns account `alice.near` with balance `X`.
2. Alice (or a relayer with a delegated action) submits a `DeleteAccount` action with `beneficiary_id = "nonexistent123.near"` — a syntactically valid `AccountId` that does not exist on-chain.
3. `action_delete_account` computes `account_balance = X`, pushes `Receipt::new_balance_refund(&"nonexistent123.near", X)`, and deletes `alice.near`: [7](#0-6) 
4. The refund receipt executes with `predecessor_id == "system"` against a receiver that does not exist; the transfer fails with `AccountDoesNotExist`, and because it is a refund receipt, the protocol burns the deposit instead of retrying or refunding the original owner: [4](#0-3) 
5. Result: Alice's entire balance `X` is permanently destroyed; neither Alice nor `nonexistent123.near` ever receives it. This is directly demonstrated by the existing test `refund_may_not_create_universal_account`, which shows a balance-refund receipt to a non-existent id fails and the funds are not delivered anywhere: [8](#0-7)

### Citations

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

**File:** runtime/runtime/src/action_validation.rs (L602-608)
```rust
fn validate_action_account_id(account_id: &AccountId) -> Result<(), ActionsValidationError> {
    AccountId::validate(account_id.as_str()).map_err(|_| {
        ActionsValidationError::InvalidAccountId { account_id: account_id.to_string() }
    })?;

    Ok(())
}
```

**File:** docs/RuntimeSpec/Refunds.md (L10-13)
```markdown
Refund receipts are identified by having `predecessor_id == "system"`. They are also special because they don't cost any gas to generate or execute. As a result, they also do not contribute to the block gas limit.

If the execution of a refund fails, the refund amount is burnt.
The refund receipt is an `ActionReceipt` that consists of a single action `Transfer` with the `deposit` amount of the refund.
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

**File:** runtime/runtime/src/tests/apply.rs (L6882-6926)
```rust
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

**File:** docs/RuntimeSpec/Actions.md (L278-300)
```markdown
## DeleteAccountAction

```rust
pub struct DeleteAccountAction {
    /// The remaining account balance will be transferred to the AccountId below
    pub beneficiary_id: AccountId,
}
```

**Outcomes**:

- The account, as well as all the data stored under the account, is deleted and the tokens are transferred to `beneficiary_id`.

### Errors

**Validation Error**:

- If `beneficiary_id` is not a valid account id, the following error will be returned

```rust
/// Invalid account ID.
InvalidAccountId { account_id: AccountId },
```
```
