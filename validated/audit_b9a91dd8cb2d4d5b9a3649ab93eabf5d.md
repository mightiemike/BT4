This confirms the bug. The comment on `CallerDeposit` explicitly states its purpose: *"This allows us to refund the caller's deposit if the cross-contract call fails."* This refund is only implemented in `rlp_execute_callback`'s `PromiseResult::Failed` branch, but the early-return failure branches in `address_check_callback` and `nep_141_storage_balance_callback` bypass it entirely.

### Title
Wallet Contract loses external caller's attached deposit when an emulated ERC-20 transfer's `storage_balance_of`/registrar pre-check fails or returns a non-conforming response - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
The Wallet Contract's `CallerDeposit` mechanism is designed to always refund an external caller's attached deposit whenever the underlying cross-contract call triggered by `rlp_execute` fails, so that a relayer (or any caller) submitting a signed Ethereum-style transaction on behalf of a wallet owner does not lose funds on failure. This refund logic is implemented only in the terminal `rlp_execute_callback`. Two intermediate callbacks used for emulated flows - `nep_141_storage_balance_callback` (ERC-20 `transfer` emulation via NEP-141) and `address_check_callback` (address-registrar lookup for EOA base-token transfer emulation) - short-circuit with an error `ExecuteResponse` on `PromiseResult::Failed` or on JSON-deserialization failure of the promise result, without ever creating the refund promise.

### Finding Description
`CallerDeposit::new` tracks any non-zero deposit attached by an external (non-self) caller specifically "to refund the caller's deposit if the cross-contract call fails" [1](#0-0) . The only place that honors this contract is `rlp_execute_callback`, which explicitly creates a transfer promise back to `caller_deposit.account_id` when `env::promise_result(0)` is `Failed` [2](#0-1) .

However, for the ERC-20 transfer emulation path, `inner_rlp_execute` first calls the token's `storage_balance_of` and routes the result through `nep_141_storage_balance_callback` [3](#0-2) . Inside that callback, if the `storage_balance_of` promise fails, or if the returned bytes cannot be deserialized as `Option<StorageBalance>` (e.g. because the token does not implement NEP-145, or its JSON response doesn't match the expected shape - directly analogous to the reported non-standard-ERC20 `approve` bug-class), the function returns immediately with `success: false` and never forwards `caller_deposit` into any refund promise: [4](#0-3) .

The same pattern exists in `address_check_callback` for the EOA base-token-transfer-with-address-check path: on `PromiseResult::Failed` or a malformed registrar response, it returns directly without forwarding `caller_deposit` [5](#0-4) .

In both cases the attached deposit was already received by the Wallet Contract account when `rlp_execute` was invoked with `#[payable]` [6](#0-5) , so it becomes stuck in the wallet's balance with no code path left to return it - the transaction has already terminated by the time these branches return, so no later logic runs.

### Impact Explanation
An external, unprivileged caller (any relayer or any account) that submits `rlp_execute` with an attached deposit for an ERC-20-emulated transfer or an address-checked base-token transfer permanently loses that deposit whenever the pre-check call (`storage_balance_of` on the NEP-141 token, or `lookup` on the address registrar) fails or returns a response the Wallet Contract doesn't expect. This can be triggered simply by targeting a token/registrar contract that doesn't strictly conform to the expected interface/response shape - the same bug class as the reported ERC20 `approve` non-bool-return issue, but here it results in the caller's NEAR deposit being irrecoverably absorbed by the wallet contract rather than merely a reverted call, i.e. permanently frozen/misappropriated funds for the caller.

### Likelihood Explanation
Reaching this requires only a single signed transaction from any unprivileged caller invoking `rlp_execute` with a non-zero deposit and a `target` account whose `storage_balance_of` (or the fixed address registrar's `lookup`) call fails or returns unexpected data - no special privileges, validator/node/peer control, or multi-step setup beyond deploying/using an arbitrary non-conforming FT-like contract as the `target`.

### Recommendation
Thread `caller_deposit` refund logic into the early-return branches of `nep_141_storage_balance_callback` and `address_check_callback` (mirroring the refund promise construction in `rlp_execute_callback`), so that any failure or malformed response in these intermediate callbacks also triggers a refund to `caller_deposit.account_id` before returning the error `ExecuteResponse`.

### Proof of Concept
1. Deploy the Wallet Contract for an eth-implicit account and deploy a NEP-141-like contract that does not implement `storage_balance_of` (or returns a response that doesn't deserialize into `Option<StorageBalance>`).
2. As an external relayer account, call `rlp_execute` with a non-zero attached deposit, targeting that non-conforming contract with an RLP-encoded ERC-20 `transfer` call.
3. `inner_rlp_execute` routes through the `ERC20Transfer` branch, calling `storage_balance_of` then `nep_141_storage_balance_callback` [3](#0-2) .
4. The `storage_balance_of` promise fails (unknown method) or its result fails to deserialize, hitting the early-return branch that never refunds `caller_deposit` [4](#0-3) .
5. Observe: `ExecuteResponse.success == false`, and the relayer's attached deposit is never returned - the wallet contract's balance permanently retains it, unlike the equivalent failure captured in `rlp_execute_callback` which does refund [2](#0-1) .

### Citations

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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L88-93)
```rust
    #[payable]
    pub fn rlp_execute(
        &mut self,
        target: AccountId,
        tx_bytes_b64: String,
    ) -> PromiseOrValue<ExecuteResponse> {
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L141-159)
```rust
        let maybe_account_id: Option<AccountId> = match env::promise_result(0) {
            PromiseResult::Failed => {
                return PromiseOrValue::Value(ExecuteResponse {
                    success: false,
                    success_value: None,
                    error: Some("Call to Address Registrar contract failed".into()),
                });
            }
            PromiseResult::Successful(value) => match serde_json::from_slice(&value) {
                Ok(x) => x,
                Err(_) => {
                    return PromiseOrValue::Value(ExecuteResponse {
                        success: false,
                        success_value: None,
                        error: Some("Unexpected response from account registrar".into()),
                    });
                }
            },
        };
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L203-220)
```rust
        let maybe_storage_balance: Option<StorageBalance> = match env::promise_result(0) {
            PromiseResult::Failed => {
                return PromiseOrValue::Value(ExecuteResponse {
                    success: false,
                    success_value: None,
                    error: Some(format!("Call to NEP-141 {token_id}::storage_balance_of failed")),
                });
            }
            PromiseResult::Successful(value) => match serde_json::from_slice(&value) {
                Ok(x) => x,
                Err(_) => {
                    return PromiseOrValue::Value(ExecuteResponse {
                        success: false,
                        success_value: None,
                        error: Some("Unexpected response from NEP-141 storage_balance_of".into()),
                    });
                }
            },
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L296-312)
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
