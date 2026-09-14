### Title
Deleting an account with a non-existent (misspelled) `beneficiary_id` permanently burns the account's balance instead of refunding it - ([File: runtime/runtime/src/actions.rs])

### Summary
`DeleteAccountAction` lets an unprivileged account owner supply an arbitrary `beneficiary_id` to receive the account's remaining balance on deletion. If the user mistypes or otherwise specifies a `beneficiary_id` that does not exist, the balance-refund receipt generated for that beneficiary fails (refunds cannot implicitly create accounts) and the entire deposited amount is **burned** rather than returned to the deleting user. This directly mirrors the reported OpenQ bug class: "user loses value because they input the wrong address," but here the loss is a supply-destroying burn rather than a simple transfer.

### Finding Description
When a `DeleteAccountAction` is executed, the runtime takes the account's current balance and issues a system balance-refund receipt addressed to `beneficiary_id`, then deletes the account: [1](#0-0) 

This uses `Receipt::new_balance_refund`, which produces a receipt with `predecessor_id == "system"`: [2](#0-1) 

When this refund receipt is later applied against `beneficiary_id`, `check_account_existence` is invoked for the `Transfer` action inside it. Because the receipt is a refund, `implicit_creation_allowed` unconditionally returns `false`, meaning the refund can never create the beneficiary account even if `beneficiary_id` happens to be a syntactically valid implicit/universal account id that simply has not been created yet: [3](#0-2) [4](#0-3) 

This is confirmed by an existing unit test showing a refund to a missing account fails with `AccountDoesNotExist` and does not create the account: [5](#0-4) 

When such a refund receipt fails, the runtime's general handling for system-originated receipts burns the failed deposit instead of returning it anywhere: [6](#0-5) 

The official documentation for `DeleteAccountAction` only documents `InvalidAccountId` as a validation-time error for a malformed id; it does not mention or guard against a syntactically-valid but non-existent `beneficiary_id`: [7](#0-6) 

And the general refunds documentation confirms the burn behavior for any failed refund receipt: [8](#0-7) 

Because a transaction signer chooses `beneficiary_id` freely and there is no on-chain, pre-execution check that the account actually exists (existence can only be checked when the receipt is later applied on the beneficiary's shard), any typo, copy-paste error, or reuse of a since-deleted/never-created account id in a self-issued `DeleteAccountAction` causes the entire remaining balance of the deleted account to be irrecoverably burned.

### Impact Explanation
This is a direct, unauthorized-by-intent, permanent loss of the account owner's funds triggered by nothing more than a single transaction from an unprivileged signer. Unlike a normal `Transfer` action to a non-existent named account (which fails atomically and is fully refunded to the original sender because that failure happens within the same execution and is retried as an ordinary failed-receipt deposit refund back to the *predecessor*), here the failure happens one hop downstream in a *system*-refund receipt that has no predecessor to fall back to — so the tokens are destroyed rather than returned. This effectively inflates/deflates total supply-accounting invariants (`other_burnt_amount`) based purely on a user input mistake, and results in complete, unrecoverable loss of the deleted account's balance for the victim.

### Likelihood Explanation
Likelihood is non-trivial: `DeleteAccountAction` is a common, easily reachable action (any account owner can delete their own account and pick any `beneficiary_id`), and account ids/typos are exactly the kind of user input error the original OpenQ report is about. Any typo in a beneficiary account name, or specifying an implicit/eth-implicit/deterministic/universal account id that has not been created/funded yet, silently burns the whole balance with no error surfaced to the user beyond a successful `DeleteAccount` execution status (the burn happens in a subsequent, separate refund receipt, which most wallets/tools would not surface prominently).

### Recommendation
Before generating the balance-refund receipt in `action_delete_account`, or when applying the resulting system refund receipt, avoid unconditionally burning tokens on beneficiary non-existence for `DeleteAccount`-originated payouts. Options include:
- Requiring `beneficiary_id` to be a currently existing account (verified at receipt-processing time, before the account is destroyed) and failing the whole `DeleteAccount` action (leaving the account intact) if the beneficiary does not exist, rather than deleting the account and then failing to deliver its balance.
- Alternatively, route the failed deletion payout back to the deleted account's own last known state/actor instead of burning it outright, if protocol semantics allow.
- At minimum, clearly surface this burn risk to wallets/tooling and add a client-side/RPC pre-check that `beneficiary_id` exists before submitting a `DeleteAccount` transaction.

### Proof of Concept
1. Alice owns `alice.near` with balance `B`.
2. Alice signs and submits `DeleteAccount { beneficiary_id: "not-there.near" }` targeting `alice.near` (mistyping the intended beneficiary, e.g. missing/extra character), where `not-there.near` does not exist.
3. `action_delete_account` executes: it computes `account_balance = B`, pushes a `Receipt::new_balance_refund("not-there.near", B)`, and deletes `alice.near` — the top-level `DeleteAccount` action reports success.
4. The generated system balance-refund receipt is later applied against `not-there.near`. `check_account_existence` for its `Transfer` action sees `is_refund = true`, so `implicit_creation_allowed` returns `false` regardless of account type, and the receipt fails with `AccountDoesNotExist` (as directly demonstrated by the existing test `refund_may_not_create_universal_account` at `runtime/runtime/src/tests/apply.rs:6883-6926`, generalizable to named/implicit beneficiaries too).
5. Because the failing receipt's `predecessor_id` is `"system"`, the runtime's refund-processing path (`runtime/runtime/src/lib.rs:1047-1054`) burns the deposit (`B`) into `stats.balance.other_burnt_amount` instead of returning it to Alice or anyone else.
6. Result: Alice's entire balance `B` is permanently destroyed due to a beneficiary-address typo, with no recourse.

Note: I was unable to find an explicit end-to-end integration test in the indexed portion of the repo that submits a `DeleteAccountAction` with a non-existent beneficiary and asserts the burn outcome specifically (only the more general `refund_may_not_create_universal_account` unit test and the general system-refund burn logic were found); a Devin session with full repo/test access could add or locate such a targeted regression test to confirm the exact accounting outcome end-to-end.

### Citations

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

**File:** core/primitives/src/receipt.rs (L493-497)
```rust
    /// Generates a receipt with a transfer from system for a given balance without a receipt_id.
    /// This should be used for token refunds instead of gas refunds.
    /// It doesn't refund the allowance of the access key. For gas refunds use `new_gas_refund`.
    pub fn new_balance_refund(receiver_id: &AccountId, refund: Balance) -> Self {
        Receipt::V0(ReceiptV0 {
```

**File:** runtime/runtime/src/tests/apply.rs (L6883-6926)
```rust
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

**File:** docs/RuntimeSpec/Actions.md (L278-314)
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

- If this action is not the last action in the action list of a receipt, the following error will be returned

```rust
/// The delete action must be a final action in transaction
DeleteActionMustBeFinal
```

- If the account still has locked balance due to staking, the following error will be returned

```rust
/// Account is staking and can not be deleted
DeleteAccountStaking { account_id: AccountId }
```
```

**File:** docs/RuntimeSpec/Refunds.md (L10-13)
```markdown
Refund receipts are identified by having `predecessor_id == "system"`. They are also special because they don't cost any gas to generate or execute. As a result, they also do not contribute to the block gas limit.

If the execution of a refund fails, the refund amount is burnt.
The refund receipt is an `ActionReceipt` that consists of a single action `Transfer` with the `deposit` amount of the refund.
```
