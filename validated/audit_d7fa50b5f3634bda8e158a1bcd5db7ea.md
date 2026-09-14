Confirmed: when a `FunctionCall` action fails (e.g. out-of-gas), the whole receipt fails and its state changes are rolled back via `TrieUpdate::rollback`, as documented in `runtime/runtime/AGENTS.md:64` ("When a receipt fails, its state changes are rolled back using `TrieUpdate`") and exercised in `runtime/runtime/src/tests/apply.rs:2766-2844` (`test_deploy_and_call_in_apply_with_failed_call`) and `apply.rs:6215-6296` (rolled-back receipt leaves no trace of any writes the failing action's call made). This confirms that any panic/abort inside a NEAR callback discards *all* state writes made earlier in that same function call, including a flag reset performed at the top of the function.

### Title
`WalletContract` in-flight-transaction flag can be permanently stuck `true` if a promise callback aborts, permanently bricking `rlp_execute` - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
`WalletContract::rlp_execute` sets `has_in_flight_tx = true` before dispatching a promise chain, and relies on exactly one of the follow-up private callbacks (`address_check_callback`, `nep_141_storage_balance_callback`, or `rlp_execute_callback`) to reset it to `false` as their very first statement [1](#0-0) , mirroring the `AutoRedemption::lastRequestId` pattern from the external report where a single "pending request" flag must always be reset by a callback that is not guaranteed to complete cleanly.

### Finding Description
Every mutating entry point checks `has_in_flight_tx` and unconditionally rejects new transactions while it is `true`: [2](#0-1) 

The only way to clear this flag is for one of the scheduled callbacks to run to completion and hit its `self.has_in_flight_tx = false;` line: [3](#0-2) [4](#0-3) [5](#0-4) 

However, in NEAR, a `FunctionCall` action's state changes are only durable if the action succeeds; if it fails (out-of-gas, an unhandled trap, or any other panic), the entire receipt's `TrieUpdate` is rolled back, discarding *every* write performed during that call, as documented at `runtime/runtime/AGENTS.md:64` and demonstrated by `runtime/runtime/src/tests/apply.rs:2766-2844` and `apply.rs:6215-6296`, where a failed action's state (including things set at the very start of execution) never lands in the trie. This is functionally identical to a Solidity `revert` discarding all storage writes made in the current call, including the reset flag.

Each callback does non-trivial work *after* resetting the flag: `address_check_callback` and `nep_141_storage_balance_callback` deserialize an arbitrary cross-contract JSON response, construct a further `Promise`, and schedule another `.then()` call [6](#0-5) [7](#0-6) , and `rlp_execute_callback` creates a refund promise batch on failure [8](#0-7) . If gas runs out or a host call traps anywhere in this post-reset logic — which is influenced by attacker-controlled inputs such as: the response returned by the address registrar or a NEP-141 token contract (`target` is user-controlled per `rlp_execute`'s `target` parameter, and NEP-141 tokens/registrars are arbitrary external contracts that can be pointed at by anyone submitting or relaying a transaction) — the callback aborts and the runtime rolls back the entire call, silently restoring `has_in_flight_tx` to `true`.

### Impact Explanation
Once `has_in_flight_tx` is stuck `true`, `rlp_execute` unconditionally rejects **every future call** for that account with `"transaction already in progress"` [9](#0-8) , and there is no other method exposed to reset the flag (the `#[private]` callbacks cannot be invoked externally). This permanently and irrecoverably bricks the account's ability to emulate any further Ethereum transaction through this Wallet Contract — a transaction-triggered halt of the account's entire eth-emulation functionality, directly analogous to the "complete DoS of the auto redemption functionality" in the source report.

### Likelihood Explanation
The relayer/predecessor who calls `rlp_execute` chooses `target`, and for `ERC20Transfer`/`address_check` transaction kinds this target is an arbitrary externally-controlled contract (a NEP-141 token or the address registrar) whose cross-contract response is deserialized and further processed inside the callback. An adversarial or merely misbehaving target contract can return data that is expensive to process, or the relayer can attach marginal gas so that the callback's post-reset logic (JSON parsing, promise/action construction, batch creation) runs out of gas — a condition entirely reachable by a normal unprivileged relayer/caller with no special privileges, matching the accepted "unprivileged transaction signer / meta-transaction sender" threat model.

### Recommendation
- Reset `has_in_flight_tx = false` and persist that specific write independently of the rest of the callback logic (e.g., via a fallible sub-call design or by ensuring the reset happens in a receipt that cannot fail after the reset), or
- Perform all failure-prone / attacker-influenced work (JSON parsing of external results, dynamic promise construction) in a `try`-style pattern that cannot trap the WASM guest, so that these callbacks can never abort after they begin executing, and
- Add an access-controlled recovery method (callable by the account owner/self) to forcibly clear `has_in_flight_tx`, mirroring the "admin function to reset" mitigation from the referenced report.

### Proof of Concept
1. Deploy the `WalletContract` and register a relayer key as in `runtime/near-wallet-contract/implementation/wallet-contract/src/tests/relayer.rs`.
2. Submit (as the relayer/predecessor) an RLP-encoded `ERC20Transfer` transaction whose `target` is a NEP-141 token contract that either: (a) is controlled by an attacker and returns a `storage_balance_of`/`ft_transfer` response crafted to make downstream processing in `nep_141_storage_balance_callback` gas-expensive, or (b) is a legitimate token but the relayer intentionally under-attaches gas so the callback traps after `self.has_in_flight_tx = false;` but before it returns (e.g., during `action_to_promise`/`Promise::then` construction at `lib.rs:230-268`).
3. Observe the callback receipt fails (out-of-gas/trap); per NEAR's rollback semantics (`runtime/runtime/AGENTS.md:64`), all state writes from that call, including the `has_in_flight_tx = false` line executed at `lib.rs:202`, are discarded, leaving the flag at its prior value `true`.
4. Call `rlp_execute` again with a valid, well-formed transaction: it is rejected with `"Error: transaction already in progress, please try again later."` indefinitely, since no code path ever clears the flag again — a permanent, transaction-triggered halt.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L93-105)
```rust
    ) -> PromiseOrValue<ExecuteResponse> {
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L117-128)
```rust
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L133-141)
```rust
    #[private]
    pub fn address_check_callback(
        &mut self,
        target: AccountId,
        action: near_action::Action,
        caller_deposit: Option<CallerDeposit>,
    ) -> PromiseOrValue<ExecuteResponse> {
        self.has_in_flight_tx = false;
        let maybe_account_id: Option<AccountId> = match env::promise_result(0) {
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L159-192)
```rust
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L194-202)
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
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L222-272)
```rust
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
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L276-281)
```rust
    pub fn rlp_execute_callback(
        &mut self,
        caller_deposit: Option<CallerDeposit>,
    ) -> ExecuteResponse {
        self.has_in_flight_tx = false;
        let n = env::promise_results_count();
```

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
