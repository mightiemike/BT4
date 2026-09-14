### Title
Wallet Contract pays relayer fee refund unconditionally, even if the corresponding ERC-20/base-token transfer action fails - ([File: runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs])

### Summary
The `inner_rlp_execute` function in the NEAR Wallet Contract creates an unconditional, independent transfer promise that pays the relayer's fee **before** knowing whether the emulated ERC-20/base-token transfer it is supposed to compensate for actually succeeds. This is analogous to the ERC20 report's core defect: the code assumes a value transfer completed successfully without verifying the outcome, resulting in a party receiving payment (the relayer) even though the corresponding transfer of value (to the user's intended recipient) never took place.

### Finding Description
In `inner_rlp_execute`, after parsing a relayed Ethereum-style transaction into a Near action, the contract unconditionally creates and dispatches a *separate* promise batch to refund the relayer's fee: [1](#0-0) 

This fee-refund promise (`env::promise_batch_create` + `env::promise_batch_action_transfer`) is **not** chained (`.then()`) to the actual action promise that performs the emulated ERC-20 transfer (`ft_transfer`) or base-token transfer. It is fired as an independent action within the same receipt, so it executes regardless of whether the subsequent action succeeds.

The actual transfer/`ft_transfer` action is built later and chained to `rlp_execute_callback` for the *outcome check*: [2](#0-1) 

but the only remediation on failure in `rlp_execute_callback` is to refund the `caller_deposit` (the `tx.value` amount attached to the transfer) — there is no code path that claws back or ever conditions the earlier fee-refund transfer on success of this promise: [3](#0-2) 

Consequently, if the underlying `ft_transfer`/NEP-141 call fails for any reason — e.g., the token contract reverts due to insufficient sender balance, `storage_deposit`/`ft_transfer` runs out of gas, or the token contract behaves unexpectedly (the exact ERC-20-style misbehavior class cited in the source report: non-reverting or fee-charging tokens) — the relayer has already been paid the fee out of the wallet's own balance, with no possibility of reversal.

### Impact Explanation
This allows unauthorized value movement out of the Wallet Contract: the relayer (an unprivileged, single-transaction caller of the public `rlp_execute` method — analogous to a "meta-transaction sender") collects payment even though the service it was paid to perform (relaying a successful ERC-20/base token transfer) did not complete. The wallet owner's funds are drained for a transfer that never actually occurred, i.e. concrete unauthorized value movement/loss of funds without a corresponding valid state transition.

### Likelihood Explanation
The `ERC20Transfer` and `EOABaseTokenTransfer` code paths — which are the ones enabling this fee refund — are triggered on ordinary, expected usage (the meta-transaction / fee-relaying feature is the primary purpose of the wallet contract's ERC-20 emulation). Any transient failure of the downstream `ft_transfer`/`storage_deposit` calls (insufficient balance, gas exhaustion in the multi-call chain, or misbehaving/fee-charging token contracts) is sufficient to trigger fund loss, and a relayer has direct incentive to submit transactions likely to fail (e.g., against tokens/receivers known to revert) purely to still collect the fee.

### Recommendation
Chain the relayer fee-refund promise to the outcome of the actual transfer action (e.g., only issue `promise_batch_action_transfer` for the fee inside `rlp_execute_callback` after confirming `PromiseResult::Successful`), rather than firing it unconditionally and independently in `inner_rlp_execute`.

### Proof of Concept
1. A user signs an Ethereum-style transaction representing an ERC-20 `transfer` call through their Wallet Contract, including a non-zero relayer fee, but has insufficient NEP-141 token balance for the transfer (or the target token contract is configured to revert/behave non-standard on transfer).
2. A relayer submits this via `rlp_execute`. `inner_rlp_execute` immediately creates and dispatches the fee-refund promise to the relayer (`lib.rs:381-384`).
3. The chained `ft_transfer` call (via `nep_141_storage_balance_callback` → `action_to_promise` → `rlp_execute_callback`) fails because the sender lacks sufficient token balance.
4. `rlp_execute_callback` only refunds the `caller_deposit` (attached NEAR value), not the fee already paid to the relayer.
5. Result: the relayer keeps the fee even though the user's intended ERC-20 transfer never executed — unauthorized value extracted from the wallet.

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
