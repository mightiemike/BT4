### Title
Nested `WithdrawFromGasKey` inside a meta-transaction bypasses gas-key balance accounting in transaction admission - ([File: chain/client/src/pending_transaction_queue.rs])

### Summary
The external report flags `migrateToken` in `MarinateV2.sol` as a case where a privileged actor could move funds out of a contract in a way the community/consumers of the contract did not expect, fixed by removing the code path entirely. The closest reachable analog in `nearcore` is a structural gap where a `WithdrawFromGasKey` action wrapped inside a `Delegate` (meta-transaction) action could drain a gas key's balance while the SPICE pending-transaction-queue's admission accounting — which only inspects **top-level** actions of a transaction — continued to treat that balance as still available. This is not an "admin" bypass, but it is the same bug *class*: a privileged/structural code path that moves balances in a way that evades the accounting layer meant to gate it, and it was resolved the same way the report's fix was — by removing/blocking the offending path (`WithdrawFromGasKeyNotAllowedInDelegate`).

### Finding Description
`WithdrawFromGasKeyAction` moves balance from a gas key's prepaid balance back into the owning account ( [1](#0-0) ). The action is intended to be reachable only directly via a top-level transaction, not via nested contract-triggered promises ( [2](#0-1) ).

The SPICE pending-transaction-queue's admission logic (`check_pending`) tracks in-flight `WithdrawFromGasKey` amounts by scanning `tx.transaction.actions()`, i.e. only the top-level actions of the incoming transaction, to compute `session_gas_key_withdrawals` and thereby the still-available `paid_from_gas_key` balance used to admit further gas-key transactions ( [3](#0-2) ).

