### Title
Missing Early Return in `validate_delegate_action_key` Allows Nonce Consumption on `DepositWithFunctionCall` Failure and Wrong Error Reporting - (`runtime/runtime/src/actions.rs`)

---

### Summary

`validate_delegate_action_key` in `runtime/runtime/src/actions.rs` is missing an early `return Ok(())` after setting `result.result = Err(DepositWithFunctionCall)` when a delegate action carries `deposit > 0` under a `FunctionCallPermission` key. Before `ProtocolFeature::FixDelegateActionDepositWithFunctionCallError` (protocol version 85), execution falls through to the receiver-id and method-name checks. This produces two concrete broken invariants: (1) if the receiver also mismatches, the `DepositWithFunctionCall` error is silently overwritten by `ReceiverMismatch`, and (2) if the receiver and method name both match, execution reaches the unconditional nonce-update block and the access-key nonce is advanced in state even though the action ultimately fails — consuming a nonce slot without executing the intended action.

---

### Finding Description

`validate_delegate_action_key` is the sole function responsible for verifying and advancing the nonce of the signing key for a `Delegate`/`DelegateV2` action (meta-transaction). Its structure is:

1. Look up the access key; early-return on `AccessKeyNotFound`.
2. Determine nonce type (plain vs. gas-key); early-return on type mismatch.
3. Range-check the nonce; early-return on `InvalidNonce` / `NonceTooLarge`.
4. If the key has `FunctionCallPermission`, validate `actions.len() == 1`, `deposit == 0`, receiver, and method name — each check early-returns on failure.
5. **Unconditionally** advance the nonce in state (lines 685–699).

The `deposit > 0` check at line 637 sets `result.result = Err(DepositWithFunctionCall)` but, before the fix, does **not** return:

