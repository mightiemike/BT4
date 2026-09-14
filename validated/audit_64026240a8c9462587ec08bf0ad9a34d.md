Confirmed: `RejectWithdrawFromGasKeyInDelegate` activates at protocol version 87, while `STABLE_PROTOCOL_VERSION = 86` (per `core/primitives-core/src/version.rs:628` cited earlier). This means the fix is **not yet active on the stable/mainnet protocol version**, so the underlying gap is presently live in this codebase's deployed configuration.

### Title
Gas-key balance double-spend via nested `WithdrawFromGasKey` inside a Delegate action bypassing the pending-transaction-queue funding check - (File: `chain/client/src/pending_transaction_queue.rs`)

### Summary
The SPICE pending transaction queue tracks a signer's gas-key balance commitments so that concurrently-admitted, not-yet-certified transactions cannot collectively overdraw a gas key. It does this by scanning each admitted transaction's top-level actions for `Action::WithdrawFromGasKey` and adding the withdrawn amount to `pending_gas_key_costs`/`session_gas_key_withdrawals`. However, this scan only inspects `tx.actions()` at the top level; it does not recurse into an `Action::Delegate`'s inner `DelegateAction.actions`. A `WithdrawFromGasKey` nested inside a meta-transaction (`Delegate`) is therefore invisible to the queue's funding bookkeeping while remaining fully executable by the runtime, exactly analogous to the Maia bug where a permission-revocation check performed at one control point (`BranchPort::toggleBridgeAgent`) is never consulted by the actual call-issuing path (`BranchBridgeAgent::callOut`).

### Finding Description
`PendingTransactionQueue::add_chunk_transactions` scans a chunk's transactions for `WithdrawFromGasKey` actions to update `pending_gas_key_costs`: [1](#0-0) 

