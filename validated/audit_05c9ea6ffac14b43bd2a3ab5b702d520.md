### Title
Nonce Consumed on Failed `DelegateAction` with `DepositWithFunctionCall` Error — (`runtime/runtime/src/actions.rs`)

---

### Summary

In `validate_delegate_action_key`, a missing early return on the `DepositWithFunctionCall` error path causes the sender's access-key nonce to be permanently incremented in the trie even when the `DelegateAction` is rejected. An unprivileged relayer can exploit this to silently consume a sender's nonce without executing the intended meta-transaction.

---

### Finding Description

`validate_delegate_action_key` in `runtime/runtime/src/actions.rs` enforces that a `FunctionCall` access key cannot be used to sign a `DelegateAction` whose inner function call carries a non-zero deposit. When that condition is detected, the function sets `result.result = Err(DepositWithFunctionCall)` but — before `ProtocolFeature::FixDelegateActionDepositWithFunctionCallError` is enabled — does **not** return early: [1](#0-0) 

Execution then falls through to the `receiver_id` and `method_name` checks. If both pass (i.e., the delegate action targets the exact receiver and method the key permits), neither of those branches fires an early return either, and control reaches the unconditional nonce-update block: [2](#0-1) 

`set_access_key` (or `set_gas_key_nonce` for a gas key) writes the incremented nonce to the `TrieUpdate`. The function then returns `Ok(())`.

Back in `apply_delegate_action`, the caller checks `result.result.is_err()` and returns without generating a new receipt — correctly rejecting the action — but the nonce write to the trie has already occurred and will be committed with the block: [3](#0-2) 

The protocol-version gate that introduces the fix is at version 85: [4](#0-3) [5](#0-4) 

The code comment itself acknowledges the pre-fix behaviour: [6](#0-5) 

---

### Impact Explanation

The sender's access-key nonce is permanently advanced in on-chain state even though the `DelegateAction` is rejected and no receipt is generated. Concretely:

1. Alice signs a `DelegateAction` with nonce N using a `FunctionCall` access key that permits calls to `token.near::ft_transfer`. The inner call carries a 1-yoctoNEAR deposit (a common pattern for security checks in FT contracts).
2. A relayer submits the outer transaction.
3. `validate_delegate_action_key` sets `result.result = Err(DepositWithFunctionCall)`, skips the early return, passes the receiver/method checks, and writes nonce N to the trie.
4. `apply_delegate_action` sees the error and returns without creating a receipt — the action fails.
5. Alice's access key now has nonce N committed. Her signed `DelegateAction` with nonce N is permanently invalidated; she must sign a new one with nonce N+1.

A malicious relayer (an ordinary unprivileged network participant) can therefore **consume a sender's nonce without executing the intended action**, breaking the sender's meta-transaction flow and forcing re-signing. This constitutes **transaction manipulation** and **unauthorized nonce state mutation**.

---

### Likelihood Explanation

The trigger requires:
- A sender who signs a `DelegateAction` with a `FunctionCall` access key and a non-zero deposit (e.g., a 1-yoctoNEAR security deposit pattern common in NEP-141 contracts).
- A relayer willing to submit the action (any network participant can act as a relayer).
- Protocol version < 85 (the fix version).

The 1-yoctoNEAR deposit pattern is widely used in NEAR fungible-token contracts, making it plausible that users would sign such delegate actions. Any relayer — including a malicious one — can submit the outer transaction.

---

### Recommendation

The fix is already present in the codebase behind the `FixDelegateActionDepositWithFunctionCallError` protocol-version gate. The corrective pattern is to add an unconditional `return Ok(())` immediately after setting the `DepositWithFunctionCall` error, before any subsequent permission checks can be evaluated and before the nonce-update block is reached:

```rust
if function_call.deposit > Balance::ZERO {
    result.result = Err(ActionErrorKind::DelegateActionAccessKeyError(
        InvalidAccessKeyError::DepositWithFunctionCall,
    ).into());
    return Ok(()); // always return; do not fall through to nonce update
}
```

This matches the behaviour already enforced for all other error paths in the same function (each sets `result.result` and immediately returns).

---

### Proof of Concept

The repository's own test suite demonstrates the pre-fix behaviour: [7](#0-6) 

The test `test_delegate_deposit_with_function_call_reports_receiver_mismatch_before_fix` shows that at `protocol_version - 1`, the `DepositWithFunctionCall` error is silently overwritten by `ReceiverMismatch`. The symmetric case — where the receiver **does** match — causes the nonce-update block to execute with `result.result` already set to `Err(DepositWithFunctionCall)`, committing the nonce increment to the trie while the action is rejected by the caller.

Minimal reproduction (pre-fix protocol version):

1. Create account `alice` with a `FunctionCall` access key restricted to `token.near::ft_transfer`, nonce = 0.
2. Construct a `DelegateAction` with `nonce = 1`, `receiver_id = token.near`, action = `FunctionCall { method_name: "ft_transfer", deposit: 1 }`.
3. Call `validate_delegate_action_key` at protocol version < 85.
4. Observe `result.result = Err(DepositWithFunctionCall)` **and** `access_key.nonce = 1` written to the trie.
5. Attempt to submit a second `DelegateAction` with `nonce = 1` — it is rejected with `DelegateActionInvalidNonce`, confirming the nonce was consumed by the failed action.

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

**File:** runtime/runtime/src/actions.rs (L1763-1781)
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