```rust
// runtime/runtime/src/actions.rs  lines 637-649
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

Because there is no unconditional `return Ok(())`, two paths diverge:

**Path A — receiver mismatches:** the receiver check at line 651 overwrites `result.result` with `ReceiverMismatch` and then returns. The nonce is not advanced, but the wrong error is reported to the user.

**Path B — receiver and method name both match:** all subsequent checks pass, execution reaches the nonce-update block at lines 685–699, and the access-key nonce (or gas-key nonce row) is written to state. Control returns to `apply_delegate_action`, which sees `result.result.is_err()` and exits without creating the inner receipt. The receipt fails with `DepositWithFunctionCall`, but the nonce has already been permanently consumed.

The nonce update is unconditional and happens after all permission checks:

```rust
// runtime/runtime/src/actions.rs  lines 685-699
match nonce_update {
    DelegateNonceUpdate::AccessKey => {
        access_key.nonce = delegate_nonce.nonce();
        set_access_key(state_update, sender_id.clone(), public_key.clone(), &access_key);
    }
    DelegateNonceUpdate::GasKey { nonce_index } => {
        set_gas_key_nonce(state_update, sender_id.clone(), public_key.clone(),
                          nonce_index, delegate_nonce.nonce());
    }
}
```

Every other validation failure in this function (key not found, wrong nonce type, nonce out of range, wrong action count, receiver mismatch, method mismatch) causes an early return **before** this block. `DepositWithFunctionCall` is the sole exception before the fix.

---

### Impact Explanation

**Nonce consumed without action execution (Path B):** A user who signs a delegate action with `deposit > 0` under a `FunctionCallPermission` key whose `receiver_id` and `method_names` match will have their access-key nonce (or gas-key nonce index) permanently advanced in state even though the action fails. The user must use a strictly higher nonce for any subsequent delegate action on that key. For a gas key with `num_nonces = 1`, this exhausts the only nonce slot. A malicious relayer who holds a user's signed delegate action (with `deposit > 0`) can submit it to consume the nonce, blocking the user from replaying or correcting the action. This is a **transaction manipulation** and **balance/nonce invariant** violation: the nonce invariant requires that a nonce is advanced only when the action it authorizes is accepted.

**Wrong error reported (Path A):** When `deposit > 0` and the receiver also mismatches, the user receives `ReceiverMismatch` instead of `DepositWithFunctionCall`. This is a **contract execution flow breakage**: the user is directed to fix the wrong field, and any tooling that branches on the error kind will take the wrong path.

---

### Likelihood Explanation

The trigger condition — a `FunctionCallPermission` delegate action with `deposit > 0` and a matching receiver/method — is reachable by any unprivileged user submitting a meta-transaction. No validator or admin privilege is required. The relayer role in NEP-366 meta-transactions is explicitly unprivileged. The bug is present in all protocol versions below 85 and the old code path is still present in the repository, gated by the protocol-version check.

---

### Recommendation

The fix is already present in the codebase as `ProtocolFeature::FixDelegateActionDepositWithFunctionCallError` (protocol version 85). The conditional guard should be removed and the `return Ok(())` made unconditional:

```rust
// runtime/runtime/src/actions.rs  lines 637-649
if function_call.deposit > Balance::ZERO {
    result.result = Err(ActionErrorKind::DelegateActionAccessKeyError(
        InvalidAccessKeyError::DepositWithFunctionCall,
    ).into());
    return Ok(()); // unconditional early return
}
```

This makes `DepositWithFunctionCall` consistent with every other validation failure in the function: the nonce is not advanced and the error is not overwritten.

---

### Proof of Concept

The existing test `test_delegate_deposit_with_function_call_reports_receiver_mismatch_before_fix` in `runtime/runtime/src/actions.rs` directly demonstrates Path A (wrong error):

```rust
// runtime/runtime/src/actions.rs  lines 1763-1780
#[test]
fn test_delegate_deposit_with_function_call_reports_receiver_mismatch_before_fix() {
    let version =
        ProtocolFeature::FixDelegateActionDepositWithFunctionCallError.protocol_version() - 1;
    let result = deposit_with_function_call_and_receiver_mismatch(version);
    // Legacy: missing early return lets ReceiverMismatch overwrite DepositWithFunctionCall.
    assert_eq!(
        result.result,
        Err(ActionErrorKind::DelegateActionAccessKeyError(
            InvalidAccessKeyError::ReceiverMismatch { ... }
        ).into()),
    );
}
```

For Path B (nonce consumed), construct a delegate action with `deposit = 1` under a `FunctionCallPermission` key whose `receiver_id` and `method_names` match the action, at protocol version < 85. Call `validate_delegate_action_key`. Observe that `result.result = Err(DepositWithFunctionCall)` and that `get_access_key` returns the key with `nonce` advanced to `delegate_action.nonce` — the nonce was consumed despite the action failing. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** runtime/runtime/src/actions.rs (L422-453)
```rust
pub(crate) fn apply_delegate_action(
    state_update: &mut TrieUpdate,
    apply_state: &ApplyState,
    action_receipt: &VersionedActionReceipt,
    sender_id: &AccountId,
    signed_delegate_action: VersionedSignedDelegateActionRef<'_>,
    result: &mut ActionResult,
) -> Result<(), RuntimeError> {
    if !signed_delegate_action.verify() {
        result.result = Err(ActionErrorKind::DelegateActionInvalidSignature.into());
        return Ok(());
    }
    let delegate_action = signed_delegate_action.delegate_action();
    if apply_state.block_height > delegate_action.max_block_height() {
        result.result = Err(ActionErrorKind::DelegateActionExpired.into());
        return Ok(());
    }
    if delegate_action.sender_id().as_str() != sender_id.as_str() {
        result.result = Err(ActionErrorKind::DelegateActionSenderDoesNotMatchTxReceiver {
            sender_id: delegate_action.sender_id().clone(),
            receiver_id: sender_id.clone(),
        }
        .into());
        return Ok(());
    }

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

**File:** runtime/runtime/src/actions.rs (L1763-1796)
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
    }

    #[test]
    fn test_delegate_deposit_with_function_call_reports_deposit_error() {
        let version =
            ProtocolFeature::FixDelegateActionDepositWithFunctionCallError.protocol_version();
        let result = deposit_with_function_call_and_receiver_mismatch(version);

        assert_eq!(
            result.result,
            Err(ActionErrorKind::DelegateActionAccessKeyError(
                InvalidAccessKeyError::DepositWithFunctionCall,
            )
            .into()),
        );
    }
```

**File:** core/primitives-core/src/version.rs (L349-352)
```rust
    /// Fix missing early return on DepositWithFunctionCall error path in
    /// validate_delegate_action_key. Previously the error could be
    /// overwritten by a subsequent receiver_id or method_name check.
    FixDelegateActionDepositWithFunctionCallError,
```

**File:** core/primitives-core/src/version.rs (L558-574)
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
