## Title
Relayer fee is paid unconditionally in `inner_rlp_execute` even when the emulated ERC-20/base-token transfer subsequently fails, causing unrecoverable loss of user (wallet) funds - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
The Sherlock report describes an ERC-20 `transferFrom` whose boolean return value is not checked, so a failed value-transfer is silently treated as successful and the counter-party still receives (or is credited) funds it should not have received. The closest reachable analog in nearcore is in the NEAR Wallet Contract's Ethereum-transaction emulation path (`rlp_execute` → `inner_rlp_execute`): the relayer's fee-refund `Promise` is dispatched **unconditionally and independently** of the outcome of the actual value-moving action (the emulated ERC-20 `ft_transfer` or base-token `Transfer`). Any unprivileged relayer/RPC caller who submits an RLP-encoded transaction through `rlp_execute` can trigger this path.

### Finding Description
`inner_rlp_execute` parses the incoming Ethereum-style transaction into a Near `Action` and a `TransactionKind`. Before it even builds/dispatches the promise that performs the intended transfer, it eagerly creates a **separate** promise to pay the relayer's fee: [1](#0-0) 

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

This `refund_promise` is a brand-new, standalone promise batch (`env::promise_batch_create`) that is never `.then()`-chained to, or joined with, the promise that actually executes the transfer (built later in the function and returned as `promise`): [2](#0-1) 

The actual transfer for `ERC20Transfer` goes through `storage_balance_of` → `nep_141_storage_balance_callback` → (optional `storage_deposit`) → `ft_transfer` → `rlp_execute_callback`, and its success/failure is only detected later via `env::promise_result(0)`: [3](#0-2) 

Because the fee-payment promise is dispatched immediately and independently, it executes and burns the wallet's NEAR balance regardless of whether the downstream `ft_transfer`/`Transfer` promise chain later fails (e.g., receiver not registered and `storage_deposit` insufficient, NEP-141 contract panics, insufficient token balance, or any other cross-contract call failure). The only compensating logic in `rlp_execute_callback` refunds the **caller's attached deposit** (`caller_deposit`) when the transfer promise fails, but it does nothing to reclaim the relayer fee that was already unconditionally paid out of the wallet's own balance: [4](#0-3) 

This mirrors the reported bug class: a value-transfer's success is not verified/gated before crediting a counterparty, so a failed or reverted primary operation still results in real funds leaving the victim's (here, the wallet owner's) account.

### Impact Explanation
Any relayer (which can be any unprivileged account, since `rlp_execute` is a permissionless entry point taking an RLP transaction and calling into the wallet contract) can craft or simply submit a transaction whose emulated ERC-20/base-token transfer is doomed to fail (e.g., transferring more tokens than the wallet holds, or targeting a token contract that will reject the call) while still specifying a non-zero `max_fee_per_gas`/`gas_limit` (which is converted into `fee`). The wallet contract will pay the relayer fee out of the wallet's real NEAR balance even though the intended value transfer never took place, resulting in an unauthorized, irrecoverable loss of the wallet owner's NEAR tokens with no corresponding effect having occurred. This is a concrete unauthorized value movement / loss of user funds, satisfying the medium/high severity bar.

### Likelihood Explanation
The path is reachable by any account able to submit a transaction calling `rlp_execute` on a NEAR Wallet Contract (eth-implicit account) — i.e., a relayer or the user themselves acting as their own relayer. Triggering a failing downstream transfer while a fee is attached does not require any special privilege, malicious validator, or network-level condition; it is a straightforward function-call sequence exercised by the existing emulation test suite (`runtime/near-wallet-contract/implementation/wallet-contract/src/tests/emulation.rs`), which never exercises the "fee paid despite the emulated transfer failing" case, indicating no protection against it.

### Recommendation
Chain the relayer fee-refund promise so that it only executes after — and conditioned on — the successful completion of the underlying transfer action (e.g., dispatch the fee transfer from within `rlp_execute_callback` only when `PromiseResult::Successful` is observed, or use `Promise::and`/callback composition so the fee payment is part of the same dependent promise chain instead of an independent, unconditionally-created batch).

### Proof of Concept
1. Deploy a wallet contract (eth-implicit account) with a small NEAR balance and holding some NEP-141 tokens.
2. Craft an RLP-encoded Ethereum transaction whose `data` encodes an `ERC20_TRANSFER_SELECTOR` call with an amount exceeding the wallet's token balance (so `ft_transfer` will fail/panic downstream), and set `gas_price`/`gas_limit` so that `tx_fee` (computed in `internal::parse_rlp_tx_to_action`) is non-zero.
3. Have a third-party relayer account submit this transaction via `rlp_execute`.
4. Observe: `inner_rlp_execute` immediately creates and sends the `refund_promise` transferring `fee` NEAR from the wallet to the relayer (`runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs:381-384`).
5. The subsequent `ft_transfer` promise chain fails (insufficient token balance), `rlp_execute_callback` returns `success: false` and refunds only the caller's `attached_deposit`, but the wallet's NEAR balance has already been permanently reduced by `fee` even though no token transfer occurred.

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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L433-470)
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
        TransactionKind::EthEmulation(EthEmulationKind::SelfBaseTokenTransfer) => {
            // Base token transfers to self are no-ops on Near, so we do not need to
            // schedule an additional call. We can simply go straight to `rlp_execute_callback`.
            let ext: WalletContractExt =
                WalletContract::ext(current_account_id).with_static_gas(RLP_EXECUTE_CALLBACK_GAS);
            ext.rlp_execute_callback(caller_deposit)
        }
        _ => {
            let ext =
                WalletContract::ext(current_account_id).with_static_gas(RLP_EXECUTE_CALLBACK_GAS);
            action_to_promise(target, action)?.then(ext.rlp_execute_callback(caller_deposit))
        }
```
