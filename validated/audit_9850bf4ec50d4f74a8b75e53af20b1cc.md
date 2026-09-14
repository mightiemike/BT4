### Title
Hardcoded gas constants for cross-contract callbacks in the NEAR Wallet Contract can leave transactions permanently stuck if actual execution cost changes - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
The audit finding flags Solidity's `transfer()` for hardcoding a fixed 2300 gas stipend that can break if the EVM's gas cost schedule changes in a hard fork. The NEAR Wallet Contract (the eth-emulation "wallet contract" reachable by any relayer submitting an RLP-encoded Ethereum transaction) has the analogous pattern: it hardcodes fixed `Gas` amounts for the callbacks it schedules after cross-contract calls, rather than computing/attaching them dynamically or using the unspent-gas-weight mechanism (`promise_batch_action_function_call_weight`) documented as the recommended alternative in nearcore's own gas model.

### Finding Description
`WalletContract` hardcodes several `Gas` constants used as `with_static_gas(...)` for its own callback methods: [1](#0-0) 

These constants (`RLP_EXECUTE_CALLBACK_GAS`, `ADDRESS_CHECK_CALLBACK_GAS`, `NEP_141_STORAGE_BALANCE_CALLBACK_GAS`, etc.) are attached as static gas to the callbacks scheduled from `rlp_execute`/`inner_rlp_execute`: [2](#0-1) 

and to the intermediate callbacks `address_check_callback` and `nep_141_storage_balance_callback`: [3](#0-2) [4](#0-3) 

This is directly analogous to `transfer()`'s fixed 2300-gas stipend: nearcore documents that wasm/host function gas costs are runtime-configurable parameters that can and do change across protocol versions (see `docs/architecture/how/gas.md`), and that hardcoded gas budgets for cross-contract calls are fragile: "Contract developers also have to pick the attached gas values when their contract calls another contract... they have to work with the unspent gas attached to the current call," and NEP-264 was introduced specifically because static-gas amounts are error-prone, allowing weight-based unspent gas distribution instead. [5](#0-4) 

The Wallet Contract sets `has_in_flight_tx = true` before returning any `Promise`, and only resets it to `false` inside the callback (`rlp_execute_callback`, `address_check_callback`, `nep_141_storage_balance_callback`, or `ban_relayer`): [6](#0-5) [7](#0-6) 

If a hardcoded static-gas budget attached to a callback (e.g. `RLP_EXECUTE_CALLBACK_GAS = 5 Tgas`, `ADDRESS_CHECK_CALLBACK_GAS`, `NEP_141_STORAGE_BALANCE_CALLBACK_GAS`) turns out to be insufficient — because the underlying wasm/host gas cost parameters were re-tuned by a protocol upgrade, or because the callback's execution path grows more expensive (e.g. additional logic added to `address_check_callback`/`nep_141_storage_balance_callback` in a future contract redeploy, or a heavier `PromiseResult` payload requiring more deserialization gas) — the callback receipt itself would run out of gas (`GasExceeded`/`FunctionCallError`) and abort with no state changes persisted. Crucially, `has_in_flight_tx` is only reset inside the callback body; if the callback function panics/aborts due to insufficient attached gas, `self.has_in_flight_tx` is **never reset to `false`**, since the entire callback execution (including the state write of `self.has_in_flight_tx = false` at line 280 or 202) is rolled back as part of the failed receipt.

### Impact Explanation
Because `rlp_execute` unconditionally rejects any new transaction while `has_in_flight_tx` is `true` (`if self.has_in_flight_tx { return ...error "transaction already in progress"... }`), an out-of-gas callback caused by an insufficient hardcoded gas constant would permanently lock the Wallet Contract account: no future `rlp_execute` call could ever succeed for that account, and any deposit already forwarded via the intermediate cross-contract calls could become unreachable/lost since the corresponding `caller_deposit` refund logic in `rlp_execute_callback` only runs when the callback executes to completion. This is a transaction/receipt-triggered halt of a specific account's meta-transaction relay capability and potential permanent freezing of user funds routed through the wallet contract, matching the "permanently frozen funds" / "transaction-triggered halt" criteria.

### Likelihood Explanation
This requires either (a) a future protocol-version change to gas cost parameters for the specific host functions used inside these callback bodies (deserialization, `env::promise_result`, `Promise::new().function_call()` construction, storage reads/writes) pushing actual usage above the hardcoded 5 Tgas budgets, or (b) a future code change to the callback bodies that increases their gas usage without a corresponding bump to the hardcoded constants. Given that nearcore explicitly documents gas costs as protocol-version-dependent and subject to change (e.g. the gas refund penalty and burnt-gas-reward changes noted in `docs/architecture/how/gas.md`), and that the wallet contract is versioned/redeployed code (not the runtime itself), this is a realistic, if not currently triggered, latent risk introduced by the hardcoded-static-gas design pattern rather than using the dynamic/weighted gas mechanism the runtime provides.

### Recommendation
Replace hardcoded static-gas budgets for the wallet contract's callbacks with the gas-weight mechanism (`with_unused_gas_weight` / `promise_batch_action_function_call_weight`) so that callbacks receive a share of whatever gas remains unused at runtime rather than a fixed hardcoded Tgas amount, consistent with the design intent of NEP-264 described in `docs/architecture/how/gas.md`. At minimum, add a safety margin and/or a monitoring/alerting process to detect when callback gas usage approaches the hardcoded budget across protocol upgrades, and ensure `has_in_flight_tx` cannot get stuck by considering an explicit unlock/reset path (e.g., a permissioned or time-locked recovery method) for the rare case a callback receipt fails purely due to gas exhaustion.

### Proof of Concept
Conceptual PoC (cannot be executed without a live/testnet protocol-version gas-cost change or a code change to callback bodies, so this is a structural argument rather than a reproduced exploit):
1. Deploy `WalletContract` for an eth-implicit account; submit an RLP transaction whose `TransactionKind` is `ERC20Transfer` to an unregistered receiver, causing `nep_141_storage_balance_callback` to be scheduled with `NEP_141_STORAGE_BALANCE_CALLBACK_GAS` (`5 Tgas + NEP_141_STORAGE_DEPOSIT_GAS + RLP_EXECUTE_CALLBACK_GAS`) as in [8](#0-7) .
2. Assume a protocol upgrade increases the wasm/host gas costs charged for `serde_json` deserialization of the `StorageBalance` promise result or for constructing/chaining the two `function_call`s in [9](#0-8) , such that the callback's actual gas usage now exceeds the hardcoded budget.
3. The callback receipt aborts with `GasExceeded` before `self.has_in_flight_tx = false;` at line 202 is committed.
4. Any subsequent call to `rlp_execute` for that account is rejected by the `has_in_flight_tx` guard at [10](#0-9) , permanently freezing the account's ability to relay further Ethereum-emulated transactions, and any deposit tied to the aborted flow is not refunded because the refund path only fires from within a successfully-executing `rlp_execute_callback`.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L34-41)
```rust
const NEP_141_STORAGE_DEPOSIT_GAS: Gas = Gas::from_tgas(5);
const NEP_141_STORAGE_BALANCE_OF_GAS: Gas = Gas::from_tgas(5);
const REGISTRAR_LOOKUP_GAS: Gas = Gas::from_tgas(5);
const RLP_EXECUTE_CALLBACK_GAS: Gas = Gas::from_tgas(5);
const ADDRESS_CHECK_CALLBACK_GAS: Gas = Gas::from_tgas(5).saturating_add(RLP_EXECUTE_CALLBACK_GAS);
const NEP_141_STORAGE_BALANCE_CALLBACK_GAS: Gas = Gas::from_tgas(5)
    .saturating_add(NEP_141_STORAGE_DEPOSIT_GAS)
    .saturating_add(RLP_EXECUTE_CALLBACK_GAS);
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L94-128)
```rust
        // To ensure user actions are executed in the desired order,
        // having multiple transactions in flight at the same time is
        // not allowed.
        if self.has_in_flight_tx {
            return PromiseOrValue::Value(ExecuteResponse {
                success: false,
                success_value: None,
                error: Some(
                    "Error: transaction already in progress, please try again later.".into(),
                ),
            });
        }
        let current_account_id = env::current_account_id();
        let predecessor_account_id = env::predecessor_account_id();
        let result = inner_rlp_execute(
            current_account_id.clone(),
            predecessor_account_id,
            target,
            tx_bytes_b64,
            &mut self.nonce,
        );

        match result {
            Ok(promise) => {
                self.has_in_flight_tx = true;
                PromiseOrValue::Promise(promise)
            }
            Err(Error::Relayer(_)) if env::signer_account_id() == current_account_id => {
                let promise = create_ban_relayer_promise(current_account_id);
                self.has_in_flight_tx = true;
                PromiseOrValue::Promise(promise)
            }
            Err(e) => PromiseOrValue::Value(e.into()),
        }
    }
```

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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L194-273)
```rust
    #[private]
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L275-317)
```rust
    #[private]
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

**File:** docs/architecture/how/gas.md (L116-142)
```markdown
Contract developers also have to pick the attached gas values when their
contract calls another contract. They cannot buy additional gas, they have to
work with the unspent gas attached to the current call. They can check how much
gas is left by subtracting the `used_gas()` from the `prepaid_gas()` host
function results. But they cannot use all the available gas, since that would
prevent the current function call from executing to the end.

The gas attached to a function can be at most `max_total_prepaid_gas`, which is
300 Tgas since the mainnet launch. Note that this limit is per
`SignedTransaction`, not per function call. In other words, batched function
calls share this limit.

There is also a limit to how much single call can burn, `max_gas_burnt`, which
used to be 200 Tgas but has been increased to 300 Tgas in protocol version 52.
(Note: When attaching gas to an outgoing function call, this is not counted as
gas burnt.) However, given a call can never burn more than was attached anyway,
this second limit is obsolete with the current configuration where the two limits
are equal.

Since protocol version 53, with the stabilization of
[NEP-264](https://github.com/near/NEPs/blob/master/neps/nep-0264.md), contract
developers do not have to specify the absolute amount of gas to attach to calls.
`promise_batch_action_function_call_weight` allows to specify a ratio of unspent
gas that is computed after the current call has finished. This allows attaching
100% of unspent gas to a call. If there are multiple calls, this allows
attaching an equal fraction to each, or any other split as defined by the weight
per call.
```
