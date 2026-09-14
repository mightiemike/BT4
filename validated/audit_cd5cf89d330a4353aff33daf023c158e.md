### Title
Wallet Contract absorbs the caller's attached NEAR deposit without refund even when the emulated action is a pure NEP-141 (ERC-20) token transfer that does not consume it - (File: runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs)

### Summary
`WalletContract::rlp_execute` is `#[payable]` and accepts an attached NEAR (native) deposit from any external caller/relayer at the same time as it decodes and executes an RLP-encoded Ethereum transaction, which may itself represent a pure NEP-141 token operation such as an ERC-20 transfer. The externally attached NEAR deposit and the token-transfer amount encoded inside the RLP payload are two completely independent values. The contract tracks the external deposit only via `CallerDeposit`, and refunds it solely when the resulting cross-contract promise fails; if the promise succeeds, the deposit is silently kept by the wallet contract's own balance with no path back to the caller, regardless of whether the succeeding action (e.g. the ERC-20/NEP-141 `ft_transfer`) needed or used that deposit at all.

### Finding Description
`rlp_execute` is marked `#[payable]` [1](#0-0) , so any predecessor can attach an arbitrary NEAR deposit to the call. That deposit is read via `env::attached_deposit()` and wrapped into `CallerDeposit` purely "to refund the caller's deposit if the cross-contract call fails" [2](#0-1) [3](#0-2) .

Separately, the actual value moved by the emulated action comes exclusively from the RLP-encoded Ethereum transaction's `value`/`yocto_near` fields, converted in `Action::try_into_near_action` [4](#0-3)  and in `parse_rlp_tx_to_action` [5](#0-4) . For an ERC-20 (NEP-141) transfer, this path builds a `FunctionCall` action whose deposit is `1 yoctoNEAR` per NEP-141 (or whatever the ETH tx encodes), and `action_to_promise` sends that deposit — not `attached_deposit` — with the promise [6](#0-5) . Thus the externally attached NEAR deposit is never forwarded into, or consumed by, the token-transfer promise.

The only place the caller's deposit is ever paid out is in `rlp_execute_callback`, and only on the `PromiseResult::Failed` branch: [7](#0-6) 
On `PromiseResult::Successful`, there is no refund logic at all — the function simply returns success and the attached deposit permanently remains part of the wallet contract's own account balance.

The `test_caller_refunds` test explicitly documents and asserts this behavior: the caller "does not get a refund when their tokens are spent" once the promise succeeds [8](#0-7) . This confirms the contract's design intentionally distinguishes "spent" vs "failed," but it does not distinguish whether the attached deposit was actually needed by, or relevant to, the specific action being emulated. Because the NEP-141/ERC-20 transfer path's deposit requirement is fixed and independent of `attached_deposit`, any caller who (mistakenly or not) attaches a NEAR deposit alongside a token-only operation loses that deposit into the wallet contract's balance the moment the unrelated token transfer succeeds — the exact analog of `BountyCore.receiveFunds` failing to return native payment attached alongside a token payment.

### Impact Explanation
Any external caller (a relayer submitting `rlp_execute` on behalf of, or interacting with, an eth-implicit wallet contract) who attaches NEAR alongside a signed transaction that resolves to an ERC-20/NEP-141 transfer permanently loses that attached NEAR into the wallet contract's balance as soon as the unrelated token transfer succeeds. This is an unauthorized, unrecoverable value transfer/fund-freezing bug reachable directly by a single transaction/contract call from an unprivileged caller, matching the "concrete unauthorized value movement / permanently frozen funds" acceptance criteria.

### Likelihood Explanation
Likelihood is moderate: it requires a caller to attach a NEAR deposit to `rlp_execute` while submitting a transaction whose decoded action is an ERC-20/NEP-141 transfer (or any action where the RLP-encoded value is unrelated to the caller's own attached deposit). Given `rlp_execute` is `#[payable]` with no validation restricting/matching `attached_deposit` to the actual value needed by the decoded action, this can occur due to relayer implementation mistakes, or be deliberately triggered by any caller against their own or another's wallet contract to demonstrate stuck value, since nothing in the contract prevents or discourages attaching an unrelated deposit.

### Recommendation
Enforce that the externally attached NEAR deposit (`attached_deposit`) is either (a) required to be zero for actions/transaction kinds that do not need caller-supplied NEAR (e.g., `EthEmulationKind::ERC20Transfer`, `ERC20Balance`, `ERC20TotalSupply`), reverting/erroring otherwise, or (b) always refunded to the predecessor in `rlp_execute_callback` regardless of whether the promise succeeded, unless the amount was explicitly consumed by the decoded action's own deposit requirement. This mirrors the recommended fix for `BountyCore.receiveFunds`: reject (or refund) unrelated native payment attached alongside token-only operations instead of silently absorbing it.

### Proof of Concept
1. An eth-implicit account has `WalletContract` deployed with balance B.
2. A caller (any account, including a relayer or the wallet's own predecessor via access key) invokes `rlp_execute(target=<nep141_token>, tx_bytes_b64=<RLP tx encoding an ERC-20 transfer(recipient, amount)>)` while attaching `X` NEAR (`attached_deposit = X`), where `X` has no relation to the ERC-20 transfer amount encoded in the RLP payload.
3. `inner_rlp_execute` parses the transaction as `TransactionKind::EthEmulation(EthEmulationKind::ERC20Transfer { .. })`, constructs `CallerDeposit { account_id: predecessor, yocto_near: X }`, and schedules the `storage_balance_of` → (optional `storage_deposit`) → `ft_transfer` promise chain, using only the deposit encoded in the RLP action (not `X`) for any Near-side deposit. [9](#0-8) 
4. The `ft_transfer` promise succeeds (the token moves as intended).
5. `rlp_execute_callback` observes `PromiseResult::Successful` and returns success without refunding `caller_deposit`. [10](#0-9) 
6. The wallet contract's account balance is now permanently increased by `X`; the caller who attached `X` has no way to reclaim it — the native payment was never returned despite being provided alongside an unrelated token operation.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L88-93)
```rust
    #[payable]
    pub fn rlp_execute(
        &mut self,
        target: AccountId,
        tx_bytes_b64: String,
    ) -> PromiseOrValue<ExecuteResponse> {
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L340-345)
```rust
    let context = ExecutionContext::new(
        current_account_id.clone(),
        predecessor_account_id,
        env::attached_deposit(),
    )?;
    let caller_deposit = CallerDeposit::new(&context);
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L433-458)
```rust
        TransactionKind::EthEmulation(EthEmulationKind::ERC20Transfer { receiver_id, .. }) => {
            // In the case of the emulated ERC-20 transfer, the receiving account
            // might not be registered with the NEP-141 contract (per the NEP-145)
            // storage standard. Therefore we must create a multi-step promise where
            // first we check if the receiver is registered and then if not call
            // `storage_deposit` in addition to `ft_transfer`.
            let token_id = target;
            let callback_gas = NEP_141_STORAGE_BALANCE_CALLBACK_GAS.saturating_add(action.gas());
            let ext: WalletContractExt =
                WalletContract::ext(current_account_id).with_static_gas(callback_gas);
            let storage_balance_args =
                format!(r#"{{"account_id": "{}"}}"#, receiver_id.as_str()).into_bytes();
            Promise::new(token_id.clone())
                .function_call(
                    "storage_balance_of".into(),
                    storage_balance_args,
                    NearToken::from_yoctonear(0),
                    NEP_141_STORAGE_BALANCE_OF_GAS,
                )
                .then(ext.nep_141_storage_balance_callback(
                    token_id,
                    receiver_id,
                    action,
                    caller_deposit,
                ))
        }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L475-483)
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
