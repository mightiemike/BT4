### Title
Nested `WithdrawFromGasKey` inside a `Delegate` action bypasses the SPICE pending-transaction-queue's gas-key balance accounting, allowing gas-key funds to be drained beyond tracked limits - ([File: runtime/runtime/src/action_validation.rs], [File: chain/client/src/pending_transaction_queue.rs], [File: core/primitives-core/src/version.rs])

### Summary
The `PendingTransactionQueue`/`PendingTxSession` admission logic that tracks uncertified gas-key spend only inspects the **top-level** actions of a transaction when accumulating `WithdrawFromGasKey` amounts against a gas key's committed balance. Before the `RejectWithdrawFromGasKeyInDelegate` protocol feature is enabled, a `WithdrawFromGasKey` action nested inside a `Delegate`/`DelegateV2` action is accepted by both action validation and runtime execution, but is invisible to this admission accounting. This is the exact "unwraps but does not update user state" pattern: the value-moving operation (`action_withdraw_from_gas_key`, which actually decrements the gas key balance and credits the account) executes correctly on-chain, but the auxiliary bookkeeping layer that other concurrently-pending transactions rely on to avoid overdrawing the same balance is never updated for this code path.

### Finding Description
`action_withdraw_from_gas_key` in `runtime/runtime/src/access_keys.rs:290-334` directly mutates a gas key's `balance` and credits the owning account when a `WithdrawFromGasKey` action executes, regardless of whether it arrived as a top-level transaction action or as an inner action of a `Delegate`/`DelegateV2` receipt. [1](#0-0) 

