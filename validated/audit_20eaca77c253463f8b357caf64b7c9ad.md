### Title
Nested `WithdrawFromGasKey` inside a `Delegate`/`DelegateV2` action bypasses the Pending Transaction Queue's gas-key balance accounting - ([File: chain/client/src/pending_transaction_queue.rs])

### Summary
This is a direct analog of the GoGoPool `whenNotPaused` bypass: a balance/accounting guard is enforced on one call path (a top-level `WithdrawFromGasKey` action) but is missing on an alternate path that reaches the same state-mutating effect (a `WithdrawFromGasKey` nested inside a `Delegate`/`DelegateV2` action), and the protocol feature meant to close that alternate path is not yet active on the current stable protocol version.

### Finding Description
`PendingTransactionQueue::add_chunk_transactions` builds the pending accounting used to admit further transactions in the same uncertified window. It only scans an included transaction's **top-level** actions for `Action::WithdrawFromGasKey` to update `pending_gas_key_costs`: [1](#0-0) 

The same top-level-only scan is repeated in the chunk-production session tracker `PendingTxSession::check_pending`, which uses `session_gas_key_withdrawals` to build `paid_from_gas_key` constraints used to reject transactions with `NotEnoughGasKeyBalance`: [2](#0-1) 

If a `WithdrawFromGasKey` action is instead nested inside a `Delegate`/`DelegateV2` action's inner action list, neither scan sees it, because both only iterate `tx.actions()` (the outer transaction's top-level actions), not the actions carried inside a delegate payload. The developers' own code comments confirm this is a known, exploitable accounting gap, not a hypothetical one: [3](#0-2) 