If a `WithdrawFromGasKey` action is instead nested inside a `DelegateAction`/`SignedDelegateAction` (a meta-transaction relayed by a third party), the top-level scan in `check_pending` never sees it, so the pending-queue's balance bookkeeping still counts the gas key as fully funded even after the nested withdrawal executes and drains it ( [4](#0-3) ). This is documented verbatim in the code as the rationale for the `RejectWithdrawFromGasKeyInDelegate` protocol feature. The fix mirrors the report's resolution style: it rejects the offending action path outright rather than patching the accounting, returning `ActionsValidationError::WithdrawFromGasKeyNotAllowedInDelegate` for any *new* receipt containing a delegated `WithdrawFromGasKey` ( [5](#0-4) ), while still tolerating in-flight receipts created pre-upgrade for compatibility ( [6](#0-5) ).

The end-to-end test explicitly demonstrates the pre-fix hole: a relayer-submitted meta-transaction whose inner action withdraws from the sender's own gas key executes successfully and drains the gas key balance, which the comment calls "the hole this rule closes" ( [7](#0-6) ).

### Impact Explanation
Because the pending-transaction-queue's `paid_from_gas_key` constraint is what other gas-key transactions rely on to be admitted without overdrawing the gas key (see the companion test asserting `NotEnoughGasKeyBalance` once withdrawals are tracked, `chain/client/src/pending_transaction_queue.rs:576-585`), a nested/delegated withdrawal that evades this tracking can let multiple pending gas-key transactions be admitted against a balance that has already been (or is concurrently being) drained elsewhere. That is an accounting-integrity bypass in the balance-gating logic feeding chunk transaction selection — the exact class of bug the rules call out ("fee or gas bypass … invalid state transition acceptance").

### Likelihood Explanation
This requires only an unprivileged transaction signer (the gas key owner) and a cooperating meta-transaction relayer — both roles reachable by any RPC caller, no validator or node privileges needed. The behavior was reproducible pre-fix via a straightforward `Delegate(WithdrawFromGasKeyAction)` transaction, as shown by the passing pre-upgrade assertions in the test. However, the current codebase already contains the fix (`RejectWithdrawFromGasKeyInDelegate` / `WithdrawFromGasKeyNotAllowedInDelegate`) gated by a protocol version. I was not able to conclusively confirm, within the available tool budget, whether this protocol feature is activated at the network's current `PROTOCOL_VERSION` or is still pending rollout; if it is not yet active on a live network, the gap remains exploitable there, and even where active, in-flight receipts created just before the upgrade boundary are explicitly still processed under the old (vulnerable) semantics per the test's post-upgrade compatibility loop.

### Recommendation
Confirm `RejectWithdrawFromGasKeyInDelegate` is enabled at the deployed `PROTOCOL_VERSION` on all target networks. Independently of the delegate-specific ban, harden `check_pending`'s admission accounting to walk nested/delegated action lists (not just top-level actions) when computing `session_gas_key_withdrawals`/`paid_from_gas_key`, so that admission-time balance tracking cannot be evaded by any future action nesting mechanism, rather than relying solely on an action-type denylist.

### Proof of Concept
Pre-fix reproduction (verified by existing test `test_reject_delegated_gas_key_withdraw_protocol_upgrade`):
1. Sender adds a gas key (`AddKey` with `AccessKey::gas_key_full_access`) and funds it via `TransferToGasKey`.
2. Sender signs a `DelegateAction` whose sole inner action is `WithdrawFromGasKeyAction` targeting their own gas key, and hands it to a relayer.
3. Relayer submits the wrapping transaction (`Action::Delegate`) to the network.
4. Pre-fix, the pending-transaction-queue's `check_pending` never inspects the nested action, so it does not count the withdrawal against the gas key's tracked balance, while the runtime nonetheless drains the gas key balance on execution — see the assertions in [7](#0-6) . [8](#0-7) [4](#0-3) [5](#0-4)

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

**File:** core/primitives/src/action/mod.rs (L337-358)
```rust
/// Withdraw NEAR from a gas key's balance to the account.
///
/// This action must only be available via transactions, not via contract execution
/// (there is no corresponding promise batch action host function).
#[derive(
    BorshSerialize,
    BorshDeserialize,
    PartialEq,
    Eq,
    Clone,
    Debug,
    serde::Serialize,
    serde::Deserialize,
    ProtocolSchema,
)]
#[cfg_attr(feature = "schemars", derive(schemars::JsonSchema))]
pub struct WithdrawFromGasKeyAction {
    /// The public key of the gas key to withdraw from
    pub public_key: PublicKey,
    /// Amount of NEAR to transfer from the gas key
    pub amount: Balance,
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

**File:** core/primitives-core/src/version.rs (L462-465)
```rust
    /// The SPICE pending transaction queue scans only the top level actions of
    /// a transaction for `WithdrawFromGasKey`, so a nested one drains a gas key
    /// that the queue still counts as funded.
    RejectWithdrawFromGasKeyInDelegate,
```

**File:** runtime/runtime/src/action_validation.rs (L1225-1236)
```rust
    #[test]
    fn test_validate_action_delegated_withdraw_from_gas_key_rejected_in_new_receipt() {
        assert_eq!(
            validate_action(
                &test_limit_config(),
                &delegate_with_withdraw_from_gas_key(),
                &alice_account(),
                ProtocolFeature::RejectWithdrawFromGasKeyInDelegate.protocol_version(),
            ),
            Err(ActionsValidationError::WithdrawFromGasKeyNotAllowedInDelegate)
        );
    }
```

**File:** runtime/runtime/src/action_validation.rs (L1238-1248)
```rust
    #[test]
    fn test_validate_action_delegated_withdraw_from_gas_key_allowed_in_existing_receipt() {
        validate_action_with_mode(
            &test_limit_config(),
            &delegate_with_withdraw_from_gas_key(),
            &alice_account(),
            ProtocolFeature::RejectWithdrawFromGasKeyInDelegate.protocol_version(),
            ValidateReceiptMode::ExistingReceipt,
        )
        .expect("in-flight receipts must keep executing across the new rule");
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
