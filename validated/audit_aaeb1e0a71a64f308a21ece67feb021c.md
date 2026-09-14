Found a direct analog: a wrong identifier (receiver id) used in a validation check instead of the correct one, closely mirroring the "wrong pool address used instead of the correct one" root cause pattern of the external report.

### Title
Pre-fix `validate_delegate_action` checked inner `DeterministicStateInit` actions against the wrong receiver id (outer tx receiver instead of `delegate_action.receiver_id`) - (File: `runtime/runtime/src/action_validation.rs`)

### Summary
In `validate_delegate_action`, the code that validates the actions nested inside a meta-transaction (`Delegate`/`DelegateV2`) needs to check `DeterministicStateInit` actions against the account id the inner action is actually addressed to. Before `ProtocolFeature::FixDelegatedDeterministicStateInit`, the code used `receiver` (the outer transaction's `receiver_id`, i.e. the relayer contract account) instead of `delegate_action.receiver_id()` (the correct inner receiver) when validating the nested actions.

### Finding Description
`validate_delegate_action` recurses into `validate_actions_with_mode` using an `inner_receiver` value that is supposed to match the account the delegated actions are actually sent to [1](#0-0) . The comments in the code explicitly document the former bug: "This is a bug fixed with `FixDelegatedDeterministicStateInit` that validated against the wrong id. This makes it impossible to initialize deterministic accounts from meta transactions." [2](#0-1) 

Deterministic account ids are derived deterministically from the `DeterministicStateInit` action's code/data payload via `derive_near_deterministic_account_id`, and `validate_deterministic_state_init` enforces that the receiver of the action must equal this derived id (`InvalidDeterministicStateInitReceiver` otherwise) [3](#0-2) . Because the pre-fix code validated against `receiver` (the relayer's own account, i.e. the outer transaction's receiver) rather than `delegate_action.receiver_id()` (the true inner receiver the action targets), the wrong "address" was used for the check — analogous to the external report's wrong-pool-address bug, where a hardcoded address belonging to a different, unrelated entity (WBGL/WETH pool) was substituted for the correct one (DAI/WETH pool), causing all downstream logic keyed on that address to be invalid.

The regression test suite documents the exploit path explicitly: "With the old (buggy) code, `validate_delegate_action` used `outer_tx.receiver_id` instead of `delegate_action.receiver_id` when checking inner actions. The exploit tx therefore passes initial tx validation." [4](#0-3)  The test further notes the saving grace: "The exploit is prevented by a following `validate_receipt` check when the meta transaction is unpacked," which re-validates the actual receipt with the correct receiver and rejects it with `InvalidDeterministicStateInitReceiver` [5](#0-4) .

### Impact Explanation
On the pre-fix protocol version, a meta-transaction (signed by any relayer/sender pair reachable via `SignedDelegateAction`) carrying a `DeterministicStateInit` action addressed to an account id that does not match the derived id would incorrectly pass the initial transaction-admission validation in `validate_actions_with_mode` / `validate_delegate_action`, because the wrong (outer) receiver id was substituted into the check [6](#0-5) . This is a state-transition validation bypass at the tx-admission layer reachable from any signer submitting a meta-transaction. The impact was ultimately bounded because the second-line defense in `validate_receipt`/`validate_action_receipt` (invoked when the receipt is actually unpacked and executed) revalidates against the correct receiver and rejects the mismatched receipt with `ReceiptValidationError::ActionsValidation(ActionsValidationError::InvalidDeterministicStateInitReceiver)` [7](#0-6) , so it did not itself enable unauthorized value movement, but it did represent an admission-layer/receipt-layer inconsistency (a transaction accepted for inclusion that could never execute correctly), which is the class of bug the fix (`ProtocolFeature::FixDelegatedDeterministicStateInit`) closes.

### Likelihood Explanation
This is a historical, already-identified and already-fixed bug in this codebase, gated behind `ProtocolFeature::FixDelegatedDeterministicStateInit` [8](#0-7) ; on any protocol version where the feature is enabled (the current/default path), `inner_receiver` correctly resolves to `delegate_action.receiver_id()` [9](#0-8) . It is presented here strictly as an analog matching the reported bug class (hardcoded/substituted wrong identifier used for a validation/lookup instead of the correct, context-specific one), not as a currently exploitable issue in the default/current protocol configuration.

### Recommendation
Ensure `ProtocolFeature::FixDelegatedDeterministicStateInit` is enabled/finalized on all live networks and remove the legacy branch once the old protocol version can no longer occur, so `validate_delegate_action` always uses `delegate_action.receiver_id()` rather than the outer `receiver` for nested-action validation [1](#0-0) . More generally, audit other places in the validation pipeline where an "outer" identifier (receiver, signer, or predecessor) is passed down into nested/derived validation for delegate or state-init style actions, to confirm the correct scoped identifier is always used rather than one belonging to an unrelated context.

### Citations

**File:** runtime/runtime/src/action_validation.rs (L249-267)
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
    validate_actions_with_mode(
        limit_config,
        &actions,
        inner_receiver,
        current_protocol_version,
        mode,
    )?;
```

**File:** runtime/runtime/src/action_validation.rs (L1401-1428)
```rust
        // correct receiver
        check_validate_state_init(
            "0s69284a5453e7be5632b28b6a01baecf6c12c156d",
            PROTOCOL_VERSION,
            expect![[r#"
                Ok(
                    (),
                )
            "#]],
        );

        // deterministic id but incorrect receiver
        check_validate_state_init(
            "0s1234567890123456789012345678901234567890",
            PROTOCOL_VERSION,
            expect![[r#"
                Err(
                    InvalidDeterministicStateInitReceiver {
                        receiver_id: AccountId(
                            "0s1234567890123456789012345678901234567890",
                        ),
                        derived_id: AccountId(
                            "0s69284a5453e7be5632b28b6a01baecf6c12c156d",
                        ),
                    },
                )
            "#]],
        );
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

**File:** runtime/runtime/src/verifier.rs (L742-770)
```rust
fn validate_action_receipt(
    limit_config: &LimitConfig,
    receipt: VersionedActionReceipt,
    receiver: &AccountId,
    current_protocol_version: ProtocolVersion,
    mode: ValidateReceiptMode,
) -> Result<(), ReceiptValidationError> {
    if receipt.input_data_ids().len() as u64 > limit_config.max_number_input_data_dependencies {
        return Err(ReceiptValidationError::NumberInputDataDependenciesExceeded {
            number_of_input_data_dependencies: receipt.input_data_ids().len() as u64,
            limit: limit_config.max_number_input_data_dependencies,
        });
    }

    if let Some(account_id) = receipt.refund_to() {
        AccountId::validate(account_id.as_ref()).map_err(|_| {
            ReceiptValidationError::InvalidRefundTo { account_id: account_id.to_string() }
        })?;
    }

    validate_actions_with_mode(
        limit_config,
        receipt.actions(),
        receiver,
        current_protocol_version,
        mode,
    )
    .map_err(ReceiptValidationError::ActionsValidation)
}
```

**File:** core/primitives-core/src/version.rs (L1-1)
```rust
use crate::types::ProtocolVersion;
```
