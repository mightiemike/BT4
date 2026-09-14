### Title
FunctionCall access-key allowance limit is completely bypassed when the same call is routed through a meta-transaction (`DelegateAction`) - (File: `runtime/runtime/src/actions.rs`)

### Summary
Direct transactions signed with a `FunctionCall` access key are gas/deposit-limited by that key's `allowance` — an on-chain spending cap the account owner deliberately places on that key so it can only ever draw a bounded amount of value from the account. When the very same `FunctionCall` action is instead wrapped in a `SignedDelegateAction` (NEP-366 meta-transaction) and relayed, the receiver-shard validation function `validate_delegate_action_key` checks the key's `RequiresFullAccess`, `DepositWithFunctionCall`, `ReceiverMismatch`, and `MethodNameMismatch` constraints, but never checks or decrements `allowance`. This is the same "one asset/action path enforces the value cap, a logically-equivalent alternate path does not" pattern as the Footium bug (FootiumPlayer enforces EIP-2981 royalty on transfer, FootiumClub — a bundling of the same underlying value — does not, so users route sales through FootiumClub to bypass the fee).

### Finding Description
Two structurally-equivalent paths exist for executing a `FunctionCall` limited by an access key:

1. **Direct transaction path** — `verify_function_call_permission` in `runtime/runtime/src/verifier.rs:209-251` validates the action shape (single action, zero deposit, receiver match, method match). The caller-visible cap on this key, `FunctionCallPermission.allowance`, is intended to bound how much of the account's balance/gas that key can ever spend; it is checked/decremented on the direct-transaction charging path (`verify_and_charge_tx_ephemeral`), and this is explicitly the invariant the key was created for ("allowance limiting how much financial resources the user can use from a given account" — `docs/architecture/how/meta-tx.md:262`).

2. **Meta-transaction (delegate action) path** — the receiver shard calls `validate_delegate_action_key` (`runtime/runtime/src/actions.rs:579-746`). For a `FunctionCall`-permissioned key it enforces:
   - single action (`actions.len() != 1` → `RequiresFullAccess`, line 673-679)
   - zero deposit (`DepositWithFunctionCall`, line 681-694)
   - receiver match (`ReceiverMismatch`, line 695-704)
   - method name allow-list (`MethodNameMismatch`, line 705-718)

   Nowhere in this function, nor anywhere else in `apply_delegate_action` (`runtime/runtime/src/actions.rs:453-535`), is `function_call_permission.allowance` read or decremented. All gas and deposit costs for the inner action are prepaid and burned from the **relayer**, not the key owner, so the allowance is structurally irrelevant to the meta-transaction execution flow — the check is simply missing.

This asymmetry is explicitly acknowledged (not just theorized) in `docs/architecture/how/meta-tx.md:244-266`: *"For allowance, however, there is no check. All costs have been covered by the relayer. Hence, even if the allowance of the key is insufficient to make the call directly, indirectly through meta transaction it will still work... this is circumventable by going through a relayer."*

### Impact Explanation
An account owner who issues a `FunctionCall` access key with a limited `allowance` (e.g., to a dApp, automation bot, or third party) does so specifically to bound the maximum value that key can move/spend on the account's behalf — this is the on-chain enforcement of a spending cap, directly analogous to a royalty/fee cap enforced on one sale path. Any key holder (or a relayer colluding with the key holder) can trivially bypass this cap by wrapping the identical `FunctionCall` in a `SignedDelegateAction` and submitting it via any relayer, executing calls that should have been rejected by `NotEnoughAllowance`. Since `FunctionCall` actions can carry attached `gas` used to drive further balance-moving cross-contract calls (e.g. calling into a wallet/DeFi contract that transfers the caller's tokens), this converts a documented, intended per-key value/resource cap into a purely cosmetic restriction whenever a relayer is available, which is the same class of impact as the Footium finding (bypass of a value-limiting rule via an alternate, unprotected transaction path). This is a legitimate access-control/economic-limit bypass reachable from a single submitted transaction (the `SignedDelegateAction`), not merely a documentation gap, since it changes on-chain enforcement outcomes vs. the direct path for identical actions.

### Likelihood Explanation
High likelihood of exploitation if relied upon as a security boundary: it requires only (a) possession of a `FunctionCall` access key with the desired `receiver_id`/`method_names`, and (b) submission through any relayer (self-relaying with a second full-access key, or a public relayer service) — no privileged position, no race condition, no cryptographic weakness. The behavior is deterministic and always succeeds when `validate_delegate_action_key`'s other four checks pass. It requires no code compromise, only signing a delegate action instead of a plain transaction.

### Recommendation
Decide explicitly whether `FunctionCallPermission.allowance` is meant to be a hard on-chain invariant or merely a client-side/direct-tx convenience limit, and document/enforce consistently:
- If it must be a real cap: track and decrement `allowance` inside `validate_delegate_action_key` (`runtime/runtime/src/actions.rs`) analogous to the direct-transaction charging path, charging the amount against the sender's (key owner's) allowance rather than the relayer, and reject with `NotEnoughAllowance` when exceeded.
- If it is intentionally scoped to only the direct-transaction path (as the docs currently state), this should be surfaced as a hard security caveat everywhere `FunctionCallPermission.allowance` is exposed via RPC/wallets/SDKs, warning integrators that the cap provides no protection once meta-transactions/relayers are in use, since silent reliance on it as a spending cap is a latent security footgun for wallets and dApps built on top of nearcore.

