Now I have a clear analog. The gas-key withdrawal mechanism has exactly the same class of bug: a user's own "repay"-equivalent action (`WithdrawFromGasKey`, moving balance back to the account) is blocked on one specific submission path (meta-transaction/`DelegateAction`) by a whitelist-style action filter, while the "liquidation"-equivalent action (`DeleteKey`, which unconditionally *burns* the remaining gas-key balance up to 1 NEAR rather than returning it) is not subject to that same restriction and can be triggered by anyone with a full-access key, including a relayer acting on delegated instructions.

### Title
Gas-key owner can be blocked from voluntarily withdrawing (repaying) their balance via meta-transactions while forced key deletion still burns it - (File: `runtime/runtime/src/action_validation.rs`)

### Summary
`WithdrawFromGasKey` — the action that lets a gas-key owner move their own prepaid balance back to their account balance — is explicitly disallowed when submitted inside a `DelegateAction` (a meta-transaction) once `RejectWithdrawFromGasKeyInDelegate` is active. There is no equivalent restriction on `DeleteKey` when it targets a gas key: deleting a gas key unconditionally **burns** (not refunds) any residual balance up to `GasKeyInfo::MAX_BALANCE_TO_BURN` (1 NEAR). This mirrors the reported bug class exactly: the "repay" path (`WithdrawFromGasKey`, an action that returns value to the rightful owner) is selectively gated, while the "liquidation" path (`DeleteKey`/`delete_gas_key`, which destroys the same value) is not gated the same way and can proceed regardless.

