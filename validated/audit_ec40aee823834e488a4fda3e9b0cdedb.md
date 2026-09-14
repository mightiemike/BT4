### Title
Meta-transaction (`DelegateAction`) relayer submissions can be forced to fail via signed-payload front-running, causing guaranteed gas-fee loss to the relayer - ([File: runtime/runtime/src/actions.rs])

### Summary
NEAR's meta-transaction mechanism (NEP-366) lets a user ("Alice") sign a `DelegateAction`/`SignedDelegateAction` off-chain and hand it to a relayer, who wraps it in a transaction and pays gas on Alice's behalf. Exactly like the Angstrom `PermitSubmitterHook` bug, the signed payload is nonce-gated against on-chain state that anyone can consume ahead of the relayer: since the `SignedDelegateAction` is public once broadcast (it must be forwarded on-chain inside the relayer's transaction, so it is visible to every node that receives that transaction before/while it is applied), any unprivileged third party can copy the exact same signed bytes, wrap them in their own transaction, and get it applied first. This advances Alice's access-key nonce, so when the original relayer's transaction is later applied, `apply_delegate_action` fails with `DelegateActionInvalidNonce`, exactly as `PermitSubmitterHook.compose()` reverts when the permit signature was already consumed.

### Finding Description
`apply_delegate_action` verifies the inner delegate action and then calls `validate_delegate_action_key`, which checks the delegate action's nonce against the sender's (Alice's) on-chain access-key nonce and rejects it if it is not strictly greater: [1](#0-0) 

The nonce check operates purely on state derivable from the `SignedDelegateAction` itself (sender id + public key), with no binding to which relayer/transaction delivers it: [2](#0-1) 

Because a `SignedDelegateAction` is forwarded as-is inside the relayer's transaction into a cross-shard receipt (i.e., it becomes visible network-wide once the relayer submits it), anyone can extract that signed payload and resubmit it themselves in a competing transaction. If that competing transaction lands first, it consumes Alice's access-key nonce via the same code path, and the original relayer's transaction subsequently fails validation with `DelegateActionInvalidNonce`: [3](#0-2) 

When this failure occurs, the relayer has already purchased gas for the transaction and burned the `SEND`/`EXEC` costs for delivering the delegate action; only unused gas is refunded, and even that refund is reduced by the NEP-536 5% penalty, which is charged regardless of whether the receipt succeeded or failed: [4](#0-3) 

This mirrors the Angstrom report precisely: a signature-gated action (`permit()` / `DelegateAction`) that anyone can "consume" by front-running with the identical signed data, forcing the legitimate submitter's transaction to fail after they've already committed to paying for it.

### Impact Explanation
Any relayer operating a meta-transaction service is exposed to griefing: an unprivileged observer can force the relayer's transaction to fail deterministically and repeatedly, causing the relayer to permanently lose the burnt `SEND`/`EXEC` gas costs and the NEP-536 refund penalty on every such attempt, with no compensating benefit. This is a direct, protocol-level fee loss inflicted by an unprivileged third party on a legitimate transaction submitter — the same "medium" impact class as the Angstrom report (forced revert/gas loss via signature front-running), not merely a resource-only nuisance, since it can be used to reliably deny service to a relayer or drain relayer funds over repeated attempts. The project's own documentation acknowledges the underlying nonce-sharing tension but frames it only in the context of a rejected alternative design (relayer-nonce checks), not the front-running griefing exposed via the sender-nonce design that was actually shipped: [5](#0-4) 

### Likelihood Explanation
The attack requires only observing a pending relayer transaction (readily available once broadcast/gossiped) and racing a copy of the embedded `SignedDelegateAction` into a transaction of one's own — no special privileges, validator status, or protocol bug beyond intended nonce semantics are needed. This is comparable in complexity to the mempool front-running described in the original report and is realistically exploitable by any RPC-connected actor.

### Recommendation
Consider binding a `DelegateAction`'s validity to the specific relayer transaction that is meant to carry it (e.g., requiring the relayer's account/public key as part of the signed payload, or allowing an idempotent/replay-safe execution path so a duplicate submission does not cause the intended relayer's transaction to fail), and/or making the failure of `apply_delegate_action` due to nonce reuse cheaper for the honest relayer (e.g., exempting a front-run-caused invalid-nonce failure from the NEP-536 refund penalty).

### Proof of Concept
1. Alice signs a `SignedDelegateAction` with nonce `N+1` and hands it to Relayer R, who wraps it in transaction `T_R` and submits it.
2. Attacker M observes `T_R` (via RPC/gossip) before it is applied, extracts the identical `SignedDelegateAction`, and wraps it in `T_M`, submitting it with a higher priority/earlier inclusion.
3. `T_M` is applied first; `apply_delegate_action`/`validate_delegate_action_key` advances Alice's access key nonce to `N+1`.
4. `T_R` is then applied; `validate_delegate_action_key` now sees `delegate_nonce.nonce() <= current_nonce` and sets `ActionErrorKind::DelegateActionInvalidNonce`, per [1](#0-0) , causing R's transaction to fail after R has already paid the associated gas costs, per the refund/penalty accounting in [4](#0-3) .

### Citations

**File:** runtime/runtime/src/actions.rs (L453-497)
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
```

**File:** runtime/runtime/src/actions.rs (L579-600)
```rust
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

**File:** runtime/runtime/src/actions.rs (L648-655)
```rust
    if delegate_nonce.nonce() <= current_nonce {
        result.result = Err(ActionErrorKind::DelegateActionInvalidNonce {
            delegate_nonce: delegate_nonce.nonce(),
            ak_nonce: current_nonce,
        }
        .into());
        return Ok(());
    }
```

**File:** runtime/runtime/src/lib.rs (L1303-1329)
```rust
        let deposit_refund = if result.result.is_err() { total_deposit } else { Balance::ZERO };
        let gross_gas_refund = if result.result.is_err() {
            prepaid_gas
                .checked_add(prepaid_exec_gas.gas)
                .ok_or(IntegerOverflowError)?
                .checked_sub(result.gas_burnt)
                .unwrap()
        } else {
            prepaid_gas
                .checked_add(prepaid_exec_gas.gas)
                .ok_or(IntegerOverflowError)?
                .checked_sub(result.gas_used)
                .unwrap()
        };

        // NEP-536 also adds a penalty to gas refund.
        let refund_penalty: Gas = config.fees.gas_penalty_for_gas_refund(gross_gas_refund);
        let penalty_gas_price = if ProtocolFeature::AccountCostIncrease.enabled(protocol_version) {
            gas_burn_price
        } else {
            gas_purchase_price
        };
        let refund_penalty_amount = safe_gas_to_balance(penalty_gas_price, refund_penalty)?;

        // Refund for the leftover gas that was not used by this receipt.
        let unused_gas_balance_refund = safe_gas_to_balance(gas_purchase_price, gross_gas_refund)?
            .saturating_sub(refund_penalty_amount);
```

**File:** docs/architecture/how/meta-tx.md (L161-168)
```markdown
An alternative solution discussed is to do NONCE checks on the relayer's access
key. This prevents replay attacks and allows implicit accounts to be used in
meta transactions without even initializing them. The downside is that meta
transactions share the same NONCE counter(s). That means, a meta transaction
sent by Bob may invalidate a meta transaction signed by Alice that was created
and sent to the relayer at the same time. Multiple access keys by the relayer
and coordination between relayer and user could potentially alleviate this
problem. But for the MVP, nothing along those lines has been approved.
```
