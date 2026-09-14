## Title
Attached NEAR deposit from an external caller to the Wallet Contract's `rlp_execute` is permanently absorbed (frozen) on success instead of being refunded — (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/types.rs`, `runtime/near-wallet-contract/implementation/wallet-contract/src/internal.rs`, `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
The NEAR Wallet Contract (`rlp_execute`) accepts an `attached_deposit` from an arbitrary external NEAR caller and only tracks it via `CallerDeposit` for the purpose of refunding it if the resulting cross-contract promise **fails**. If the promise **succeeds**, none of the unconsumed portion of the caller's attached deposit is refunded or forwarded anywhere — it is simply absorbed into the Wallet Contract account's own balance, permanently, with no code path to reclaim it. This mirrors the Connext Executor bug class: value attached to a call that is not fully "claimed" by the intended downstream action gets stuck in the intermediary contract because the contract's logic only accounts for the failure path, not the "excess on success" path.

### Finding Description
`rlp_execute` is `#[payable]`, so any NEAR account can call it on any deployed Wallet Contract and attach an arbitrary deposit [1](#0-0) . Inside `inner_rlp_execute`, `ExecutionContext::new` captures the full `env::attached_deposit()` and `CallerDeposit::new` stores it *only if the predecessor differs from the current account*, explicitly annotated: "This allows us to refund the caller's deposit if the cross-contract call fails" [2](#0-1) .

Separately, the NEAR deposit that is actually attached to the outgoing action (`FunctionCall`/`Transfer`) is computed purely from the Ethereum transaction's `value` field and any inline `yocto_near`, **not** from the caller's `attached_deposit`: [3](#0-2) [4](#0-3) 

These two amounts are completely disjoint. The only place `CallerDeposit` is consumed is in `rlp_execute_callback`, and only in the `PromiseResult::Failed` branch: [5](#0-4) 

On the `PromiseResult::Successful` branch, `caller_deposit` is simply dropped — no refund, no forwarding to the target action, nothing. The attached NEAR that the external caller sent to `rlp_execute` therefore remains as part of the Wallet Contract's own account balance permanently whenever the downstream promise succeeds, regardless of whether that deposit was needed by (or even related to) the action that was executed.

This exact behavior is confirmed by the project's own test, which explicitly documents it: [6](#0-5) 
The comment "External caller does not get a refund when their tokens are spent" is misleading — the deposit isn't "spent" by the action at all (the parsed action here carries `yocto_near: 0`/no value), it is simply retained by the contract because success suppresses the refund path unconditionally.

### Impact Explanation
Any account (an "unprivileged transaction signer"/RPC caller in the terms of this analysis) that attaches NEAR to a `rlp_execute` call on someone else's Wallet Contract account — for example a relayer fronting funds, or a user misconfiguring a deposit — permanently loses that value if the underlying promise chain happens to succeed for any reason unrelated to consuming that deposit. There is no owner/admin recovery mechanism and no automatic sweep; the funds are indistinguishable from the Wallet Contract's regular NEAR balance once absorbed. This is a concrete "permanently frozen funds" outcome reachable from a single external transaction, matching the required impact category.

### Likelihood Explanation
This is trivially reachable: it requires only a single NEAR transaction calling `rlp_execute` on any Wallet Contract account with a nonzero `attached_deposit` where the corresponding parsed action does not require that deposit (e.g., the `yocto_near`/`tx.value` fields are zero or smaller than the attached amount) and where the downstream cross-contract promise succeeds. No malicious relayer, validator, or privileged role is needed — the caller is fully in control of triggering this condition themselves (as demonstrated by the existing `test_caller_refunds` test using `worker.root_account()` as an ordinary external caller).

### Recommendation
Decide explicitly what should happen to any portion of the caller's `attached_deposit` that is not consumed by the resulting action, and apply this consistently for both success and failure outcomes, e.g.:
- Always refund the `CallerDeposit` to its `account_id` in `rlp_execute_callback` regardless of success/failure (not only on `Failed`), or
- Only track/carry forward the deposit amount that is actually required by the parsed action and refund any deposit in excess of that amount immediately, or
- Reject `rlp_execute` calls where `attached_deposit` does not exactly match the deposit required by the parsed action, forcing well-formed callers.

### Proof of Concept
1. Deploy a Wallet Contract for an eth-implicit account `A` (as in `TestContext::new`).
2. As an arbitrary external NEAR account `caller` (not `A` itself), call `A.rlp_execute(target, tx_bytes_b64)` attaching `deposit_amount` (e.g. 3 NEAR), where the RLP-encoded Ethereum transaction encodes an action whose parsed `yocto_near`/`tx.value` is `0` (e.g. a `FunctionCall` to a real, existing method that requires no deposit) — following the exact pattern of `test_caller_refunds`'s second sub-case (`runtime/near-wallet-contract/implementation/wallet-contract/src/tests/sanity.rs:216-226`), but targeting any real, always-succeeding receiver instead of the address registrar.
3. Observe (as asserted in that test) that `result.success == true` and `caller`'s balance decreases by at least `deposit_amount`, while `A`'s (the Wallet Contract's) balance increases by that same amount.
4. There is no subsequent call or mechanism by which `caller` (or anyone) can retrieve that deposit from `A` — it is now indistinguishable from `A`'s regular funds, confirming the funds are permanently stuck.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L88-114)
```rust
    #[payable]
    pub fn rlp_execute(
        &mut self,
        target: AccountId,
        tx_bytes_b64: String,
    ) -> PromiseOrValue<ExecuteResponse> {
        // To ensure user actions are executed in the desired order,
        // having multiple transactions in flight at the same time is
        // not allowed.
        if self.has_in_flight_tx {
            return PromiseOrValue::Value(ExecuteResponse {
                success: false,
                success_value: None,
                error: Some(
                    "Error: transaction already in progress, please try again later.".into(),
                ),
            });
        }
        let current_account_id = env::current_account_id();
        let predecessor_account_id = env::predecessor_account_id();
        let result = inner_rlp_execute(
            current_account_id.clone(),
            predecessor_account_id,
            target,
            tx_bytes_b64,
            &mut self.nonce,
        );
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L296-317)
```rust
        match env::promise_result(0) {
            PromiseResult::Failed => {
                // The cross-contract call failed, refund the caller if needed
                if let Some(CallerDeposit { account_id, yocto_near }) = caller_deposit {
                    let refund_promise = env::promise_batch_create(&account_id);
                    env::promise_batch_action_transfer(
                        refund_promise,
                        NearToken::from_yoctonear(yocto_near.into()),
                    );
                }

                ExecuteResponse {
                    success: false,
                    success_value: None,
                    error: Some("Failed Near promise".into()),
                }
            }
            PromiseResult::Successful(value) => {
                ExecuteResponse { success: true, success_value: Some(value), error: None }
            }
        }
    }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/types.rs (L172-192)
```rust
/// A data type to keep track of the deposit given by an external caller.
/// This allows us to refund the caller's deposit if the cross-contract call fails.
#[derive(Debug, PartialEq, Eq, Clone, serde::Serialize, serde::Deserialize)]
pub struct CallerDeposit {
    pub account_id: AccountId,
    pub yocto_near: NonZeroU128,
}

impl CallerDeposit {
    pub fn new(context: &ExecutionContext) -> Option<Self> {
        // Only track for external (non-self) callers
        if context.current_account_id == context.predecessor_account_id {
            return None;
        }

        NonZeroU128::new(context.attached_deposit.as_yoctonear()).map(|yocto_near| Self {
            account_id: context.predecessor_account_id.clone(),
            yocto_near,
        })
    }
}
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/types.rs (L238-261)
```rust
    pub fn try_into_near_action(
        self,
        additional_value: u128,
    ) -> Result<near_action::Action, Error> {
        let action = match self {
            Action::FunctionCall { receiver_id: _, method_name, args, gas, yocto_near } => {
                let action = FunctionCallAction {
                    method_name,
                    args,
                    gas: Gas::from_gas(gas),
                    deposit: NearToken::from_yoctonear(
                        additional_value.saturating_add(yocto_near.into()),
                    ),
                };
                near_action::Action::FunctionCall(action)
            }
            Action::Transfer { receiver_id: _, yocto_near } => {
                let action = TransferAction {
                    deposit: NearToken::from_yoctonear(
                        additional_value.saturating_add(yocto_near.into()),
                    ),
                };
                near_action::Action::Transfer(action)
            }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/internal.rs (L159-166)
```rust
    validate_tx_value(&tx)?;

    // Call to `low_u128` here is safe because of the validation done in `validate_tx_value`
    let near_action = action
        .try_into_near_action(tx.value.raw().low_u128().saturating_mul(MAX_YOCTO_NEAR.into()))?;

    Ok((near_action, transaction_kind))
}
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/tests/sanity.rs (L215-226)
```rust
    // External caller does not get a refund when their tokens are spent
    let pre_tx_account_balance = post_tx_account_balance;
    let receiver_id = address_registrar.id();
    let result = wallet_contract
        .rlp_execute_from(&caller, receiver_id.as_str(), &create_tx(receiver_id, 1), deposit_amount)
        .await?;
    assert!(result.success);
    let post_tx_account_balance = caller.view_account().await?.balance;
    assert!(
        pre_tx_account_balance.as_yoctonear() - post_tx_account_balance.as_yoctonear()
            >= deposit_amount.as_yoctonear()
    );
```
