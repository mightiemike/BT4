I have sufficient evidence to confirm this analog. The `DeleteAccountAction` does not validate that `beneficiary_id` refers to an existing account before deletion, and the resulting balance-refund receipt is unconditionally burnt on failure.

### Title
`DeleteAccount` action permanently burns the deleted account's remaining balance when `beneficiary_id` does not exist or otherwise rejects the refund - (File: `runtime/runtime/src/actions.rs`)

### Summary
`action_delete_account` pays out the deleted account's full remaining balance by emitting a `Receipt::new_balance_refund` addressed to the caller-supplied `beneficiary_id`, without any pre-check that this account exists or can accept the transfer. Because this receipt has `predecessor_id == "system"` (a refund receipt), if its execution fails for any reason — most commonly `AccountDoesNotExist` — the runtime does not requeue, redirect, or otherwise preserve the funds: it unconditionally adds the deposit to `other_burnt_amount`, permanently destroying it with no recovery path. This mirrors the structure of the reported bug class (a value-moving fallback mechanism whose only recipient is a fixed, unvalidated address, so failure to deliver leaves the value stuck/lost) but is strictly worse for the user, since NEAR's design intentionally burns rather than escrows the failed transfer.

### Finding Description
When a user submits a `DeleteAccount` action, `action_delete_account` computes `account_balance = account_ref.amount()` and, if positive, pushes a refund receipt to `beneficiary_id` before removing the account from state: [1](#0-0) 

This uses `Receipt::new_balance_refund`, which sets `predecessor_id = "system"`: [2](#0-1) 

There is no validation anywhere in the action's execution path that `beneficiary_id` corresponds to an existing (or creatable) account — the only checks performed are account size, gas-key balance, and that the account isn't staking (per the action's documented errors): [3](#0-2) 

When the emitted refund receipt is later processed on `beneficiary_id`'s shard, if the beneficiary account does not exist, execution fails with `ActionErrorKind::AccountDoesNotExist`. Because the receipt's predecessor is `system`, the runtime treats this specially: refund receipts can never fail-refund again, and per the documented design, "If the execution of a refund fails, the refund amount is burnt": [4](#0-3) 

The exact enforcement point is here — on `result.result.is_err()` for a system-predecessor receipt, the full deposit is added to `other_burnt_amount` unconditionally, with no fallback recipient or escrow: [5](#0-4) 

This exact behavior is confirmed by an existing unit test, which asserts that a refund to a non-existent account fails with `AccountDoesNotExist` and explicitly does **not** create the account (i.e., the deposit is simply lost, not delivered or escrowed): [6](#0-5) 

A test comment elsewhere in the codebase independently confirms the intended precondition for correctness — that the beneficiary must already exist, or the funds silently come back as a lost refund instead of reaching anyone: [7](#0-6) 

### Impact Explanation
Any unprivileged transaction signer can trigger this by submitting a single `DeleteAccount` action naming a `beneficiary_id` that does not exist — a typo, a subaccount that was never created, a previously-deleted account, or simply an account they mistakenly believe exists. The deletion itself succeeds unconditionally (the account is removed from state and its storage freed) while the entire remaining NEAR balance is irrecoverably burned via `other_burnt_amount`. Unlike the original report — where failed transfers were at least escrowed in `pendingWithdrawals` and recoverable via a contract upgrade/migration — here the tokens are destroyed from total supply with no possible recovery for the affected user, and no way for validators, the protocol, or governance to make the user whole after the fact. This is a permanent, protocol-level loss of user funds triggered entirely by a single, unprivileged transaction.

### Likelihood Explanation
High likelihood of accidental triggering: `beneficiary_id` is a free-form `AccountId` string chosen by the caller with zero validation against the existence of the target account, and wallets/SDKs/CLIs that construct `DeleteAccount` transactions do not universally guarantee the beneficiary exists at the time of submission (race conditions, typos, or beneficiary accounts deleted between construction and inclusion are all realistic). No adversarial or privileged capability is required — a normal user's own signed transaction is sufficient to destroy their own remaining balance.

### Recommendation
Before emitting the balance-refund receipt in `action_delete_account`, verify that `beneficiary_id` corresponds to an existing account (or is a valid implicit-account form that will auto-create on transfer) as part of receipt/action validation, and reject the `DeleteAccount` action with a clear error (e.g., a new `ActionErrorKind::BeneficiaryDoesNotExist`) if it does not. Alternatively, since implicit accounts (NEAR-implicit/ETH-implicit) can be created by a lone transfer, ensure the refund receipt is constructed so it is eligible for implicit-account creation rather than being a "free" refund receipt exempted from account creation, closing the gap between named-beneficiary and implicit-beneficiary behavior.

### Proof of Concept
1. Create account `alice` with a non-zero balance and no locked stake.
2. Sign and submit a single transaction from `alice` containing one `Action::DeleteAccount(DeleteAccountAction { beneficiary_id: "nonexistent.near" })`, where `nonexistent.near` has never been created on-chain.
3. Observe: the transaction succeeds (`SuccessReceiptId`); `alice`'s account is deleted from state; the spawned balance-refund receipt to `nonexistent.near` fails with `ActionErrorKind::AccountDoesNotExist`; the refunded amount is added to `other_burnt_amount` and permanently removed from circulating supply — confirmed by the existing test `refund_may_not_create_universal_account` [8](#0-7)  which demonstrates the identical mechanic (refund to a non-existent account fails and the account is never brought into existence, i.e., the value is lost rather than delivered).

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
