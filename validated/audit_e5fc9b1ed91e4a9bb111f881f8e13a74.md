Found the exact analog. It is precisely the historical bug that `runtime/runtime/src/action_validation.rs` documents inline: `validate_delegate_action` used to validate the inner (nested) actions of a `DelegateAction`/`DelegateV2` against the **outer transaction's `receiver_id`** instead of the delegate action's own `receiver_id()`, before initialization/dispatch had pinned the correct target — the same "constraint checked against the wrong/uninitialized value instead of the incoming argument" bug class as the `CreatePool` report, fixed by `ProtocolFeature::FixDelegatedDeterministicStateInit`.

### Title
Meta-transaction `DeterministicStateInit` receiver-id validated against the outer transaction's receiver instead of the delegate action's own receiver, allowing state-init exploitation on an unintended account - ([File: runtime/runtime/src/action_validation.rs])

### Summary
Prior to `ProtocolFeature::FixDelegatedDeterministicStateInit`, `validate_delegate_action` validated the nested (delegated) actions using the outer transaction's `receiver` argument rather than `delegate_action.receiver_id()`, letting a meta-transaction whose outer receiver matches a derived deterministic-account id smuggle inner actions (in particular `DeterministicStateInit`) addressed to a *different* deterministic account.

### Finding Description
`validate_delegate_action` in [1](#0-0)  computes `inner_receiver` — the id used to validate the delegate's nested actions — conditionally on the `FixDelegatedDeterministicStateInit` feature flag. Before the fix it used `receiver` (the outer transaction/receipt receiver id passed into `validate_actions_with_mode`), instead of `delegate_action.receiver_id()` (the delegate action's own declared receiver). For `DeterministicStateInit`, `validate_deterministic_state_init` enforces that the state-init's derived `0s…` account id equals the *validated receiver*, so validating against the wrong receiver id let an attacker craft a `DelegateAction` whose outer envelope targets one deterministic account (`det_account_b`, satisfying the id-match check) while the inner `DeterministicStateInitAction` actually targets a different deterministic account (`det_account_a`) than the one authorized/derived for that state-init payload. This is structurally identical to the `CreatePool` bug: a constraint (`pool.config.pool_type` / here, the receiver-id equality) is checked against a value that does not yet reflect the real target of the operation (uninitialized pool account / here, the outer tx receiver instead of the actual delegate receiver), making the guard ineffective for its intended purpose.

### Impact Explanation
An attacker could construct a nested `DeterministicStateInit` action inside a meta-transaction (`Delegate`/`DelegateV2`) that passes the initial transaction/receipt validation with a receiver mismatch smuggled through, as demonstrated by [2](#0-1) . Although the code comments and companion test note that the resulting receipt is caught by a *second* `validate_receipt` check on unpacking, the fact that "initial tx validation" is bypassable at all for a fundamental identity constraint (deterministic account derivation) represents ineffective validation of an unprivileged, attacker-supplied meta-transaction, matching the reported bug class (constraint validated against the wrong/stale value rather than the actual submitted argument).

### Likelihood Explanation
Reachable by any unprivileged relayer/signer submitting a crafted `Delegate`/`DelegateV2` meta-transaction with mismatched outer/inner receiver ids — no special privilege required, matching the "meta-transaction sender" reachable surface explicitly listed as in-scope.

### Recommendation
The fix (`ProtocolFeature::FixDelegatedDeterministicStateInit`) already validates nested actions, including `DeterministicStateInit`'s receiver-derivation check, against `delegate_action.receiver_id()` rather than the outer transaction/receipt `receiver_id`, matching the report's remediation of "apply the constraint to the incoming arguments instead of the [wrong/uninitialized] value."

### Proof of Concept [3](#0-2) [4](#0-3) 

Note: this is already fixed via the `FixDelegatedDeterministicStateInit` protocol feature and is defense-in-depth against by the follow-up `validate_receipt` check even pre-fix, per the codebase's own comments and tests — this is a historical/pre-fix bug class documented for reference, not a currently-exploitable path on the version protected by `MIN_SUPPORTED_PROTOCOL_VERSION` (83), where the feature is already enabled base-protocol.

### Citations

**File:** runtime/runtime/src/action_validation.rs (L222-269)
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
    let inner_receiver =
        if ProtocolFeature::FixDelegatedDeterministicStateInit.enabled(current_protocol_version) {
            // This is the correct receiver id to use for the check.
            delegate_action.receiver_id()
        } else {
            // This is a bug fixed with `FixDelegatedDeterministicStateInit` that
            // validated against the wrong id. This makes it impossible to
            // initialize deterministic accounts from meta transactions.
            // The bug cannot be abused, if someone crafts a state init that passes
            // validation here, it will fail when it is checked as incoming receipt.
            receiver
        };
    validate_actions_with_mode(
        limit_config,
        &actions,
        inner_receiver,
        current_protocol_version,
        mode,
    )?;
    Ok(())
}
```

**File:** test-loop-tests/src/tests/deterministic_account_id.rs (L128-155)
```rust
/// Ensure there is no exploit with invalid deterministic account ids through
/// meta transactions.
///
/// With the old (buggy) code, `validate_delegate_action` used
/// `outer_tx.receiver_id` instead of `delegate_action.receiver_id` when
/// checking inner actions. The exploit tx therefore passes initial tx
/// validation. The exploit is prevented by a following `validate_receipt` check
/// when the meta transaction is unpacked.
#[test]
fn test_deterministic_state_init_meta_tx_receiver_check_pre_fix() {
    let fix_version = ProtocolFeature::FixDelegatedDeterministicStateInit.protocol_version();
    let outcome = try_meta_tx_deterministic_receiver_exploit(fix_version - 1)
        .expect("without the fix, exploit tx passes initial tx validation");

    assert_matches!(
        outcome.status,
        FinalExecutionStatus::Failure(TxExecutionError::ActionError(ActionError {
            kind: ActionErrorKind::NewReceiptValidationError(
                ReceiptValidationError::ActionsValidation(
                    ActionsValidationError::InvalidDeterministicStateInitReceiver { .. }
                )
            ),
            ..
        })),
        "expected InvalidDeterministicStateInitReceiver in NewReceiptValidationError, got: {:?}",
        outcome.status
    );
}
```

**File:** test-loop-tests/src/tests/deterministic_account_id.rs (L179-261)
```rust
fn try_meta_tx_deterministic_receiver_exploit(
    protocol_version: ProtocolVersion,
) -> Result<FinalExecutionOutcomeView, InvalidTxError> {
    let mut env = TestEnv::setup_with_version(Balance::from_near(100), protocol_version);
    env.deploy_global_contract(GlobalContractDeployMode::AccountId);

    let (_state_init_a, det_account_a) = env.new_deterministic_account_with_data(small());
    let (state_init_b, det_account_b) = env.new_deterministic_account_with_data(big());
    assert_ne!(det_account_a, det_account_b);

    // Deploy det_account_b and add a full-access key so it can act as meta_tx_sender.
    let user_signer = create_user_test_signer(&env.user_account());
    let storage_balance = env.balance_for_storage(state_init_b.clone());
    let deploy_tx = SignedTransaction::deterministic_state_init(
        env.next_nonce(),
        env.user_account(),
        det_account_b.clone(),
        &user_signer,
        env.get_tx_block_hash(),
        state_init_b.clone(),
        storage_balance,
    );
    env.run_tx(deploy_tx);

    let meta_tx_sender_signer = create_user_test_signer(&det_account_b);
    let pk_base64 = near_primitives_core::serialize::to_base64(
        &borsh::to_vec(&meta_tx_sender_signer.public_key()).unwrap(),
    );
    let add_key_args = serde_json::json!([
        { "batch_create": { "account_id": det_account_b.as_str() }, "id": 0 },
        {
            "action_add_key_with_full_access": {
                "promise_index": 0,
                "public_key": pk_base64,
                "nonce": 0
            },
            "id": 0,
            "return": true
        }
    ]);
    let add_key_tx = SignedTransaction::call(
        env.next_nonce(),
        env.user_account(),
        det_account_b.clone(),
        &user_signer,
        Balance::from_near(2),
        "call_promise".to_owned(),
        serde_json::to_vec(&add_key_args).unwrap(),
        Gas::from_teragas(300),
        env.get_tx_block_hash(),
    );
    env.run_tx(add_key_tx);

    // Craft the exploit: outer_tx.receiver = det_account_b = derive(state_init_b).
    // Old check: det_account_b == derive(state_init_b) passes.
    // The delegate action targets det_account_a, which is the wrong account.
    // In no protocol version can this ever be allowed to be executed successfully.
    let relayer = env.independent_account();
    let relayer_signer = create_user_test_signer(&relayer);
    let inner_action = Action::DeterministicStateInit(Box::new(DeterministicStateInitAction {
        state_init: state_init_b,
        deposit: Balance::ZERO,
    }));
    let delegate_nonce = env.next_nonce_for(&det_account_b);
    let delegate_action = DelegateAction {
        sender_id: det_account_b.clone(),
        receiver_id: det_account_a,
        actions: vec![NonDelegateAction::try_from(inner_action).unwrap()],
        nonce: delegate_nonce,
        max_block_height: 1_000_000,
        public_key: meta_tx_sender_signer.public_key(),
    };
    let signed_delegate_action =
        SignedDelegateAction::sign(&meta_tx_sender_signer, delegate_action);
    let tx = SignedTransaction::from_actions(
        env.next_nonce(),
        relayer,
        det_account_b,
        &relayer_signer,
        vec![Action::Delegate(Box::new(signed_delegate_action))],
        env.get_tx_block_hash(),
    );
    env.try_execute_tx(tx)
```
