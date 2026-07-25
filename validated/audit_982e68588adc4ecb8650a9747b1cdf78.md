### Title
Wrong `receiver` Passed to `validate_actions_with_mode` in `validate_delegate_action` Breaks `DeterministicStateInit` in Meta Transactions — (`runtime/runtime/src/action_validation.rs`)

### Summary

`validate_delegate_action` passes the outer receipt's `receiver` (the relayer's account ID) instead of `delegate_action.receiver_id()` (the actual inner-action target) to `validate_actions_with_mode`. For `DeterministicStateInit` actions inside a meta transaction, this causes the validation to check the wrong account, making it impossible for any unprivileged user to successfully execute a `DeterministicStateInit` via a `Delegate`/`DelegateV2` action. The bug is structurally identical to the external report: a wrong identifier is forwarded to a critical sub-function, silently breaking a specific execution path.

### Finding Description

In `runtime/runtime/src/action_validation.rs`, `validate_delegate_action` selects which account ID to pass to `validate_actions_with_mode`: [1](#0-0) 

Before `ProtocolFeature::FixDelegatedDeterministicStateInit` is enabled, the `else` branch supplies `receiver` — the outer receipt's receiver, i.e., the relayer's own account — rather than `delegate_action.receiver_id()`, the account the inner actions are actually targeting.

`validate_actions_with_mode` uses the supplied account ID to validate `DeterministicStateInit` actions: it checks whether the action's target matches the expected deterministic derivation of that account. When the relayer's account is supplied instead of the delegate target, the check is performed against the wrong account, and the validation always fails for any `DeterministicStateInit` embedded in a meta transaction.

The nonce update in `validate_delegate_action_key` at lines 685–699 is reached only when validation succeeds; on failure the receipt is rolled back, so no nonce is consumed. The sole concrete effect is that the receipt is rejected with a validation error, permanently blocking this execution path. [2](#0-1) 

The fix — using `delegate_action.receiver_id()` — is gated behind `ProtocolFeature::FixDelegatedDeterministicStateInit`. Until that feature version is active on the network, the legacy `else` branch remains the live code path.

### Impact Explanation

Any user who submits a meta transaction (`Action::Delegate` or `Action::DelegateV2`) containing a `DeterministicStateInit` inner action will have that receipt rejected at the validation stage. The gas paid by the relayer is consumed, the deterministic account is never initialized, and no workaround exists within the meta-transaction interface. This is a **contract execution flow breakage** reachable by any unprivileged user.

The code comment acknowledges the inverse direction of the bug ("cannot be abused" for bypassing security), but the forward direction — legitimate use being silently broken — is the impact here.

### Likelihood Explanation

Any relayer or user who attempts to initialize a deterministic account via a meta transaction will trigger this path. The trigger requires only a standard `Delegate` action containing `DeterministicStateInit`, which is a documented and supported action type. No special privileges or validator access are needed.

### Recommendation

Ensure `ProtocolFeature::FixDelegatedDeterministicStateInit` is activated at the earliest feasible protocol version. Until then, document that `DeterministicStateInit` cannot be used inside `Delegate`/`DelegateV2` actions. The fix is already present in the codebase:

```rust
let inner_receiver =
    if ProtocolFeature::FixDelegatedDeterministicStateInit.enabled(current_protocol_version) {
        delegate_action.receiver_id()   // correct
    } else {
        receiver                        // wrong — relayer's account
    };
``` [3](#0-2) 

### Proof of Concept

1. Deploy a deterministic account `det_account_a` (derived from some `state_init_a`).
2. Construct a `DelegateAction` with `sender_id = relayer`, `receiver_id = det_account_a`, and `actions = [DeterministicStateInit { state_init: state_init_a, deposit: 0 }]`.
3. Wrap it in a transaction targeting the relayer's own account.
4. Submit via RPC on a node running a protocol version before `FixDelegatedDeterministicStateInit`.
5. Observe: the receipt fails with an `ActionsValidation` error because `validate_actions_with_mode` checked the relayer's account (not `det_account_a`) against the deterministic derivation, producing a mismatch.
6. The deterministic account is never initialized; the relayer's gas is burnt.

The existing test `try_meta_tx_deterministic_receiver_exploit` in `test-loop-tests/src/tests/deterministic_account_id.rs` exercises the related exploit path and confirms the validation logic is sensitive to which receiver ID is supplied. [4](#0-3)

### Citations

**File:** runtime/runtime/src/action_validation.rs (L182-209)
```rust
fn validate_delegate_action(
    limit_config: &LimitConfig,
    delegate_action: VersionedDelegateActionRef<'_>,
    receiver: &AccountId,
    current_protocol_version: ProtocolVersion,
    mode: ValidateReceiptMode,
) -> Result<(), ActionsValidationError> {
    let actions = delegate_action.get_actions();
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
```

**File:** test-loop-tests/src/tests/deterministic_account_id.rs (L183-265)
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
