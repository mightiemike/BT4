This confirms the finding: `inner_rlp_execute` creates and dispatches a standalone relayer-refund `Promise` (`env::promise_batch_create(&context.predecessor_account_id)` / `env::promise_batch_action_transfer(refund_promise, *fee)` at [1](#0-0)  that is **not chained** (`.then(...)`) to the actual user action promise built later in the same function (e.g. the `ERC20Transfer`/base-token-transfer promise at [2](#0-1) ). Both promises are scheduled from the same receipt but execute independently — the code comments even acknowledge "the relayer will receive some compensation for sending the transaction" unconditionally, only asking users/relayers to "verify the fee" socially, not enforcing it on-chain [3](#0-2) .

### Title
Relayer fee is paid unconditionally via an unchained promise even when the underlying ETH-emulated action later fails - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
`inner_rlp_execute` dispatches the relayer's fee-refund transfer as an independent promise batch before creating/attaching the promise for the actual user-intended action (base-token transfer or NEP-141 `ft_transfer`/`ft_transfer_call` emulating ERC-20). Because the refund promise is not `.then()`-chained to, or otherwise gated on, the success of the action promise, the wallet contract pays the relayer regardless of whether the underlying action succeeds.

### Finding Description
In `inner_rlp_execute`, once the RLP transaction is parsed into a `TransactionKind::EthEmulation(EOABaseTokenTransfer{fee,..})` or `ERC20Transfer{fee,..}`, a separate top-level promise is immediately created and fired to pay `fee` to `predecessor_account_id` (the relayer): [4](#0-3) 

Only afterward does the function build the promise chain that actually performs the requested action — e.g. the ERC-20 emulation path that queries `storage_balance_of`, optionally calls `storage_deposit`, and finally `ft_transfer`/`ft_transfer_call`, chained via `.then(ext.rlp_execute_callback(...))`: [2](#0-1) 

These two promises (the relayer refund and the action chain) are independent receipts scheduled from the same function execution; the refund is not conditioned on `rlp_execute_callback`'s eventual `PromiseResult::Successful`/`Failed` outcome. Consequently, if the token transfer fails downstream (e.g. `ft_transfer` panics due to insufficient balance, receiver rejects, or the NEP-141 contract reverts for any reason), `rlp_execute_callback` correctly reports failure and even refunds the *caller's attached deposit* — [5](#0-4)  — but it has no way to claw back the relayer fee that was already sent unconditionally at the start.

### Impact Explanation
The wallet contract's owned $NEAR balance is unconditionally reduced by `fee` on every submitted ETH-emulated transfer, independent of whether the emulated action actually completes. A malicious or careless relayer can repeatedly submit transactions with a non-zero fee whose emulated action is guaranteed to fail (e.g. transferring more tokens than the wallet holds, or targeting a token contract that always reverts) and collect the fee every time with no successful transfer taking place, draining the wallet's NEAR balance without ever completing the user's intended action. This is an unauthorized value movement out of the user's wallet contract triggered purely by relayer-submitted transactions.

### Likelihood Explanation
The wallet contract is reachable by any relayer holding (or without) an access key, and standard NEP-141 tokens legitimately panic/fail on invalid transfers (insufficient balance, unregistered receiver edge cases, malicious token contracts), so the failing-action-but-paid-fee condition is trivially reproducible by any relayer, not requiring privileged access. The nonce is incremented before the refund fires (for the non-`address_check` branches) so this can be repeated once per nonce, i.e., on every transaction relayed, making the drain systematic rather than a one-off edge case.

### Recommendation
Chain the relayer-fee transfer to the outcome of the action-execution promise rather than firing it as an independent promise — e.g. attach the transfer action as part of the `.then(...)` continuation in `rlp_execute_callback` (or a new callback) only after `env::promise_result(0)` is confirmed `PromiseResult::Successful`, and route the fee out of proceeds already known to have been secured, mirroring the existing caller-deposit-refund-on-failure pattern used at [5](#0-4) .

### Proof of Concept
1. Deploy the wallet contract for an ETH-implicit account and fund it with $NEAR.
2. Have the account hold some NEP-141 token balance less than an intended transfer amount.
3. As an unprivileged relayer, submit an RLP-encoded ERC-20 `transfer` (or base-token transfer with a nonzero `fee`) whose `value`/amount exceeds the wallet's actual token balance, ensuring the eventual `ft_transfer` call will panic/fail.
4. Observe: `inner_rlp_execute` creates and sends the relayer-fee transfer promise at lines 382-384 immediately; the subsequent action-promise chain later fails and `rlp_execute_callback` returns `success: false`, refunding only the caller's *attached deposit* (not the fee) per lines 296-311.
5. Repeat with successive nonces — each call drains `fee` from the wallet's $NEAR balance to the relayer even though zero token transfers ever succeed.

### Citations

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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L367-373)
```rust
            // If the action is an emulated base token or ERC-20 transfer with a non-zero fee then
            // create a promise to send the refund to the relayer. This allows any relayer
            // to safely serve base token transfers from any wallet without additional
            // on-boarding because the relayer will receive some compensation for sending
            // the transaction. Users should always verify the fee before signing a base token
            // transfer. Relayers should also verify the fee before sending to make sure the
            // user's signed transaction will refund enough to cover the relayer's gas costs.
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L374-385)
```rust
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L433-450)
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
```
