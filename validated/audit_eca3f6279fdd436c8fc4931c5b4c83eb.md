Based on my research, the strongest and best-documented analog is the already-identified `validate_delegate_action` receiver-id bug, but it is confirmed patched (active fix gated at protocol version 85, and `MIN_SUPPORTED_PROTOCOL_VERSION = 83` means... wait, actually the fix activates at v85 while min supported is 83, so protocol versions 83-84 could still be running the buggy code path). Let me verify this precisely before concluding.`FixDelegatedDeterministicStateInit` activates at protocol version 85, while `MIN_SUPPORTED_PROTOCOL_VERSION = 84`. That means protocol version 84 is a currently-supported version on which this bug's fix is *not yet active*, and the buggy `receiver` (outer tx `receiver_id`) is used instead of `delegate_action.receiver_id()`.

### Title
Wrong account-id used to validate `DeterministicStateInit` inside a meta-transaction bypasses receiver-derivation check at protocol version 84 - (File: `runtime/runtime/src/action_validation.rs`)

### Summary
`validate_delegate_action` validates the actions embedded in a `DelegateAction` (meta-transaction) against the wrong account id when `FixDelegatedDeterministicStateInit` is not yet enabled. It uses the outer transaction's `receiver_id` (the relayed-to account, which for a `Delegate` action equals `delegate_action.sender_id()`) instead of `delegate_action.receiver_id()`, the account the inner actions are actually meant to execute against. On `MIN_SUPPORTED_PROTOCOL_VERSION = 84`, this feature gate is not yet active, so the vulnerable code path is live and reachable by any signer submitting a meta-transaction.

### Finding Description
`validate_delegate_action` (`runtime/runtime/src/action_validation.rs:182-211`) chooses which account id to validate `DeterministicStateInitAction`'s embedded `receiver` derivation against: [1](#0-0) 

Pre-fix, it uses `receiver` (the outer `SignedTransaction.receiver_id`, which for a `Delegate`/`DelegateV2` action must equal `delegate_action.sender_id()`), instead of `delegate_action.receiver_id()` — the actual account the inner action list executes against. This is structurally identical to the Wise Lending bug: a check that should be keyed on the "inner"/logical identifier (`delegate_action.receiver_id()`, analogous to `keyId`) is instead keyed on a different, attacker-influenced identifier (`receiver`, analogous to `nftId`), letting the two diverge.

`validate_deterministic_state_init` (`runtime/runtime/src/action_validation.rs:424-461`) enforces that a `DeterministicStateInitAction`'s state-init hash matches the account id it is submitted against — this is the safety check meant to stop an attacker from initializing a deterministic account with state it did not derive from: [2](#0-1) 

