## Title
Relayer fee refund in `WalletContract::rlp_execute` is transferred unconditionally before the underlying action succeeds, causing loss of user funds — (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
`inner_rlp_execute` schedules the relayer's fee refund as an independent promise batch immediately after parsing the transaction, decoupled from the promise chain that performs the user's actual intended action (base-token transfer or ERC-20 emulated transfer). If the underlying action later fails, the relayer fee has already been paid, mirroring the `FootiumPrizeDistributor.claimERC20Prize()` pattern of updating accounted state/value transfer before confirming the corresponding operation succeeded.

### Finding Description
In `inner_rlp_execute`, once parsing succeeds, the code unconditionally creates a *separate* promise batch to refund the relayer's fee: [1](#0-0) 

This refund batch (`env::promise_batch_create` / `env::promise_batch_action_transfer`) is not chained via `.then()` to the promise that actually performs the user's requested transfer/ERC-20 call. The actual action is only attempted afterward, in a completely separate promise built later in the same function: [2](#0-1) 

Because both promises are dispatched from the same receipt but are independent branches, the relayer refund executes regardless of whether the paired action (`rlp_execute_callback`, `nep_141_storage_balance_callback`, or `address_check_callback`) later reports `PromiseResult::Failed`: [3](#0-2) 

Notably, this also affects the address-check path used to detect a *faulty relayer* (`EOABaseTokenTransfer { address_check: Some(_), .. }`). Even though the code comment states "we still do not know if the transaction has a relayer error", the fee refund is dispatched before the registrar lookup resolves: [4](#0-3) 

If the registrar check subsequently reveals the target is an existing named account (a relayer error that leads to `create_ban_relayer_promise`), the relayer has already been paid despite the transaction failing: [5](#0-4) 

Similarly, for `ERC20Transfer`, the fee refund fires immediately while the `storage_balance_of` → `storage_deposit`/`ft_transfer` chain executes afterward and can fail independently (e.g., insufficient token balance, token contract panics, or the callback runs out of attached gas): [6](#0-5) 

### Impact Explanation
The user's wallet contract balance is unconditionally decremented to compensate the relayer even when the intended value transfer to the recipient never completes. Any relayer (an unprivileged party that merely calls `rlp_execute` and pays gas) can submit a transaction that is guaranteed or likely to fail post-refund (e.g., pointing to a token/account state that causes the second leg of the promise chain to fail) and still collect the fee, draining the user's Near balance without delivering the corresponding action. This is a concrete unauthorized value movement / loss-of-funds scenario reachable from a single submitted transaction.

### Likelihood Explanation
Any relayer submitting an `rlp_execute` transaction on behalf of a wallet-contract-controlled account controls whether the paired action succeeds (e.g., they can pick to relay right when the sender's on-chain FT balance is insufficient, or target an address that fails the registrar check). Because the fee promise is not gated on the action's outcome, exploitation requires no special privilege beyond being able to submit (or convince someone to submit) a transaction — which is exactly the relayer/meta-transaction-sender role this contract is designed to serve.

### Recommendation
Chain the relayer fee refund to the outcome of the underlying action instead of firing it unconditionally: only transfer the fee to the relayer inside the success branch of `rlp_execute_callback` (or an equivalent final callback), after confirming `PromiseResult::Successful`. Alternatively, use `Promise::and`/`.then()` composition so the refund promise is scheduled after — and conditioned on — the paired action's resolution.

### Proof of Concept
1. A relayer submits an RLP-encoded ERC-20 `transfer` transaction on behalf of a wallet-contract account with a non-zero relayer `fee`, targeting a receiver that will cause `ft_transfer` to fail post-refund (e.g., token contract intentionally reverts, or gas for the two-call chain is insufficient).
2. `inner_rlp_execute` parses the transaction, increments the nonce, and immediately creates and dispatches the fee-refund promise batch to the relayer (`lib.rs:381-384`).
3. Separately, the `storage_balance_of` → `ft_transfer` promise chain is dispatched; the `ft_transfer` (or its wrapping call) fails.
4. `rlp_execute_callback`/`nep_141_storage_balance_callback` reports `ExecuteResponse { success: false, ... }`, but the relayer has already received the fee in step 2 — the wallet owner's balance decreased with no successful action performed.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L160-192)
```rust
        let current_account_id = env::current_account_id();
        let promise = if maybe_account_id.is_some() {
            // We intentionally do not increment the nonce in this case because the
            // error is caused by a faulty relayer, not the user. An honest relayer
            // may still be able to successfully send the user's intended transaction.
            if env::signer_account_id() == current_account_id {
                create_ban_relayer_promise(current_account_id)
            } else {
                return PromiseOrValue::Value(ExecuteResponse {
                    success: false,
                    success_value: None,
                    error: Some("Invalid target: target is address corresponding to existing named account_id".into()),
                });
            }
        } else {
            // We must increment the nonce at this point to prevent replay of the transaction.
            // Recall that the nonce was not incremented in `inner_rlp_execute` in the case that
            // the registrar contract was called (i.e. in the case we end up inside this callback).
            self.nonce = self.nonce.saturating_add(1);
            let ext =
                WalletContract::ext(current_account_id).with_static_gas(RLP_EXECUTE_CALLBACK_GAS);
            match action_to_promise(target, action)
                .map(|p| p.then(ext.rlp_execute_callback(caller_deposit)))
            {
                Ok(p) => p,
                Err(e) => {
                    return PromiseOrValue::Value(e.into());
                }
            }
        };
        self.has_in_flight_tx = true;
        PromiseOrValue::Promise(promise)
    }
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L358-366)
```rust
            if let TransactionKind::EthEmulation(EthEmulationKind::EOABaseTokenTransfer {
                address_check: Some(_),
                ..
            }) = &transaction_kind
            {
            } else {
                *nonce = nonce.saturating_add(1);
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
