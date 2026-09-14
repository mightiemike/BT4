This confirms the analog precisely. The test `test_caller_refunds` in `runtime/near-wallet-contract/implementation/wallet-contract/src/tests/sanity.rs` explicitly documents and asserts this behavior: "External caller does not get a refund when their tokens are spent" — the caller's full `attached_deposit` (analogous to `msg.value`) is retained by the wallet contract on success, regardless of whether the actual action executed required that full amount.

### Title
Excess `attached_deposit` sent to `WalletContract::rlp_execute` is permanently absorbed instead of refunded on success - (File: runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs)

### Summary
`WalletContract::rlp_execute` is `#[payable]` and tracks the caller's `attached_deposit` only via `CallerDeposit`, which is refunded solely when the downstream cross-contract promise fails. On success, any attached deposit that exceeds what the wallet actually needed for the requested action is silently kept by the wallet contract with no refund path, mirroring the reported pattern of a payer's excess payment being retained instead of returned.

### Finding Description
`inner_rlp_execute` captures the caller's deposit at the start of execution: `env::attached_deposit()` is wrapped into `ExecutionContext` and then into `CallerDeposit::new(&context)`, which stores the full `attached_deposit.as_yoctonear()` amount for the predecessor [1](#0-0) [2](#0-1) .

Critically, the actual NEAR amount moved by the resulting action (e.g. a `Transfer`) is computed independently from a completely different source: the `value` field of the user's *signed Ethereum transaction*, via `Action::try_into_near_action`, which derives `action.deposit` from `additional_value + yocto_near` (both taken from the RLP-decoded ETH tx, not from `attached_deposit`) [3](#0-2) . This action is then executed using the wallet's *own* account balance via `Promise::new(target).transfer(action.deposit)` in `action_to_promise` [4](#0-3)  — it never spends the caller's `attached_deposit` at all.

The only place `CallerDeposit` is consulted is in `rlp_execute_callback`, and only on the `PromiseResult::Failed` branch, where it issues a refund transfer of `yocto_near` back to the caller [5](#0-4) . On `PromiseResult::Successful`, the function returns `ExecuteResponse { success: true, ... }` with no refund logic whatsoever, so the entire `attached_deposit` — whether zero, exactly sufficient, or vastly in excess of what the operation needed — is absorbed into the wallet contract's balance forever.

This exact behavior is confirmed and asserted in the test suite itself: `test_caller_refunds` explicitly checks that "External caller does not get a refund when their tokens are spent" on the success path, i.e. `post_tx_account_balance` decreases by at least the full `deposit_amount` even though the underlying action (`register` on the address registrar, requiring far less) succeeded [6](#0-5) .

### Impact Explanation
Any relayer or third-party caller (an unprivileged transaction/RPC caller) who attaches more NEAR than strictly required when invoking `rlp_execute` on a wallet contract permanently loses the excess: it is merged into the wallet contract's account balance with no on-chain accounting trail tying it back to the depositor, and no code path ever returns it. This is a permanent, unrecoverable loss of funds for the caller — the direct nearcore analog of the reported Sherlock finding where `msg.value` in excess of `sellOrder.price` is fully transferred to the maker instead of only the required amount.

### Likelihood Explanation
This is trivially reachable by any account calling `rlp_execute` with a deposit attached (a normal, unprivileged NEAR `FunctionCall` action with `deposit > 0`), which is an entirely ordinary operation for relayers interacting with eth-implicit accounts. No malicious validator, network, or privileged access is required — only a caller (potentially confused, or a relayer covering costs pessimistically) attaching more NEAR than the wallet's `action.deposit` requires.

### Recommendation
When constructing the success response in `rlp_execute_callback` (and in the `Successful` branch generally), compute the difference between the tracked `CallerDeposit.yocto_near` and the amount actually consumed by the dispatched action (`action.value()` / `action.deposit`), and issue a refund transfer of the surplus back to `CallerDeposit.account_id`, mirroring the refund-on-failure logic already present for the failure branch.

### Proof of Concept
1. Caller (not the wallet's own account) calls `rlp_execute(target, tx_bytes_b64)` with `attached_deposit = X` NEAR, where the signed Ethereum transaction encodes an action (e.g. `FunctionCall` to `register` with `yocto_near: 0`) that requires little or no NEAR value to succeed.
2. `inner_rlp_execute` records `CallerDeposit { account_id: caller, yocto_near: X }`.
3. `action_to_promise` dispatches the action using the wallet's own balance, unrelated to `X`.
4. The promise succeeds; `rlp_execute_callback` hits the `PromiseResult::Successful` branch and returns without ever inspecting or refunding `caller_deposit`.
5. Caller's balance decreases by `X` with nothing returned; this is exactly what `test_caller_refunds` verifies as expected behavior [6](#0-5) .

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L296-316)
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
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L340-345)
```rust
    let context = ExecutionContext::new(
        current_account_id.clone(),
        predecessor_account_id,
        env::attached_deposit(),
    )?;
    let caller_deposit = CallerDeposit::new(&context);
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L475-484)
```rust
fn action_to_promise(target: AccountId, action: near_action::Action) -> Result<Promise, Error> {
    match action {
        near_action::Action::FunctionCall(action) => Ok(Promise::new(target).function_call(
            action.method_name,
            action.args,
            action.deposit,
            action.gas,
        )),
        near_action::Action::Transfer(action) => Ok(Promise::new(target).transfer(action.deposit)),
        near_action::Action::AddKey(action) => match action.access_key.permission {
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/types.rs (L180-192)
```rust
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
