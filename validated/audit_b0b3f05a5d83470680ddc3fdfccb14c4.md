### Title
Missing Early Return in `validate_delegate_action_key` Advances Access-Key Nonce on a Failed `DelegateAction` — (`File: runtime/runtime/src/actions.rs`)

### Summary

`validate_delegate_action_key` in `runtime/runtime/src/actions.rs` is missing an unconditional early return after setting the `DepositWithFunctionCall` error for protocol versions below 85. Execution falls through to the `receiver_id` and `method_name` checks. When those subsequent checks pass (receiver and method match the function-call permission), the function reaches the nonce-update block at lines 685–699 and **persists the advanced nonce to the trie** even though `result.result` is already `Err(DepositWithFunctionCall)`. The caller (`apply_delegate_action`) then sees the error and aborts receipt creation, but the nonce has already been written. This is the direct nearcore analog of the Redstone oracle bug: a missing early return allows state to be mutated after an error is set, breaking the invariant that the access-key nonce only advances when an action is successfully authorized.

### Finding Description

`validate_delegate_action_key` performs three sequential checks on a function-call access key:

1. `deposit > 0` → sets `DepositWithFunctionCall` error (no early return pre-fix)
2. `receiver_id` mismatch → sets `ReceiverMismatch` error and returns
3. `method_name` mismatch → sets `MethodNameMismatch` error and returns

If check 1 fires but checks 2 and 3 do **not** fire (receiver and method both match the permission), execution falls through to the unconditional nonce-update block:

```rust
// runtime/runtime/src/actions.rs lines 685-699
match nonce_update {
    DelegateNonceUpdate::AccessKey => {
        access_key.nonce = delegate_nonce.nonce();
        set_access_key(state_update, sender_id.clone(), public_key.clone(), &access_key);
    }
    DelegateNonceUpdate::GasKey { nonce_index } => {
        set_gas_key_nonce(...);
    }
}
```

The nonce is written to the trie. Control then returns to `apply_delegate_action`, which checks `result.result.is_err()` and returns without creating a receipt. The action fails, but the nonce has been permanently advanced.

The fix — a conditional early return gated on `ProtocolFeature::FixDelegateActionDepositWithFunctionCallError` — was introduced at protocol version 85, but the legacy code path remains active for any node running an older protocol version:

```rust
// runtime/runtime/src/actions.rs lines 637-650
if function_call.deposit > Balance::ZERO {
    result.result = Err(ActionErrorKind::DelegateActionAccessKeyError(
        InvalidAccessKeyError::DepositWithFunctionCall,
    ).into());
    // Before this fix, the missing early return allowed execution
    // to fall through to the receiver_id and method_name checks,
    // which could overwrite this error with a different one.
    if ProtocolFeature::FixDelegateActionDepositWithFunctionCallError
        .enabled(apply_state.current_protocol_version)
    {
        return Ok(());
    }
}
```

A second consequence: when check 1 fires **and** check 2 also fires (receiver mismatch), the `ReceiverMismatch` error overwrites `DepositWithFunctionCall`. The caller sees the wrong error, which can mislead a user into resubmitting with the correct receiver but still with `deposit > 0`, triggering the nonce-advancement path described above.

### Impact Explanation

**Broken invariant**: The access-key nonce must advance only when a `DelegateAction` is fully authorized and a receipt is created. Under the pre-fix code path, the nonce advances on a failed action.