The runtime added a guard for this — `ProtocolFeature::RejectWithdrawFromGasKeyInDelegate` — which rejects any new receipt whose delegate action contains a nested `WithdrawFromGasKey`: [4](#0-3) 

However, per the protocol version activation table, `DelegateV2` (which enables meta-transactions carrying an inner gas-key-nonce-advancing action) activates at protocol version **85**, while `RejectWithdrawFromGasKeyInDelegate` and `RejectDelegateV2` only activate at protocol version **87**: [5](#0-4) 

This mirrors the reported pattern exactly: `restakeGGP`/`claimAndRestake` bypassed `whenNotPaused` because the pause guard was added to one function but not the alternate path that reached the same effect; here, the `WithdrawFromGasKey`-balance-accounting guard exists in the pending-transaction-queue’s top-level scan but is absent for the semantically identical action reached via nested `Delegate`/`DelegateV2`, and the compensating validation-layer guard (`RejectWithdrawFromGasKeyInDelegate`) is not active until a later protocol version than the one (`DelegateV2`, v85) that opened the alternate path.

### Impact Explanation
An attacker (or a relayer acting on behalf of a gas-key holder) can submit multiple transactions within the same uncertified window, each wrapping a `WithdrawFromGasKey` action inside a `Delegate`/`DelegateV2` action. Because `add_chunk_transactions` and `check_pending` never add these nested withdrawals to `pending_gas_key_costs` / `session_gas_key_withdrawals`, the pending-transaction-queue's admission check (`NotEnoughGasKeyBalance`) never accounts for the cumulative effect of these pending withdrawals. This can allow the same gas key balance to be committed to multiple uncertified withdrawals simultaneously, undermining the very invariant the queue exists to protect (per-account/per-gas-key balance commitments across uncertified chunks), which is a form of unauthorized/duplicated value movement against gas key balances.

### Likelihood Explanation
Reachable by any single account holding (or targeted by) a gas key and able to submit ordinary signed transactions containing a `Delegate`/`DelegateV2` action with a nested `WithdrawFromGasKey` — no validator or operator privilege required. The gap is only closed by a not-yet-enabled protocol feature (`RejectWithdrawFromGasKeyInDelegate`, v87) while the feature that opens the path (`DelegateV2`, v85) is already active on the deployed protocol version, and the pending-transaction-queue's scanning logic itself has no depth-aware nested-action traversal regardless of protocol version.

### Recommendation
- Make the pending-transaction-queue's `WithdrawFromGasKey` scan recurse into `Delegate`/`DelegateV2` inner actions (in both `add_chunk_transactions` and `PendingTxSession::check_pending`), so nested withdrawals are always tracked, independent of protocol-version gating.
- Alternatively/additionally, advance `RejectWithdrawFromGasKeyInDelegate` (and `RejectDelegateV2`) to activate at or before the same version that enables `DelegateV2`, so there is never a window where the alternate path is open without the compensating guard.

### Proof of Concept
1. Create an account with a gas key funded for exactly N withdrawals.
2. Submit N+1 transactions in the same uncertified window, each containing a `Delegate`/`DelegateV2` action whose inner action list is `[WithdrawFromGasKey { public_key, amount }]` targeting the funded gas key, on the currently-active stable protocol version (where `DelegateV2` is enabled but `RejectWithdrawFromGasKeyInDelegate` is not).
3. Observe that `PendingTransactionQueue::add_chunk_transactions`/`PendingTxSession::check_pending` do not increment `pending_gas_key_costs`/`session_gas_key_withdrawals` for these nested withdrawals (contrast with the existing test `test_ptq_withdraw_from_gas_key`, which only exercises top-level `WithdrawFromGasKey` actions and confirms the tracked-case behavior): [6](#0-5) 
4. Because the (N+1)th transaction's nested withdrawal was never counted, it is admitted by the pending-transaction-queue despite the gas key balance already being logically committed, breaking the balance-commitment invariant the queue is designed to enforce.

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

**File:** core/primitives-core/src/version.rs (L461-464)
```rust
    /// Reject a `WithdrawFromGasKey` action nested inside a delegate action.
    /// The SPICE pending transaction queue scans only the top level actions of
    /// a transaction for `WithdrawFromGasKey`, so a nested one drains a gas key
    /// that the queue still counts as funded.
```

**File:** core/primitives-core/src/version.rs (L600-622)
```rust
            ProtocolFeature::_DeprecatedWasmtime => 84,
            ProtocolFeature::FixDelegateActionDepositWithFunctionCallError
            | ProtocolFeature::FixDeleteAccountGlobalContractStorageUsage
            | ProtocolFeature::FixDelegatedDeterministicStateInit
            | ProtocolFeature::GasKeys
            | ProtocolFeature::ContinuousEpochSync
            | ProtocolFeature::DynamicResharding
            | ProtocolFeature::StickyReshardingValidatorAssignment
            | ProtocolFeature::StrictNonce
            | ProtocolFeature::PostQuantumSignatures
            | ProtocolFeature::UniqueChunkTransactions
            | ProtocolFeature::ValidateBlockOrdinalAndEpochSyncDataHash
            | ProtocolFeature::YieldWithId
            | ProtocolFeature::ExecutionMetadataV4
            | ProtocolFeature::SignedContractCodeResponse
            | ProtocolFeature::ClampOutgoingGasAdmission
            | ProtocolFeature::AccountCostIncrease
            | ProtocolFeature::DelegateV2 => 85,
            ProtocolFeature::EnforcePerReceiptStorageProofLimit => 86,
            ProtocolFeature::FixContractLoadingError => 87,
            ProtocolFeature::RejectEmptyMethodName => 87,
            ProtocolFeature::RejectDelegateV2 => 87,
            ProtocolFeature::RejectWithdrawFromGasKeyInDelegate => 87,
```

**File:** runtime/runtime/src/action_validation.rs (L243-248)
```rust
    if mode == ValidateReceiptMode::NewReceipt
        && ProtocolFeature::RejectWithdrawFromGasKeyInDelegate.enabled(current_protocol_version)
        && actions.iter().any(|action| matches!(action, Action::WithdrawFromGasKey(_)))
    {
        return Err(ActionsValidationError::WithdrawFromGasKeyNotAllowedInDelegate);
    }
```

**File:** test-loop-tests/src/tests/pending_transaction_queue.rs (L499-556)
```rust
#[test]
#[cfg_attr(not(feature = "protocol_feature_spice"), ignore)]
fn test_ptq_withdraw_from_gas_key() {
    init_test_logger();

    let account = create_account_id("withdraw_account");
    let receiver = create_account_id("receiver");
    let fund_amount = Balance::from_millinear(1);
    let half = fund_amount.checked_div(2).unwrap();
    let setup = setup_gas_key_spice_env(&account, &receiver, 1, fund_amount);
    let mut env = setup.env;

    // Submit two access key txs, each withdrawing half the gas key balance.
    let block_hash = env.validator().head().last_block_hash;
    let mut next_nonce = setup.next_access_key_nonce;
    let withdraw_tx1 = SignedTransaction::from_actions(
        next_nonce,
        account.clone(),
        account.clone(),
        &create_user_test_signer(&account),
        vec![Action::WithdrawFromGasKey(Box::new(WithdrawFromGasKeyAction {
            public_key: setup.gas_key_signer.public_key(),
            amount: half,
        }))],
        block_hash,
    );
    next_nonce += 1;
    let withdraw_tx2 = SignedTransaction::from_actions(
        next_nonce,
        account.clone(),
        account.clone(),
        &create_user_test_signer(&account),
        vec![Action::WithdrawFromGasKey(Box::new(WithdrawFromGasKeyAction {
            public_key: setup.gas_key_signer.public_key(),
            amount: half,
        }))],
        block_hash,
    );
    let hashes = [withdraw_tx1.get_hash(), withdraw_tx2.get_hash()];
    env.validator().submit_tx(withdraw_tx1);
    env.validator().submit_tx(withdraw_tx2);
    env.validator_runner().run_until_included(&hashes);

    // Submit a gas key tx via execute_tx. The pending transaction queue
    // should accumulate both withdrawals against the gas key balance,
    // so the RPC handler should reject it with NotEnoughGasKeyBalance.
    let block_hash = env.validator().head().last_block_hash;
    let gas_key_tx = SignedTransaction::from_actions_v1(
        TransactionNonce::from_nonce_and_index(setup.gas_key_nonces[0] + 1, 0),
        account,
        receiver,
        &setup.gas_key_signer,
        vec![Action::Transfer(TransferAction { deposit: Balance::from_millinear(0) })],
        block_hash,
    );
    let result = env.validator_runner().execute_tx(gas_key_tx, Duration::seconds(5));
    assert!(matches!(result, Err(InvalidTxError::NotEnoughGasKeyBalance { .. })),);
}
```
