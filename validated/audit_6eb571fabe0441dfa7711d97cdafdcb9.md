### Title
Unauthorized double-spend of gas key balance via `WithdrawFromGasKey` nested inside a `Delegate`/`DelegateV2` action bypassing the pending transaction queue's balance tracking - (File: `chain/client/src/pending_transaction_queue.rs`)

### Summary
The Sherlock report describes a lending protocol where `borrowCrossChain()` records collateral usage in storage and fires an async cross-chain message, but places no lock on the collateral; because the local `redeem()` check only looks at already-recorded borrows, the same collateral can be spent twice before the async message resolves. The direct nearcore analog is the `PendingTransactionQueue`'s gas-key balance admission control: a `WithdrawFromGasKeyAction` nested inside a `Delegate`/`DelegateV2` action moves balance out of a gas key when the resulting receipt executes (asynchronously, potentially cross-shard), but the mempool-level admission control that is supposed to "lock" pending spend against that same gas key only inspects an accepted transaction's **top-level** actions.

### Finding Description
`PendingTransactionQueue::add_chunk_transactions` scans each transaction's top-level actions for `Action::WithdrawFromGasKey` to update `pending_gas_key_costs`, the running total of gas-key balance already committed by chunks that have been included in blocks but not yet certified/executed: [1](#0-0) 

The same top-level-only scan is repeated in `PendingTxSession::check_pending`, which computes `paid_from_gas_key` used to admit or reject a new gas-key transaction: [2](#0-1) 

However, `WithdrawFromGasKeyAction` can also be delivered as an **inner action of a `Delegate`/`DelegateV2` action** (a meta-transaction), which the queue never unpacks. `action_withdraw_from_gas_key` in the runtime still executes the withdrawal and moves the balance from the gas key to the account when the receipt is finally applied, with no reference to any pending-queue lock: [3](#0-2) 

This exact gap is acknowledged directly in the codebase's own protocol-feature documentation, which introduces `RejectDelegateV2` and `RejectWithdrawFromGasKeyInDelegate` specifically to close it: [4](#0-3) 

A dedicated test reproduces the pre-fix behavior end-to-end: a delegated (relayed) transaction whose inner action is `WithdrawFromGasKey` is admitted and successfully drains the gas key, and only after the protocol upgrade is it rejected at admission with `WithdrawFromGasKeyNotAllowedInDelegate`: [5](#0-4) 

The exploit mirrors the report's root cause precisely: an action that will assuredly move value (`WithdrawFromGasKey`, analogous to `borrowCrossChain`) is committed/queued without being reflected in the accounting structure (`pending_gas_key_costs`, analogous to the missing collateral lock) that a second, concurrent admission check (`check_pending`, analogous to `redeem()`'s liquidity check) relies on to prevent overcommitment of the same funds.

### Impact Explanation
Because `pending_gas_key_costs`/`paid_from_gas_key` never account for a `WithdrawFromGasKey` nested in a delegate action, a user can submit multiple such delegated withdrawal transactions (directly, or via one or more relayers) targeting the same gas key across several pending-but-uncertified chunks. Each is admitted independently as if the gas key still holds its full balance, since the queue "sees" no cost against it. When the underlying receipts are ultimately applied, `action_withdraw_from_gas_key` performs `checked_sub` per withdrawal and can still fail once the real balance is exhausted, but the failure mode depends on receipt ordering and cross-shard delivery timing, and multiple withdrawals admitted concurrently defeat the purpose of the admission-control balance guarantee, allowing more spend to be queued/relayed than the account actually backs. This is a concrete instance of unauthorized value movement / accounting bypass in the transaction admission and chunk transaction selection layer, which the project itself treats as serious enough to require a protocol-version gate (`RejectDelegateV2`, `RejectWithdrawFromGasKeyInDelegate`) to close.

### Likelihood Explanation
The path is reachable by any unprivileged account holder with a gas key and, optionally, a relayer relaying a signed `DelegateAction` — no validator or operator privilege is required. It requires only standard RPC transaction submission (`AddKey` with gas-key permission, `TransferToGasKey`, then a `Delegate` action wrapping `WithdrawFromGasKey`), all of which are ordinary, unprivileged flows exercised in the referenced test.

### Recommendation
Ensure the pending transaction queue (and any related admission-control accounting) recursively inspects actions nested inside `Delegate`/`DelegateV2` payloads, not just top-level actions, when tracking gas-key balance commitments — or otherwise ensure (as `RejectDelegateV2`/`RejectWithdrawFromGasKeyInDelegate` do) that `WithdrawFromGasKey` cannot be delivered as a nested action at all. Confirm this protocol feature is active on the target network before it processes any `Delegate`/`DelegateV2` transactions with gas keys enabled.

### Proof of Concept [6](#0-5)

### Citations

**File:** chain/client/src/pending_transaction_queue.rs (L311-320)
```rust
            // Scan actions for WithdrawFromGasKey (affects gas key balance).
            for action in tx.actions() {
                if let Action::WithdrawFromGasKey(withdraw) = action {
                    let gas_key_entry = chunk_data
                        .gas_key_costs
                        .entry((signer_id.clone(), (&withdraw.public_key).into()))
                        .or_insert(Balance::ZERO);
                    *gas_key_entry = gas_key_entry.saturating_add(withdraw.amount);
                }
            }
```

**File:** chain/client/src/pending_transaction_queue.rs (L576-592)
```rust
        // Track WithdrawFromGasKey amounts from this tx's actions.
        for action in tx.transaction.actions() {
            if let Action::WithdrawFromGasKey(withdraw) = action {
                let entry = self
                    .session_gas_key_withdrawals
                    .entry((signer_id.clone(), (&withdraw.public_key).into()))
                    .or_insert(Balance::ZERO);
                *entry = entry.saturating_add(withdraw.amount);
            }
        }

        PendingTxCheckResult::Admit(PendingConstraints {
            paid_from_balance: snapshot.paid_from_balance,
            paid_from_gas_key,
            max_nonce: snapshot.max_nonce,
            max_bootstrap_nonce: snapshot.max_bootstrap_nonce,
        })
```

**File:** runtime/runtime/src/access_keys.rs (L290-334)
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
```

**File:** core/primitives-core/src/version.rs (L453-465)
```rust
    /// Reject `Action::DelegateV2`. This disables meta transactions from gas
    /// keys, because the inner nonce advances a gas key of the delegate sender
    /// and `PendingTransactionQueue` does not see it: the queue reads only the
    /// outer transaction's signer, public key and nonce index, so its nonce and
    /// gas key balance commitments would miss that key. The `DelegateV2`
    /// variant and `VersionedDelegateActionPayload` remain so a later delegate
    /// action version can reuse them.
    RejectDelegateV2,
    /// Reject a `WithdrawFromGasKey` action nested inside a delegate action.
    /// The SPICE pending transaction queue scans only the top level actions of
    /// a transaction for `WithdrawFromGasKey`, so a nested one drains a gas key
    /// that the queue still counts as funded.
    RejectWithdrawFromGasKeyInDelegate,
```

**File:** test-loop-tests/src/tests/reject_delegated_gas_key_withdraw.rs (L26-137)
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
