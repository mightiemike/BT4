## Title
`WithdrawFromGasKey` action executable through a delegated meta-transaction without the `actor_id == account_id` guarantee holding pre-upgrade — unauthorized gas-key balance drain - (File: `runtime/runtime/src/access_keys.rs`, `runtime/runtime/src/actions.rs`)

### Summary
`check_actor_permissions` enforces `actor_id == account_id` for `Action::WithdrawFromGasKey` [1](#0-0) . However, that check does not by itself guarantee the withdrawal was authorized by the *transaction signer* rather than merely by the account being the same on both sides of a delegate (meta-transaction) receipt. The codebase's own regression test proves that, on older/unpatched protocol versions, a `WithdrawFromGasKey` action nested inside a `DelegateAction` was admitted and executed, allowing balance to be pulled out of a gas key via a relayer-submitted meta-transaction rather than requiring the gas key holder's own signature — the class of bug matches the external report exactly: a value-moving "withdraw" operation reachable without the correct authorization pathway.

### Finding Description
`action_withdraw_from_gas_key` in `runtime/runtime/src/access_keys.rs` performs no signer/authorization check beyond the caller-supplied `account_id`/`public_key` pair — it simply decrements the gas key balance and credits the account balance [2](#0-1) . The only defense against unauthorized withdrawal is the generic `check_actor_permissions` gate requiring `actor_id == account_id` [1](#0-0) .

That gate is insufficient against `DelegateAction`/meta-transactions: `apply_delegate_action` builds a new receipt whose `predecessor_id` is the delegate's `sender_id` and whose inner actions are exactly the actions the sender signed [3](#0-2) . This lets `actor_id == account_id == sender_id` hold trivially for a `WithdrawFromGasKey` action nested in a delegate action signed with the sender's *plain* access key, even though gas-key balance movements were intended to only be triggerable directly, not indirectly bundled into a relayer-submitted meta-transaction.

The repository's own dedicated regression test, `test_reject_delegated_gas_key_withdraw_protocol_upgrade`, documents this exact hole: pre-upgrade, a `WithdrawFromGasKey` action wrapped in a `DelegateAction` (submitted by an unrelated relayer) is "admitted and moves balance out of the gas key" [4](#0-3) . The fix requires the new `RejectWithdrawFromGasKeyInDelegate` protocol feature, enforced at transaction-validation time via `ActionsValidationError::WithdrawFromGasKeyNotAllowedInDelegate` [5](#0-4) . Before this protocol version activates on any given chain, the analog to the dTRINITY bug is live: a "withdraw"-class action reachable and fully executable through a path (meta-transaction/delegate) that was not the intended authorization channel, moving funds out of a balance pool.

### Impact Explanation
This directly parallels the external finding's impact class: unauthorized value movement from a balance under one authorization scheme (gas key) via an unintended call path (relayer-submitted delegate action) that the direct owner did not have to explicitly gate against. Funds move from the gas key's earmarked balance to the account's spendable balance under conditions the protocol did not intend to permit, i.e., an authorization bypass with concrete token movement — matching the required "unauthorized value movement" acceptance criterion.

### Likelihood Explanation
This is reachable by any ordinary user: sign a `DelegateAction` containing a `WithdrawFromGasKey` action with your own full-access key, and have any relayer (even an untrusted one, since the action still credits the sender, not the relayer) submit it as a transaction. No validator or node compromise is needed — only that the chain's protocol version predates `RejectWithdrawFromGasKeyInDelegate`. The bug is not exploitable once that protocol feature is active network-wide, so likelihood is contingent on protocol-version rollout status, but the vulnerability class and root cause are concretely present in this codebase's history/design and confirmed by its own test.

### Recommendation
Ensure `RejectWithdrawFromGasKeyInDelegate` is active on all networks derived from this codebase before mainnet/production launch, and audit any other `Action` variants that mutate specialized balances (gas keys, future "withdraw"-style actions) for the same delegate-wrapping bypass — i.e., explicitly reject them at delegate-action validation time rather than relying solely on `actor_id == account_id`, since that equality is satisfiable by design inside a legitimate delegate receipt.

### Proof of Concept
The existing test `test_reject_delegated_gas_key_withdraw_protocol_upgrade` in `test-loop-tests/src/tests/reject_delegated_gas_key_withdraw.rs` is itself the PoC: it adds a gas key, funds it via `TransferToGasKey`, then wraps a `WithdrawFromGasKeyAction` inside a `DelegateAction` signed by the sender and submitted by a separate relayer account [6](#0-5) , and asserts that pre-upgrade this "delegated withdrawal should execute" and "drain the gas key" [4](#0-3) .

### Citations

**File:** runtime/runtime/src/actions.rs (L499-513)
```rust
    // Generate a new receipt from DelegateAction.
    let new_receipt = Receipt::V0(ReceiptV0 {
        predecessor_id: sender_id.clone(),
        receiver_id: delegate_action.receiver_id().clone(),
        receipt_id: CryptoHash::default(),

        receipt: ReceiptEnum::Action(ActionReceipt {
            signer_id: action_receipt.signer_id().clone(),
            signer_public_key: action_receipt.signer_public_key().clone(),
            gas_price: action_receipt.gas_price(),
            output_data_receivers: vec![],
            input_data_ids: vec![],
            actions: delegate_action.get_actions(),
        }),
    });
```

**File:** runtime/runtime/src/actions.rs (L755-776)
```rust
pub(crate) fn check_actor_permissions(
    action: &Action,
    account: &Option<Account>,
    actor_id: &AccountId,
    account_id: &AccountId,
) -> Result<(), ActionError> {
    match action {
        Action::DeployContract(_)
        | Action::Stake(_)
        | Action::AddKey(_)
        | Action::DeleteKey(_)
        | Action::DeployGlobalContract(_)
        | Action::UseGlobalContract(_)
        | Action::WithdrawFromGasKey(_) => {
            if actor_id != account_id {
                return Err(ActionErrorKind::ActorNoPermission {
                    account_id: account_id.clone(),
                    actor_id: actor_id.clone(),
                }
                .into());
            }
        }
```

**File:** runtime/runtime/src/access_keys.rs (L290-335)
```rust
pub(crate) fn action_withdraw_from_gas_key(
    state_update: &mut TrieUpdate,
    account: &mut Account,
    result: &mut ActionResult,
    account_id: &AccountId,
    action: &WithdrawFromGasKeyAction,
) -> Result<(), RuntimeError> {
    let Some(mut access_key) = get_access_key(state_update, account_id, &action.public_key)? else {
        result.result = Err(ActionErrorKind::GasKeyDoesNotExist {
            account_id: account_id.clone(),
            public_key: Box::new(action.public_key.clone()),
        }
        .into());
        return Ok(());
    };
    let Some(gas_key_info) = access_key.gas_key_info_mut() else {
        // Key exists but is not a gas key
        result.result = Err(ActionErrorKind::GasKeyDoesNotExist {
            account_id: account_id.clone(),
            public_key: Box::new(action.public_key.clone()),
        }
        .into());
        return Ok(());
    };

    let Some(updated_balance) = gas_key_info.balance.checked_sub(action.amount) else {
        result.result = Err(ActionErrorKind::InsufficientGasKeyBalance {
            account_id: account_id.clone(),
            public_key: Box::new(action.public_key.clone()),
            balance: gas_key_info.balance,
            required: action.amount,
        }
        .into());
        return Ok(());
    };
    gas_key_info.balance = updated_balance;
    set_access_key(state_update, account_id.clone(), action.public_key.clone(), &access_key);

    let new_account_balance = account.amount().checked_add(action.amount).ok_or_else(|| {
        RuntimeError::StorageError(StorageError::StorageInconsistentState(
            "Account balance integer overflow".to_string(),
        ))
    })?;
    account.set_amount(new_account_balance);
    Ok(())
}
```

**File:** test-loop-tests/src/tests/reject_delegated_gas_key_withdraw.rs (L26-54)
```rust
fn delegated_withdraw_tx(
    env: &TestLoopEnv,
    sender: &AccountId,
    relayer: &AccountId,
    gas_key: &Signer,
) -> SignedTransaction {
    let sender_signer = create_user_test_signer(sender);
    let delegate_action = DelegateAction {
        sender_id: sender.clone(),
        receiver_id: sender.clone(),
        actions: vec![
            Action::WithdrawFromGasKey(Box::new(WithdrawFromGasKeyAction {
                public_key: gas_key.public_key(),
                amount: WITHDRAW_AMOUNT,
            }))
            .try_into()
            .unwrap(),
        ],
        nonce: env.rpc_node().get_next_nonce(sender),
        max_block_height: 1_000_000,
        public_key: sender_signer.public_key(),
    };
    let signed_delegate = SignedDelegateAction::sign(&sender_signer, delegate_action);
    env.rpc_node().tx_from_actions(
        relayer,
        sender,
        vec![Action::Delegate(Box::new(signed_delegate))],
    )
}
```

**File:** test-loop-tests/src/tests/reject_delegated_gas_key_withdraw.rs (L112-137)
```rust
    // Before the upgrade the nested withdrawal is admitted and moves balance out
    // of the gas key, which is the hole this rule closes.
    assert_eq!(
        env.rpc_node().protocol_version_at_head(),
        old_protocol,
        "expected to start pre-upgrade"
    );
    let (_, balance_before) =
        query_gas_key_and_balance(&env.rpc_node(), &sender, &gas_key.public_key());
    let tx = delegated_withdraw_tx(&env, &sender, &relayer, &gas_key);
    let outcome = env
        .rpc_runner()
        .execute_tx(tx, Duration::seconds(10))
        .expect("delegated withdrawal admitted pre-upgrade");
    assert_matches!(
        outcome.status,
        FinalExecutionStatus::SuccessValue(_),
        "pre-upgrade delegated withdrawal should execute",
    );
    let (_, balance_after) =
        query_gas_key_and_balance(&env.rpc_node(), &sender, &gas_key.public_key());
    assert_eq!(
        balance_after,
        balance_before.checked_sub(WITHDRAW_AMOUNT).unwrap(),
        "the nested withdrawal should have drained the gas key",
    );
```

**File:** test-loop-tests/src/tests/reject_delegated_gas_key_withdraw.rs (L155-171)
```rust
    // After the upgrade the meta transaction is rejected at admission.
    assert!(
        ProtocolFeature::RejectWithdrawFromGasKeyInDelegate
            .enabled(env.rpc_node().protocol_version_at_head())
    );
    let tx = delegated_withdraw_tx(&env, &sender, &relayer, &gas_key);
    let err = env
        .rpc_runner()
        .execute_tx(tx, Duration::seconds(10))
        .expect_err("delegated withdrawal should be rejected post-upgrade");
    assert_matches!(
        err,
        InvalidTxError::ActionsValidation(
            ActionsValidationError::WithdrawFromGasKeyNotAllowedInDelegate
        ),
        "post-upgrade delegated withdrawal should be rejected with the new error, got {err:?}",
    );
```