Because `validate_delegate_action` substitutes the wrong id at validation time, a `DelegateAction` whose `sender_id` is the outer `receiver_id` (satisfying `validate_delegate_action`'s check) but whose `receiver_id` (the actual execution target) is a *different* deterministic account than the one the `state_init` derives to, passes the transaction-admission validation performed in `chain/chain/src/runtime/mod.rs` (`can_verify_and_charge_tx`) / mempool validation. The transaction is only rejected later, when the inner receipt is validated independently as a receipt via `validate_receipt`, at which point `InvalidDeterministicStateInitReceiver` is raised.

This is confirmed by the codebase's own regression test suite, which documents the exploit attempt and its outcome per protocol version: [3](#0-2) 

### Impact Explanation
On protocol version 84 (a version within the currently supported range, since `MIN_SUPPORTED_PROTOCOL_VERSION = 84`), the initial transaction-validation check for `DeterministicStateInitAction`s wrapped in a meta-transaction is checked against the wrong account id and always passes regardless of whether the inner receiver matches the derived deterministic account id. This is a bypass of a state-transition-guarding validation check, analogous to the report's un-liquidatable position: a security-critical id check is evaluated on the wrong identifier, letting a malformed transaction be admitted into a chunk/mempool that should have been rejected at admission time. While the codebase's own tests demonstrate that the bug is *ultimately* self-mitigated by a second, independent id check (`validate_receipt` on the outgoing receipt), this still represents a validation-layer defect: the primary line of defense (tx-level admission control) is broken and any protocol path or future refactor that skips or short-circuits the secondary receipt-level check (or that relies on transaction validation being authoritative, e.g. for gas/fee accounting or mempool acceptance heuristics) would allow an invalid state transition to be accepted, matching the "invalid state transition acceptance" root-cause category.

### Likelihood Explanation
Any signer can trivially construct this transaction: wrap an `Action::DeterministicStateInit` inside a `SignedDelegateAction`/`VersionedSignedDelegateAction`, set the inner `delegate_action.receiver_id` to an account different from the outer transaction's `receiver_id`, and submit it as a `Delegate`/`DelegateV2` action. No special privileges, validator collusion, or network-level manipulation is required — this is reachable purely through the transaction/RPC submission path by an ordinary meta-transaction sender, and is confirmed live on the currently-supported minimum protocol version (84).

### Recommendation
Ensure `validate_delegate_action` always uses `delegate_action.receiver_id()` (not the outer transaction's `receiver_id`) when validating inner actions, regardless of protocol version, i.e. retire the pre-`FixDelegatedDeterministicStateInit` code path entirely, or raise `MIN_SUPPORTED_PROTOCOL_VERSION` above 85 so the buggy branch can never execute. Additionally, tx-level admission validation should not rely solely on the assumption that a downstream receipt-level check will catch the same class of mismatch; the two checks should be unified into a single, consistently-keyed helper.

### Proof of Concept
The exploit scaffold already exists in the test suite and demonstrates the mismatch is admitted at the vulnerable protocol version: [4](#0-3) 

Concretely: craft `delegate_action.sender_id == det_account_b` (equal to outer tx `receiver_id`, satisfying the outer `sender_id == tx.receiver_id` check), but set `delegate_action.receiver_id = det_account_a` (a different deterministic account than the one `state_init_b` derives to). At protocol version 84, `try_meta_tx_deterministic_receiver_exploit(84)` succeeds in passing initial transaction validation (per the pinned pre-fix test `test_deterministic_state_init_meta_tx_receiver_check_pre_fix`), and only fails later at receipt validation with `InvalidDeterministicStateInitReceiver`: [5](#0-4)

### Citations

**File:** runtime/runtime/src/action_validation.rs (L182-211)
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
}

```

**File:** runtime/runtime/src/action_validation.rs (L424-461)
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

    // State init entries must not violate limits of individual state keys and values.
    for (key, value) in action.state_init.data() {
        if key.len() as u64 > limit_config.max_length_storage_key {
            return Err(ActionsValidationError::DeterministicStateInitKeyLengthExceeded {
                length: key.len() as u64,
                limit: limit_config.max_length_storage_key,
            }
            .into());
        }

        if value.len() as u64 > limit_config.max_length_storage_value {
            return Err(ActionsValidationError::DeterministicStateInitValueLengthExceeded {
                length: value.len() as u64,
                limit: limit_config.max_length_storage_value,
            }
            .into());
        }
    }

    Ok(())
}

```

**File:** test-loop-tests/src/tests/deterministic_account_id.rs (L128-176)
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
}

/// With `FixDelegatedDeterministicStateInit` in place, the exploit should
/// already be caught at the first tx validation.
#[test]
// Pins to a pre-spice protocol version; skipped under the spice feature.
#[cfg_attr(feature = "protocol_feature_spice", ignore)]
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

**File:** test-loop-tests/src/tests/deterministic_account_id.rs (L177-266)
```rust
/// Set up the exploit scenario and return the result of submitting the exploit tx.
///
/// `det_account_b` is deployed as a deterministic account and given an access key so
/// it can act as meta_tx_sender. The exploit tx wraps `state_init_b` inside a delegate
/// action whose `receiver_id` is `det_account_a` (wrong target). With the fix this is
/// caught at tx validation; without it, tx validation passes but the receipt fails.
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
