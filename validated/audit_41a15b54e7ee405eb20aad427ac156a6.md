### Title
WalletContract pays relayer fee unconditionally before confirming the corresponding action succeeds - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
The external report describes TesseraSwap sending out one side of a swap (USDC) before verifying/collecting the corresponding repayment (WETH) from the callback, letting the attacker keep the spread. The nearcore Wallet Contract (`near-wallet-contract`) has an analogous pattern in `inner_rlp_execute`: for `EOABaseTokenTransfer`/`ERC20Transfer` transactions carrying a non-zero `fee`, it fires a **separate, independent** `promise_batch_create`/`promise_batch_action_transfer` paying the relayer (`predecessor_account_id`) *before* creating the promise that actually performs the requested transfer action, and the two promises are not chained together.

### Finding Description
In `inner_rlp_execute` (`runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs:367-385`), once the RLP transaction is parsed into an `EOABaseTokenTransfer`/`ERC20Transfer` action with a non-zero `fee`: [1](#0-0) 

The relayer fee-refund promise (`env::promise_batch_create` + `env::promise_batch_action_transfer`) is created and submitted independently of the actual action promise that is built afterward: [2](#0-1) 

Because these are two independent promises produced by the *same* function call (not a `.then()` chain, and the fee promise is not made contingent on the action promise's `rlp_execute_callback` result), both are dispatched to the runtime regardless of whether the underlying action (the base-token/ERC-20 transfer to `target`) ultimately succeeds or fails. `rlp_execute_callback` (`runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs:276-317`) only refunds the *caller's attached deposit* (`caller_deposit`) on failure — it has no knowledge of, and cannot claw back, the fee that was already sent to the relayer via the unrelated promise created earlier in `inner_rlp_execute`.

This mirrors the TesseraSwap bug class: a value transfer (the relayer's fee) is committed optimistically before the corresponding obligation (the actual requested transfer completing) is confirmed, allowing the counterparty (the relayer) to collect value even when the paired action fails.

### Impact Explanation
If the main emulated transfer action fails (e.g., insufficient balance at execution time, invalid target, or any other post-parse failure) after the fee promise has already been dispatched, the relayer keeps a fee for a service that was not rendered, and the eth-implicit account/wallet owner loses NEAR with no corresponding transfer taking place. Since `rlp_execute` explicitly serializes transactions (`has_in_flight_tx` guard) but does not gate the fee payment on the transfer's success, a malicious or opportunistic relayer could submit borderline transactions engineered to fail post-parse (e.g., targeting accounts/conditions that cause the inner action to fail after the fee-check point) to repeatedly extract fees without ever completing transfers — an unauthorized value movement out of the wallet owner's controlled funds.

### Likelihood Explanation
This requires a relayer (an unprivileged submitter of the RLP-encoded transaction to the Wallet Contract via `rlp_execute`) crafting a transaction whose fee is non-zero and whose main action fails after the fee-promise dispatch point but before/without producing a corresponding successful transfer. This is reachable by any account able to call `rlp_execute` on a deployed Wallet Contract instance, i.e., a standard external call — no validator, network, or sync-layer privilege is required.

### Recommendation
Chain the fee-refund promise so it only fires after (or is conditioned on) successful completion of the main action, e.g. by moving the fee transfer into `rlp_execute_callback` (or a dedicated callback) that inspects `env::promise_result` for the main action before crediting the relayer, mirroring the existing caller-deposit-refund-on-failure logic already present in `rlp_execute_callback`.

### Proof of Concept
Not independently reproducible from the indexed context alone: verifying the exact exploit path requires confirming (a) the precise source of `fee` funds (whether debited from the wallet's own NEAR balance or from the transfer amount) and (b) the exact conditions under which the main action can fail post-parse while the initial fee promise has already been queued. Note: due to index size limits, the full contents of `runtime/near-wallet-contract/implementation/wallet-contract/src/eth_emulation.rs` and `types.rs` (where `fee` is computed and typed) were not fully retrievable in this session — a Devin session with full repository access would be needed to trace the fee-accounting path precisely and construct a concrete PoC transaction sequence.

### Citations

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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L412-471)
```rust
    let promise = match transaction_kind {
        TransactionKind::EthEmulation(EthEmulationKind::EOABaseTokenTransfer {
            address_check: Some(address),
            ..
        }) => {
            let callback_gas = ADDRESS_CHECK_CALLBACK_GAS.saturating_add(action.gas());
            let ext = WalletContract::ext(current_account_id).with_static_gas(callback_gas);
            let address_registrar = {
                let account_id = ADDRESS_REGISTRAR_ACCOUNT_ID
                    .trim()
                    .parse()
                    .unwrap_or_else(|_| env::panic_str("Invalid address registrar"));
                ext_registrar::ext(account_id).with_static_gas(REGISTRAR_LOOKUP_GAS)
            };
            let address = format!("0x{}", hex::encode(address));
            address_registrar.lookup(address).then(ext.address_check_callback(
                target,
                action,
                caller_deposit,
            ))
        }
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
    };
```
