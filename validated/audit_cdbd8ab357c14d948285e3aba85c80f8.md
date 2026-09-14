Based on my research, the strongest reachable analog to the reported bug class — a caller-set restriction (`sell.islisted`) that gates an action being silently bypassed through an alternate code path that doesn't re-check it — is the **FunctionCall access key allowance bypass in meta-transactions (`DelegateAction`)**.

### Title
FunctionCall access key `allowance` restriction is never checked when the key signs a meta-transaction, letting a relayer execute calls the key owner explicitly capped - (File: `runtime/runtime/src/actions.rs`)

### Summary
A `FunctionCallPermission` access key carries an `allowance` field that the account owner sets specifically to cap how much of the account's balance that key can spend on gas/fees for direct transactions. When the same key is instead used to sign a `DelegateAction` (NEP-366 meta-transaction), the runtime's delegate-key validation path checks receiver, method name, deposit and nonce, but never checks or decrements `allowance`. This mirrors the external report's root cause exactly: a restriction flag set by an account/asset owner (`sell.islisted` / `allowance`) is enforced on one code path (`setbidtobuy` direct purchase / direct `FunctionCall` transaction) but is silently skipped on another reachable path (delegated/meta buy call).

### Finding Description
`validate_delegate_action_key` in [1](#0-0)  loads the sender's access key and validates nonce, receiver, deposit and method name for `FunctionCallPermission` keys, but the block that inspects `function_call_permission` at [2](#0-1)  contains no allowance check or decrement anywhere in this function.

By contrast, the direct-transaction path explicitly enforces and decrements allowance: `check_and_compute_new_allowance` is called from `verify_and_charge_tx_ephemeral` as documented in [3](#0-2) .

This asymmetry is explicitly acknowledged in the architecture docs: [4](#0-3)  states "For allowance, however, there is no check... even if the allowance of the key is insufficient to make the call directly, indirectly through meta transaction it will still work... if someone were to limit a function access key to one trivial action by setting a very small allowance, that is circumventable by going through a relayer." This is also demonstrated by the integration test `meta_tx_fn_call_access_key_insufficient_allowance` at [5](#0-4) , whose own comment says "this should still succeed because we use the gas of the relayer, not of the access key."

### Impact Explanation
Any account owner who mitigates the blast radius of a leaked/limited-trust `FunctionCall` access key by setting a small `allowance` (the *only* NEP-defined mechanism for capping what that key can spend/authorize) loses that guarantee the moment the key is used to sign a `DelegateAction` through any relayer. The key's other real restrictions (`receiver_id`, `method_names`, zero-deposit) are still enforced, so this is not unauthorized fund transfer out of the account directly, but it is a **fee/spending-restriction bypass**: an authorization boundary the protocol advertises (`allowance`) is unconditionally circumvented on a code path reachable by any unprivileged meta-transaction sender/relayer, executing calls the signer was explicitly configured to be unable to trigger via that key.

### Likelihood Explanation
High reachability: any account holding a `FunctionCall` access key with a nonzero-but-limited allowance is affected as soon as any relayer wraps its authorized method call in a `DelegateAction`; no privileged role, validator collusion, or race condition is required — a normal RPC caller/relayer acting entirely within protocol rules triggers it every time.

### Recommendation
Either enforce/decrement `allowance` inside `validate_delegate_action_key` for `FunctionCall`-permissioned keys (charging the relayer or rejecting the delegate action when the key's allowance is insufficient), or, since this is currently a deliberate design trade-off, treat it as intended behavior and ensure it is unambiguously documented anywhere `allowance` semantics are exposed to wallet/dApp developers (RPC docs, `AccessKey` schema descriptions) so users do not rely on `allowance` as a hard cap once meta-transactions are in play.

### Proof of Concept
`integration-tests/src/tests/features/delegate_action.rs::meta_tx_fn_call_access_key_insufficient_allowance` already reproduces this: it creates a `FunctionCall` access key with `allowance = 1 yoctoNEAR` (insufficient to cover even 1 gas unit), signs a `DelegateAction` calling a method restricted to that key, and asserts the call **succeeds** and executes the restricted method, explicitly commented "this should still succeed because we use the gas of the relayer, not of the access key" — confirming the allowance restriction is bypassed end-to-end. [5](#0-4)

### Citations

**File:** runtime/runtime/src/actions.rs (L574-600)
```rust
/// Validate access key which was used for signing DelegateAction:
///
/// - Checks whether the access key is present fo given public_key and sender_id.
/// - Validates nonce and updates it if it's ok.
/// - Validates access key permissions.
fn validate_delegate_action_key(
    state_update: &mut TrieUpdate,
    apply_state: &ApplyState,
    delegate_action: VersionedDelegateActionRef<'_>,
    result: &mut ActionResult,
) -> Result<(), RuntimeError> {
    let sender_id = delegate_action.sender_id();
    let public_key = delegate_action.public_key();
    // 'sender_id' account existence must be checked by a caller
    let mut access_key = match get_access_key(state_update, sender_id, public_key)? {
        Some(access_key) => access_key,
        None => {
            result.result = Err(ActionErrorKind::DelegateActionAccessKeyError(
                InvalidAccessKeyError::AccessKeyNotFound {
                    account_id: sender_id.clone(),
                    public_key: public_key.clone().into(),
                },
            )
            .into());
            return Ok(());
        }
    };
```

**File:** runtime/runtime/src/actions.rs (L668-727)
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
            // There should Action::FunctionCall when "function call" permission is used
            result.result = Err(ActionErrorKind::DelegateActionAccessKeyError(
                InvalidAccessKeyError::RequiresFullAccess,
            )
            .into());
            return Ok(());
        }
    };
```

**File:** protocol-model/spec/accounts-keys.md (L57-58)
```markdown
4. **Allowance**: `check_and_compute_new_allowance` (`verifier.rs:240`) — for a FunctionCall key with a finite `allowance`, subtracts `total_cost`; underflow → `NotEnoughAllowance` (`:252`). Allowance is decremented in lockstep with the account balance.
5. **Regular path** (`verify_and_charge_tx_ephemeral`, `verifier.rs:272`): asserts the tx has no `nonce_index` (`:284`); if the key is actually a gas key it is rejected (`InvalidNonceIndex { tx_nonce_index: None }`, `:290`) — gas keys *must* use the gas-key path. Verifies nonce, checks balance (`NotEnoughBalance`, `:315`), debits `total_cost` (`:324`), runs allowance + `check_storage_stake` (`:345`) + FunctionCall permission (`:359`), and returns `AccessKeyUpdate::Regular { nonce: tx_nonce, new_allowance }` (`:372`).
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

**File:** integration-tests/src/tests/features/delegate_action.rs (L395-431)
```rust
/// Call a function in a meta tx where the user only has access through a
/// function call access that has too little allowance left.
#[test]
fn meta_tx_fn_call_access_key_insufficient_allowance() {
    let sender = bob_account();
    let relayer = alice_account();
    let receiver = carol_account();

    // 1 yocto near, that's less than 1 gas unit
    let initial_allowance = Balance::from_yoctonear(1);
    let signer = create_user_test_signer(&sender);

    let node = setup_with_access_key(
        &relayer,
        &receiver,
        &sender,
        signer.public_key(),
        initial_allowance,
        TEST_METHOD,
    );

    let actions = vec![log_something_fn_call()];
    // this should still succeed because we use the gas of the relayer, not of the access key
    let outcome = check_meta_tx_fn_call(
        &node,
        actions,
        TEST_METHOD_LEN,
        Balance::ZERO,
        sender,
        relayer,
        receiver,
    );

    // Check that the function call was executed as expected
    let fn_call_logs = &outcome.receipts_outcome[1].outcome.logs;
    assert_eq!(fn_call_logs, &vec!["hello".to_owned()]);
}
```
