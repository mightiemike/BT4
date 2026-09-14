### Title
DeleteAccount action can permanently burn an account's balance when `beneficiary_id` does not exist - ([File: runtime/runtime/src/actions.rs])

### Summary
The `DeleteAccount` action lets an account holder redirect their remaining balance to a `beneficiary_id` of their choosing. Like the reported UXD `mint()` issue — where the `receiver` parameter is never checked for reachability before tokens are minted to it — nearcore's `validate_delete_action` only checks that `beneficiary_id` is a syntactically valid `AccountId`; it never checks that the account actually exists. Because the balance transfer is implemented as a **refund receipt**, which by protocol design can never create a new account and simply burns its deposit on failure, a `beneficiary_id` that does not exist (typo, deleted account, account never created) causes the entire remaining balance of the deleted account to be permanently destroyed instead of delivered.

### Finding Description
`action_delete_account` computes the account's remaining balance and unconditionally queues a `Receipt::new_balance_refund(&delete_account.beneficiary_id, account_balance)`, then deletes the account: [1](#0-0) 

`Receipt::new_balance_refund` builds a system-predecessor ("refund") `ActionReceipt` containing a single `Transfer` action: [2](#0-1) 

Validation of the `DeleteAccountAction` only verifies the `beneficiary_id` is a well-formed account id — it never checks the account exists or is reachable: [3](#0-2) 

When this refund receipt is later processed, `check_account_existence` explicitly forbids refund receipts from implicitly creating any account (named, near-implicit, eth-implicit, deterministic, or universal): [4](#0-3) 

If the target account does not exist, the `Transfer` action inside the refund fails with `AccountDoesNotExist`. Because this is a refund receipt (`predecessor_id().is_system()`), the runtime does not bounce it back or retry — it simply burns the deposit: [5](#0-4) 

This is confirmed by the codebase's own test coverage, which documents the burn as the expected outcome of a refund to a non-existent account: [6](#0-5) 

and by `docs/RuntimeSpec/Refunds.md`, which states plainly: "If the execution of a refund fails, the refund amount is burnt." [7](#0-6) 

The overall effect: any signer can submit a `DeleteAccount` action (directly, via `promise_batch_action_delete_account`, or via a meta-transaction) with a `beneficiary_id` that passes syntactic validation but does not correspond to an existing account, and their entire remaining NEAR balance is irrecoverably destroyed.

### Impact Explanation
This results in concrete, permanent loss of funds — the deleted account's full remaining balance is burnt rather than transferred, with no possibility of recovery, exactly mirroring the "loss of collateral funds" impact described in the reference report (funds sent to an unreachable receiver are unusable). Because the amount destroyed can be the account's entire balance (up to all its NEAR), this constitutes a Medium-severity, transaction-triggered, permanent-loss-of-funds condition reachable by any unprivileged signer acting on their own account.

### Likelihood Explanation
The path requires nothing beyond a single, ordinary `DeleteAccount` action submitted by the account owner (or a relayer via a meta-transaction on the owner's behalf) with an incorrect/mistyped/non-existent `beneficiary_id`. No special privileges, races, or malicious infrastructure are required — only a client bug, a typo, or a stale/deleted beneficiary account, all of which are realistic operational occurrences (e.g., wallets, SDKs, or scripts that fail to verify the beneficiary account exists before submitting the delete transaction).

### Recommendation
Before generating the balance-refund receipt in `action_delete_account`, verify that `beneficiary_id` corresponds to an existing account (a state lookup, analogous to what `check_account_existence` already performs for other actions), and reject the `DeleteAccount` action with a clear error (e.g., a new `BeneficiaryAccountDoesNotExist` `ActionErrorKind`) if it does not. This mirrors the reference recommendation of adding a sanity check for receiver/beneficiary existence before moving value to it.

### Proof of Concept
1. Attacker/careless user account `alice.near` holds `N` NEAR.
2. `alice.near` submits a transaction with a single `DeleteAccount { beneficiary_id: "typo-account.near" }` action, where `typo-account.near` was never created (or has itself been deleted).
3. `validate_delete_action` accepts the action because `typo-account.near` is a syntactically valid `AccountId`.
4. `action_delete_account` removes `alice.near` and queues `Receipt::new_balance_refund("typo-account.near", N)`.
5. When the refund receipt executes, `check_account_existence` refuses to implicitly create `typo-account.near` (refunds can never create accounts), the `Transfer` action fails with `ActionErrorKind::AccountDoesNotExist`, and per `runtime/runtime/src/lib.rs:1047-1054` the entire `N` NEAR deposit is added to `other_burnt_amount` and permanently destroyed — matching the assertion pattern already exercised in `refund_may_not_create_universal_account` (`runtime/runtime/src/tests/apply.rs:6882-6926`).

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

**File:** runtime/runtime/src/actions.rs (L928-934)
```rust
/// Whether a transfer to an account that does not exist yet may create it.
fn implicit_creation_allowed(account_type: AccountType, receipt_shape: ReceiptShape) -> bool {
    let ReceiptShape { is_refund, is_the_only_action } = receipt_shape;
    if is_refund {
        return false; // Refund can never create an account
    }

```

**File:** core/primitives/src/receipt.rs (L493-510)
```rust
    /// Generates a receipt with a transfer from system for a given balance without a receipt_id.
    /// This should be used for token refunds instead of gas refunds.
    /// It doesn't refund the allowance of the access key. For gas refunds use `new_gas_refund`.
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

**File:** runtime/runtime/src/action_validation.rs (L447-451)
```rust
fn validate_delete_action(action: &DeleteAccountAction) -> Result<(), ActionsValidationError> {
    validate_action_account_id(&action.beneficiary_id)?;

    Ok(())
}
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
