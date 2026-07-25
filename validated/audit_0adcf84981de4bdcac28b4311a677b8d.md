### Title
Wrong Receiver-ID Field Used in `validate_delegate_action` Breaks `DeterministicStateInitAction` in Meta-Transactions — (`File: runtime/runtime/src/action_validation.rs`)

---

### Summary

`validate_delegate_action` passes the **outer transaction's receiver** (`receiver`) to `validate_actions_with_mode` instead of the **delegate action's own receiver** (`delegate_action.receiver_id()`). This is the exact same wrong-field comparison class as the external report: a check that should use the direct identity of the inner object instead uses an indirect, outer-context value, causing the check to evaluate against the wrong account and permanently breaking a legitimate execution path.

---

### Finding Description

In `validate_delegate_action` (`runtime/runtime/src/action_validation.rs:182`), when validating the inner actions of a `DelegateAction` (meta-transaction), the pre-`FixDelegatedDeterministicStateInit` code path passes `receiver` — the outer transaction's `receiver_id`, i.e., the relayer's target account — to `validate_actions_with_mode` instead of `delegate_action.receiver_id()`, which is the actual intended receiver of the inner actions. [1](#0-0) 

`validate_actions_with_mode` then calls `validate_deterministic_state_init`, which checks:

```rust
if derived_id != *receiver_id { ... }
``` [2](#0-1) 

Because `receiver_id` is the **outer** tx receiver (not the delegate action's inner receiver), the check compares the `DeterministicAccountStateInit` payload against the wrong account. This is structurally identical to the Solana bug: `.owner.key()` (an indirect field) is compared instead of `.key()` (the direct identity).

The protocol feature `FixDelegatedDeterministicStateInit` (activated at protocol version 85) corrects this by switching to `delegate_action.receiver_id()`: [3](#0-2) 

The feature is declared in `version.rs`: [4](#0-3) [5](#0-4) 

---

### Impact Explanation

**Contract execution flow breakage** (in-scope per allowed impact gate):

1. **Legitimate use permanently broken (pre-v85):** Any correctly-formed meta-transaction containing a `DeterministicStateInitAction` is rejected at receipt validation with `InvalidDeterministicStateInitReceiver`, because the tx-admission check validated against the wrong receiver. A user who wants to create a deterministic account via a relayer (meta-tx) cannot do so — the action is always blocked.

2. **Malformed meta-tx passes tx admission (pre-v85):** An attacker can craft a `DelegateAction` where `outer_tx.receiver = det_account_b = derive(state_init_b)` but `delegate_action.receiver_id = det_account_a` (a different deterministic account). The wrong-field check passes at tx admission because it compares `state_init_b` against `det_account_b` (outer receiver). The tx is admitted to the chain and the relayer pays gas for a receipt that then fails at `NewReceiptValidationError`. The secondary receipt-level check prevents actual state corruption, but the relayer's gas is wasted. [6](#0-5) 

---

### Likelihood Explanation

- **Trigger**: Any unprivileged user can submit a `SignedTransaction` containing a `DelegateAction` wrapping a `DeterministicStateInitAction`. No special role or key is required.
- **Pre-condition**: Protocol version < 85 (the buggy code path is still present in the repository, gated by the protocol version check).
- **Discoverability**: The bug is self-documented in the code with an explicit comment and a dedicated test (`test_deterministic_state_init_meta_tx_receiver_check_pre_fix`) that confirms the exploit path. [7](#0-6) 

---

### Recommendation

The fix is already applied at protocol version 85 via `FixDelegatedDeterministicStateInit`. The corrected path uses `delegate_action.receiver_id()` as the receiver for inner-action validation. No further action is needed for the live network (protocol version 86). The pre-fix code path remains in the repository for backward compatibility with older protocol versions and should be treated as a known, resolved issue.

---

### Proof of Concept

The exploit scenario is fully documented and tested in the repository:

1. Deploy `det_account_b` (derived from `state_init_b`).
2. Add a full-access key to `det_account_b` so it can act as meta-tx sender.
3. Craft a `DelegateAction` with:
   - `sender_id = det_account_b`
   - `receiver_id = det_account_a` ← wrong target
   - inner action: `DeterministicStateInitAction { state_init: state_init_b, ... }`
4. Wrap in an outer tx with `receiver_id = det_account_b` (matches `derive(state_init_b)`).
5. On pre-v85: tx admission passes (wrong-field check compares `state_init_b` vs `det_account_b`). Receipt execution fails with `InvalidDeterministicStateInitReceiver`.
6. On post-v85: tx admission correctly rejects with `InvalidDeterministicStateInitReceiver`. [8](#0-7)

### Citations

**File:** runtime/runtime/src/action_validation.rs (L190-201)
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

**File:** runtime/runtime/src/action_validation.rs (L431-438)
```rust
    let derived_id = derive_near_deterministic_account_id(&action.state_init);

    if derived_id != *receiver_id {
        return Err(ActionsValidationError::InvalidDeterministicStateInitReceiver {
            derived_id,
            receiver_id: receiver_id.clone(),
        });
    }
```

**File:** core/primitives-core/src/version.rs (L408-410)
```rust
    /// Allow creating `DeterministicStateInitAction` from a delegated action by
    /// fixing the receiver id check.
    FixDelegatedDeterministicStateInit,
```

**File:** core/primitives-core/src/version.rs (L558-560)
```rust
            ProtocolFeature::FixDelegateActionDepositWithFunctionCallError
            | ProtocolFeature::FixDeleteAccountGlobalContractStorageUsage
            | ProtocolFeature::FixDelegatedDeterministicStateInit
```

**File:** test-loop-tests/src/tests/deterministic_account_id.rs (L128-156)
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
// Pins to a pre-spice protocol version; skipped under the spice feature.
#[cfg_attr(feature = "protocol_feature_spice", ignore)]
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
```

**File:** test-loop-tests/src/tests/deterministic_account_id.rs (L183-266)
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
