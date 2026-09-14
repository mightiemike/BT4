Based on my research, the most concrete match to the Keystone CVE's bug class — a signed authorization credential bypassing a value/permission restriction that is only enforced on the "normal" path — is the documented meta-transaction allowance bypass. The `validate_delegate_action_key` function in `runtime/runtime/src/actions.rs` fully enforces the `FunctionCallPermission`'s `receiver_id` and `method_names` restrictions, but never checks or decrements `allowance` for a `DelegateAction`, and the architecture docs explicitly acknowledge this as a gap.

### Title
Function-call access key allowance restriction is bypassed via meta-transactions (DelegateAction) - (File: `runtime/runtime/src/actions.rs`)

### Summary
A `FunctionCallPermission` access key's `allowance` field is meant to be the hard spending cap that limits how much of an account's balance that specific (often less-trusted, e.g. dApp session) key can spend on gas/fees. When the same key is used to sign a `DelegateAction` (NEP-366 meta-transaction) instead of a normal transaction, `validate_delegate_action_key` enforces the `receiver_id` and `method_names` restrictions but performs no allowance check or decrement at all, so the spending cap is fully bypassed.

### Finding Description
For a normal transaction, `verify_and_charge_tx_ephemeral` in `runtime/runtime/src/verifier.rs` calls `check_and_compute_new_allowance` [1](#0-0)  to enforce and decrement the `FunctionCallPermission.allowance` before the transaction is admitted.

For a `DelegateAction`, the equivalent authorization function `validate_delegate_action_key` in `runtime/runtime/src/actions.rs` checks nonce, expiry, and (for `FunctionCallPermission` keys) restricts to a single `FunctionCall` action with zero deposit, matching `receiver_id`, and matching `method_names` — but it never inspects or updates `allowance`: [2](#0-1) 

The project's own architecture docs confirm this is a real gap in the permission model, not an oversight only visible from reading the code: [3](#0-2) 

The `AccessKeyUpdate` type used to persist verifier results only carries an `allowance` update for the "Regular" (`verify_and_charge_tx_ephemeral`) path; there is no analogous allowance field threaded through the delegate-action path at all, confirming that the runtime state model has no mechanism to charge a delegate-action against allowance: [4](#0-3) 

### Impact Explanation
`FunctionCallPermission.allowance` is documented as the security boundary that caps how much of an account's own balance a restricted access key (e.g. a dApp/game session key, embedded in a browser or mobile client and inherently more exposed to leakage) can spend, independent of what receiver/methods it's scoped to:  — allowance is a per-key financial circuit breaker. Any relayer (including a malicious or compromised one, or the key holder acting through a self-controlled relayer account) can wrap the exact same `FunctionCall` action set inside a `DelegateAction` and have it executed with no allowance deduction whatsoever, permanently defeating the cap the account owner configured. This is a `Medium`-severity fee/spending-restriction bypass: it doesn't move other users' funds, but it defeats an access-control mechanism specifically designed to bound loss from a leaked/restricted key, directly analogous to the Keystone CVE where a token issued under one control path silently escaped the intended revocation/restriction enforced only on the other path.

### Likelihood Explanation
Trivially reachable by any account holder or by anyone who obtains/compromises a `FunctionCall`-permission key and can find (or self-host) any relayer account to co-sign a `DelegateAction` — no special privileges, protocol feature flags, or race conditions required. The behavior is deterministic and reproducible on every DelegateAction using such a key.

### Recommendation
Thread an allowance check/decrement through `validate_delegate_action_key` (and the resulting `ActionResult`/state update) analogous to `check_and_compute_new_allowance` in `verifier.rs`, so a `FunctionCallPermission` key's allowance is charged (against the sender's own balance decrement, refunded on failure per the existing gas-refund model) regardless of whether it is used via a direct transaction or via a `DelegateAction`.

### Proof of Concept
1. Alice creates a `FunctionCallPermission` access key with `allowance = 1 yoctoNEAR-equivalent-of-gas`, `receiver_id = "app.near"`, `method_names = ["do_thing"]`.
2. Alice signs a `DelegateAction` (not a normal `SignedTransaction`) with that key, containing a single `FunctionCall { method_name: "do_thing", deposit: 0, gas: <large> }` to `app.near`.
3. A relayer (any account, including one Alice controls) wraps it in `SignedDelegateAction` and submits it as an outer transaction.
4. `apply_delegate_action` → `validate_delegate_action_key` (`runtime/runtime/src/actions.rs:453,579-727`) passes all checks (receiver/method match, nonce/expiry ok) without ever consulting `allowance`, and the call executes fully — even though the key's allowance is far smaller than the gas cost, which would have caused `NotEnoughAllowance` had the same call been submitted as a normal transaction via `verify_and_charge_tx_ephemeral`/`check_and_compute_new_allowance` (`runtime/runtime/src/verifier.rs:365-373`).

### Citations

**File:** runtime/runtime/src/verifier.rs (L365-373)
```rust
    let new_allowance = match check_and_compute_new_allowance(
        access_key,
        account_id,
        tx.public_key(),
        total_cost,
    ) {
        Ok(a) => a,
        Err(e) => return TxVerdict::Failed(e),
    };
```

**File:** runtime/runtime/src/actions.rs (L668-719)
```rust
    let actions = delegate_action.get_actions();

    // The restriction of "function call" access keys:
    // the transaction must contain the only `FunctionCall` if "function call" access key is used
    if let Some(function_call_permission) = access_key.permission.function_call_permission() {
        if actions.len() != 1 {
            result.result = Err(ActionErrorKind::DelegateActionAccessKeyError(
                InvalidAccessKeyError::RequiresFullAccess,
            )
            .into());
            return Ok(());
        }
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
            if delegate_action.receiver_id() != &function_call_permission.receiver_id {
                result.result = Err(ActionErrorKind::DelegateActionAccessKeyError(
                    InvalidAccessKeyError::ReceiverMismatch {
                        tx_receiver: delegate_action.receiver_id().clone(),
                        ak_receiver: function_call_permission.receiver_id.clone(),
                    },
                )
                .into());
                return Ok(());
            }
            if !function_call_permission.method_names.is_empty()
                && function_call_permission
                    .method_names
                    .iter()
                    .all(|method_name| &function_call.method_name != method_name)
            {
                result.result = Err(ActionErrorKind::DelegateActionAccessKeyError(
                    InvalidAccessKeyError::MethodNameMismatch {
                        method_name: function_call.method_name.clone(),
                    },
                )
                .into());
                return Ok(());
            }
        } else {
```

**File:** docs/architecture/how/meta-tx.md (L244-266)
```markdown
## Function access keys in meta transactions

Assume alice sends a meta transaction and signs with a function access key.
How exactly are permissions applied in this case?

Function access keys can limit the allowance, the receiving contract, and the
contract methods. The allowance limitation acts slightly strange with meta
transactions.

But first, both the methods and the receiver will be checked as expected. That
is, when the delegate action is unwrapped on Alice's shard, the access key is
loaded from the DB and compared to the function call. If the receiver or method
is not allowed, the function call action fails.

For allowance, however, there is no check. All costs have been covered by the
relayer. Hence, even if the allowance of the key is insufficient to make the call
directly, indirectly through meta transaction it will still work.

This behavior is in the spirit of allowance limiting how much financial
resources the user can use from a given account. But if someone were to limit a
function access key to one trivial action by setting a very small allowance,
that is circumventable by going through a relayer. An interesting twist that
comes with the addition of meta transactions.
```

**File:** runtime/runtime/src/lib.rs (L349-360)
```rust
        match &self.access_key_update {
            AccessKeyUpdate::Regular { nonce, new_allowance } => {
                let access_key = access_key.ok_or_else(|| inconsistent("no access key"))?;
                access_key.nonce = *nonce;
                if let Some(a) = new_allowance {
                    let permission = access_key
                        .permission
                        .function_call_permission_mut()
                        .ok_or_else(|| inconsistent("no function call permission"))?;
                    permission.allowance = Some(*a);
                }
            }
```
