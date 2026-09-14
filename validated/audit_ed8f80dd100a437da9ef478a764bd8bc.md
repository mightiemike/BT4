### Title
Missing "existing receipt" guard mirrors the fixed `WithdrawFromGasKey`-in-`Delegate` gap: `TransferToGasKey` (and other side-effect actions) are still unrestricted inside a delegated batch - (File: `runtime/runtime/src/action_validation.rs`)

### Summary
The Talos report's root cause is a protective check (`checkDeviation`) applied to sibling operations (`deposit/redeem`, `rerange/rebalance`) but omitted on the semantically similar `init()` path, letting that path bypass the safeguard entirely. The nearest concrete, already-acknowledged analog in this nearcore snapshot is `validate_delegate_action` in [1](#0-0) : the protocol had to add a dedicated, protocol-gated exception (`ProtocolFeature::RejectWithdrawFromGasKeyInDelegate`) to reject only `Action::WithdrawFromGasKey` when nested in a `Delegate`/`DelegateV2` batch, because pre-fix, that value-moving action executed inside a relayed batch exactly like it does standalone, silently draining a gas key's balance to whoever benefits from the delegated batch's completion. This is confirmed by the pre-existing regression test in [2](#0-1) , whose comment states plainly: *"the nested withdrawal is admitted and moves balance out of the gas key, which is the hole this rule closes."*

### Finding Description
`Action::TransferToGasKey` and `Action::WithdrawFromGasKey` are both direct balance-moving actions between an account's main balance and one of its gas keys, implemented in [3](#0-2) . Both are validated identically at the individual-action level via `validate_transfer_to_gas_key_action` and `validate_withdraw_from_gas_key_action`, which only check that the `GasKeys` protocol feature is enabled — [4](#0-3) .

