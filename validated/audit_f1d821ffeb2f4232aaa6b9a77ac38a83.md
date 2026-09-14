### Title
Wallet Contract pays relayer fee refund unconditionally, decoupled from the outcome of the emulated transfer action - ([File: runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs])

### Summary
In the NEAR Wallet Contract (used to emulate Ethereum transactions on NEAR via `rlp_execute`), the relayer fee refund for `EOABaseTokenTransfer` and `ERC20Transfer` transaction kinds is dispatched as an independent promise batch *before* the outcome of the actual transfer action is known, and it is never chained (via `.then()`) to that action's success. This mirrors the reported vesting-contract bug class: a code path that is supposed to be conditioned on a particular outcome (successful token operation) instead always executes unconditionally, causing unauthorized value movement.

### Finding Description
`inner_rlp_execute` in [1](#0-0)  unconditionally creates and dispatches a refund promise to the relayer (`context.predecessor_account_id`) whenever the parsed transaction kind is `EOABaseTokenTransfer` or `ERC20Transfer` and the fee is non-zero:

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

This `refund_promise` is a standalone promise batch — it is not `.then()`-chained to the promise that performs the actual transfer/ERC-20 action, which is constructed later in the same function at [2](#0-1) . Because the two promises are dispatched independently from the same receipt, the refund is paid to the relayer regardless of whether the subsequent action succeeds.

Concretely, the underlying action can fail for reasons entirely outside the user's control after the fee has already been committed to disburse:
- The `ERC20Transfer` path performs a `storage_balance_of` check and then `ft_transfer`/`storage_deposit` + `ft_transfer` via `nep_141_storage_balance_callback` ( [3](#0-2) ) — this can fail (e.g., insufficient token balance, non-existent token contract, `ft_transfer` panics) yet the relayer refund was already sent.
- The `EOABaseTokenTransfer` path with `address_check: Some(address)` invokes the address registrar and, if the address is unexpectedly registered, bans the relayer entirely via `create_ban_relayer_promise` ( [4](#0-3) ) — yet the fee refund promise was already dispatched to that same (faulty) relayer before this check runs.
- `rlp_execute_callback` ( [5](#0-4) ) only refunds the `caller_deposit` on `PromiseResult::Failed`; it has no mechanism to claw back the already-sent relayer fee.

This is the same bug class as the referenced Vesting contract issue: a value-transferring code path (`token.safeTransferFrom`/here, the fee-refund transfer) executes unconditionally instead of being gated on the state/outcome it is supposed to depend on (whether `token == address(1)`/here, whether the emulated action actually succeeds).

### Impact Explanation
Any relayer submitting an Ethereum-emulated transaction through `rlp_execute` can collect the `fee` refund even when the corresponding NEP-141/base-token action fails downstream, draining value from the wallet-contract account without delivering the service the fee was meant to pay for. Because `rlp_execute` is reachable directly by any RPC caller/relayer sending a signed Ethereum transaction (no privileged role required), this is a transaction-triggered, concretely exploitable fee bypass / unauthorized value movement out of the user's NEAR wallet-contract account.

### Likelihood Explanation
Likelihood is high given normal operation: any relayer (a role explicitly designed to be untrusted/interchangeable per the code's own comments) can trigger this simply by relaying a transaction whose downstream NEP-141 `ft_transfer` or registrar lookup fails (e.g., targeting a token account with insufficient balance, an unregistered/malicious NEP-141 contract, or an address that happens to be registered). No special privileges, timing races, or malicious validator/node behavior are required — a single crafted transaction plus relayer submission suffices.

### Recommendation
Do not dispatch the relayer fee-refund transfer eagerly and independently. Instead, chain it via `.then()` onto the promise(s) that perform the actual base-token/ERC-20 transfer (or pay it inside `rlp_execute_callback`/`nep_141_storage_balance_callback` only after confirming `PromiseResult::Successful`), so the fee is only paid out when the user's intended action actually completed successfully.

### Proof of Concept
1. User signs an Ethereum-style ERC-20 `transfer` calldata targeting a NEP-141 token account with a non-zero `max_fee_per_gas`/`gas_limit` (i.e., non-zero `fee`), intending to send tokens they do not actually hold (or targeting a token contract that will reject the transfer, e.g. insufficient balance).
2. A relayer submits this via `rlp_execute` with `target` = the token account.
3. `inner_rlp_execute` classifies this as `TransactionKind::EthEmulation(EthEmulationKind::ERC20Transfer { fee, .. })`, unconditionally fires `env::promise_batch_create(&context.predecessor_account_id)` + `promise_batch_action_transfer(refund_promise, *fee)` at [6](#0-5) , paying the relayer.
4. Separately, the `storage_balance_of` → `ft_transfer` chain in `nep_141_storage_balance_callback` fails because the wallet contract's token balance is insufficient (NEP-141 `ft_transfer` panics on insufficient balance).
5. `rlp_execute_callback` reports `success: false` and only refunds `caller_deposit` (unrelated attached deposit), but the relayer has already unconditionally received `fee` from the wallet contract's NEAR balance — value was extracted with no successful corresponding action.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L160-173)
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
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L195-273)
```rust
    pub fn nep_141_storage_balance_callback(
        &mut self,
        token_id: AccountId,
        receiver_id: AccountId,
        action: near_action::Action,
        caller_deposit: Option<CallerDeposit>,
    ) -> PromiseOrValue<ExecuteResponse> {
        self.has_in_flight_tx = false;
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
        };
        let current_account_id = env::current_account_id();
        let ext = WalletContract::ext(current_account_id).with_static_gas(RLP_EXECUTE_CALLBACK_GAS);
        let promise = match maybe_storage_balance {
            Some(_) => {
                // receiver_id is registered so we can send the transfer
                // without additional actions. Note: in the standard NEP-141
                // implementation it is impossible to have `Some` storage balance,
                // but have it be insufficient to transact.
                match action_to_promise(token_id, action)
                    .map(|p| p.then(ext.rlp_execute_callback(caller_deposit)))
                {
                    Ok(p) => p,
                    Err(e) => {
                        return PromiseOrValue::Value(e.into());
                    }
                }
            }
            None => {
                // receiver_id is not registered so we must call `storage_deposit` first.
                let storage_deposit_args =
                    format!(r#"{{"account_id": "{receiver_id}"}}"#).into_bytes();
                let transfer_function_call = match action {
                    near_action::Action::FunctionCall(x) => x,
                    _ => {
                        return PromiseOrValue::Value(ExecuteResponse {
                            success: false,
                            success_value: None,
                            error: Some(
                                "Expected function call action to perform NEP-141 transfer".into(),
                            ),
                        });
                    }
                };
                Promise::new(token_id)
                    .function_call(
                        "storage_deposit".into(),
                        storage_deposit_args,
                        NEP_141_STORAGE_DEPOSIT_AMOUNT,
                        NEP_141_STORAGE_DEPOSIT_GAS,
                    )
                    .function_call(
                        transfer_function_call.method_name,
                        transfer_function_call.args,
                        transfer_function_call.deposit,
                        transfer_function_call.gas,
                    )
                    .then(ext.rlp_execute_callback(caller_deposit))
            }
        };
        self.has_in_flight_tx = true;
        PromiseOrValue::Promise(promise)
    }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L276-317)
```rust
    pub fn rlp_execute_callback(
        &mut self,
        caller_deposit: Option<CallerDeposit>,
    ) -> ExecuteResponse {
        self.has_in_flight_tx = false;
        let n = env::promise_results_count();

        if n == 0 {
            // `rlp_execute_callback` is called directly in the case of an emulated self-transfer.
            return ExecuteResponse { success: true, success_value: None, error: None };
        } else if n > 1 {
            return ExecuteResponse {
                success: false,
                success_value: None,
                error: Some(format!(
                    "Invariant violation: this callback comes after a single promise. n={n}"
                )),
            };
        }

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
