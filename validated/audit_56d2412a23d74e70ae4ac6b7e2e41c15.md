### Title
Pending Transaction Queue undercounts nested `WithdrawFromGasKey` inside a delegate action, bypassing the gas-key balance cap check - (File: `chain/client/src/pending_transaction_queue.rs`)

### Summary
The reported pattern — a balance/cap-tracking guard (`checkPoolCap`) enforced on one deposit-affecting entry path but silently skipped on another path that mutates the same tracked balance (`swapIntoFromRouter`) — has a direct structural analog in nearcore's SPICE `PendingTransactionQueue`. The queue enforces a gas-key balance cap (`pending_gas_key_costs` / `NotEnoughGasKeyBalance`) by scanning transaction actions for `WithdrawFromGasKey`, but it only scans **top-level** actions of a transaction, not actions nested inside a `DelegateAction` (meta-transaction). This is explicitly documented as a known hole and was closed by a protocol-version-gated fix (`RejectWithdrawFromGasKeyInDelegate`), meaning the vulnerable behavior is real pre-upgrade and was significant enough to require a dedicated protocol feature to reject it.

### Finding Description
`PendingTransactionQueue::add_chunk_transactions` and `PendingTxSession::check_pending` both scan `tx.actions()` / `tx.transaction.actions()` directly for `Action::WithdrawFromGasKey` to accumulate `pending_gas_key_costs`, which is the analog of the pool's tracked "asset balance": [1](#0-0) [2](#0-1) 

This scan only inspects the outer transaction's action list. A `WithdrawFromGasKey` action can also reach execution nested inside a `DelegateAction` (a meta-transaction relayed by another account), which is a completely separate code path from a plain top-level action — analogous to `swapIntoFromRouter` mutating the pool's tracked balance through a path other than `deposit`. The nearcore codebase explicitly documents this gap: [3](#0-2) 

The runtime's actual balance check for gas-key transactions (`verify_and_charge_gas_key_tx_ephemeral`) relies on `pending.paid_from_gas_key` supplied by the PTQ to decide whether a gas-key transaction can be admitted without exceeding the real on-chain balance: [4](#0-3) 

Because the PTQ's cap-tracking scan misses `WithdrawFromGasKey` nested in a `Delegate` action, `pending_gas_key_costs` for that gas key is understated relative to the gas key's true (pending) drain, so the RPC/chunk-production admission check (`NotEnoughGasKeyBalance`) can pass for transactions that should have been rejected — the reverse-but-symmetric failure mode of the reported bug, where a cap tracked through one path is bypassed via another mutating path.

The fix, `ProtocolFeature::RejectWithdrawFromGasKeyInDelegate`, closes this by rejecting `WithdrawFromGasKey` nested inside a delegate action outright at action validation, confirming the underlying root cause (the PTQ's incomplete scan) was a real, exploitable accounting gap prior to the protocol upgrade that introduces the rejection: [5](#0-4) 

### Impact Explanation
When this hole is open (protocol versions before `RejectWithdrawFromGasKeyInDelegate` activates), a delegated (meta-transaction) `WithdrawFromGasKey` drains a gas key's balance without the pending transaction queue's admission logic accounting for it. This allows the gas key's tracked "funded" balance used for RPC/chunk-production admission to diverge from the true balance, letting additional gas-key transactions be admitted that the runtime may later have to reject for insufficient balance, or letting a withdrawal double-count against the account in ways the PTQ's optimistic admission model does not anticipate. This is a balance-cap bypass causing incorrect transaction admission decisions (impact: Medium, consistent with the report's classification), not itself a state-root divergence since the runtime's own ephemeral balance check at execution time is authoritative — but it can cause legitimate gas-key transactions to be wrongly admitted/rejected and gas key balance accounting inconsistencies during the pending (uncertified) window, mirroring the reported pool-cap DoS/inconsistency pattern.

### Likelihood Explanation
Likelihood is Medium: this requires SPICE's pending-transaction-queue path (`protocol_feature_spice`) and a `DelegateAction` (any user can be a "sender" of a meta-transaction relayed by any relayer, and no special privilege is required to construct a `WithdrawFromGasKeyAction` nested in a `Delegate` action) prior to the protocol version that activates `RejectWithdrawFromGasKeyInDelegate`. It is trivially reachable by any transaction signer using ordinary RPC submission — no validator or node privilege needed — which matches an unprivileged, transaction-triggered analog. The nearcore team apparently discovered and gated this via a dedicated protocol feature specifically because it was reachable in production-shaped code.

### Recommendation
Ensure `RejectWithdrawFromGasKeyInDelegate` is active on all relevant networks (mainnet/testnet) before the SPICE pending-transaction-queue feature ships, and audit `PendingTransactionQueue::add_chunk_transactions` / `PendingTxSession::check_pending` to recursively scan `DelegateAction` inner actions for `WithdrawFromGasKey` (or any other gas-key/balance-mutating action) rather than only rejecting the case rather than tracking it, to prevent equivalent gaps for future gas-key-affecting nested actions.

### Proof of Concept
1. Run a network at a protocol version below `RejectWithdrawFromGasKeyInDelegate.protocol_version()` with SPICE's pending transaction queue enabled.
2. Create a gas key for account `alice` and fund it via `TransferToGasKey`.
3. As `alice`, sign a `DelegateAction` whose inner action is `WithdrawFromGasKeyAction { public_key: <gas_key>, amount: <drain_amount> }`, and have any `relayer` submit it as `Action::Delegate`.
4. Observe (as demonstrated in `test_reject_delegated_gas_key_withdraw_protocol_upgrade`) that pre-upgrade the delegated withdrawal executes and drains the gas key balance, while the `PendingTransactionQueue`'s `pending_gas_key_costs` for that gas key never accounted for it (because it only scans top-level actions), so subsequent gas-key transaction admission decisions made by the RPC/chunk producer are based on a stale/incorrect gas-key balance commitment. [6](#0-5)

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