**Concrete corrupted value**: `access_key.nonce` (stored in the trie under the sender's account) is incremented to `delegate_nonce.nonce()` even though no receipt is emitted and no transfer or function call executes.

**Downstream effects**:
- Any pre-signed `DelegateAction` carrying the same nonce value is now permanently invalid (replay protection treats it as a replay).
- A user who pre-signs a batch of sequential `DelegateAction`s and submits one with `deposit > 0` (e.g., after being misled by the wrong error message) silently burns a nonce slot, invalidating subsequent actions in the batch.
- The wrong error (`ReceiverMismatch` instead of `DepositWithFunctionCall`) is surfaced to wallets and relayers, causing incorrect remediation attempts that can re-trigger the nonce-advancement path.

### Likelihood Explanation

The trigger requires a `DelegateAction` signed with a function-call access key that carries `deposit > 0` while the `receiver_id` and `method_name` match the key's permission. This is an unusual but reachable combination: a user confused by the wrong error message (ReceiverMismatch) may correct the receiver while leaving the deposit in place, then resubmit — hitting the nonce-advancement path. The bug is active on any node running protocol version < 85. Protocol version 85 is already deployed on mainnet, so the live impact window is closed, but the legacy code path remains in the repository.

### Recommendation

The fix is already present and gated on `ProtocolFeature::FixDelegateActionDepositWithFunctionCallError` (protocol version 85). No further action is needed for mainnet. For clarity and to prevent future regressions, the legacy fallthrough code path (lines 642–649) should be removed once protocol version 85 is universally adopted and backward compatibility with older versions is no longer required.

### Proof of Concept

The repository's own test at `runtime/runtime/src/actions.rs` lines 1763–1780 demonstrates the pre-fix behavior:

```rust
// test_delegate_deposit_with_function_call_reports_receiver_mismatch_before_fix
// Protocol version < 85: DepositWithFunctionCall is overwritten by ReceiverMismatch.
assert_eq!(
    result.result,
    Err(ActionErrorKind::DelegateActionAccessKeyError(
        InvalidAccessKeyError::ReceiverMismatch { ... }
    ).into()),
);
```

To demonstrate nonce advancement on failure (pre-fix, protocol version < 85):

1. Create a function-call access key for `sender.near` with `receiver_id = token.near`, `method_names = []`, nonce = N.
2. Craft a `DelegateAction` with `nonce = N+1`, `deposit = 1 yoctoNEAR`, `receiver_id = token.near` (matches permission), `method_name = any_method`.
3. Call `validate_delegate_action_key` with `current_protocol_version < 85`.
4. Observe: `result.result = Err(DepositWithFunctionCall)` (action fails), but `get_access_key(state_update, sender_id, public_key).nonce == N+1` (nonce was advanced).
5. Submit a second `DelegateAction` with `nonce = N+1` and no deposit: it is rejected with `DelegateActionInvalidNonce` because the nonce slot is already consumed. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** runtime/runtime/src/actions.rs (L448-453)
```rust
    validate_delegate_action_key(state_update, apply_state, delegate_action, result)?;
    if result.result.is_err() {
        // Validation failed. Need to return Ok() because this is not a runtime error.
        // "result.result" will be return to the User as the action execution result.
        return Ok(());
    }
```

**File:** runtime/runtime/src/actions.rs (L636-650)
```rust
        if let Some(Action::FunctionCall(function_call)) = actions.get(0) {
            if function_call.deposit > Balance::ZERO {
                result.result = Err(ActionErrorKind::DelegateActionAccessKeyError(
                    InvalidAccessKeyError::DepositWithFunctionCall,
                )
                .into());
                // Before this fix, the missing early return allowed execution
                // to fall through to the receiver_id and method_name checks,
                // which could overwrite this error with a different one.
                if ProtocolFeature::FixDelegateActionDepositWithFunctionCallError
                    .enabled(apply_state.current_protocol_version)
                {
                    return Ok(());
                }
            }
```

**File:** runtime/runtime/src/actions.rs (L685-699)
```rust
    match nonce_update {
        DelegateNonceUpdate::AccessKey => {
            access_key.nonce = delegate_nonce.nonce();
            set_access_key(state_update, sender_id.clone(), public_key.clone(), &access_key);
        }
        DelegateNonceUpdate::GasKey { nonce_index } => {
            set_gas_key_nonce(
                state_update,
                sender_id.clone(),
                public_key.clone(),
                nonce_index,
                delegate_nonce.nonce(),
            );
        }
    }
```

**File:** runtime/runtime/src/actions.rs (L1763-1780)
```rust
    #[test]
    fn test_delegate_deposit_with_function_call_reports_receiver_mismatch_before_fix() {
        let version =
            ProtocolFeature::FixDelegateActionDepositWithFunctionCallError.protocol_version() - 1;
        let result = deposit_with_function_call_and_receiver_mismatch(version);

        // Legacy: missing early return lets ReceiverMismatch overwrite
        // DepositWithFunctionCall.
        assert_eq!(
            result.result,
            Err(ActionErrorKind::DelegateActionAccessKeyError(
                InvalidAccessKeyError::ReceiverMismatch {
                    tx_receiver: "token.test.near".parse().unwrap(),
                    ak_receiver: "other.test.near".parse().unwrap(),
                },
            )
            .into()),
        );
```

**File:** core/primitives-core/src/version.rs (L349-352)
```rust
    /// Fix missing early return on DepositWithFunctionCall error path in
    /// validate_delegate_action_key. Previously the error could be
    /// overwritten by a subsequent receiver_id or method_name check.
    FixDelegateActionDepositWithFunctionCallError,
```

**File:** core/primitives-core/src/version.rs (L555-571)
```rust
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
```
