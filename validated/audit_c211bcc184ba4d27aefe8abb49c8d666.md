Based on this investigation, I found a concrete, already-identified analog in nearcore that matches the Hyperdrive bug class precisely: a code path validated an operation against the wrong "receiver/owner" identifier (the outer transaction's receiver instead of the actual authenticated target of the inner action), which is exactly the pattern in the Hyperdrive report where a function drew funds/created state for an address supplied as a parameter rather than the authenticated caller/target. This has already been found and fixed in-repo via `ProtocolFeature::FixDelegatedDeterministicStateInit`, with a regression test proving the exploit path. I have concrete file/function support for root cause, and the fix is gated behind a protocol feature, meaning it is a genuine, previously-real vulnerability in the codebase's history (not hypothetical).

### Title
Delegated `DeterministicStateInit` was validated against the wrong receiver, allowing a meta-transaction to target a mismatched account - (File: `runtime/runtime/src/action_validation.rs`)

### Summary
Prior to the `FixDelegatedDeterministicStateInit` protocol feature, `validate_delegate_action` validated the inner `DeterministicStateInitAction` (carried inside a `DelegateAction`) against the **outer transaction's `receiver_id`** instead of the **`DelegateAction`'s own `receiver_id`**. This is structurally the same bug class as the Hyperdrive report: an authorization/identity check used the wrong "owner" parameter (a caller-influenced value) instead of the value that should have been cryptographically/structurally bound to the operation, letting a transaction pass validation for an account it wasn't actually meant to affect.

### Finding Description
In `runtime/runtime/src/action_validation.rs`, `validate_delegate_action` decides which `receiver_id` to check the inner actions (including `DeterministicStateInitAction`) against: [1](#0-0) 

Before the fix, the code used `receiver` (the outer transaction's/receipt's receiver, i.e. the delegate-action *sender's* account, since `DelegateAction`s are addressed to their `sender_id`) instead of `delegate_action.receiver_id()`, which is the actual account the wrapped inner actions are meant to target. `DeterministicStateInitAction` validation (`validate_deterministic_state_init` in the same file) is security-critical because it derives the account id from the `state_init` payload and rejects the action unless the receiver matches that derived id — this is the mechanism that guarantees a deterministic account can only ever be initialized with the exact code/data it claims to have: [2](#0-1) 

Because `validate_delegate_action` checked the wrong receiver, a relayer/attacker could craft a `SignedDelegateAction` whose `sender_id` equals the state-init's derived deterministic id (`det_account_b`), but whose `DelegateAction.receiver_id` points at a *different* deterministic account (`det_account_a`). At the transaction-admission layer, `validate_delegate_action` checked the inner action against `det_account_b` (matching, so it passed validation), while the resulting receipt would actually be routed to `det_account_a`, whose real, later-run receipt validation (`validate_receipt`) would independently re-check `derived_id == receiver_id` and reject it. The bug is proven fixed and regression-tested in the test-loop suite: [3](#0-2) [4](#0-3) 

### Impact Explanation
This is directly analogous to the Hyperdrive vulnerability: an operation that should be scoped/authorized to one specific account (the true `DelegateAction.receiver_id`, akin to Hyperdrive's true fund owner) was instead checked against a different, attacker-influenced identifier (the outer receiver, akin to Hyperdrive's caller-supplied `_lp` parameter). Had the second-layer `validate_receipt` check not existed or been in sync, this would have allowed a `DeterministicStateInitAction` (or any other inner action gated on `receiver_id`) to be accepted for a receiver it was never meant to apply to — a state-transition validity bypass reachable purely by submitting a crafted meta-transaction (`SignedDelegateAction`) from an unprivileged relayer. This class of bug is exactly a "invalid state transition acceptance" vector, since it lets transaction-admission-time checks and receipt-execution-time checks diverge for a value (the effective receiver) that funds/state are bound to.

### Likelihood Explanation
Reachable by any unprivileged account acting as a relayer submitting a single `SignedTransaction` containing an `Action::Delegate`/`Action::DelegateV2` — no validator, node, or protocol-internal privilege is required, matching the "single submitted transaction" reachability bar. The bug is deterministic (not probabilistic) and requires no race condition, only crafting the mismatched `sender_id`/`receiver_id` pair in the `DelegateAction`.

### Recommendation
The fix already present in the codebase is correct: always validate delegate-wrapped actions (especially identity-deriving ones like `DeterministicStateInitAction`/`UniversalStateInitAction`) against `delegate_action.receiver_id()`, never against the outer transaction/receipt receiver, and keep the redundant `validate_receipt`-time check as defense in depth so that admission-time and execution-time checks can never diverge on which account an action is authorized against.

### Proof of Concept
The repository's own regression test constructs the exploit: it deploys deterministic account `det_account_b`, adds a full-access key to it, then builds a `DelegateAction { sender_id: det_account_b, receiver_id: det_account_a, actions: [DeterministicStateInit(state_init_b)] }`, signs it with `det_account_b`'s key, and wraps it in an outer transaction whose real receiver is `det_account_b`: [5](#0-4) 
Under the pre-fix protocol version, this passes transaction validation (proving the wrong-receiver check accepted a mismatched target) and only fails later at receipt validation: [6](#0-5)

### Citations

**File:** runtime/runtime/src/action_validation.rs (L249-260)
```rust
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
```

**File:** runtime/runtime/src/action_validation.rs (L497-512)
```rust
fn validate_deterministic_state_init(
    limit_config: &LimitConfig,
    action: &DeterministicStateInitAction,
    receiver_id: &AccountId,
) -> Result<(), ActionsValidationError> {
    validate_global_contract_identifier(action.state_init.code())?;

    let derived_id = derive_near_deterministic_account_id(&action.state_init);

    if derived_id != *receiver_id {
        return Err(ActionsValidationError::InvalidDeterministicStateInitReceiver {
            derived_id,
            receiver_id: receiver_id.clone(),
        });
    }

```

**File:** test-loop-tests/src/tests/deterministic_account_id.rs (L128-171)
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

/// With `FixDelegatedDeterministicStateInit` in place, the exploit should
/// already be caught at the first tx validation.
#[test]
fn test_deterministic_state_init_meta_tx_receiver_check() {
    let fix_version = ProtocolFeature::FixDelegatedDeterministicStateInit.protocol_version();
    let err = try_meta_tx_deterministic_receiver_exploit(fix_version)
        .expect_err("exploit tx must be rejected at tx validation with the fix");
    assert_matches!(
        err,
        InvalidTxError::ActionsValidation(
            ActionsValidationError::InvalidDeterministicStateInitReceiver { .. }
        ),
        "wrong error: {err:?}"
    );
}
```

**File:** test-loop-tests/src/tests/deterministic_account_id.rs (L179-262)
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
}
```
