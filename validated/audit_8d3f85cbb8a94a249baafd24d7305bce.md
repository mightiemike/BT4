## Analysis

The Envoy CVE is a lifecycle/cleanup bug: a stateful filter's cleanup depends on a "normal completion" path, but an unusual failure path (local reply short-circuits the extension) causes the object to be used/torn down in a way that skips proper lifecycle bookkeeping, crashing the process (DoS). The closest reachable analog in `nearcore--019` is the in-flight-transaction lock in the NEAR wallet contract, which is directly reachable by any transaction signer (relayer or account owner) submitting an `rlp_execute` call.

### Title
Wallet contract's `has_in_flight_tx` lock is permanently stuck if a promise callback fails after resetting the flag but before completing - ([File: runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs])

### Summary
`WalletContract::rlp_execute` guards against concurrent execution using a `has_in_flight_tx` boolean that must be reset to `false` by whichever callback finishes the multi-step promise chain [1](#0-0) . Every callback (`address_check_callback`, `nep_141_storage_balance_callback`, `rlp_execute_callback`) resets the flag as its *first* statement, then proceeds to build and schedule the next promise in the chain using caller/owner-controlled gas values [2](#0-1) [3](#0-2) . Because NEAR's runtime treats a single function-call receipt's state mutations as atomic — a failing/panicking receipt is rolled back in its entirety via `state_update.rollback()` — any panic occurring *after* the flag reset but before the callback returns normally discards the flag reset along with everything else, leaving `has_in_flight_tx = true` on-chain forever [4](#0-3) .

### Finding Description
The gas budgets attached to each callback are computed as fixed overhead plus attacker/owner-supplied `action.gas()` (e.g. `ADDRESS_CHECK_CALLBACK_GAS.saturating_add(action.gas())`, `NEP_141_STORAGE_BALANCE_CALLBACK_GAS.saturating_add(action.gas())`) [5](#0-4) [6](#0-5) . Inside the callback, after `self.has_in_flight_tx = false;` runs, the same `action.gas()` value is re-used to attach gas to the forwarded action promise, with only a small fixed slack (5 Tgas) reserved for the callback's own execution [7](#0-6) [8](#0-7) . For the ERC-20 path, `target`/`token_id` is fully attacker-controlled (any deployable NEAR account can be named as the "token"), so the JSON payload returned from `storage_balance_of` that the callback deserializes with `serde_json::from_slice` is also attacker-controlled [9](#0-8) . A malicious "token" contract can return a payload engineered to consume more than the small fixed slack while being parsed/rejected, causing the callback receipt to fail (gas exhaustion is a receipt-level panic in NEAR). Because the flag reset and the subsequent promise scheduling are part of the same atomic receipt, this failure reverts `has_in_flight_tx` back to `true`. From then on, `rlp_execute` on line 97-105 unconditionally short-circuits with `"transaction already in progress"` for every subsequent call, and since it never schedules a new promise, no future callback can ever run to reset the flag again [10](#0-9) .

### Impact Explanation
This permanently disables the meta-transaction (`rlp_execute`) capability of the affected wallet-contract account — a transaction-triggered halt of that specific account's Ethereum-emulation relay path, with no recovery mechanism since the lock can only be cleared by a callback that will never run. This matches the "permanently frozen funds" / "transaction-triggered halt" impact classes: the account remains fully controlled by its owner via normal NEAR access keys, but the wallet-contract-mediated ETH transaction flow (and any funds/allowances routed exclusively through it) becomes permanently unusable.

### Likelihood Explanation
Reachable from a single external transaction/RLP payload submitted by any account (no validator, sync, or privileged access required) targeting the ERC20Transfer emulation path with an attacker-deployed fake token contract, or via the address-check path with a sufficiently crafted `action.gas()` value. Exploitability depends on precisely engineering the gas slack overrun (5 Tgas reserved for callback logic vs. attacker-influenced parsing cost), which requires empirical gas-cost tuning but is plausible given NEAR's per-byte JSON/host-call gas costs and the attacker's full control over the "token" contract's response bytes and size.

### Recommendation
Do not reset `has_in_flight_tx` to `false` at the top of each callback. Instead, compute the flag's final value only after all fallible operations in the callback (including promise construction/gas attachment) have succeeded, and set it exactly once right before returning — so any panic partway through the callback leaves `has_in_flight_tx` in its pre-callback state without special-casing, but structure the code so recovery is possible (e.g., add a permissionless "unstick" path that resets the flag if no in-flight receipt could plausibly still be pending, or bound `action.gas()` far enough below the callback's own execution budget that gas-exhaustion mid-callback cannot occur).

### Proof of Concept
1. Attacker deploys a NEAR contract `evil-token.near` implementing `storage_balance_of` to return an oversized/malformed JSON blob as its return value.
2. Attacker (or the wallet owner, tricked into signing) submits an RLP-encoded Ethereum transaction via `rlp_execute(target = evil-token.near, tx_bytes_b64 = <ERC20Transfer-emulated tx>)`.
3. `inner_rlp_execute` schedules `storage_balance_of` on `evil-token.near` followed by `nep_141_storage_balance_callback`, setting `has_in_flight_tx = true` [11](#0-10) .
4. `evil-token.near` returns the crafted oversized payload; `nep_141_storage_balance_callback` runs, sets `has_in_flight_tx = false`, then attempts `serde_json::from_slice` and/or subsequent promise scheduling, exhausting the small gas slack and panicking.
5. The entire callback receipt is rolled back per NEAR's atomic receipt semantics, restoring `has_in_flight_tx = true`.
6. Every subsequent call to `rlp_execute` on this wallet-contract account now immediately returns `"transaction already in progress, please try again later"` forever, since no promise is ever scheduled to run a callback that could clear the flag.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L46-55)
```rust
pub struct WalletContract {
    pub nonce: u64,
    /// Tracks whether a transaction is currently being executed
    /// (i.e. has receipts that have not yet resolved).
    /// Invariant: `has_in_flight_tx` must be `true` when a mutable method
    /// of this contract returns a promise and `false` otherwise (except
    /// for the check if a transaction is already in flight at the beginning
    /// of `rlp_execute`).
    pub has_in_flight_tx: bool,
}
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L94-105)
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
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L134-192)
```rust
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L417-424)
```rust
            let callback_gas = ADDRESS_CHECK_CALLBACK_GAS.saturating_add(action.gas());
            let ext = WalletContract::ext(current_account_id).with_static_gas(callback_gas);
            let address_registrar = {
                let account_id = ADDRESS_REGISTRAR_ACCOUNT_ID
                    .trim()
                    .parse()
                    .unwrap_or_else(|_| env::panic_str("Invalid address registrar"));
                ext_registrar::ext(account_id).with_static_gas(REGISTRAR_LOOKUP_GAS)
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

**File:** protocol-model/spec/runtime-execution.md (L69-70)
```markdown
6. **Refunds** (see below): system-predecessor receipts (refund receipts) are free — no refund generated, and a failed refund burns its deposit into `other_burnt_amount` (`runtime/runtime/src/lib.rs:929`). Otherwise `refund_unspent_gas_and_deposits` runs (`:943`).
7. **Commit or rollback**: success commits with `ReceiptProcessing`; failure calls `state_update.rollback()`, discarding all state changes from the receipt (`runtime/runtime/src/lib.rs:961`-`970`).
```