The SPICE `PendingTransactionQueue`, however, only scans `tx.actions()` (the outer transaction's action list) for `Action::WithdrawFromGasKey` when building the per-chunk aggregate `gas_key_costs`, and `PendingTxSession::check_pending` does the same when tracking in-session withdrawals: [2](#0-1) [3](#0-2) 

Because a `Delegate` action's inner actions are not top-level actions of the outer transaction, a `WithdrawFromGasKey` nested inside a `Delegate` action's `actions` list is never counted into `pending_gas_key_costs`/`session_gas_key_withdrawals`. `verify_and_charge_gas_key_tx_ephemeral` (`runtime/runtime/src/verifier.rs:524-594`) relies on this pending accounting (`pending.paid_from_gas_key`) to reject gas-key transactions when the tracked balance is exhausted, with an explicit assumption baked into the comment that "gas key balance only changes through transactions that PTQ explicitly tracks, so pending should never exceed the balance" — an assumption this path violates. [4](#0-3) 

The protocol feature `RejectWithdrawFromGasKeyInDelegate` was added specifically to close this hole, with the code comment stating: "The SPICE pending transaction queue scans only the top level actions of a transaction for `WithdrawFromGasKey`, so a nested one drains a gas key that the queue still counts as funded." [5](#0-4) 

The enforcement is implemented in `validate_delegate_action`, gated behind the feature flag and only for newly-created receipts (`ValidateReceiptMode::NewReceipt`), meaning in-flight receipts created pre-upgrade — and any protocol version below the feature's activation that remains within `MIN_SUPPORTED_PROTOCOL_VERSION` — still admit the nested withdrawal: [6](#0-5) 

The regression/documentation test explicitly demonstrates the exploit path pre-upgrade: a `Delegate` action whose inner action is `WithdrawFromGasKey` is admitted and "moves balance out of the gas key, which is the hole this rule closes," while a relayer keeps submitting the same style of transaction across the protocol upgrade boundary to confirm in-flight receipts must still be tolerated. [7](#0-6) [8](#0-7) 

Notably, the same design intent is documented at the WASM host-function boundary: there is deliberately no promise-batch-action host function for `WithdrawFromGasKey` "because... they will not be visible to the pending transaction queue," confirming visibility-to-PTQ is treated as a hard security invariant for any code path that can reduce gas-key balance — an invariant the `Delegate`-nested path breaks. [9](#0-8) 

### Impact Explanation
Any unprivileged transaction signer/relayer who can submit a `Delegate` action to a shard that has not yet activated `RejectWithdrawFromGasKeyInDelegate` (or whose in-flight receipts predate the upgrade) can drain a gas key's balance via a path invisible to admission control. Because other pending, uncertified gas-key transactions are validated against a `paid_from_gas_key` figure that does not include this drain, the actual on-chain balance can be depleted below what concurrently pending transactions assumed was reserved. This can cause: (1) later legitimately-admitted gas-key transactions to fail execution (in `action_withdraw_from_gas_key`/gas charging) after having already been optimistically admitted by chunk producers, producing chunk-production/execution inconsistencies under the SPICE uncertified-execution model; and (2) unauthorized/uncoordinated depletion of a shared gas-key balance beyond the reservation invariant the system explicitly relies on ("pending should never exceed the balance"), directly analogous to the WJLP report's "funds can be stolen because internal accounting wasn't updated before withdrawal." This is scoped to the SPICE (uncertified execution / pending-transaction-queue) feature.

### Likelihood Explanation
Exploitation requires only a single signed `Delegate`/`DelegateV2` transaction with a nested `WithdrawFromGasKey` action naming the attacker's own (or a colluding) gas key — no validator, network, or node compromise is needed, and the construction is demonstrated directly in the referenced test helper `delegated_withdraw_tx`. It applies only pre-upgrade or to receipts admitted before `RejectWithdrawFromGasKeyInDelegate` activates for the relevant chunk producer, so its window is bounded by the protocol upgrade rollout and by `ValidateReceiptMode` gating for already-created receipts.

### Recommendation
Ensure `PendingTransactionQueue::add_chunk_transactions` and `PendingTxSession::check_pending` recursively scan `Delegate`/`DelegateV2` inner actions (not just top-level `tx.actions()`) for `WithdrawFromGasKey`, in addition to (or instead of) the protocol-level rejection, so that any node/version still admitting nested withdrawals cannot silently bypass gas-key balance tracking. Confirm `RejectWithdrawFromGasKeyInDelegate` is activated network-wide as early as possible and that no code path (including future `Delegate` variants) can introduce gas-key-balance-reducing actions without an explicit visibility guarantee to the pending transaction queue.

### Proof of Concept
1. On a shard/chunk producer running a protocol version below `RejectWithdrawFromGasKeyInDelegate` activation (as constructed by `test_reject_delegated_gas_key_withdraw_protocol_upgrade`), create a gas key with `AddKey`+`TransferToGasKeyAction` funding it, e.g. 10 NEAR.
2. Craft a `Delegate` action (`DelegateAction`) whose single inner action is `Action::WithdrawFromGasKey(WithdrawFromGasKeyAction { public_key: gas_key.public_key(), amount: WITHDRAW_AMOUNT })`, signed by the sender's normal access key and sent via any relayer, exactly as in `delegated_withdraw_tx`. [10](#0-9) 
3. Submit this transaction; it is admitted and executed, and the gas key balance decreases as verified by `query_gas_key_and_balance` before/after — `balance_after == balance_before - WITHDRAW_AMOUNT` — while `PendingTransactionQueue`'s `gas_key_costs`/`pending_gas_key_costs` never recorded this withdrawal because it only scans top-level `tx.actions()`. [11](#0-10) 
4. Repeat concurrently with other gas-key transactions relying on the (stale, over-optimistic) `paid_from_gas_key` snapshot to demonstrate that admission decisions can diverge from the true remaining balance, violating the invariant asserted in `verify_and_charge_gas_key_tx_ephemeral`. [4](#0-3)

### Citations

**File:** runtime/runtime/src/access_keys.rs (L290-326)
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
```

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

**File:** chain/client/src/pending_transaction_queue.rs (L576-586)
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

```

**File:** runtime/runtime/src/verifier.rs (L575-594)
```rust
    // Check gas key has enough balance for gas costs, accounting for
    // pending gas key costs (prior gas key txs + pending WithdrawFromGasKey).
    // Unlike account balance, gas key balance only changes through transactions
    // that PTQ explicitly tracks, so pending should never exceed the balance.
    let Some(available_gas_key_balance) =
        gas_key_info.balance.checked_sub(pending.paid_from_gas_key)
    else {
        tracing::error!(
            target: "runtime",
            balance = %gas_key_info.balance,
            paid_from_gas_key = %pending.paid_from_gas_key,
            "pending gas key costs exceed gas key balance"
        );
        return TxVerdict::Failed(InvalidTxError::NotEnoughGasKeyBalance {
            signer_id: account_id.clone(),
            balance: Balance::ZERO,
            cost: gas_cost,
        });
    };
    if available_gas_key_balance < gas_cost {
```

**File:** core/primitives-core/src/version.rs (L461-465)
```rust
    /// Reject a `WithdrawFromGasKey` action nested inside a delegate action.
    /// The SPICE pending transaction queue scans only the top level actions of
    /// a transaction for `WithdrawFromGasKey`, so a nested one drains a gas key
    /// that the queue still counts as funded.
    RejectWithdrawFromGasKeyInDelegate,
```

**File:** runtime/runtime/src/action_validation.rs (L222-248)
```rust
fn validate_delegate_action(
    limit_config: &LimitConfig,
    delegate_action: VersionedDelegateActionRef<'_>,
    receiver: &AccountId,
    current_protocol_version: ProtocolVersion,
    mode: ValidateReceiptMode,
) -> Result<(), ActionsValidationError> {
    // Check the count before `get_actions()` clones the list, so a huge
    // nested-action list can't force the allocation before being rejected.
    // Consensus-neutral: same `TotalNumberOfActionsExceeded` as the check in
    // `validate_actions_with_mode` below.
    let num_actions = delegate_action.actions().len() as u64;
    if num_actions > limit_config.max_actions_per_receipt {
        return Err(ActionsValidationError::TotalNumberOfActionsExceeded {
            total_number_of_actions: num_actions,
            limit: limit_config.max_actions_per_receipt,
        });
    }
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

**File:** test-loop-tests/src/tests/reject_delegated_gas_key_withdraw.rs (L139-153)
```rust
    // Keep submitting across the upgrade boundary. Nothing should crash:
    // transactions admitted just before the upgrade produce receipts that may
    // only execute after it, and those existing receipts must still be tolerated.
    let mut blocks_after_upgrade = 0;
    let mut iterations = 0;
    while blocks_after_upgrade < 5 {
        iterations += 1;
        assert!(iterations < 20 * epoch_length, "the upgrade never happened");
        let tx = delegated_withdraw_tx(&env, &sender, &relayer, &gas_key);
        env.rpc_node().submit_tx(tx);
        env.rpc_runner().run_for_number_of_blocks(1);
        if env.rpc_node().protocol_version_at_head() >= new_protocol {
            blocks_after_upgrade += 1;
        }
    }
```

**File:** runtime/near-vm-runner/src/imports.rs (L311-317)
```rust
    ] -> []>,
    // NOTE: There are intentionally no promise batch actions for
    // WithdrawFromGasKey. Actions that reduce gas key balance must only be
    // initiated via transactions, not by contracts. Otherwise, they will not be
    // visible to the pending transaction queue. Do not add host functions for
    // them. See NEP-611 for details.
    // #######################
```
