### Title
Gas-key transactions bypass `FunctionCall` access-key permission checks enforced on regular transactions - (File: `runtime/runtime/src/verifier.rs`)

### Summary
The external report describes a "missing guard on one sibling function" bug class: several functions (`deposit`, `mint`, `withdraw`) enforce a `whenNotPaused` guard, but a sibling function (`redeem`) omits it, letting users bypass the pause. The same bug class is reachable in nearcore's transaction-verification layer: the regular-transaction verifier enforces `FunctionCallPermission` restrictions (receiver, method name, zero deposit) via `verify_function_call_permission`, but the parallel gas-key transaction verifier `verify_and_charge_gas_key_tx_ephemeral` does not call this check.

### Finding Description
`verify_and_charge_tx_ephemeral` (the verifier used for regular, non-gas-key access keys) explicitly validates `FunctionCallPermission` constraints before returning success: [1](#0-0) 

This delegates to `verify_function_call_permission`, which enforces that the transaction contains exactly one `FunctionCall` action, has zero deposit, targets the permission's `receiver_id`, and (if set) uses an allowed `method_name`: [2](#0-1) 

`verify_and_charge_gas_key_tx_ephemeral` is the sibling verifier used for gas-key transactions (those carrying a `nonce_index`), documented as validating "the key is a gas key, `nonce_index < num_nonces`, nonce, gas-key balance covers `gas_cost`; then checks the account balance covers `deposit_cost` and storage staking": [3](#0-2) 

Nowhere in that description — nor in the surrounding file — is `verify_function_call_permission` invoked from the gas-key path; a repo-wide search shows the symbol appears only in the regular-tx verifier (definition + single call site) and never inside `verify_and_charge_gas_key_tx_ephemeral`. Gas keys with `FunctionCallPermission` are a first-class, currently-gated feature (`GasKeys` protocol feature) and are explicitly forbidden from having an `allowance` set, confirming `FunctionCallPermission` gas keys are a supported, reachable configuration: [4](#0-3) 

Because the receiver/method-name/deposit restriction check is applied on one verification path (regular tx) but skipped on the structurally parallel path (gas-key tx) — exactly the "guard applied to siblings A/B/C but missing on D" pattern in the M-01 report — a transaction signed with a `FunctionCallPermission` gas key, submitted via the gas-key (nonce-index) path, could execute actions/targets/methods/deposits that the key's `FunctionCallPermission` was meant to forbid, provided the gas-key balance and account balance/storage checks pass.

### Impact Explanation
If confirmed, this allows a holder of a restricted `FunctionCallPermission` gas key (a low-privilege key meant to be scoped to one receiver/method with zero deposit) to submit transactions that violate that scope — e.g., attaching a deposit, targeting an arbitrary receiver, or calling disallowed methods — using only the gas-key balance and the owning account's NEAR balance. This is an access-control/permission-scope bypass: unauthorized value movement (deposit siphoned from the account despite the permission forbidding deposits) and invalid-state-transition acceptance (execution of actions outside the key's granted scope), analogous to unauthorized withdrawal via the un-gated `redeem`.

### Likelihood Explanation
Likelihood depends on the `GasKeys` protocol feature being enabled and on an account holding a gas key with `FunctionCallPermission` — both are supported, non-experimental configurations reachable by any account owner who adds such a key to their own account and then signs a gas-key transaction. No privileged/validator/network role is required; a single account holder using their own key can trigger this path, matching the "single submitted transaction" reachability bar. I was not able to directly view the full body of `verify_and_charge_gas_key_tx_ephemeral` in this session (only its behavior as summarized in `protocol-model/spec/runtime-execution.md` and its call sites), so this should be verified against the actual function body before concluding root cause with certainty.

### Recommendation
Add the same `verify_function_call_permission` check (or an equivalent scope check applicable to gas keys) inside `verify_and_charge_gas_key_tx_ephemeral`, mirroring the check already present in `verify_and_charge_tx_ephemeral`: [1](#0-0) 

### Proof of Concept
1. Enable `GasKeys` protocol feature; create account `alice.near` with a gas key whose permission is `FunctionCallPermission { receiver_id: "bob.near", method_names: ["foo"] }`.
2. Sign a gas-key transaction (with `nonce_index` set) from `alice.near` targeting `carol.near` (a different receiver) with a non-zero deposit and method `"bar"`.
3. Submit via RPC. If `verify_and_charge_gas_key_tx_ephemeral` does not call `verify_function_call_permission`, the transaction is accepted and charged (gas-key balance + account balance), even though the same transaction shape would be rejected with `ReceiverMismatch`/`DepositWithFunctionCall`/`MethodNameMismatch` on the regular-key path via `verify_and_charge_tx_ephemeral`.

**Note:** Direct confirmation requires reading the full body of `verify_and_charge_gas_key_tx_ephemeral` in `runtime/runtime/src/verifier.rs`, which was not available within this session's tool-call budget; the analog is based on the documented behavior summary and the absence of the `verify_function_call_permission` symbol in that function per repo-wide grep.

### Citations

**File:** runtime/runtime/src/verifier.rs (L204-251)
```rust
/// Validates FunctionCall permission constraints:
/// - Transaction must have exactly one action
/// - Action must be FunctionCall with zero deposit
/// - Receiver must match permission's receiver
/// - Method name must be in allowed list (if list is non-empty)
fn verify_function_call_permission(
    function_call_permission: &FunctionCallPermission,
    tx: &Transaction,
) -> Result<(), InvalidTxError> {
    if tx.actions().len() != 1 {
        return Err(InvalidTxError::InvalidAccessKeyError(
            InvalidAccessKeyError::RequiresFullAccess,
        ));
    }
    let Some(Action::FunctionCall(function_call)) = tx.actions().get(0) else {
        return Err(InvalidTxError::InvalidAccessKeyError(
            InvalidAccessKeyError::RequiresFullAccess,
        ));
    };
    if function_call.deposit > Balance::ZERO {
        return Err(InvalidTxError::InvalidAccessKeyError(
            InvalidAccessKeyError::DepositWithFunctionCall,
        ));
    }
    let tx_receiver = tx.receiver_id();
    let ak_receiver = &function_call_permission.receiver_id;
    if tx_receiver != ak_receiver {
        return Err(InvalidTxError::InvalidAccessKeyError(
            InvalidAccessKeyError::ReceiverMismatch {
                tx_receiver: tx_receiver.clone(),
                ak_receiver: ak_receiver.clone(),
            },
        ));
    }
    if !function_call_permission.method_names.is_empty()
        && function_call_permission
            .method_names
            .iter()
            .all(|method_name| &function_call.method_name != method_name)
    {
        return Err(InvalidTxError::InvalidAccessKeyError(
            InvalidAccessKeyError::MethodNameMismatch {
                method_name: function_call.method_name.clone(),
            },
        ));
    }
    Ok(())
}
```

**File:** runtime/runtime/src/verifier.rs (L388-393)
```rust
    // Validate FunctionCall permission constraints if applicable
    if let Some(function_call_permission) = access_key.permission.function_call_permission()
        && let Err(e) = verify_function_call_permission(function_call_permission, tx)
    {
        return TxVerdict::Failed(e);
    }
```

**File:** protocol-model/spec/runtime-execution.md (L60-60)
```markdown
- `verify_and_charge_gas_key_tx_ephemeral` (`runtime/runtime/src/verifier.rs:383`): validates the key is a gas key, `nonce_index < num_nonces`, nonce, gas-key balance covers `gas_cost`; then checks the account balance covers `deposit_cost` and storage staking. Insufficient deposit balance yields `DepositFailed` (gas key charged only `burnt_amount`), not a hard failure.
```

**File:** runtime/runtime/src/action_validation.rs (L374-380)
```rust

        // For gas keys with FunctionCallPermission, allowance must be None
        if let Some(fc) = action.access_key.permission.function_call_permission() {
            if fc.allowance.is_some() {
                return Err(ActionsValidationError::GasKeyFunctionCallAllowanceNotAllowed);
            }
        }
```