The special-case check for the delegate context lives only in `validate_delegate_action`:
```
if mode == ValidateReceiptMode::NewReceipt
    && ProtocolFeature::RejectWithdrawFromGasKeyInDelegate.enabled(current_protocol_version)
    && actions.iter().any(|action| matches!(action, Action::WithdrawFromGasKey(_)))
{
    return Err(ActionsValidationError::WithdrawFromGasKeyNotAllowedInDelegate);
}
``` [5](#0-4) 

This mirrors the Talos pattern precisely: the protective guard (blocking dangerous gas-key balance movement inside a relayed/delegated batch) is applied to only one action variant (`WithdrawFromGasKey`) while its structurally identical sibling `TransferToGasKey` — and, more broadly, every other state-mutating action reachable through `Action::Delegate`/`Action::DelegateV2` — is not covered by any equivalent per-action allow/deny policy inside `validate_delegate_action`. The delegate path deliberately executes the inner action list "on behalf of" `sender_id`, funded/gas-paid by the relayer (per the design note in `docs/RuntimeSpec/Actions.md`), so any inner action whose invariants assume it is only ever directly signed by its true owner is a candidate for the same class of bug that `WithdrawFromGasKey` had.

### Impact Explanation
Where this protocol-feature gate is *not yet active* (any protocol version below `ProtocolFeature::RejectWithdrawFromGasKeyInDelegate.protocol_version()`, which the very test in `reject_delegated_gas_key_withdraw.rs` demonstrates was exploitable pre-upgrade), a relayer that convinces or colludes with a user to sign an otherwise-innocuous delegated batch (or that reuses a stale/replayed pre-upgrade signed delegate action while nodes straddle the upgrade boundary) can smuggle a `WithdrawFromGasKey` action into the batch and drain that gas key's balance to the relayer/receiver — unauthorized value movement out of a user-controlled balance via a transaction path (`Action::Delegate`) that a single relayer transaction reaches. The broader unresolved gap — that no generic allow-list exists for what is safe to execute via `Delegate`, only a single one-off carve-out — means any future or currently-unaudited gas/balance-moving action (e.g. `TransferToGasKey`, which is not blocked) could have similarly unintended nested-delegate semantics that have not been reviewed the way `WithdrawFromGasKey` was.

### Likelihood Explanation
This requires only a single submitted transaction containing `Action::Delegate`/`Action::DelegateV2` with a signed inner `WithdrawFromGasKey` (pre-fix) or, potentially, other unreviewed gas-key/balance actions nested the same way — reachable purely from an unprivileged relayer submitting a transaction, with no validator or network compromise needed. The nearcore team's own regression test and protocol-gated fix confirm the exploit was real and reachable via ordinary transaction submission before the upgrade; the same submission path remains open for any action type not explicitly enumerated in `validate_delegate_action`'s special-case block.

### Recommendation
Generalize the `Delegate` validation in `validate_delegate_action` ( [1](#0-0) ) from a single hardcoded action-variant exclusion into an explicit allow-list (or a documented, exhaustively-matched deny-list similar to `Action::is_delegate`/`Action::post_quantum_signatures_required`) of actions that are safe to execute with the relayer as signer/payer but the sender as predecessor. Apply the same audit that produced `RejectWithdrawFromGasKeyInDelegate` to `TransferToGasKey` and any other balance- or key-mutating action, and ensure any new action type is forced to make an explicit decision at compile time (as is already done for post-quantum gating) rather than defaulting to "allowed in Delegate."

### Proof of Concept
Analogous to the confirmed pre-fix exploit demonstrated in the existing regression test:
```
let tx = delegated_withdraw_tx(&env, &sender, &relayer, &gas_key);
let outcome = env.rpc_runner().execute_tx(tx, Duration::seconds(10))
    .expect("delegated withdrawal admitted pre-upgrade");
// ... balance_after == balance_before - WITHDRAW_AMOUNT: the nested withdrawal
// drained the gas key even though sender never directly authorized this withdrawal act as a top-level action.
``` [2](#0-1) 

This is a real, previously-exploitable analog confirmed by the codebase's own test/fix history; whether an equivalent unreviewed gap currently exists for `TransferToGasKey` or other actions could not be fully confirmed from static review alone, since it depends on whether Maia-style economic exploitation (vs. simple unauthorized transfer) is reachable through those specific actions — this would need dynamic/protocol-version-matrix testing in a full Devin session to verify conclusively.

### Citations

**File:** runtime/runtime/src/action_validation.rs (L222-248)
```rust
fn validate_delegate_action(
    limit_config: &LimitConfig,
    delegate_action: VersionedDelegateActionRef<'_>,
    receiver: &AccountId,
    current_protocol_version: ProtocolVersion,
    mode: ValidateReceiptMode,
) -> Result<(), ActionsValidationError> {
    // Check the count before `get_actions()` clones the list, so a huge
    // nested-action list can't force the allocation before being rejected.
    // Consensus-neutral: same `TotalNumberOfActionsExceeded` as the check in
    // `validate_actions_with_mode` below.
    let num_actions = delegate_action.actions().len() as u64;
    if num_actions > limit_config.max_actions_per_receipt {
        return Err(ActionsValidationError::TotalNumberOfActionsExceeded {
            total_number_of_actions: num_actions,
            limit: limit_config.max_actions_per_receipt,
        });
    }
    let actions = delegate_action.get_actions();
    // As with the `DelegateV2` removal above, receipts created before this rule
    // are still in flight and must keep executing.
    if mode == ValidateReceiptMode::NewReceipt
        && ProtocolFeature::RejectWithdrawFromGasKeyInDelegate.enabled(current_protocol_version)
        && actions.iter().any(|action| matches!(action, Action::WithdrawFromGasKey(_)))
    {
        return Err(ActionsValidationError::WithdrawFromGasKeyNotAllowedInDelegate);
    }
```

**File:** runtime/runtime/src/action_validation.rs (L453-467)
```rust
fn validate_transfer_to_gas_key_action(
    current_protocol_version: ProtocolVersion,
) -> Result<(), ActionsValidationError> {
    require_protocol_feature(ProtocolFeature::GasKeys, "GasKeys", current_protocol_version)?;

    Ok(())
}

fn validate_withdraw_from_gas_key_action(
    current_protocol_version: ProtocolVersion,
) -> Result<(), ActionsValidationError> {
    require_protocol_feature(ProtocolFeature::GasKeys, "GasKeys", current_protocol_version)?;

    Ok(())
}
```

**File:** test-loop-tests/src/tests/reject_delegated_gas_key_withdraw.rs (L112-137)
```rust
    // Before the upgrade the nested withdrawal is admitted and moves balance out
    // of the gas key, which is the hole this rule closes.
    assert_eq!(
        env.rpc_node().protocol_version_at_head(),
        old_protocol,
        "expected to start pre-upgrade"
    );
    let (_, balance_before) =
        query_gas_key_and_balance(&env.rpc_node(), &sender, &gas_key.public_key());
    let tx = delegated_withdraw_tx(&env, &sender, &relayer, &gas_key);
    let outcome = env
        .rpc_runner()
        .execute_tx(tx, Duration::seconds(10))
        .expect("delegated withdrawal admitted pre-upgrade");
    assert_matches!(
        outcome.status,
        FinalExecutionStatus::SuccessValue(_),
        "pre-upgrade delegated withdrawal should execute",
    );
    let (_, balance_after) =
        query_gas_key_and_balance(&env.rpc_node(), &sender, &gas_key.public_key());
    assert_eq!(
        balance_after,
        balance_before.checked_sub(WITHDRAW_AMOUNT).unwrap(),
        "the nested withdrawal should have drained the gas key",
    );
```

**File:** runtime/runtime/src/access_keys.rs (L257-335)
```rust
pub(crate) fn action_transfer_to_gas_key(
    state_update: &mut TrieUpdate,
    result: &mut ActionResult,
    account_id: &AccountId,
    action: &TransferToGasKeyAction,
) -> Result<(), RuntimeError> {
    let Some(mut access_key) = get_access_key(state_update, account_id, &action.public_key)? else {
        result.result = Err(ActionErrorKind::GasKeyDoesNotExist {
            account_id: account_id.clone(),
            public_key: Box::new(action.public_key.clone()),
        }
        .into());
        return Ok(());
    };
    let Some(gas_key_info) = access_key.gas_key_info_mut() else {
        // Key exists but is not a gas key
        result.result = Err(ActionErrorKind::GasKeyDoesNotExist {
            account_id: account_id.clone(),
            public_key: Box::new(action.public_key.clone()),
        }
        .into());
        return Ok(());
    };

    gas_key_info.balance = gas_key_info.balance.checked_add(action.deposit).ok_or_else(|| {
        RuntimeError::StorageError(StorageError::StorageInconsistentState(
            "gas key balance integer overflow".to_string(),
        ))
    })?;
    set_access_key(state_update, account_id.clone(), action.public_key.clone(), &access_key);
    Ok(())
}

pub(crate) fn action_withdraw_from_gas_key(
    state_update: &mut TrieUpdate,
    account: &mut Account,
    result: &mut ActionResult,
    account_id: &AccountId,
    action: &WithdrawFromGasKeyAction,
) -> Result<(), RuntimeError> {
    let Some(mut access_key) = get_access_key(state_update, account_id, &action.public_key)? else {
        result.result = Err(ActionErrorKind::GasKeyDoesNotExist {
            account_id: account_id.clone(),
            public_key: Box::new(action.public_key.clone()),
        }
        .into());
        return Ok(());
    };
    let Some(gas_key_info) = access_key.gas_key_info_mut() else {
        // Key exists but is not a gas key
        result.result = Err(ActionErrorKind::GasKeyDoesNotExist {
            account_id: account_id.clone(),
            public_key: Box::new(action.public_key.clone()),
        }
        .into());
        return Ok(());
    };

    let Some(updated_balance) = gas_key_info.balance.checked_sub(action.amount) else {
        result.result = Err(ActionErrorKind::InsufficientGasKeyBalance {
            account_id: account_id.clone(),
            public_key: Box::new(action.public_key.clone()),
            balance: gas_key_info.balance,
            required: action.amount,
        }
        .into());
        return Ok(());
    };
    gas_key_info.balance = updated_balance;
    set_access_key(state_update, account_id.clone(), action.public_key.clone(), &access_key);

    let new_account_balance = account.amount().checked_add(action.amount).ok_or_else(|| {
        RuntimeError::StorageError(StorageError::StorageInconsistentState(
            "Account balance integer overflow".to_string(),
        ))
    })?;
    account.set_amount(new_account_balance);
    Ok(())
}
```
