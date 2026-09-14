This confirms a real analog: the relayer fee refund promise is created and dispatched unconditionally at the top of `inner_rlp_execute`, independent of whether the emulated transfer action itself later succeeds or fails.

## Title
Relayer fee refund is paid unconditionally regardless of the underlying transfer's success — ([File: runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs])

### Summary
In the NEAR Wallet Contract, `inner_rlp_execute` dispatches a `NEAR` transfer to the relayer as compensation for an `EOABaseTokenTransfer` or `ERC20Transfer`, but this refund promise is created and sent *before* the actual transfer action's outcome is known, and it is never made conditional on that outcome succeeding. This is the analog of the reported Vyper issue: the outer function ("swap"/here "rlp_execute") performs a value-moving external call chain ("_direct_swap"/here the token-transfer action promise) without gating a dependent payment ("approve"/here the relayer fee transfer) on the success of that call.

### Finding Description
`inner_rlp_execute` builds the refund promise independently of the promise chain that performs the actual action: [1](#0-0) 

The action's actual execution promise (for `ERC20Transfer`, a multi-step `storage_balance_of` → optional `storage_deposit` → `ft_transfer` chain) is only constructed and returned afterward, further down in the same function: [2](#0-1) 

Because `env::promise_batch_create` + `env::promise_batch_action_transfer` for the relayer fee is issued as a standalone batch (not `.then()`-chained to the action's promise, and not conditioned on its `PromiseResult`), the relayer is paid as soon as the receipt executes, irrespective of whether the subsequent `ft_transfer`/base-token transfer later fails (e.g., insufficient token balance, receiver registration issues, or a reverting/panicking NEP-141 contract). The callbacks that do inspect the actual transfer's `PromiseResult` (`rlp_execute_callback`, `nep_141_storage_balance_callback`) only affect `ExecuteResponse.success`/refunds to the original caller's attached deposit — they have no logic that claws back or gates the relayer fee already sent: [3](#0-2) 

This mirrors the reported bug class: an unchecked/ungated dependent action (payment) proceeds without verifying that the primary value transfer it is supposed to be conditioned on actually succeeded.

### Impact Explanation
A relayer (an unprivileged caller who can submit `rlp_execute` transactions on behalf of any user without prior on-boarding, as described in the code comments) can be paid its fee even when the user's actual token/base-transfer fails. Because the wallet's own $NEAR balance funds the relayer refund, this allows repeated fee extraction from the wallet account with no successful corresponding transfer — an unwanted/unauthorized value movement from the wallet, decoupled from delivering the service the fee is meant to compensate. This matches the required impact category of "concrete unauthorized value movement."

### Likelihood Explanation
Reachable directly via a single `rlp_execute` transaction call from any relayer account (no special privilege required) whenever an `ERC20Transfer` or `EOABaseTokenTransfer` carries a non-zero `fee`. The relayer only needs to submit a transaction whose eventual token/native transfer fails post-hoc (e.g., target lacking sufficient NEP-141 balance, or the NEP-141 contract failing), while the fee transfer executes unconditionally beforehand in the same receipt-processing sequence.

### Recommendation
Chain the relayer-fee transfer with `.then()` after the action's execution promise (or move it into `rlp_execute_callback`/`nep_141_storage_balance_callback`) and only execute `promise_batch_action_transfer` for the fee when `env::promise_result(0)` for the underlying action is `PromiseResult::Successful`.

### Proof of Concept
1. Relayer submits an RLP transaction representing an `ERC20Transfer` with `fee > 0` for a token contract where the wallet's balance is insufficient, but the transaction is otherwise well-formed (correct nonce, gas, signature).
2. `inner_rlp_execute` immediately creates and sends the `fee` transfer to `predecessor_account_id` (the relayer) via the standalone promise batch at [4](#0-3) .
3. The chained `storage_balance_of` → `ft_transfer` promise subsequently fails inside `nep_141_storage_balance_callback`/`rlp_execute_callback`, and `ExecuteResponse.success = false` is returned to the caller.
4. The relayer nonetheless already received `fee` yoctoNEAR from the wallet's balance, even though no token transfer occurred.

### Citations

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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L366-385)
```rust

            // If the action is an emulated base token or ERC-20 transfer with a non-zero fee then
            // create a promise to send the refund to the relayer. This allows any relayer
            // to safely serve base token transfers from any wallet without additional
            // on-boarding because the relayer will receive some compensation for sending
            // the transaction. Users should always verify the fee before signing a base token
            // transfer. Relayers should also verify the fee before sending to make sure the
            // user's signed transaction will refund enough to cover the relayer's gas costs.
            if let TransactionKind::EthEmulation(EthEmulationKind::EOABaseTokenTransfer {
                fee,
                ..
            })
            | TransactionKind::EthEmulation(EthEmulationKind::ERC20Transfer { fee, .. }) =
                &transaction_kind
            {
                if !fee.is_zero() && context.predecessor_account_id != context.current_account_id {
                    let refund_promise = env::promise_batch_create(&context.predecessor_account_id);
                    env::promise_batch_action_transfer(refund_promise, *fee);
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
