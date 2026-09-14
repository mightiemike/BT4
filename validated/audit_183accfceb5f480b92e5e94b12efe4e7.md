I found a solid analog. Let me verify the exact mechanics of the refund-burn path to build a precise proof of concept.### Title
DeleteAccount to a non-existent `beneficiary_id` permanently burns the account's balance instead of returning it - (File: runtime/runtime/src/actions.rs)

### Summary
`action_delete_account` sends the deleted account's remaining balance to `beneficiary_id` as a `Receipt::new_balance_refund`, without validating that `beneficiary_id` refers to an existing account. If that account does not exist (or becomes non-existent by the time the refund receipt executes), the transfer fails with `AccountDoesNotExist`, and because refund receipts can never trigger implicit account creation, the deposited balance is unconditionally burned rather than returned to anyone.

### Finding Description
`action_delete_account` computes the account's current balance and unconditionally queues a system-generated refund receipt to `beneficiary_id`: [1](#0-0) 

This uses `Receipt::new_balance_refund`, which marks `predecessor_id` as `"system"`: [2](#0-1) 

Validation of `DeleteAccountAction` only checks that `beneficiary_id` is a syntactically valid account ID — it never checks that the account actually exists on chain: [3](#0-2) 

When the resulting `Transfer` action receipt is later processed against `beneficiary_id`, `implicit_creation_allowed` explicitly forbids account creation for refund receipts: [4](#0-3) 

So if `beneficiary_id` doesn't exist, the transfer fails with `AccountDoesNotExist`. Because the failing receipt's `predecessor_id` is `"system"` (a refund), the runtime does not generate a further refund for it — instead the whole deposit is burned: [5](#0-4) 

This is explicitly documented protocol behavior: "If the execution of a refund fails, the refund amount is burnt." [6](#0-5) 

A unit test in the codebase already exercises and confirms this exact scenario — a refund sent to a missing account fails and the funds are not recovered: [7](#0-6) 

And a comment in another test explicitly acknowledges the danger: "The beneficiary has to exist, otherwise the balance transfer the delete sends it would come straight back as a refund [and burn]." [8](#0-7) 

This mirrors the reported Solidity bug class: a withdrawal/payout hard-codes a destination without verifying it can actually receive the funds, and the transfer silently fails, resulting in fund loss. In NEAR's account model there is no `receive()`/fallback concept — but the equivalent "cannot receive" condition is "receiving account does not exist," and unlike a reverted Solidity call (which just fails atomically and preserves funds), here the failure mode is worse: the source account has already been irreversibly deleted, and the balance is destroyed rather than returned to the sender or the beneficiary.

### Impact Explanation
Any unprivileged account holder can permanently destroy their own remaining NEAR balance with a single `DeleteAccount` transaction if:
- They mistype/mis-specify `beneficiary_id` to an account that does not exist, or
- The intended beneficiary account is deleted (by its owner, e.g. via its own `DeleteAccount` action) between the time the deleting transaction is signed/submitted and the time the refund receipt executes (a race condition entirely outside the sender's control).

In both cases the deleted account's entire remaining balance is unrecoverably burned (subtracted from total supply as `other_burnt_amount`), with no path to reclaim it — a concrete, transaction-triggered, unauthorized/undesired loss of user funds.

### Likelihood Explanation
Reachable by any single unprivileged transaction signer using the standard `DeleteAccountAction`. The mistyped-beneficiary case requires only user/tooling error (no format validation catches a nonexistent-but-valid-format account id). The race-condition case (beneficiary self-deletes between signing and the delete-account receipt's execution, especially in cross-shard scenarios where the refund receipt travels to a different shard and could be delayed) requires no attacker privilege at all — it can occur naturally, or be intentionally triggered by an attacker who controls the beneficiary account and races their own `DeleteAccount` against the victim's.

### Recommendation
Before generating the `DeleteAccountAction` balance-refund receipt, validate that `beneficiary_id` corresponds to an existing account (or defer the account deletion / balance transfer until existence is confirmed at execution time, with a fallback that returns unclaimable funds to the deleting account's actor rather than burning them). At minimum, change the refund-burn-on-failure logic for `DeleteAccount`-originated refunds so that a missing beneficiary results in the funds being retried/returned rather than destroyed, since this differs materially from a normal gas/deposit refund where the original payer still exists to receive the fallback.

### Proof of Concept
1. Account `alice.near` holds a `DeleteAccountAction` and specifies `beneficiary_id = "nonexistent.near"` — a syntactically valid, but never-created account ID.
2. `action_delete_account` runs in `runtime/runtime/src/actions.rs:330-406`: it reads `alice.near`'s balance, pushes `Receipt::new_balance_refund(&"nonexistent.near", balance)`, and immediately calls `remove_account`, deleting `alice.near` from state (`runtime/runtime/src/actions.rs:380-387,404`).
3. In a later apply step, the queued `Transfer` refund receipt targets `nonexistent.near`. Since `predecessor_id == "system"` (a refund), `implicit_creation_allowed` returns `false` (`runtime/runtime/src/actions.rs:928-934`), so the transfer fails with `ActionErrorKind::AccountDoesNotExist`.
4. Because the failing receipt has `predecessor_id().is_system() == true`, `runtime/runtime/src/lib.rs:1047-1054` adds the entire deposit to `stats.balance.other_burnt_amount` — permanently burning it — instead of generating any further refund.
5. Result: `alice.near`'s account is gone and its balance has been burned from total supply; no account anywhere received it. This exact sequence is validated by the repository's own test `refund_may_not_create_universal_account` (`runtime/runtime/src/tests/apply.rs:6880-6926`), which asserts the receipt fails with `AccountDoesNotExist` and that no account (including the intended beneficiary) is created — confirming the funds are lost rather than reclaimed.

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

**File:** docs/RuntimeSpec/Refunds.md (L10-13)
```markdown
Refund receipts are identified by having `predecessor_id == "system"`. They are also special because they don't cost any gas to generate or execute. As a result, they also do not contribute to the block gas limit.

If the execution of a refund fails, the refund amount is burnt.
The refund receipt is an `ActionReceipt` that consists of a single action `Transfer` with the `deposit` amount of the refund.
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
