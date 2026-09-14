### Title
DeleteAccount's user-controlled `beneficiary_id` permanently burns the account's balance when the recipient doesn't exist - (File: `runtime/runtime/src/actions.rs`, `runtime/runtime/src/lib.rs`)

### Summary
`DeleteAccountAction` lets any account holder pick an arbitrary `beneficiary_id` to receive the deleted account's remaining balance. That payout is implemented as a system-generated "refund" receipt, and refund receipts can never implicitly create the destination account. If the chosen `beneficiary_id` does not exist at execution time, the payout receipt fails and the entire balance is unconditionally burned rather than returned to the deleter or the account being deleted.

### Finding Description
When `DeleteAccount` executes, `action_delete_account` pays out the account's full balance via a `Receipt::new_balance_refund(beneficiary_id, account_balance)`, which is a `Transfer` action sent from the special `system` predecessor: [1](#0-0) 

`Receipt::new_balance_refund` always sets `predecessor_id: "system"`, marking it as a refund receipt: [2](#0-1) 

When this receipt is later processed as an action receipt, `check_account_existence` is invoked for the `Transfer` action. Implicit account creation is gated by `implicit_creation_allowed`, which unconditionally returns `false` for refund receipts, regardless of account type: [3](#0-2) 

So if `beneficiary_id` doesn't already exist as an account (typo, deleted account, or an implicit/named account that was never funded/initialized), the transfer fails with `AccountDoesNotExist`: [4](#0-3) 

Finally, in `apply_action_receipt`, any refund receipt (`predecessor_id().is_system()`) that fails has its full deposit burned rather than refunded anywhere: [5](#0-4) 

This burn-on-failed-refund behavior is also documented as the general refund contract for the protocol: [6](#0-5) 

A regression test explicitly confirms a refund to a nonexistent recipient fails and the account is never created (i.e., the funds have nowhere to land and are burned): [7](#0-6) 

This is the direct analog of the reported bug class: a user-specified payout recipient that turns out to be an "invalid"/non-existent destination causes the value to be irrecoverably destroyed instead of being handled by a fallback path (e.g., returning it to the deleting actor or the beneficiary being auto-created, similar to how the ordinary `Transfer` action *can* implicitly create a fresh account when it is the sole action in a non-refund receipt).

### Impact Explanation
Any account holder who deletes their own account with a `beneficiary_id` that does not exist at execution time (e.g., a typo, an as-yet-unfunded/uncreated implicit account they intended to bootstrap, or an account that was independently deleted between transaction construction and execution) will have their entire remaining NEAR balance permanently burned rather than returned to any party. This is a genuine, protocol-level "permanently frozen/lost funds" outcome reachable by a single unprivileged, self-submitted transaction — no attacker or third party is required, but the value destruction is real, unrecoverable, and asymmetric to the ordinary `Transfer` action's more forgiving implicit-account-creation semantics.

### Likelihood Explanation
This requires only a normal `DeleteAccount` action with a mis-specified `beneficiary_id` — a plausible and easy-to-hit user/wallet-tooling mistake (e.g., a wallet computing an implicit account ID for a beneficiary key that hasn't received any prior transaction, or racing an account deletion against another transaction that deletes the intended beneficiary first). No special privileges, timing attacks on other users, or protocol-level exploits are needed; it is purely a matter of the destination account not existing at the moment the refund receipt executes.

### Recommendation
Mirror the ordinary `Transfer` action's fallback: if the `beneficiary_id` account does not exist when the `DeleteAccount` payout receipt executes, either (a) allow the payout receipt to implicitly create the beneficiary account (consistent with how a plain `Transfer` as the sole action can create a new/implicit account), or (b) redirect the balance to a safe fallback (e.g., the deleting actor, or fail the `DeleteAccount` action outright at validation/execution time if the beneficiary account cannot be confirmed to exist) rather than silently burning it via the generic "refund failure burns funds" path.

### Proof of Concept
1. Attacker/user account `alice.near` calls `DeleteAccount { beneficiary_id: "nonexistent.near" }` (or an implicit account ID they haven't yet funded), targeting their own account which holds a balance.
2. `action_delete_account` removes `alice.near` and enqueues `Receipt::new_balance_refund("nonexistent.near", balance)` (`runtime/runtime/src/actions.rs:380-386`).
3. When this refund receipt is processed, `check_account_existence` sees `nonexistent.near` doesn't exist and `implicit_creation_allowed` returns `false` because `is_refund == true` (`runtime/runtime/src/actions.rs:928-933`, `842-850`), so the transfer action fails with `AccountDoesNotExist`.
4. Because the receipt's predecessor is `system`, `apply_action_receipt` burns the full deposit into `other_burnt_amount` instead of refunding it anywhere (`runtime/runtime/src/lib.rs:1047-1055`).
5. Result: `alice.near`'s entire balance is permanently destroyed with no recovery path, confirmed by the existing test `refund_may_not_create_universal_account` (`runtime/runtime/src/tests/apply.rs:6880-6926`), which demonstrates the refund-to-nonexistent-account failure and non-creation of the target account.

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

**File:** runtime/runtime/src/actions.rs (L928-933)
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
