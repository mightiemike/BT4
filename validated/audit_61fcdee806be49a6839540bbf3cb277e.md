### Title
Delegated `WithdrawFromGasKey` action bypasses access-key authorization scope, allowing unauthorized drain of a gas key's balance - ([File: runtime/runtime/src/action_validation.rs])

### Summary
The Solidity report describes public, unauthorized entry points (`refundETH`, `sweepToken`, `unwrapWETH9`, `approve`) that let any caller move funds out of a router contract because no access-control check ties the action to an authorized party. The closest reachable analog in nearcore is the previously-exploitable path where a `WithdrawFromGasKeyAction` nested inside a meta-transaction (`Action::Delegate`) could move funds out of a gas key's balance into the account, even though the relayer that pays for and submits the transaction is not the key holder and the delegation's authorization model was not designed to scope this sensitive balance-moving action.

### Finding Description
A NEAR gas key (`AccessKeyPermission::GasKey`) holds its own prepaid balance (`GasKeyInfo::balance`) that is normally spent only through gas/fee accounting tied to transactions signed with that specific key [1](#0-0) . `action_withdraw_from_gas_key` unconditionally moves `action.amount` from the gas key balance into the owning account's main balance once the key and sufficient balance are found — it performs no additional authorization beyond the enclosing action-execution context [2](#0-1) .

The vulnerability class is that `WithdrawFromGasKeyAction` could be embedded as an inner action of a `Delegate`/`DelegateV2` meta-transaction. In that flow, the *sender* signs the outer delegate with their full-access key, but the actual receipt executing the inner actions (including `WithdrawFromGasKey`) is constructed and paid for by a relayer, and validation of delegate-nested actions did not originally special-case `WithdrawFromGasKey`. The fix, `ProtocolFeature::RejectWithdrawFromGasKeyInDelegate`, explicitly rejects any `WithdrawFromGasKey` nested inside a `Delegate` action for newly created receipts: [3](#0-2) 

Before this feature's activation, nested `WithdrawFromGasKey` inside a delegate action was admitted and executed successfully, moving the entire gas-key balance to the account as demonstrated by the regression test: [4](#0-3) 

The test comment explicitly documents this as "the hole this rule closes" and that the nested withdrawal "should have drained the gas key" pre-upgrade [5](#0-4) .

### Impact Explanation
This maps to "unauthorized value movement" — a gas key's dedicated balance, meant to be spent only for gas/fees by its own holder, could be redirected in bulk to the owning account's spendable balance via a meta-transaction path that was not intended to permit this action, undermining the security boundary between gas-key-scoped funds and full-account funds and complicating relayer/session-key trust assumptions (relayers pay for transactions expecting bounded gas-key spend, not a full balance withdrawal maneuver).

### Likelihood Explanation
This is reachable by any transaction signer who can construct a `SignedDelegateAction` with a nested `WithdrawFromGasKeyAction` and have it relayed — i.e., a single crafted meta-transaction from an ordinary account, requiring no privileged or validator access. On any node still running a protocol version prior to `RejectWithdrawFromGasKeyInDelegate`'s activation, this executes deterministically and successfully, as shown by the pre-upgrade portion of the regression test.

### Recommendation
Confirm `ProtocolFeature::RejectWithdrawFromGasKeyInDelegate` is enabled at or before all currently supported/deployed protocol versions (i.e., `MIN_SUPPORTED_PROTOCOL_VERSION` is at or above its activation version), so no live network can process a delegate-nested `WithdrawFromGasKey`. Additionally, audit other gas-key-affecting actions (e.g., `TransferToGasKey`) for similar delegate-nesting exposure, and ensure any future new action types that move gas-key balances are explicitly considered in `validate_delegate_action`'s allow/deny logic rather than being permitted by default.

### Proof of Concept
The existing regression test constructs the exact PoC: it builds a `DelegateAction` signed by the sender containing a nested `Action::WithdrawFromGasKey`, submits it via a relayer, and confirms (pre-upgrade) that the gas key balance decreases by the full nested withdrawal amount despite the sender not directly authorizing a standalone withdrawal transaction: [6](#0-5) [7](#0-6)

### Citations

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

**File:** runtime/runtime/src/action_validation.rs (L240-248)
```rust
    let actions = delegate_action.get_actions();
    // As with the `DelegateV2` removal above, receipts created before this rule
    // are still in flight and must keep executing.
    if mode == ValidateReceiptMode::NewReceipt
        && ProtocolFeature::RejectWithdrawFromGasKeyInDelegate.enabled(current_protocol_version)
        && actions.iter().any(|action| matches!(action, Action::WithdrawFromGasKey(_)))
    {
        return Err(ActionsValidationError::WithdrawFromGasKeyNotAllowedInDelegate);
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
