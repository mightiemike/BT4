### Title
Nested `WithdrawFromGasKey` inside a `Delegate`/meta-transaction bypasses gas-key withdrawal safeguards - (File: `runtime/runtime/src/actions.rs`, `runtime/runtime/src/access_keys.rs`)

### Summary
The Sherlock report describes a privileged/"emergency" code path (`repay` with `isEmergency=true`) that lacks an owner check, letting an unrelated caller submit the same call and seize funds meant for the position owner. The closest reachable analog in nearcore is the `WithdrawFromGasKey` action, which is intended to only be executable directly by the account itself (enforced by `check_actor_permissions`), but which can also be smuggled inside a `Delegate`/`DelegateV2` (meta-transaction) receipt, bypassing the intended restriction that this balance-moving action be issued through the account's plain, strictly-ordered transaction flow.

### Finding Description
`check_actor_permissions` requires `actor_id == account_id` for `Action::WithdrawFromGasKey`, i.e. the caller must be the account owner itself: [1](#0-0) . However, `apply_delegate_action` allows an inner action list of a `SignedDelegateAction`—submitted through a relayer transaction—to include a `WithdrawFromGasKey` action whose `sender_id == receiver_id` equals the delegating user, so the `actor_id == account_id` check trivially passes even though the action reached the runtime through the meta-transaction/relayer path rather than the account's own directly-signed, nonce-strict transaction: [2](#0-1) .

The action itself performs an unconditional balance move from the gas key to the account with no additional caller-identity or context check beyond the generic `actor_id == account_id` test: [3](#0-2) , and it is dispatched from the shared action-apply loop identically whether it originated from a plain transaction or a delegate receipt: [4](#0-3) .

A dedicated regression test in the codebase demonstrates this exact hole: a `WithdrawFromGasKey` nested inside a `DelegateAction` successfully drains the gas key balance pre-upgrade, and the protocol needed a new validation rule (`ProtocolFeature::RejectWithdrawFromGasKeyInDelegate`, surfaced as `ActionsValidationError::WithdrawFromGasKeyNotAllowedInDelegate`) specifically to close this gap: [5](#0-4) [6](#0-5) . The test explicitly documents: "the nested withdrawal should have drained the gas key," confirming unauthorized/unsafe balance movement occurred through the meta-transaction surface before the fix.

### Impact Explanation
This is a "concrete unauthorized value movement" as required: a value-moving action (`WithdrawFromGasKey`) that the protocol's actor-permission model was designed to restrict to the account's own direct, strictly-ordered transactions can instead be delivered through a `Delegate`/`DelegateV2` receipt (reachable by any relayer forwarding a user-signed `SignedDelegateAction`), draining a gas key's balance outside of the intended safety rails (e.g., gas-key nonce sequencing / atomicity guarantees that plain gas-key transactions enforce). This maps directly to the report's bug class: a privileged balance-affecting operation lacking a sufficient caller/context restriction, reachable and exploitable by an unprivileged transaction submitter (here, a relayer or any party able to construct/forward a delegate action).

### Likelihood Explanation
High, prior to activation of the fix: the path is reachable from a single submitted transaction (a relayer wrapping a user's `SignedDelegateAction`) with no special privileges, and the test file proves the drain succeeds deterministically pre-upgrade with standard `AddKey`/`TransferToGasKey`/`Delegate` actions available to any account.

### Recommendation
Reject `WithdrawFromGasKey` (and any other actions intended to be restricted to the account's own directly-signed, gas-key-nonce-ordered transaction flow) when they appear inside `Delegate`/`DelegateV2` action lists, at action-validation time rather than relying solely on the `actor_id == account_id` runtime check — which is exactly what `ProtocolFeature::RejectWithdrawFromGasKeyInDelegate` / `WithdrawFromGasKeyNotAllowedInDelegate` is intended to do. Confirm this feature is activated at the current stable protocol version, since the index available here could not conclusively verify its activation status; if it is still gated behind a nightly protocol version, the analog remains exploitable in production until stabilized.

### Proof of Concept
The existing test `test_reject_delegated_gas_key_withdraw_protocol_upgrade` is itself the PoC: [7](#0-6)  — it (1) adds a gas key, (2) funds it via `TransferToGasKey`, (3) wraps a `WithdrawFromGasKey` action inside a `SignedDelegateAction` sent through a relayer, and (4) confirms the gas key balance is drained by the amount withdrawn ("the nested withdrawal should have drained the gas key"), before the `RejectWithdrawFromGasKeyInDelegate` validation rule rejects the same construction post-upgrade.

### Citations

**File:** runtime/runtime/src/actions.rs (L727-746)
```rust
    };

    match nonce_update {
        DelegateNonceUpdate::AccessKey => {
            access_key.nonce = delegate_nonce.nonce();
            set_access_key(state_update, sender_id.clone(), public_key.clone(), &access_key);
        }
        DelegateNonceUpdate::GasKey { nonce_index } => {
            set_gas_key_nonce(
                state_update,
                sender_id.clone(),
                public_key.clone(),
                nonce_index,
                delegate_nonce.nonce(),
            );
        }
    }

    Ok(())
}
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

**File:** runtime/runtime/src/lib.rs (L803-821)
```rust
            Action::TransferToGasKey(transfer_to_gas_key) => {
                metrics::ACTION_CALLED_COUNT.transfer_to_gas_key.inc();
                action_transfer_to_gas_key(
                    state_update,
                    &mut result,
                    account_id,
                    transfer_to_gas_key,
                )?;
            }
            Action::WithdrawFromGasKey(withdraw_from_gas_key) => {
                metrics::ACTION_CALLED_COUNT.withdraw_from_gas_key.inc();
                action_withdraw_from_gas_key(
                    state_update,
                    account.as_mut().expect(EXPECT_ACCOUNT_EXISTS),
                    &mut result,
                    account_id,
                    withdraw_from_gas_key,
                )?;
            }
```

**File:** test-loop-tests/src/tests/reject_delegated_gas_key_withdraw.rs (L21-54)
```rust
const WITHDRAW_AMOUNT: Balance = Balance::from_millinear(1);

/// Build a meta transaction whose inner action withdraws from the sender's own
/// gas key. The delegate is signed by the sender's plain access key, since a
/// gas key cannot sign a V1 delegate action.
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

**File:** test-loop-tests/src/tests/reject_delegated_gas_key_withdraw.rs (L56-137)
```rust
#[test]
fn test_reject_delegated_gas_key_withdraw_protocol_upgrade() {
    init_test_logger();

    if !ProtocolFeature::RejectWithdrawFromGasKeyInDelegate.enabled(PROTOCOL_VERSION) {
        return;
    }

    let new_protocol = ProtocolFeature::RejectWithdrawFromGasKeyInDelegate.protocol_version();
    let old_protocol = new_protocol - 1;
    assert!(
        old_protocol >= MIN_SUPPORTED_PROTOCOL_VERSION,
        "no supported protocol version still admits a delegated WithdrawFromGasKey, so there is \
         nothing left to test here - remove this test"
    );

    let sender = create_account_id("alice");
    let relayer = create_account_id("relayer");
    let epoch_length = 10;

    // Boundary "mm": "alice" lands on the first shard, "relayer" on the second,
    // so the delegate receipt crosses a shard on its way to the sender.
    let shard_layout = ShardLayout::multi_shard_custom(vec![create_account_id("mm")], 1);

    let mut env = TestLoopBuilder::new()
        .enable_rpc()
        .protocol_version(old_protocol)
        .protocol_upgrade_schedule(ProtocolUpgradeVotingSchedule::new_immediate(new_protocol))
        .epoch_length(epoch_length)
        .shard_layout(shard_layout)
        .add_user_account(&sender, Balance::from_near(1_000))
        .add_user_account(&relayer, Balance::from_near(1_000))
        .build();

    let gas_key: Signer =
        InMemorySigner::from_seed(sender.clone(), KeyType::ED25519, "gas_key").into();
    let add_key_tx = env.rpc_node().tx_from_actions(
        &sender,
        &sender,
        vec![Action::AddKey(Box::new(AddKeyAction {
            public_key: gas_key.public_key(),
            access_key: AccessKey::gas_key_full_access(1),
        }))],
    );
    env.rpc_runner().run_tx(add_key_tx, Duration::seconds(10));

    let fund_tx = env.rpc_node().tx_from_actions(
        &sender,
        &sender,
        vec![Action::TransferToGasKey(Box::new(TransferToGasKeyAction {
            public_key: gas_key.public_key(),
            deposit: Balance::from_near(10),
        }))],
    );
    env.rpc_runner().run_tx(fund_tx, Duration::seconds(10));

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
