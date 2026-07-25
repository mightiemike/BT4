### Title
Delegate Action Nonce Consumed Without Execution on `DepositWithFunctionCall` Validation Failure When Receiver Matches — (`runtime/runtime/src/actions.rs`)

### Summary

In `validate_delegate_action_key()`, when a `DelegateAction` is signed with a `FunctionCall` access key and the inner function call carries a nonzero deposit (`deposit > 0`) while the receiver and method name both match the access key's permissions, the function sets `result.result = Err(DepositWithFunctionCall)` but — on protocol versions below 85 — does **not** return early. Execution falls through to the nonce-update block at lines 685–699, which writes the advanced nonce to `state_update`. The caller (`apply_delegate_action`) then sees `result.result.is_err()` and returns without creating the inner receipt, but the nonce write is already staged. The sender's access key nonce is consumed even though the delegate action was rejected.

### Finding Description

`validate_delegate_action_key` in `runtime/runtime/src/actions.rs` enforces three sequential constraints for `FunctionCall`-permission access keys:

1. **Deposit check** (line 637): `function_call.deposit > Balance::ZERO` → sets `result.result = Err(DepositWithFunctionCall)`.
2. **Receiver check** (line 651): receiver mismatch → sets `result.result = Err(ReceiverMismatch)` and `return Ok(())`.
3. **Method-name check** (line 661): method not in allowed list → sets `result.result = Err(MethodNameMismatch)` and `return Ok(())`.

Checks 2 and 3 both carry an early `return Ok(())`, so the nonce-update block at lines 685–699 is never reached when they fire. Check 1 does **not** carry an early return before `ProtocolFeature::FixDelegateActionDepositWithFunctionCallError` is enabled:

```rust
// runtime/runtime/src/actions.rs  lines 637-650
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

When `deposit > 0` **and** the receiver and method name both match, neither check 2 nor check 3 fires, so execution reaches:

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

The nonce is written to `state_update`. Back in `apply_delegate_action`:

```rust
// runtime/runtime/src/actions.rs  lines 448-453
validate_delegate_action_key(state_update, apply_state, delegate_action, result)?;
if result.result.is_err() {
    return Ok(());
}
// ... new receipt is never created
```

The receipt is not created, but the nonce write is already staged. The sender's access key nonce is permanently advanced.

A secondary consequence exists when `deposit > 0` **and** the receiver does **not** match: the `DepositWithFunctionCall` error is silently overwritten by `ReceiverMismatch`, so the user and relayer see the wrong error code and may attempt to fix only the receiver, not realising the deposit is also forbidden.

Both behaviors are confirmed by the in-tree regression tests:

```rust
// runtime/runtime/src/actions.rs  lines 1763-1795
fn test_delegate_deposit_with_function_call_reports_receiver_mismatch_before_fix()
fn test_delegate_deposit_with_function_call_reports_deposit_error()
```

The protocol feature that gates the fix is registered at version 85:

```rust
// core/primitives-core/src/version.rs  lines 555-571
ProtocolFeature::FixDelegateActionDepositWithFunctionCallError
| ...
| ProtocolFeature::DelegateV2 => 85,
```

### Impact Explanation

On nodes running protocol version < 85 the sender's `FunctionCall` access key nonce is advanced without the delegate action executing. This breaks the invariant that a rejected action must not mutate authorisation state. Concretely:

- **Nonce exhaustion / sequencing breakage**: a relayer holding a signed `DelegateAction` with `deposit > 0` to the correct receiver can submit it, consuming the sender's nonce. Any subsequent delegate action the sender signed with the next sequential nonce is now invalid (`DelegateActionInvalidNonce`), permanently blocking that key's delegate-action pipeline.
- **Error masking**: when the receiver also mismatches, the wrong error (`ReceiverMismatch`) is surfaced, misleading the sender into fixing only the receiver and resubmitting, which then hits the nonce-consumption path.

Both outcomes fall under **contract execution flow breakage** in the allowed impact gate.

### Likelihood Explanation

The trigger requires a `FunctionCall` access key (common for dApp integrations), a nonzero deposit in the inner function call (a user mistake or a malicious relayer crafting the payload), and a receiver that matches the key's permitted receiver. The relayer model for meta-transactions makes this reachable from an unprivileged position: the relayer, not the sender, decides when and whether to submit the signed `DelegateAction`.

### Recommendation

The fix is already present in the codebase and is activated at protocol version 85 via `ProtocolFeature::FixDelegateActionDepositWithFunctionCallError`. The unconditional early return should be applied regardless of protocol version, or the legacy code path should be removed once version 85 is universally deployed:

```rust
if function_call.deposit > Balance::ZERO {
    result.result = Err(ActionErrorKind::DelegateActionAccessKeyError(
        InvalidAccessKeyError::DepositWithFunctionCall,
    ).into());
    return Ok(()); // always return; do not fall through to nonce update
}
```

### Proof of Concept

1. Alice holds a `FunctionCall` access key permitting calls to `token.near` with any method.
2. Alice signs a `DelegateAction` (nonce N) whose inner action is `FunctionCall { receiver: token.near, method: "ft_transfer", deposit: 1 }`.
3. A relayer submits the signed delegate action on Alice's behalf.
4. On protocol version < 85, `validate_delegate_action_key` sets `result.result = Err(DepositWithFunctionCall)`, falls through the receiver and method checks (both pass), and writes nonce N to Alice's access key in `state_update`.
5. `apply_delegate_action` returns early; no receipt is created.
6. Alice's access key nonce is now N. Any subsequent delegate action Alice signed with nonce N+1 is rejected with `DelegateActionInvalidNonce { delegate_nonce: N+1, ak_nonce: N }` — but Alice's intended action never executed.

The existing test `test_delegate_deposit_with_function_call_reports_receiver_mismatch_before_fix` (lines 1763–1780) and `test_delegate_deposit_with_function_call_reports_deposit_error` (lines 1783–1795) in `runtime/runtime/src/actions.rs` directly demonstrate the pre-fix and post-fix behaviour. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

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

**File:** runtime/runtime/src/actions.rs (L637-650)
```rust
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

**File:** runtime/runtime/src/actions.rs (L1763-1795)
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
