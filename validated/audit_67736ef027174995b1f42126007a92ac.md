## Title
`DeleteAccount` funds sent to a nonexistent `beneficiary_id` are silently burned instead of refunded to the deleting account - (File: `runtime/runtime/src/actions.rs`)

### Summary
The `Action::DeleteAccount` handler transfers the deleted account's remaining balance to an attacker/user-controlled `beneficiary_id` via a system-generated balance-refund receipt, but never validates that this account actually exists. If it doesn't (or ever stops existing before the receipt executes), the refund receipt fails and the funds are permanently destroyed rather than returned to anyone, exactly mirroring the referenced ERC721 issue where an asset is sent to a recipient unable to receive it and becomes unrecoverable.

### Finding Description
`validate_delete_action` only checks that `beneficiary_id` is a syntactically valid `AccountId`, never that the account exists: [1](#0-0) 

When the `DeleteAccount` action executes, the deleting account's remaining balance is unconditionally pushed as a system `Receipt::new_balance_refund` to `beneficiary_id`, and the account is removed immediately: [2](#0-1) 

That refund receipt is executed later with `predecessor_id == "system"`. Because refunds are treated as a special `is_refund` receipt shape, they can never implicitly create an account for the beneficiary: [3](#0-2) 

If `beneficiary_id` does not exist, `check_account_existence` rejects the `Transfer` action inside the refund receipt with `AccountDoesNotExist`, as confirmed by the test `refund_may_not_create_universal_account`: [4](#0-3) 

And per the protocol's documented refund semantics, when a refund receipt fails, its deposit is not returned anywhere — it is burnt: [5](#0-4) 

This is also called out explicitly in test comments in this codebase itself, describing this as expected (but destructive) behavior: [6](#0-5) 

The `beneficiary_id` is fully controlled by the unprivileged transaction signer deleting their own account (or any account they hold a full-access key for) — there is no requirement that the beneficiary exist at submission time or even remain existing until the delete receipt executes (e.g., another transaction/receipt could delete the beneficiary account concurrently in the same or an adjacent block, a scenario feasible under cross-shard/async receipt execution).

### Impact Explanation
Unlike a normal failed transfer (which is rejected before any balance leaves the source account), here the account is deleted and its balance is *already committed* to a refund receipt targeting a target that cannot receive it. The result is an irreversible, protocol-level burn of the user's NEAR balance — a "permanently frozen" (in this case destroyed) funds condition triggered entirely by a mistake or race in a single, unprivileged transaction, with no way for the depositor or the intended beneficiary to recover the value.

### Likelihood Explanation
This is trivially reachable by any account holder: submit a `DeleteAccount` action naming a misspelled, deleted, or not-yet-created account as `beneficiary_id`. It requires no special privileges, no contract interaction, and no validator collusion — a simple typo or a benign race (beneficiary account deleted between delete-transaction submission and refund-receipt execution) is sufficient to trigger fund loss.

### Recommendation
Before allowing `DeleteAccount` to proceed (or before generating the balance-refund receipt), verify that `beneficiary_id` corresponds to an existing account, or route the refund through a mechanism that can create the beneficiary account (similar to how ordinary `Transfer` actions are allowed to implicitly create NEAR-implicit accounts) instead of using the refund path that can never create accounts. At minimum, disallow non-existent beneficiaries at validation time so the user gets a clear failure instead of silently burning their balance.

### Proof of Concept
1. Account `alice.near` holds funds and wants to delete itself.
2. Alice submits `SignedTransaction` with `Action::DeleteAccount(DeleteAccountAction { beneficiary_id: "typo-account.near" })`, where `typo-account.near` does not exist (validated only for syntax by `validate_delete_action`).
3. `action_delete_account` removes `alice.near` and enqueues `Receipt::new_balance_refund("typo-account.near", alice_balance)`.
4. When that refund receipt executes, `check_account_existence` rejects the inner `Transfer` because `implicit_creation_allowed` returns `false` for a refund (`is_refund == true`), yielding `ActionErrorKind::AccountDoesNotExist`.
5. Per documented behavior, the failed refund's deposit is burnt — `alice`'s balance is gone forever, credited to nobody.

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

**File:** runtime/runtime/src/tests/apply.rs (L6355-6357)
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
