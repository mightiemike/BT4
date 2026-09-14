## Analog Found [1](#0-0) 

### Title
Relayer fee is paid unconditionally before the emulated ERC-20/base-token transfer is known to succeed - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
In `inner_rlp_execute`, when the parsed transaction is an `EOABaseTokenTransfer` or `ERC20Transfer` with a non-zero `fee`, the Wallet Contract immediately dispatches a `Transfer` action to refund the relayer (`context.predecessor_account_id`) *before* it schedules and resolves the actual value-moving action (the NEAR transfer or the `ft_transfer` cross-contract call). The refund is sent as its own independent receipt, completely detached from the promise chain that performs and later checks the outcome of the underlying transfer.

### Finding Description
The code path in question: [2](#0-1) 

```rust
if let TransactionKind::EthEmulation(EthEmulationKind::EOABaseTokenTransfer { fee, .. })
| TransactionKind::EthEmulation(EthEmulationKind::ERC20Transfer { fee, .. }) = &transaction_kind
{
    if !fee.is_zero() && context.predecessor_account_id != context.current_account_id {
        let refund_promise = env::promise_batch_create(&context.predecessor_account_id);
        env::promise_batch_action_transfer(refund_promise, *fee);
    }
}
```

This runs unconditionally as soon as the transaction is parsed and the nonce is incremented, well before the code later builds the promise that actually performs the transfer (for `ERC20Transfer`, a chain of `storage_balance_of` → possibly `storage_deposit` → `ft_transfer` → `rlp_execute_callback`): [3](#0-2) 

The only place that inspects the actual outcome of the transfer is `rlp_execute_callback`, which only handles refunding the *caller's attached deposit* (`caller_deposit`) on failure — it never claws back or gates the relayer `fee` that was already sent out in a separate, earlier receipt: [4](#0-3) 

This mirrors the reported Solidity pattern — moving value (`transferFrom`/here, a NEAR `Transfer` action funding the relayer) without checking whether the associated operation (`ft_transfer` to the intended recipient) actually succeeded.

### Impact Explanation
Because the fee-refund receipt is dispatched independent of and prior to confirmation of the `ft_transfer`/base-token transfer outcome, a relayer is paid the fee **even when the underlying transfer to the recipient fails** (e.g., insufficient token balance, receiver registration issues that exhaust the attached `NEP_141_STORAGE_DEPOSIT_AMOUNT`, or any other cross-contract failure). This allows unauthorized/unwarranted value extraction from the wallet contract's NEAR balance: the relayer collects payment for work that was not actually completed, silently draining the wallet owner's account balance across repeated user-signed-but-failing transactions, since the nonce is incremented (preventing replay) regardless of the transfer's success.

### Likelihood Explanation
Any relayer (an unprivileged, non-owner account with no special privilege beyond being handed a validly signed RLP transaction) can trigger this by simply submitting to `rlp_execute` a user-signed emulated ERC-20/base-token transfer whose fee is non-zero and whose downstream action is known/expected to fail (or fails due to normal conditions such as insufficient token balance). No malicious validator, network, or sync assumptions are required — it is reachable purely from a standard `FunctionCall` transaction to the Wallet Contract.

### Recommendation
Defer the relayer fee payment until after the underlying action's promise resolves successfully — i.e., move the `promise_batch_action_transfer` for `fee` into the success branch of `rlp_execute_callback` (or an equivalent callback), so the relayer is only compensated when `PromiseResult::Successful` is observed for the actual transfer, mirroring the existing `caller_deposit` refund-on-failure logic but as pay-on-success for the fee.

### Proof of Concept
1. Wallet owner signs an RLP-encoded ERC-20 `transfer` transaction with `gas_price` set such that `tx_fee` (computed in `parse_rlp_tx_to_action`) is non-zero, targeting a NEP-141 token contract where the wallet's token balance is insufficient to cover `value`. [5](#0-4) 
2. Any relayer account submits this signed transaction via `rlp_execute(target, tx_bytes_b64)`.
3. `inner_rlp_execute` immediately creates and dispatches the `fee` transfer receipt to the relayer's account.
4. The subsequent `ft_transfer` call fails (insufficient balance), and `rlp_execute_callback` returns `success: false`, refunding only `caller_deposit` (if any) — the already-paid relayer `fee` is never returned.
5. The relayer has been paid despite the transfer failing, and can repeat this with further signed-but-failing transactions to keep draining the wallet's NEAR balance via the fee mechanism.

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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L367-385)
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/internal.rs (L54-64)
```rust
    // Compute the fee based on the user's Ethereum transaction.
    // This is sent as a refund to the relayer in the case of an emulated base token
    // transfer or ERC-20 transfer. The reason for this refund is that it allows a
    // user with $NEAR to use a relayer service from their wallet immediately without
    // additional on-boarding.
    let tx_fee = {
        // Limit the cost by `VALUE_MAX` since we will convert this to a $NEAR amount.
        // The call to `low_u128` is safe because `VALUE_MAX` is the largest accepted value.
        let wei_amount = tx.max_fee_per_gas.saturating_mul(tx.gas_limit).min(VALUE_MAX).low_u128();
        NearToken::from_yoctonear(wei_amount.saturating_mul(MAX_YOCTO_NEAR as u128))
    };
```
