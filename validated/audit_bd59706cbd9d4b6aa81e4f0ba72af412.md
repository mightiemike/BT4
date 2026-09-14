## Title
Wallet Contract sends the relayer fee unconditionally before knowing if the emulated transfer/target succeeds - a faulty/self-serving relayer can collect the fee while the underlying action fails - ([File: runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs])

### Summary
In `inner_rlp_execute` the fee owed to a relayer for an emulated base-token transfer or ERC-20 transfer is dispatched as an independent, unconditional `Promise`/receipt (`env::promise_batch_create` + `env::promise_batch_action_transfer`) at the moment the transaction is parsed, before the actual action (the `Transfer`/`ft_transfer` call, or the registrar address-check) has executed or resolved [1](#0-0) . Because this fee-transfer promise is a separate receipt from the chained action promise, it is scheduled and paid to the predecessor (relayer) regardless of whether the subsequent action, registrar validation, or NEP-141 transfer ultimately succeeds or fails.

### Finding Description
`inner_rlp_execute` parses the RLP-encoded Ethereum transaction into a Near `Action` plus a `TransactionKind`. For `EOABaseTokenTransfer` and `ERC20Transfer` kinds carrying a non-zero `fee`, the code immediately creates and dispatches a transfer promise paying the fee to `context.predecessor_account_id` (the relayer): [2](#0-1) 

This happens *before* the function later builds the "real" promise chain that performs the actual action and eventually resolves through `rlp_execute_callback` / `address_check_callback` / `nep_141_storage_balance_callback` [3](#0-2) .

Because Near dispatches every promise/receipt created during a function-call execution as an independent outgoing receipt (only the *returned* promise chain determines the call's return value, not which receipts get sent), the fee-transfer receipt to the relayer is unconditionally sent alongside the "real" action's promise chain. There is no dependency (via `.then`) linking the fee payment to the success of the action or to the outcome of the registrar address-check.

Concretely:
- For `EOABaseTokenTransfer { address_check: Some(address), fee }` (case where the target is another wallet contract whose registrar membership hasn't yet been checked - i.e., the "possibly-faulty-relayer" case), the fee is paid out immediately, while the actual check of whether the relayer supplied a valid `target` happens later in `address_check_callback` [4](#0-3) . If the registrar lookup determines the relayer was "faulty" (i.e., it should have used the registered account instead of the eth-implicit target), the contract only revokes the relayer's access key via `create_ban_relayer_promise` - it never claws back the fee that was already transferred out in the earlier unconditional promise [5](#0-4) .
- For `ERC20Transfer { fee, .. }`, the fee is likewise sent out before the storage-balance check / `ft_transfer` call is attempted; if the token call ultimately fails (e.g., insufficient token balance, bad token contract, receiver rejects, gas exhaustion), `rlp_execute_callback` only refunds the *caller's attached $NEAR deposit* (`caller_deposit`) - it has no mechanism to refund the fee already paid to the relayer [6](#0-5) .

This is the direct analog of the external report's root cause: value ("leftover"/conditional tokens - here the relayer fee) is disbursed without first confirming that the operation it is meant to compensate for actually completes successfully, and the disbursement cannot be reclaimed afterward. An attacker acting as relayer can trigger this repeatedly.

### Impact Explanation
A malicious or compromised relayer that is permitted to call `rlp_execute` on a user's Wallet Contract (either as an arbitrary external caller, or as a "faulty" relayer using a `FunctionCallPermission` access key registered for `rlp_execute`) can drain the wallet's $NEAR fee payments without providing the corresponding transaction relay service:
- It can submit a base-token-transfer transaction whose `target` triggers the `address_check: Some(_)` path (unregistered eth-implicit account when a named registrar entry should have been used). The wallet pays the fee to the relayer immediately; the wallet only discovers afterward (via the registrar callback) that the relayer was "faulty" and can merely revoke its access key - the fee is already gone.
- Similarly for ERC-20 transfers, if the underlying `ft_transfer`/`storage_deposit` sequence fails for any reason, the relayer still keeps the fee.

This is unauthorized value movement out of the user's Wallet Contract account with no refund path - a concrete fund-loss bug reachable by a single crafted RLP transaction/relay call.

### Likelihood Explanation
High for a relayer intentionally exploiting this: no special privileges beyond being allowed to call `rlp_execute` (any account can call it as an external caller with `caller_deposit`, or an authorized relayer with an access key) are needed. The `address_check: Some(_)` path is reachable whenever the user signs a transaction whose payload happens to be interpretable as a Near action but is directed at another wallet-contract-style eth-implicit account not yet vetted by the registrar - a state fully controllable by the relayer choosing `target`. The ERC-20 failure path is reachable any time the downstream `ft_transfer`/`storage_deposit` calls fail for a reason outside the wallet contract's control (e.g., token contract quirks, insufficient balance, gas). No race condition or timing dependency is required since the fee promise is dispatched unconditionally in the same function-call execution as the action attempt.

### Recommendation
Do not create the relayer-fee transfer as an independent, unconditional promise. Instead, chain the fee payment after the action's outcome is known (e.g., dispatch the fee-transfer only inside the success branch of `rlp_execute_callback`/`address_check_callback`, after confirming `PromiseResult::Successful`), or bundle the fee transfer into the same promise/receipt actions that get executed only if the preceding registrar/action calls succeed. If the underlying action fails or the relayer is determined to be faulty, the fee should either not be sent at all, or should be refunded together with the `caller_deposit` refund logic already present in `rlp_execute_callback`.

### Proof of Concept
1. A relayer (attacker) obtains or is granted an `rlp_execute`-scoped access key on victim's Wallet Contract (or calls as any external caller supplying `caller_deposit`).
2. The relayer submits an RLP-encoded Ethereum transaction, signed by the wallet owner, whose payload parses as `ParsableTransactionKind::EthEmulation` (e.g., an ERC-20-transfer-shaped payload) but where the relayer deliberately sets `target` to an eth-implicit account address that has *not* been registered in the address registrar, and includes a non-zero fee (`tx.max_fee_per_gas * tx.gas_limit`).
3. `parse_rlp_tx_to_action` returns `TransactionKind::EthEmulation(EthEmulationKind::EOABaseTokenTransfer { address_check: Some(address), fee })` (per `internal.rs` lines 107-122).
4. `inner_rlp_execute` immediately creates and dispatches the fee-transfer promise to the relayer (`predecessor_account_id`) at lines 374-385 of `lib.rs`, before the registrar lookup / action ever executes.
5. `address_check_callback` later resolves the registrar lookup, discovers the target actually corresponds to a registered named account, concludes the relayer was "faulty," and merely bans the relayer's key via `create_ban_relayer_promise` (lines 160-171 of `lib.rs`).
6. The relayer has already received the fee transfer from step 4 and suffers no consequence beyond losing the now-banned access key (which is expected to be a cost of doing business per fee amount received, especially if the relayer submits many such transactions from many stolen/borrowed keys before detection, or is an external caller not using an access key at all).

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L130-192)
```rust
    /// Callback after checking if an address is contained in the registrar.
    /// This check happens when the target is another eth implicit account to
    /// confirm that the relayer really did check for a named account with that address.
    #[private]
    pub fn address_check_callback(
        &mut self,
        target: AccountId,
        action: near_action::Action,
        caller_deposit: Option<CallerDeposit>,
    ) -> PromiseOrValue<ExecuteResponse> {
        self.has_in_flight_tx = false;
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L412-472)
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
    Ok(promise)
```
