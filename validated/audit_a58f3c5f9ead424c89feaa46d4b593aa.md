This is exactly the analog bug class from the report: a check enforced on the "normal" path (top-level `WithdrawFromGasKey` transactions, tracked by `PendingTransactionQueue::add_chunk_transactions`) can be bypassed by nesting the same action inside a different code path (a `Delegate`/meta-transaction). The nearcore protocol explicitly documents and closes this hole via `ProtocolFeature::RejectWithdrawFromGasKeyInDelegate`, confirming it was a real, previously-exploitable analog of "party B bypasses the pause by using a different function."

### Title
Pre-`RejectWithdrawFromGasKeyInDelegate` protocol versions allow gas-key balance-accounting bypass via nested `WithdrawFromGasKey` inside a `Delegate` action - (File: `runtime/runtime/src/action_validation.rs`)

### Summary
The SPICE `PendingTransactionQueue` only scans **top-level** actions of a submitted transaction for `Action::WithdrawFromGasKey` in order to keep its gas-key balance commitments accurate while chunks are pending certification. Before protocol feature `RejectWithdrawFromGasKeyInDelegate`, a caller could nest the exact same `WithdrawFromGasKey` action inside an `Action::Delegate` (meta-transaction) payload, which is invisible to that top-level scan, letting the withdrawal execute while the pending-queue's gas-key balance bookkeeping stays stale/wrong.

### Finding Description
`PendingTransactionQueue::add_chunk_transactions` iterates `tx.actions()` directly and increments `gas_key_costs` only for `Action::WithdrawFromGasKey` found at the top level of the transaction: [1](#0-0) 
This mirrors the exact same class of bug in the same file where `RejectDelegateV2`'s doc comment explains the general problem: "the queue reads only the outer transaction's signer, public key and nonce index" and therefore misses effects nested one level down inside a `Delegate`/`DelegateV2` payload: [2](#0-1) 
Before the fix, `validate_delegate_action` in `action_validation.rs` performed no special check for a nested `WithdrawFromGasKey`, so an outer `Action::Delegate` whose inner action was `WithdrawFromGasKey` validated successfully and executed, draining the gas key balance without the pending-queue seeing the effect: [3](#0-2) 
This is functionally identical to the reported pattern: a restriction meant to gate a specific "action type" (top-level `WithdrawFromGasKey`, tracked/limited by the pending-transaction queue) is bypassed by reaching the same effect through an alternate action wrapper (`Delegate`) that the guard does not inspect.

### Impact Explanation
The regression test explicitly demonstrates the pre-fix behavior draining the gas key balance via the nested path, and the fix's own doc comment states the queue "still counts [the gas key] as funded" despite the drain: [4](#0-3) 
This causes stale/incorrect balance accounting in the pending-transaction queue used for chunk admission during uncertified windows — a gas key could be treated as still funded for further gas-key transactions when its true balance had already been reduced by an unaccounted delegated withdrawal, letting a signer over-spend from a gas key relative to what the admission layer believes is available (an admission-time invalid-state-acceptance / accounting-bypass condition, not merely a resource limit).

### Likelihood Explanation
The bypass required only a single ordinary transaction: any account holding a gas key could sign a `Delegate` action wrapping `WithdrawFromGasKey` and submit it through the normal RPC — no privileged, validator-only, or adversarial access was needed, making it trivially and cheaply reachable pre-fix.

### Recommendation
Confirm `ProtocolFeature::RejectWithdrawFromGasKeyInDelegate` is enabled at or below the network's live protocol version so `validate_delegate_action`'s check rejecting `WithdrawFromGasKey` nested in a `Delegate` for `ValidateReceiptMode::NewReceipt` is active in production: [5](#0-4) 
More generally, any future action-effect enumerated by a pending/queue-style admission tracker (`PendingTransactionQueue`, gas-key or balance commitments) must recursively inspect nested `Delegate`/`DelegateV2` payloads, not just top-level actions, to avoid re-introducing this class of bypass.

### Proof of Concept
The existing regression test reproduces the exploit end-to-end on the pre-fix protocol version: it adds a gas key, funds it, signs a `Delegate` action whose sole inner action is `WithdrawFromGasKey`, submits it via a relayer, and asserts the withdrawal succeeds and drains the gas key balance before the protocol upgrade activates the fix: [6](#0-5)

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
