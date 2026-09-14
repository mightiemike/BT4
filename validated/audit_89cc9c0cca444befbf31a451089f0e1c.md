This confirms the bug: two intermediate callbacks (`address_check_callback` and `nep_141_storage_balance_callback`) drop the `caller_deposit` refund on the `PromiseResult::Failed` branch, while `rlp_execute_callback` explicitly refunds it. This is a genuine asymmetry reachable from any account calling `rlp_execute` with an attached NEAR deposit on the Wallet Contract, and it matches the report's bug class (external call failure → permanently locked user funds).

### Title
NEAR Wallet Contract loses external caller's attached deposit when the intermediate promise fails - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
The NEAR Wallet Contract's ERC-20/eth-implicit emulation flow (`rlp_execute` → `address_check_callback` / `nep_141_storage_balance_callback` → `rlp_execute_callback`) tracks an external caller's attached NEAR deposit via `CallerDeposit` so it can be refunded if the underlying cross-contract call fails. However, only the terminal callback (`rlp_execute_callback`) implements this refund. The two intermediate callbacks silently drop `caller_deposit` on failure, permanently stranding the caller's attached NEAR in the wallet contract.

### Finding Description
`CallerDeposit::new` [1](#0-0)  captures the predecessor's attached deposit whenever an external (non-owner) account calls `rlp_execute`, explicitly so it "allows us to refund the caller's deposit if the cross-contract call fails" [2](#0-1) .

This `caller_deposit` is threaded through every callback in the promise chain, including `address_check_callback` and `nep_141_storage_balance_callback`. Both of these are entered for the `EOABaseTokenTransfer{address_check: Some(_)}` path (registrar lookup, used when the target of a base-token transfer is another eth-implicit account) and the `ERC20Transfer` path (NEP-141 `storage_balance_of` lookup), respectively — both very common, unprivileged entry points reachable by any relayer/caller.

In `address_check_callback`, on `PromiseResult::Failed` the function returns immediately without ever inspecting `caller_deposit`: [3](#0-2) 

Likewise, in `nep_141_storage_balance_callback`, on `PromiseResult::Failed` (i.e. the NEP-141 token's `storage_balance_of` call fails/reverts — exactly the ERC-20-pause style scenario cited in the source report) the function returns immediately with no refund: [4](#0-3) 

Compare this to the only place the refund logic actually exists, in `rlp_execute_callback`'s `Failed` arm: [5](#0-4) 

Because `caller_deposit` is an unused function parameter in the two intermediate failure branches, the deposit the external caller attached to their `rlp_execute` call (tracked via `env::attached_deposit()` in `ExecutionContext::new` and `CallerDeposit::new`) is never returned. The Near runtime's automatic refund for failed `FunctionCall` actions only returns *unused/failed* attached-deposit-per-receipt, not this contract-level bookkeeping value which the wallet contract already consumed by forwarding it as part of subsequent action deposits (e.g., `additional_value` merged into a Transfer/FunctionCall's `deposit` in `Action::try_into_near_action`) [6](#0-5) . The test suite itself only verifies the refund path through the final callback (`test_caller_refunds`), not through the two intermediate ones: [7](#0-6) 

### Impact Explanation
Any external NEAR account (not the wallet owner) that calls `rlp_execute` with a non-zero attached deposit — e.g. a relayer fronting NEAR for an eth-emulated ERC-20 transfer to an unregistered receiver, or a base-token transfer to another eth-implicit target that requires an address-registrar lookup — permanently loses that attached deposit whenever the first cross-contract call in the chain (`storage_balance_of` on the NEP-141 token, or `lookup` on the address registrar) fails. This directly reproduces the report's bug class: an external dependency failure (e.g., the target NEP-141 token being paused, non-existent, or misbehaving) causes concrete, permanent loss of the caller's NEAR funds inside a production NEAR contract, with no recovery path (the deposit is simply never referenced again once the closure returns `ExecuteResponse{success:false,...}`).

### Likelihood Explanation
This is trivially triggerable by any unprivileged account: attach a deposit to `rlp_execute` while targeting a token contract whose `storage_balance_of` reverts (paused/buggy/malicious NEP-141 token, or simply a nonexistent account), or targeting another eth-implicit account requiring an address-registrar check that fails. No special privileges, races, or validator collusion are required — a single transaction from any signer suffices.

### Recommendation
Add the same `caller_deposit` refund logic (creating a `promise_batch_create` + `promise_batch_action_transfer` back to `caller_deposit.account_id`) to the `PromiseResult::Failed` arms of both `address_check_callback` and `nep_141_storage_balance_callback`, mirroring the existing logic in `rlp_execute_callback`.

### Proof of Concept
1. Deploy a NEP-141 token contract whose `storage_balance_of` method is made to panic/fail (or simply target an account ID that does not exist / a non-NEP-141 contract as the ERC-20 `target`).
2. From an external NEAR account `caller` (not the wallet owner), call `wallet_contract.rlp_execute(target, tx_bytes_b64)` with the RLP-encoded transaction encoding an ERC-20 `transfer(...)` call to that token, attaching a non-zero NEAR deposit (as in `test_caller_refunds` but routing through the ERC20Transfer path instead of a direct function call) [7](#0-6) .
3. Observe: the promise chain reaches `nep_141_storage_balance_callback`, `env::promise_result(0)` is `PromiseResult::Failed`, and the function returns `ExecuteResponse{success:false, ...}` at lines 204-210 without ever creating a refund promise back to `caller`.
4. Verify `caller`'s balance decreased by the attached deposit and never receives it back, unlike the scenario in `test_caller_refunds` which exercises only the `rlp_execute_callback` failure path.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/types.rs (L172-178)
```rust
/// A data type to keep track of the deposit given by an external caller.
/// This allows us to refund the caller's deposit if the cross-contract call fails.
#[derive(Debug, PartialEq, Eq, Clone, serde::Serialize, serde::Deserialize)]
pub struct CallerDeposit {
    pub account_id: AccountId,
    pub yocto_near: NonZeroU128,
}
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L141-148)
```rust
        let maybe_account_id: Option<AccountId> = match env::promise_result(0) {
            PromiseResult::Failed => {
                return PromiseOrValue::Value(ExecuteResponse {
                    success: false,
                    success_value: None,
                    error: Some("Call to Address Registrar contract failed".into()),
                });
            }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L203-210)
```rust
        let maybe_storage_balance: Option<StorageBalance> = match env::promise_result(0) {
            PromiseResult::Failed => {
                return PromiseOrValue::Value(ExecuteResponse {
                    success: false,
                    success_value: None,
                    error: Some(format!("Call to NEP-141 {token_id}::storage_balance_of failed")),
                });
            }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L296-311)
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
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/tests/sanity.rs (L170-213)
```rust
// An external caller gets its deposit back if the cross-contract call fails.
#[tokio::test]
async fn test_caller_refunds() -> anyhow::Result<()> {
    let TestContext { worker, wallet_contract, wallet_sk, address_registrar, .. } =
        TestContext::new().await?;

    let caller = worker.root_account()?;
    let deposit_amount = NearToken::from_near(3);
    let create_tx = |receiver_id: &AccountId, nonce: u64| {
        let method = "register";
        let args = br#"{"account_id": "birchmd.near"}"#;
        let action = Action::FunctionCall {
            receiver_id: receiver_id.to_string(),
            method_name: method.into(),
            args: args.to_vec(),
            gas: Gas::from_tgas(10).as_gas(),
            yocto_near: 0,
        };
        utils::create_signed_transaction(
            nonce,
            receiver_id,
            Wei::new_u128(deposit_amount.as_yoctonear() / (MAX_YOCTO_NEAR as u128)),
            action,
            &wallet_sk,
        )
    };

    // External caller gets a refund when the cross-contract call fails
    let pre_tx_account_balance = caller.view_account().await?.balance;
    let receiver_id: AccountId = "fake.near".parse()?;
    let result = wallet_contract
        .rlp_execute_from(
            &caller,
            receiver_id.as_str(),
            &create_tx(&receiver_id, 0),
            deposit_amount,
        )
        .await?;
    assert!(!result.success);
    let post_tx_account_balance = caller.view_account().await?.balance;
    assert!(
        pre_tx_account_balance.as_yoctonear() - post_tx_account_balance.as_yoctonear()
            < deposit_amount.as_yoctonear()
    );
```