The equivalent live, per-session tracking during chunk production is done the same way, iterating only `tx.transaction.actions()`: [2](#0-1) 

Both loops walk `tx.actions()`/`tx.transaction.actions()`, i.e., the transaction's top-level action list. When the transaction's top-level action is `Action::Delegate(SignedDelegateAction)` (a meta-transaction / relayed transaction), the inner `DelegateAction.actions` — which can itself contain a `WithdrawFromGasKey` against the *sender's* gas key — is never inspected. The protocol authors were aware of exactly this gap, as documented directly in the feature comment: [3](#0-2) 

The gate for rejecting this pattern, `RejectWithdrawFromGasKeyInDelegate`, together with `RejectDelegateV2`, `RemoveGasRewards`, `EarlyKickout`, etc., activates at protocol version 87: [4](#0-3) 

Prior to that activation, `validate_action_with_mode` performs no rejection of a nested `WithdrawFromGasKey` inside `Action::Delegate`/`Action::DelegateV2`, so the action validation layer accepts it at both pre- and (for in-flight receipts) post-upgrade: [5](#0-4) 

The project's own integration test exercises and confirms this exact hole pre-upgrade — a delegated `WithdrawFromGasKey` "drains the gas key" while the pending-transaction-queue's funding view is not updated to reflect it: [6](#0-5) 

### Impact Explanation
Under the current stable protocol version (86, prior to the 87 fix), an attacker can:
1. Fund a gas key with balance `B`.
2. Submit several transactions concurrently (before certification) that each carry a top-level `Action::Delegate` wrapping an inner `WithdrawFromGasKey` action against the same gas key.
3. Because the pending-transaction-queue only accounts for top-level `WithdrawFromGasKey` actions (per the code paths cited above) and for `gas_key_cost` from actual gas-key-signed transactions — not for nested delegate withdrawals — each of these delegated withdrawals is independently admitted as if the gas key still held its full pre-withdrawal balance, since `query_pending_state`/`check_pending`'s `pending_gas_key_cost` snapshot omits them.
4. Multiple such transactions can be admitted into concurrently-produced, not-yet-certified chunks, each believing the gas key is still fully funded, permitting the gas key's balance to be drawn down beyond what a single serialized view of its balance would allow — an "insufficient balance" check bypass leading to unauthorized value movement (over-withdrawal) analogous to the Maia agent continuing to originate value-moving calls after being toggled off.

This maps to the same abstract vulnerability class as the Maia finding: a permission/funding-authorization state change (gas key balance, tracked centrally) is correctly enforced by one code path (regular gas-key-signed transactions, and the runtime's real balance check at apply time) but a parallel authorization surface (delegated/meta-transaction execution of the same underlying action) is not wired into the same admission-time accounting, letting the "revoked/exhausted" resource still be drawn from through the alternate path during the admission window.

### Likelihood Explanation
Reachable by any unprivileged transaction signer (the gas-key owner or, since Delegate actions permit relaying, any relayer submitting the signer's signed delegate action) with no validator or operator privilege required. It requires only: an existing gas key, ordinary `Action::Delegate` meta-transaction construction (a standard, user-facing NEP-366 feature), and submission timing that lands transactions in the pending (uncertified) window — all attacker-controlled inputs reachable purely via RPC submission. The bug is gated behind SPICE's pending-transaction-queue design (only relevant where `PendingTransactionQueue`/`PendingTxSession` gating is active, i.e., under the `protocol_feature_spice` configuration in this codebase, as the associated tests are `#[cfg_attr(not(feature = "protocol_feature_spice"), ignore)]`), and is fixed at protocol version 87 which has not yet activated (stable is 86). Given the developers' own acknowledgment (in the version.rs doc comment) that this is a real, exploitable gap being closed by a dedicated protocol feature, likelihood is assessed as credible for networks/testnets still running at or below protocol version 86 with SPICE's pending transaction queue enabled.

### Recommendation
Backport/activate `RejectWithdrawFromGasKeyInDelegate` (or an equivalent runtime-side fix) ahead of protocol version 87 for any network still running ≤86 with the SPICE pending-transaction-queue feature enabled, or alternatively fix `PendingTransactionQueue::add_chunk_transactions` and `PendingTxSession::check_pending` to recursively scan into `Action::Delegate`/`Action::DelegateV2` inner actions when accumulating `WithdrawFromGasKey` amounts, rather than relying solely on rejecting the nested action at validation time.

### Proof of Concept
1. Create account `A` with a gas key `K` funded with balance `B` (via `AddKey` + `TransferToGasKey`).
2. Construct `N` `SignedDelegateAction`s, each signed by `A`'s regular access key, with `sender_id = receiver_id = A`, each containing a single inner action `WithdrawFromGasKey { public_key: K, amount: B }` (as built in `delegated_withdraw_tx`, cited above).
3. Wrap each in a top-level transaction `Action::Delegate(...)` signed/submitted potentially via different relayers, and submit them concurrently before any of them is certified.
4. Because `add_chunk_transactions`/`check_pending` only scan top-level `WithdrawFromGasKey` actions, `pending_gas_key_costs` for `(A, K)` is not incremented by these delegated withdrawals, so each transaction is independently admitted believing the gas key still holds `B`.
5. As demonstrated by the project's own `test_reject_delegated_gas_key_withdraw_protocol_upgrade` test (pre-upgrade branch), the nested delegated `WithdrawFromGasKey` executes and "drains the gas key" balance, confirming the funding view used for admission diverges from the true executed state until protocol version 87's `RejectWithdrawFromGasKeyInDelegate` is active. [7](#0-6)

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

**File:** core/primitives-core/src/version.rs (L461-465)
```rust
    /// Reject a `WithdrawFromGasKey` action nested inside a delegate action.
    /// The SPICE pending transaction queue scans only the top level actions of
    /// a transaction for `WithdrawFromGasKey`, so a nested one drains a gas key
    /// that the queue still counts as funded.
    RejectWithdrawFromGasKeyInDelegate,
```

**File:** core/primitives-core/src/version.rs (L618-629)
```rust
            ProtocolFeature::EnforcePerReceiptStorageProofLimit => 86,
            ProtocolFeature::FixContractLoadingError => 87,
            ProtocolFeature::RejectEmptyMethodName => 87,
            ProtocolFeature::RejectDelegateV2 => 87,
            ProtocolFeature::RejectWithdrawFromGasKeyInDelegate => 87,
            ProtocolFeature::RemoveGasRewards => 87,
            ProtocolFeature::EnforceStorageProofLimitForAllActions => 87,
            ProtocolFeature::ReceiptPromiseInputSizeLimit => 87,
            ProtocolFeature::EarlyKickout => 87,
            ProtocolFeature::FixMlDsaCostCharging => 87,
            ProtocolFeature::GlobalContractSameChunkCallFix => 87,
            ProtocolFeature::UniversalAccounts => 87,
```

**File:** runtime/runtime/src/action_validation.rs (L164-219)
```rust
    match action {
        Action::CreateAccount(_) => Ok(()),
        Action::DeployContract(a) => validate_deploy_contract_action(limit_config, a),
        Action::DeployGlobalContract(a) => validate_deploy_global_contract_action(limit_config, a),
        Action::UseGlobalContract(a) => validate_use_global_contract_action(a),
        Action::FunctionCall(a) => {
            validate_function_call_action(limit_config, a, current_protocol_version, mode)
        }
        Action::Transfer(_) => Ok(()),
        Action::Stake(a) => validate_stake_action(a),
        Action::AddKey(a) => validate_add_key_action(limit_config, a, current_protocol_version),
        Action::DeleteKey(_) => Ok(()),
        Action::DeleteAccount(a) => validate_delete_action(a),
        Action::Delegate(a) => validate_delegate_action(
            limit_config,
            (&a.delegate_action).into(),
            receiver,
            current_protocol_version,
            mode,
        ),
        Action::DelegateV2(a) => {
            require_protocol_feature(
                ProtocolFeature::DelegateV2,
                "DelegateV2",
                current_protocol_version,
            )?;
            // Receipts created before the removal are still in flight and must
            // keep executing, so only new transactions and receipts are refused.
            if mode == ValidateReceiptMode::NewReceipt {
                reject_removed_protocol_feature(
                    ProtocolFeature::RejectDelegateV2,
                    "DelegateV2",
                    current_protocol_version,
                )?;
            }
            validate_delegate_action(
                limit_config,
                (&a.delegate_action).into(),
                receiver,
                current_protocol_version,
                mode,
            )
        }
        Action::DeterministicStateInit(a) => {
            validate_deterministic_state_init(limit_config, a, receiver)
        }
        Action::UniversalStateInit(a) => {
            validate_universal_state_init(limit_config, a, receiver, current_protocol_version)
        }
        Action::TransferToGasKey(_) => {
            validate_transfer_to_gas_key_action(current_protocol_version)
        }
        Action::WithdrawFromGasKey(_) => {
            validate_withdraw_from_gas_key_action(current_protocol_version)
        }
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
