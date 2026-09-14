### Title
Missing existence check on `DeleteAccountAction::beneficiary_id` permanently burns the deleted account's remaining balance - (File: `runtime/runtime/src/actions.rs`)

### Summary
`DeleteAccountAction.beneficiary_id` is validated only for AccountId *syntax*, never for *existence*. When a transaction signer deletes their own account and the chosen `beneficiary_id` is a well-formed but non-existent (or already-deleted) account, the account is still deleted and its remaining balance is sent as a system refund receipt to that address. Because refund receipts can never implicitly create an account, the refund fails and the balance is unconditionally burned — an irrecoverable, unauthorized loss of the depositor's funds, structurally analogous to the reported "funds sent to an unchecked/zero address get burned" issue.

### Finding Description
`DeleteAccountAction` only carries a `beneficiary_id: AccountId` with no on-chain existence guarantee: [1](#0-0) 

Action-level validation for `DeleteAccount` calls only `validate_action_account_id`, which checks the string is a syntactically valid account id — it never checks the account actually exists in state: [2](#0-1) [3](#0-2) 

At execution time, `action_delete_account` unconditionally pushes a `Receipt::new_balance_refund` to `beneficiary_id` if the account has a positive balance, then removes the account regardless of whether the beneficiary exists or is reachable: [4](#0-3) 

The generated receipt is a "system" refund (`predecessor_id == "system"`): [5](#0-4) 

When that refund receipt is later processed against `beneficiary_id`, `check_account_existence` rejects `Transfer` actions to a missing account unless implicit creation is allowed — and refunds are explicitly excluded from implicit account creation: [6](#0-5) 

Finally, when a refund receipt (system-predecessor) fails, the runtime does not retry or bounce the value back to the deleted account (which no longer exists) — it burns it into `other_burnt_amount`: [7](#0-6) 

This is confirmed by an existing unit test that shows a refund to a non-existent (universal/`0u`-style) account id fails with `AccountDoesNotExist` and does not create the account, i.e., the value is dropped/burned rather than delivered: [8](#0-7) 

### Impact Explanation
Any transaction signer who submits a `DeleteAccount` action naming a syntactically-valid but non-existent (e.g., mistyped, or an account that was itself deleted/never created) `beneficiary_id` will have their entire remaining account balance permanently and unrecoverably burned the moment the delete-account receipt executes — the account is destroyed at that same step, so there is no way to reissue or reroute the refund afterward. This is an unintended, uncompensated reduction of total token supply and a permanent loss of user funds triggered purely by a single, unprivileged, self-authored transaction. It matches the reported bug class ("missing zero/validity check on a destination address causes funds to be irrecoverably destroyed"), transplanted onto NEAR's native `DeleteAccount` action instead of a Solidity `passThrough` address.

### Likelihood Explanation
The trigger requires only a single signed transaction from the account owner (or anyone with a full-access key on that account) containing a `DeleteAccount` action with an incorrect `beneficiary_id`. No validator collusion, malicious peer, or privileged role is required — it is fully reachable by an ordinary unprivileged signer, including via wallets/relayers/meta-transactions that construct this action programmatically (e.g., a bug in a relayer or SDK computing the wrong beneficiary account id, or a simple user typo, would trigger permanent fund loss with no recourse).

### Recommendation
Require that `beneficiary_id` refer to an account that exists in state (or restrict it to the predecessor/signer id, or a system-designated "burn is intentional" flow) as part of `check_actor_permissions`/`action_delete_account`, rejecting the `DeleteAccount` action outright (before destroying the account) if the beneficiary does not exist, analogous to how `validate_delete_action` already checks format — extend it (or the action-execution path) to check existence as well.

### Proof of Concept
1. Account `alice.near` holds a nonzero balance and has no locked/staked balance.
2. `alice.near` signs and submits a transaction to itself containing `Action::DeleteAccount(DeleteAccountAction { beneficiary_id: "nonexistent123456.near".parse().unwrap() })`, per the test helper used throughout the test suite: [9](#0-8) .
3. `action_delete_account` executes: it creates a `Receipt::new_balance_refund(&"nonexistent123456.near", account_balance)` and immediately deletes `alice.near` from state [10](#0-9) .
4. The refund receipt is later processed against `nonexistent123456.near`. Since the receiver does not exist and refunds cannot implicitly create accounts (`is_refund` forces `implicit_creation_allowed` to `false`) [11](#0-10) , the `Transfer` action fails with `AccountDoesNotExist`.
5. Because the failing receipt is a system refund, its deposit is added to `other_burnt_amount` and permanently removed from supply [7](#0-6) .
6. `alice.near`'s entire balance is gone; the account no longer exists to receive anything back, matching the assertion pattern in `refund_may_not_create_universal_account` [12](#0-11) .

### Citations

**File:** core/primitives/src/action/mod.rs (L72-75)
```rust
#[cfg_attr(feature = "schemars", derive(schemars::JsonSchema))]
pub struct DeleteAccountAction {
    pub beneficiary_id: AccountId,
}
```

**File:** runtime/runtime/src/action_validation.rs (L447-451)
```rust
fn validate_delete_action(action: &DeleteAccountAction) -> Result<(), ActionsValidationError> {
    validate_action_account_id(&action.beneficiary_id)?;

    Ok(())
}
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

**File:** runtime/runtime/src/actions.rs (L380-405)
```rust
    // We use current amount as a pay out to beneficiary.
    let account_balance = account_ref.amount();
    if account_balance > Balance::ZERO {
        result
            .new_receipts
            .push(Receipt::new_balance_refund(&delete_account.beneficiary_id, account_balance));
    }
    let remove_result = remove_account(state_update, account_id)?;
    result.tokens_burnt =
        result.tokens_burnt.checked_add(gas_key_balance_to_burn).ok_or_else(|| {
            StorageError::StorageInconsistentState("tokens_burnt overflow".to_string())
        })?;
    if remove_result.gas_key_nonce_count > 0 {
        let compute = storage_removes_compute(
            &config.wasm_config.ext_costs,
            remove_result.gas_key_nonce_count,
            remove_result.gas_key_nonce_total_key_bytes,
            AccessKey::NONCE_VALUE_LEN * remove_result.gas_key_nonce_count,
        );
        result.compute_usage = safe_add_compute(result.compute_usage, compute).map_err(|_| {
            StorageError::StorageInconsistentState("compute_usage overflow".to_string())
        })?;
    }
    *actor_id = receipt.predecessor_id().clone();
    *account = None;
    Ok(())
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

**File:** core/primitives/src/test_utils.rs (L436-452)
```rust
    pub fn delete_account(
        nonce: Nonce,
        signer_id: AccountId,
        receiver_id: AccountId,
        beneficiary_id: AccountId,
        signer: &Signer,
        block_hash: CryptoHash,
    ) -> Self {
        Self::from_actions(
            nonce,
            signer_id,
            receiver_id,
            signer,
            vec![Action::DeleteAccount(DeleteAccountAction { beneficiary_id })],
            block_hash,
        )
    }
```