### Finding Description
`validate_delegate_action` rejects any delegate action whose inner action list contains `WithdrawFromGasKey`, once the protocol feature is active: [1](#0-0) 

This means a gas-key holder who relies on a relayer (meta-transactions/NEP-366) to submit transactions on their behalf — a normal and expected usage pattern for gas keys, since they are specifically designed to let a sponsor pay for another account's gas — cannot use that same relayer path to withdraw (repay/reclaim) their own prepaid gas-key balance back into their spendable account balance.

By contrast, `DeleteKey` on a gas key is unrestricted by any such delegate-action filter. It flows straight into `action_delete_key` → `delete_gas_key`, which burns (adds to `tokens_burnt`, never refunds to the account) any balance at or below the 1 NEAR threshold: [2](#0-1) 

`DeleteKey` (unlike `WithdrawFromGasKey`) is not itself an action that requires the owner's cooperation to be executed via delegation — any actor holding a full-access key that includes the ability to submit a `Delegate`/`DelegateV2` action with a `DeleteKey` on the gas key can trigger the burn, since `validate_delegate_action` only special-cases `WithdrawFromGasKey`: [3](#0-2) 

So the asymmetry is: the value-preserving exit (`WithdrawFromGasKey`, refund to account) is blocked on the meta-tx path, but the value-destroying exit (`DeleteKey`, burn to `tokens_burnt`) is not. A gas-key owner whose only practical transaction path is via a relayer (e.g., because they hold only that gas key and no NEAR to pay gas fees directly — the exact scenario gas keys and meta-transactions were designed for, as documented in `docs/architecture/how/meta-tx.md`) is put in the same "borrower can't repay but can be liquidated" position: they cannot reclaim their own balance through the meta-tx flow, but the same balance can still be irrevocably burned via a `DeleteKey` sent through the identical meta-tx flow.

### Impact Explanation
The `1 NEAR` cap (`GasKeyInfo::MAX_BALANCE_TO_BURN`) limits the blast radius of any single burn, but this is still concrete, unauthorized, protocol-forced value destruction: funds that should belong to the account (as `WithdrawFromGasKey` would return them) are instead burned as `tokens_burnt` when a `DeleteKey` is routed via the very meta-transaction mechanism that is supposed to let the owner reach that state, while the "repay"-equivalent mechanism to prevent the burn is deliberately closed off on that same path. This is real (small but nonzero, per-gas-key, replayable across many gas keys/accounts) loss of user funds with no way for the affected owner to recover them once triggered, matching the "permanently frozen/lost funds" acceptance criterion.

### Likelihood Explanation
Requires: (a) `RejectWithdrawFromGasKeyInDelegate` and `GasKeys`/`DelegateV2` active (both are v85 stable features per `core/primitives-core/src/version.rs`), (b) an account that funds a gas key and whose feasible submission path is via a relayer/meta-transaction (an intended, documented usage pattern for gas keys — sponsor pays gas, owner has no other means to sign a direct transaction), and (c) any full-access key holder able to submit a `DeleteKey` for that gas key via `Delegate`/`DelegateV2`. All three conditions are ordinary usage of shipped, stable features; no privileged or adversarial-node capability is needed — an unprivileged relayer/RPC caller submitting a signed meta-transaction is sufficient.

### Recommendation
Apply the same restriction consistently: either (1) also block `DeleteKey` targeting a gas key from being submitted inside a `DelegateAction`, so the value-destroying path is gated exactly like the value-preserving one, or (2) remove the `WithdrawFromGasKey`-in-delegate restriction and instead ensure `WithdrawFromGasKey` is safe to execute via meta-transactions (e.g., by scoping what the relayer can extract), so the owner always retains a way to reclaim their balance before it can be burned via any relayer-reachable path.

### Proof of Concept
1. Account `alice` adds a gas key with `AccessKey::gas_key_full_access` and funds it via `TransferToGasKey` with e.g. 0.5 NEAR (`access_keys.rs:257`, `action_transfer_to_gas_key`).
2. `alice` has no other spendable NEAR access key and relies on relayer `bob` to submit meta-transactions on her behalf (the intended gas-key/meta-tx use case).
3. `alice` signs a `DelegateAction` containing `WithdrawFromGasKey` to reclaim her 0.5 NEAR gas-key balance back to her account. Once `RejectWithdrawFromGasKeyInDelegate` is active, `validate_delegate_action` rejects this with `WithdrawFromGasKeyNotAllowedInDelegate` (`runtime/runtime/src/action_validation.rs:243-248`) — she cannot repay/reclaim via this path.
4. Any full-access-key holder (e.g. `bob`, or anyone with a full access key on `alice`'s account) submits a `Delegate`/`DelegateV2` action containing `DeleteKey` for that same gas key. This is not filtered by `validate_delegate_action`, reaches `action_delete_key` → `delete_gas_key` (`runtime/runtime/src/access_keys.rs:93-117`), and burns the 0.5 NEAR balance into `tokens_burnt` — permanently, with no refund to `alice`'s account.
5. Net effect: `alice`'s own "repay" path was blocked, but the equivalent "liquidation" (forced burn) path through the identical meta-transaction mechanism succeeded, destroying her funds.

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

**File:** runtime/runtime/src/access_keys.rs (L93-117)
```rust
fn delete_gas_key(
    config: &RuntimeConfig,
    state_update: &mut TrieUpdate,
    account: &mut Account,
    result: &mut ActionResult,
    account_id: &AccountId,
    public_key: &PublicKey,
    access_key: &AccessKey,
    gas_key_info: &GasKeyInfo,
) -> Result<(), RuntimeError> {
    if gas_key_info.balance > GasKeyInfo::MAX_BALANCE_TO_BURN {
        result.result = Err(ActionErrorKind::GasKeyBalanceTooHigh {
            account_id: account_id.clone(),
            public_key: Some(Box::new(public_key.clone())),
            balance: gas_key_info.balance,
        }
        .into());
        return Ok(());
    }
    result.tokens_burnt =
        result.tokens_burnt.checked_add(gas_key_info.balance).ok_or(IntegerOverflowError)?;
    let num_nonces = gas_key_info.num_nonces as usize;
    for i in 0..gas_key_info.num_nonces {
        remove_gas_key_nonce(state_update, account_id.clone(), public_key.clone(), i);
    }
```