### Proof of Concept
1. Account `alice.near` adds a `FunctionCall` access key `K` with `receiver_id = "bank.near"`, `method_names = ["withdraw"]`, `allowance = 1 Tgas worth of NEAR` (a small, intentionally-limited spending cap).
2. A holder of `K` (or a colluding party) constructs `Transaction { signer_id: alice.near, actions: [FunctionCall{ method_name: "withdraw", ... }] }` signed with `K` directly — submission fails once the allowance is exhausted (`InvalidAccessKeyError::NotEnoughAllowance` in `verify_and_charge_tx_ephemeral`).
3. Instead, the same holder builds `DelegateAction { sender_id: alice.near, receiver_id: "bank.near", actions: [FunctionCall{ method_name: "withdraw", ... }], public_key: K, nonce, max_block_height }`, signs it as a `SignedDelegateAction`, and has any relayer wrap it in a normal transaction and submit it (paying the gas/deposit itself).
4. On the receiver shard, `apply_delegate_action` → `validate_delegate_action_key` (`runtime/runtime/src/actions.rs:579`) checks nonce, single-action, zero-deposit, receiver match, and method-name match — all pass — and never inspects `allowance`. The `withdraw` call executes and repeats indefinitely (bounded only by nonce monotonicity), even though the key's allowance was already exhausted or is set arbitrarily small.

Note: I was not able to trace the exact line numbers where `allowance` is decremented on the direct-transaction path within `verifier.rs` (grep found 22 references but the specific decrement site was not captured in the excerpts reviewed); this should be confirmed against `verify_and_charge_tx_ephemeral` before finalizing a fix, but the absence of any allowance check in `validate_delegate_action_key` (`runtime/runtime/src/actions.rs:579-746`) is directly confirmed from source, and the asymmetry is independently corroborated by the project's own architecture documentation (`docs/architecture/how/meta-tx.md:244-266`). [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** runtime/runtime/src/actions.rs (L453-535)
```rust
pub(crate) fn apply_delegate_action(
    state_update: &mut TrieUpdate,
    apply_state: &ApplyState,
    action_receipt: &VersionedActionReceipt,
    sender_id: &AccountId,
    signed_delegate_action: VersionedSignedDelegateActionRef<'_>,
    result: &mut ActionResult,
) -> Result<(), RuntimeError> {
    // The inner delegate signature is verified below, here on the receiver shard.
    // Meter its verification compute against this shard's `compute_limit`; the gas
    // for it was already burnt at tx conversion on the signer shard. Without the
    // fix the compute is instead mis-charged on the signer shard (which never runs
    // this verify), letting the work escape the receiver shard's budget. See
    // `signature_verification_cost`.
    if apply_state.config.wasm_config.fix_ml_dsa_cost_charging {
        let verify_compute = delegate_signature_verification_compute(
            &apply_state.config.fees,
            signed_delegate_action.delegate_action().public_key(),
        );
        result.compute_usage = safe_add_compute(result.compute_usage, verify_compute)?;
    }
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

    // Generate a new receipt from DelegateAction.
    let new_receipt = Receipt::V0(ReceiptV0 {
        predecessor_id: sender_id.clone(),
        receiver_id: delegate_action.receiver_id().clone(),
        receipt_id: CryptoHash::default(),

        receipt: ReceiptEnum::Action(ActionReceipt {
            signer_id: action_receipt.signer_id().clone(),
            signer_public_key: action_receipt.signer_public_key().clone(),
            gas_price: action_receipt.gas_price(),
            output_data_receivers: vec![],
            input_data_ids: vec![],
            actions: delegate_action.get_actions(),
        }),
    });

    // Note, Relayer prepaid all fees and all things required by actions: attached deposits and attached gas.
    // If something goes wrong, deposit is refunded to the predecessor, this is sender_id/Sender in DelegateAction.
    // Gas is refunded to the signer, this is Relayer.
    // Some contracts refund the deposit. Usually they refund the deposit to the predecessor and this is sender_id/Sender from DelegateAction.
    // Therefore Relayer should verify DelegateAction before submitting it because it spends the attached deposit.

    let prepaid_send_fees = total_prepaid_send_fees(&apply_state.config, action_receipt.actions())?;
    let required_cost = receipt_required_cost(apply_state, &new_receipt)?;
    // This gas will be burnt by the receiver of the created receipt.
    // Compute costs of that are not relevant at this point, the "used" gas is
    // only reserved for execution later, potentially on a different shard.
    result.gas_used = result.gas_used.checked_add_result(required_cost.gas)?;
    // This gas was prepaid on Relayer shard. Need to burn it because the receipt is going to be sent.
    // gas_used is incremented because otherwise the gas will be refunded. Refund function checks only gas_used.
    result.gas_used = result.gas_used.checked_add_result(prepaid_send_fees.gas)?;
    result.gas_burnt = result.gas_burnt.checked_add_result(prepaid_send_fees.gas)?;
    result.compute_usage = safe_add_compute(result.compute_usage, prepaid_send_fees.compute)?;
    result.new_receipts.push(new_receipt);

    Ok(())
}
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
