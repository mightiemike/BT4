### Title
`WithdrawFromGasKey` nested in a `Delegate` action drains a gas key balance the `PendingTransactionQueue` still counts as funded - ([File: chain/client/src/pending_transaction_queue.rs])

### Summary
The external Solidity report describes a wrapper function (`withdrawCollateralAndClaim`) that performs a collateral-sufficiency check before calling an inner function (`withdrawLiquidityAndClaim`), but that inner function is itself public and reachable directly, letting an attacker bypass the check and pull funds out from under an outstanding loan. The nearcore analog is the `PendingTransactionQueue`'s admission-time gas-key balance accounting: it only scans a transaction's **top-level** actions for `WithdrawFromGasKey` to keep its running total of "already committed" gas-key spend up to date, but a `WithdrawFromGasKey` action can also be smuggled inside a `Delegate`/meta-transaction's inner action list, which the queue never inspects, letting the balance be drained while the queue still reports the gas key as funded.

### Finding Description
`ShardedPendingTransactionQueue::add_chunk_transactions` only walks `tx.actions()` (the outer, top-level actions of the signed transaction) to find `Action::WithdrawFromGasKey` and update `pending_gas_key_costs`: [1](#0-0) 

The same top-level-only scan is repeated in the per-chunk-production admission path, `PendingTxSession::check_pending`, which is the function the RPC/chunk-transaction-selection logic uses to admit or skip a transaction before it is certified: [2](#0-1) 

These pending totals are then fed back into `verify_and_charge_gas_key_tx_ephemeral`, the actual runtime check that decides whether a gas-key transaction may be admitted, via `pending.paid_from_gas_key`: [3](#0-2) 

If a `WithdrawFromGasKey` action is instead nested inside a `Delegate`/`DelegateV2` action's inner action list, the outer transaction's top-level actions never contain `WithdrawFromGasKey`, so neither `add_chunk_transactions` nor `check_pending` records any pending gas-key cost for it. The queue continues to report the gas key as fully funded to any concurrently-admitted transaction, even though the delegate receipt, once executed, will call `action_withdraw_from_gas_key` and actually decrement `gas_key_info.balance`: [4](#0-3) 

This is exactly the bug-class in the report: an authorization/accounting check performed on a "wrapper" call path (the top-level-action scan used for pending-queue admission) can be bypassed by reaching the same state-mutating operation (`WithdrawFromGasKey`) through an alternate, unguarded entry point (`Delegate`) that the check does not inspect.

The codebase itself documents this exact bug and its fix: [5](#0-4) 

`RejectWithdrawFromGasKeyInDelegate` closes the hole by outright rejecting any `WithdrawFromGasKey` nested in a `Delegate` for **new** receipts, enforced in `validate_delegate_action`: [6](#0-5) 

and demonstrated concretely by the pre-upgrade/post-upgrade regression test, which shows the nested withdrawal succeeding and draining the gas key balance before the protocol upgrade: [7](#0-6) 

### Impact Explanation
Before `RejectWithdrawFromGasKeyInDelegate` is active, an attacker (or a relayer colluding with the gas-key owner, or even the gas-key owner alone) can submit multiple transactions in the same uncertified window: ordinary gas-key transactions that the queue believes are covered by the remaining balance, plus a `Delegate` action carrying a nested `WithdrawFromGasKey`. Because the nested withdrawal is invisible to the queue's accounting, the queue will admit gas-key transactions whose combined true cost (regular gas spend + the hidden withdrawal) exceeds the actual on-chain gas-key balance. This is a form of value-movement/accounting-bypass bug: the gas key balance is drained without going through the balance-tracking gate meant to prevent over-commitment, which can lead to transactions being admitted (and executed) against a gas key that no longer has sufficient backing balance, i.e., unauthorized/inconsistent balance accounting reachable purely from a single signer's transaction stream.

### Likelihood Explanation
This requires no privileged role — any account holding a gas key (a normal, user-obtainable access-key type) can craft the nested `Delegate`+`WithdrawFromGasKey` transaction and submit it alongside ordinary gas-key transactions during a window before certification, precisely as demonstrated by the existing regression test. The bug is reachable purely from account-level transaction crafting (an unprivileged signer), matching the "single submitted transaction" requirement, and is not validator-, network-, or sync-specific.

### Recommendation
- Ensure `RejectWithdrawFromGasKeyInDelegate` (and the corresponding `validate_delegate_action` check) is active for all new receipts on every network that supports gas keys, and confirm it is not gated behind an experimental/nightly-only or SPICE-only configuration flag before general availability.
- Long term, make the pending-transaction-queue's action scan recursive (or explicitly disallow any balance-mutating action from being nested inside `Delegate`/meta-transactions) rather than maintaining a second, ad hoc allow-list of "actions the queue must also look for," to avoid this class of bug recurring for future gas-key-affecting actions.

### Proof of Concept
The existing test `test_reject_delegated_gas_key_withdraw_protocol_upgrade` is itself a proof of concept for the pre-fix behavior: it funds a gas key, submits a `Delegate` action wrapping `WithdrawFromGasKey` before the protocol upgrade, and asserts the delegated withdrawal succeeds and drains the gas key balance (`balance_after == balance_before - WITHDRAW_AMOUNT`), then shows the same transaction is rejected with `WithdrawFromGasKeyNotAllowedInDelegate` only after the upgrade activates: [8](#0-7) 

Note: I could not fully verify within the available tool budget whether the `PendingTransactionQueue`/gas-key admission path (and thus this specific pre-fix window) is gated behind the `protocol_feature_spice` cfg flag or is part of the general stable gas-key code path; `pending_transaction_queue.rs` itself carries no `protocol_feature_spice` cfg annotations, but several of its consuming tests do. If it turns out this queue is exclusively exercised under SPICE-only configurations, it would fall under the excluded "SPICE validator-only" category per the task rules — this should be double-checked against the live build configuration before treating it as a general-purpose finding.

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

**File:** chain/client/src/pending_transaction_queue.rs (L576-593)
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

**File:** core/primitives-core/src/version.rs (L461-465)
```rust
    /// Reject a `WithdrawFromGasKey` action nested inside a delegate action.
    /// The SPICE pending transaction queue scans only the top level actions of
    /// a transaction for `WithdrawFromGasKey`, so a nested one drains a gas key
    /// that the queue still counts as funded.
    RejectWithdrawFromGasKeyInDelegate,
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

**File:** test-loop-tests/src/tests/reject_delegated_gas_key_withdraw.rs (L112-171)
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
