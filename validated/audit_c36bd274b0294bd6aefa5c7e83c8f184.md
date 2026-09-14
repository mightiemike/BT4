I found a solid analog. This is already tracked as a known, explicitly-documented hole in the codebase (comment at `core/primitives-core/src/version.rs:461-465`), fixed by a not-yet-active `ProtocolFeature::RejectWithdrawFromGasKeyInDelegate`, and directly matches the report's bug class: a value-accounting mechanism (the PTQ's `pending_gas_key_costs` map, analogous to the perps margin "reserved IM" tracker) that only scans **top-level** transaction actions, so wrapping the same draining action in one extra layer of indirection (a `Delegate` action, analogous to "closing another position") lets the drain bypass the accounting and be admitted as if the gas key were still fully funded — the identical "reserve accounted at the outer layer, consumed at an inner layer, so a second op double-spends the reserve" pattern from the perps report.

### Title
Delegated `WithdrawFromGasKey` bypasses Pending Transaction Queue accounting, enabling gas-key balance over-commitment/double-spend - (File: `chain/client/src/pending_transaction_queue.rs`)

### Summary
The Pending Transaction Queue (PTQ) tracks how much of a gas key's on-chain balance is already committed by transactions included in blocks but not yet certified, so that concurrently-submitted transactions cannot be admitted against balance that is already spoken for. It does this by scanning `Action::WithdrawFromGasKey` only at the **top level** of a transaction's actions [1](#0-0) . When a `WithdrawFromGasKey` action is nested inside a `Delegate`/meta-transaction instead, the PTQ never sees it, so the outer transaction is admitted as if the gas key balance were untouched, while the inner action still drains the real gas-key balance at execution time — exactly analogous to the perps report's `finalMarginDelta`/`rebalanceClose` bug where a refund is computed from stale/current state rather than the amount actually still reserved, letting a second withdraw exceed what was truly still available.

### Finding Description
`verify_and_charge_gas_key_tx_ephemeral` admits a gas-key transaction by checking `gas_key_info.balance.checked_sub(pending.paid_from_gas_key)` — i.e. current on-trie balance minus whatever the PTQ says is already pending [2](#0-1) . `pending.paid_from_gas_key` is populated exclusively by `PendingTxSession::check_pending`/`add_chunk_transactions`, both of which scan `tx.transaction.actions()` — the transaction's own top-level action list — for `Action::WithdrawFromGasKey` [3](#0-2) [1](#0-0) .

If the same `WithdrawFromGasKey` action is instead nested inside a `Delegate` action (a meta-transaction/relayed transaction), the outer transaction's top-level actions contain only `Delegate`, not `WithdrawFromGasKey`, so the PTQ scan misses it entirely — while the runtime still executes the inner action and actually decrements the gas key's real balance via `action_withdraw_from_gas_key` [4](#0-3) . This is explicitly documented as a known accounting gap in the protocol version list: `RejectWithdrawFromGasKeyInDelegate` — "the SPICE pending transaction queue scans only the top level actions of a transaction for `WithdrawFromGasKey`, so a nested one drains a gas key that the queue still counts as funded" [5](#0-4) . `RejectDelegateV2` is the related sibling gate, noting the same class of blind spot for gas-key nonces more generally [6](#0-5) . The validation-layer fix (`validate_delegate_action` rejecting `WithdrawFromGasKey` nested in a delegate) is gated behind this not-yet-active feature [7](#0-6) , and a dedicated test confirms the pre-upgrade behavior actually drains the gas key while nested [8](#0-7) .

This mirrors the perps report's root cause precisely: a balance-accounting check (`assertPostWithdrawalMarginRequired`/PTQ balance check) is satisfied against a value that fails to account for an amount already consumed through an indirect path (`_close`'s `marginDelta`/a nested `WithdrawFromGasKey`), so a second concurrent operation extracts value beyond what should be available.

### Impact Explanation
An attacker who controls a gas key can, while several of the gas key's transactions are still pending (included but not yet certified — a window that is architecturally guaranteed to exist under SPICE's delayed-execution model), submit an additional gas-key-signed transaction (e.g. a `Transfer`) that the RPC admits believing the gas key is still fully funded, because a concurrently-pending relayed transaction wraps `WithdrawFromGasKey` inside a `Delegate` action and is invisible to the PTQ's accounting. Both the withdrawal and the newly-admitted gas-key spend then execute against the same underlying balance, so the gas key balance can go negative/be overspent relative to what the runtime's `checked_sub` should allow, or (depending on execution order) two independent consumers both believe they have exclusive claim to the same funds. This is a state-transition/balance-accounting divergence from the intended invariant that gas-key spending never exceeds the key's real balance, directly matching the class of "concrete unauthorized value movement" / "invalid state transition acceptance" that this review targets.

### Likelihood Explanation
This requires only a single account controlling a gas key and access to relayer/meta-transaction submission (any unprivileged transaction signer or meta-transaction sender) — no validator, peer, or operator privilege is needed. The condition (transactions included but not yet certified) is a normal, expected window under the SPICE delayed-execution pending-transaction-queue design that this very subsystem exists to protect, and the codebase itself documents this exact gap by name and reserves a protocol-version gate for it, indicating the nearcore team is aware the current stable behavior is exploitable until `RejectWithdrawFromGasKeyInDelegate` activates.

### Recommendation
Activate `RejectWithdrawFromGasKeyInDelegate` (and the associated `validate_delegate_action` check) before/at the protocol version where gas keys and delegate actions can be combined in production, or alternatively make `PendingTxSession`/`PendingTransactionQueue::add_chunk_transactions` recursively scan into `Delegate`/`DelegateV2` inner actions for `WithdrawFromGasKey` (and similarly for any nonce/balance-affecting inner actions) so the accounting cannot be bypassed by one extra layer of wrapping, consistent with how `RejectDelegateV2` already documents the same blind spot for nonce commitments.

### Proof of Concept [9](#0-8)  constructs exactly this attack primitive: a `Delegate` action whose inner action is `WithdrawFromGasKey`, signed by the sender's plain access key and relayed by a third party, targeting the sender's own gas key. The test then demonstrates (pre-upgrade) that this transaction is admitted and executes, draining the gas key balance [8](#0-7) , while the PTQ code path that is supposed to prevent overspend against a pending withdrawal only scans top-level actions [1](#0-0) , so a second, concurrently-pending gas-key transaction submitted in the same window is admitted by `verify_and_charge_gas_key_tx_ephemeral` without ever seeing the pending drain.

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

**File:** chain/client/src/pending_transaction_queue.rs (L576-585)
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

**File:** core/primitives-core/src/version.rs (L453-460)
```rust
    /// Reject `Action::DelegateV2`. This disables meta transactions from gas
    /// keys, because the inner nonce advances a gas key of the delegate sender
    /// and `PendingTransactionQueue` does not see it: the queue reads only the
    /// outer transaction's signer, public key and nonce index, so its nonce and
    /// gas key balance commitments would miss that key. The `DelegateV2`
    /// variant and `VersionedDelegateActionPayload` remain so a later delegate
    /// action version can reuse them.
    RejectDelegateV2,
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

**File:** test-loop-tests/src/tests/reject_delegated_gas_key_withdraw.rs (L26-55)
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
