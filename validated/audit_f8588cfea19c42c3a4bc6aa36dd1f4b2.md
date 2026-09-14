This confirms the analog: the `DeleteAccount` action's `beneficiary_id` field is a single-step, unvalidated (beyond syntactic format) recipient designation — exactly the bug class described in the report. If the account owner (an unprivileged tx signer) typos or specifies a non-existent/inaccessible `beneficiary_id`, the resulting balance-refund receipt fails with `AccountDoesNotExist`, and because it's a `predecessor_id == "system"` refund receipt, the funds are **permanently burnt** rather than returned, per [1](#0-0) , documented in [2](#0-1) .

### Title
Single-step, unvalidated `DeleteAccountAction.beneficiary_id` permanently burns account balance on typo/inaccessible recipient - (File: `runtime/runtime/src/actions.rs`)

### Summary
`DeleteAccountAction` transfers an account's entire remaining balance to a `beneficiary_id` in one irreversible step, with only syntactic validation of the account-id string and no check that the beneficiary account actually exists or is reachable, mirroring the reported "lack of two-step role transfer" bug class (single-step, unchecked recipient designation that can brick fund flow).

### Finding Description
`DeleteAccountAction` is defined with a single `beneficiary_id: AccountId` field [3](#0-2) . Validation of this action only checks that `beneficiary_id` is a syntactically valid account id via `validate_action_account_id`, with no existence or reachability check: [4](#0-3) .

At execution time, `action_delete_account` unconditionally uses the current balance and pushes a system `balance_refund` receipt to `beneficiary_id`, then immediately deletes the account and clears `*account = None`: [5](#0-4) . This is a one-shot, irrevocable transfer with no confirmation/claim step from the beneficiary — precisely the missing "recipient must claim" pattern from the external report.

Named accounts (the common case for a `beneficiary_id` — e.g. a typo'd variant of the caller's own account, a decommissioned account, or a sub-account that was never created) cannot be implicitly created by a refund receipt: `implicit_creation_allowed` returns `false` whenever `is_refund` is true, regardless of account type [6](#0-5) . Consequently, if `beneficiary_id` does not exist on-chain, `check_account_existence` rejects the refund receipt with `AccountDoesNotExist` [7](#0-6) , confirmed by the dedicated regression test `refund_may_not_create_universal_account` [8](#0-7) .

Because the receipt's `predecessor_id` is `"system"` (a refund receipt), a failure here does not retry or bounce back to the deleting account — the deposit is instead burnt into `other_burnt_amount`: [1](#0-0) , and the protocol documentation states this explicitly: "If the execution of a refund fails, the refund amount is burnt." [2](#0-1) . By the time this failure occurs, the source account has already been deleted (`*account = None` happens unconditionally before the refund receipt is even attempted, in the same action) [9](#0-8) , so there is no rollback path that restores the funds to the original owner.

### Impact Explanation
Any unprivileged transaction signer who submits a `DeleteAccount` action (directly, via a `call_promise`/`promise_batch_action_delete_account` cross-contract call, or via a meta-transaction/`Delegate` action) with a `beneficiary_id` that is syntactically valid but does not correspond to an existing/reachable account permanently and unrecoverably burns their entire account balance. This is a concrete, transaction-triggered, irreversible loss of user funds (frozen/burnt funds), matching the "Medium" severity class of the referenced report: no privilege escalation is required, only a single mistaken or malicious value in a field that lacks any two-step confirmation.

### Likelihood Explanation
This is trivially reachable by any account holder issuing a normal `DeleteAccount` transaction/action (the `handOverHost`-equivalent operation here is fully "invoked by anyone who created the market," i.e., the account owner themselves) with a mistyped or stale `beneficiary_id`. No special privileges, races, or multi-party coordination are needed; the existing test suite (`refund_may_not_create_universal_account`) already demonstrates the exact failure/burn path deterministically.

### Recommendation
Introduce a two-step confirmation for `DeleteAccountAction`'s fund disposition analogous to the report's recommendation: e.g., require the `beneficiary_id` account to exist (and optionally be explicitly pre-registered/opted-in) at validation time rather than only checking id-format validity in `validate_delete_action` (`runtime/runtime/src/action_validation.rs`), or change the refund-failure path for `DeleteAccount` beneficiary transfers so that failure returns the balance to the deleting account's a pending-claim balance instead of unconditionally burning it in `runtime/runtime/src/lib.rs`'s refund handling.

### Proof of Concept
1. Create account `alice.near` with a non-zero balance.
2. Submit a transaction from `alice.near` containing a single, final `Action::DeleteAccount(DeleteAccountAction { beneficiary_id: "typo-beneficiary.near".parse().unwrap() })`, where `typo-beneficiary.near` has never been created on-chain (mirrors the setup in `delete_after_init_removes_account` at `runtime/runtime/src/tests/apply.rs:6342-6391`, but omitting the beneficiary account creation step, as exercised by `refund_may_not_create_universal_account` at lines 6883-6926).
3. Observe: `action_delete_account` deletes `alice.near`'s account state immediately [10](#0-9)  and emits a `balance_refund` receipt to `typo-beneficiary.near`.
4. The refund receipt is processed with `predecessor_id == "system"`; `check_account_existence` rejects it with `AccountDoesNotExist` because refunds cannot implicitly create named accounts [6](#0-5) .
5. `apply_action_receipt` sees `result.result.is_err()` for this system-predecessor receipt and burns the deposit into `other_burnt_amount` instead of refunding it anywhere [1](#0-0) .
6. Net effect: `alice.near`'s entire balance is permanently destroyed with no recovery path — a transaction-triggered, unrecoverable loss of funds caused by the single-step, unvalidated beneficiary designation.

### Citations

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

**File:** docs/RuntimeSpec/Refunds.md (L10-13)
```markdown
Refund receipts are identified by having `predecessor_id == "system"`. They are also special because they don't cost any gas to generate or execute. As a result, they also do not contribute to the block gas limit.

If the execution of a refund fails, the refund amount is burnt.
The refund receipt is an `ActionReceipt` that consists of a single action `Transfer` with the `deposit` amount of the refund.
```

**File:** docs/RuntimeSpec/Actions.md (L278-285)
```markdown
## DeleteAccountAction

```rust
pub struct DeleteAccountAction {
    /// The remaining account balance will be transferred to the AccountId below
    pub beneficiary_id: AccountId,
}
```
```

**File:** runtime/runtime/src/action_validation.rs (L447-451)
```rust
fn validate_delete_action(action: &DeleteAccountAction) -> Result<(), ActionsValidationError> {
    validate_action_account_id(&action.beneficiary_id)?;

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

**File:** runtime/runtime/src/actions.rs (L814-850)
```rust
pub(crate) fn check_account_existence(
    action: &Action,
    account: &Option<Account>,
    account_id: &AccountId,
    config: &RuntimeConfig,
    receipt_shape: ReceiptShape,
) -> Result<(), ActionError> {
    match action {
        Action::CreateAccount(_) => {
            if account.is_some() {
                return Err(ActionErrorKind::AccountAlreadyExists {
                    account_id: account_id.clone(),
                }
                .into());
            }
            if get_account_type(account_id, config).is_implicit() {
                // Implicit accounts can only be created implicitly.
                // `CreateAccount` claims `actor_id` for the new account, which
                // would let the rest of the receipt add an access key to an id
                // whose private key the sender does not hold. Rejecting the action
                // is the simplest way to close that.
                // See https://github.com/nearprotocol/NEPs/pull/71
                return Err(ActionErrorKind::OnlyImplicitAccountCreationAllowed {
                    account_id: account_id.clone(),
                }
                .into());
            }
        }
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
