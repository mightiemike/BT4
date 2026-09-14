This confirms the key mechanism: `runtime/runtime/AGENTS.md` states plainly that "when a receipt fails, its state changes are rolled back using `TrieUpdate`" [1](#0-0) , and `runtime/runtime/src/lib.rs:1079-1088` shows the commit/rollback logic: on `Err`, `state_update.rollback()` discards *all* state changes made by that receipt [2](#0-1) . This means the `has_in_flight_tx = false` write at the very top of each Wallet Contract callback (`address_check_callback`, `nep_141_storage_balance_callback`, `rlp_execute_callback`, `ban_relayer`) is not durable unless the whole receipt succeeds.

## Title
Wallet Contract callback out-of-gas/panic permanently strands `has_in_flight_tx`, freezing all funds and control of the ETH-implicit account - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
The NEAR Wallet Contract (deployed as the global contract behind every ETH-implicit account) uses a boolean flag `has_in_flight_tx` to serialize `rlp_execute` calls [3](#0-2) . `rlp_execute` unconditionally rejects any further calls while `has_in_flight_tx == true` [4](#0-3) , and the flag is only reset back to `false` at the very top of the scheduled callback (`address_check_callback`, `nep_141_storage_balance_callback`, `rlp_execute_callback`, `ban_relayer`) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7) . Because a failed (e.g. out-of-gas) receipt is rolled back in its entirety — including that very first assignment — any callback receipt that reaches `FunctionCallError`/`GasExceeded` before completing normally leaves `has_in_flight_tx` permanently `true`, and there is no other function in the contract able to clear it. There is no owner/admin recovery path (analogous to the JUSDExchange report's missing withdrawal function), so the account becomes permanently unable to authorize any further NEAR action, freezing the NEAR balance and any assets controlled by that account.

### Finding Description
`rlp_execute` schedules a cross-contract promise and sets `self.has_in_flight_tx = true` right before returning `PromiseOrValue::Promise(promise)` [9](#0-8) . The promise chain (depending on the emulated Ethereum action) can route through `address_check_callback` (Tgas budget `ADDRESS_CHECK_CALLBACK_GAS`), `nep_141_storage_balance_callback` (`NEP_141_STORAGE_BALANCE_CALLBACK_GAS`), or directly to `rlp_execute_callback` (`RLP_EXECUTE_CALLBACK_GAS`), all fixed, small constants (5–10 Tgas plus the forwarded action's own gas) [10](#0-9) . Each of these callbacks resets the flag as its first statement, then goes on to deserialize/process the previous promise's result (`env::promise_result(0)`, `serde_json::from_slice`, string formatting, further promise scheduling) [11](#0-10) .

Per the runtime's execution model, a receipt whose action fails for *any* reason (including gas exhaustion inside the callback body, or an unhandled `env::panic_str` from a malformed/oversized response) is entirely rolled back: `state_update.rollback()` discards all state writes of that receipt, keeping only outcome/gas accounting [2](#0-1) . The `runtime/runtime/AGENTS.md` documentation is explicit about this invariant: "When a receipt fails, its state changes are rolled back using `TrieUpdate`" [1](#0-0) .

Because `self.has_in_flight_tx = false` is the very first write in the callback body, an OOG/panic anywhere after it in the same receipt (deserializing an attacker-influenced `promise_result`, formatting an oversized `receiver_id`/token id string for a follow-up `storage_deposit` call, or exceeding the fixed callback gas budget while chaining further promises) reverts that write along with everything else. The only place capable of ever clearing `has_in_flight_tx` is these same callbacks, and once `rlp_execute` sees `has_in_flight_tx == true` it refuses to schedule any further promise (it just returns an error value without touching the flag) [12](#0-11) . There is no owner/admin/recovery method in this contract that can reset the flag directly, mirroring the missing-recovery pattern described in the JUSDExchange report.

### Impact Explanation
An ETH-implicit account's only way to authorize outgoing NEAR actions (transfers, function calls, key management) from the account controlling its underlying $NEAR/$NEP-141 balances is via `rlp_execute`. If `has_in_flight_tx` gets stuck `true`, every subsequent `rlp_execute` call is unconditionally rejected forever — the account's NEAR balance (and any fungible-token balances it can otherwise move via emulated ERC-20 transfers) becomes permanently frozen with no recovery mechanism, matching a "permanently frozen funds" outcome. This is broader than the reported JUSD case since it affects any and all value held by the affected ETH-implicit account, not merely one deposited token batch.

### Likelihood Explanation
Triggering requires only a single unprivileged, attacker- or user-submitted transaction: reaching a chained-promise EthEmulation branch (`ERC20Transfer` via `nep_141_storage_balance_callback`, or the address-registrar lookup via `address_check_callback`) with a result/response that is large enough, or attaching insufficient callback gas relative to the forwarded action's `gas` field, to cause the callback receipt to run out of gas before completing. Both the size of `promise_result(0)` (controlled by whatever contract is called, which itself can be attacker-deployed as the `token_id`/registrar target) and the forwarded `action.gas()` value are attacker/relayer-influenced inputs to `inner_rlp_execute`, making the precise gas budget of the callback receipt manipulable from ordinary transactions.

### Recommendation
- Add a recovery path that does not depend on the in-flight callback completing, e.g. a time-boxed/self-callback-independent mechanism (such as a scheduled timeout receipt, or a full-access-key-gated "force reset" that requires proof the previous receipt has resolved) to clear `has_in_flight_tx`.
- Ensure the flag is committed durably before any gas-variable work is attempted in the callback (e.g. split the reset into its own minimal, gas-bounded receipt/action prior to the variable-cost logic), or bound the callback's downstream work (deserialization, string formatting, additional promise fan-out) so it cannot exceed the statically reserved gas under adversarial inputs.
- Alternatively, redesign the "single in-flight tx" guard to be enforced via a mechanism that is inherently safe against partial receipt failure, such as gating on the *existence* of a specific pending `data_id`/yielded-promise rather than a plain boolean flag mutated inside the same fallible receipt it guards.

### Proof of Concept
1. Fund an ETH-implicit account (deploy is via the shared `Global` wallet contract per `EthImplicitGlobalContract`, `actions.rs:238`) with NEAR.
2. As a relayer (or externally, since external callers are permitted per `CallerDeposit`), submit an RLP-encoded Ethereum transaction targeting a NEP-141 token identifier for an ERC-20 transfer whose receiver account is not registered (forcing the `storage_balance_of` → `storage_deposit` → transfer chain through `nep_141_storage_balance_callback`) [13](#0-12) .
3. Have the `token_id` account (attacker-controlled or any contract willing to return an oversized/log-heavy `storage_balance_of` response) return a payload sized so that deserializing it and chaining the subsequent `storage_deposit`+transfer promise actions inside `nep_141_storage_balance_callback` exceeds the statically reserved `NEP_141_STORAGE_BALANCE_CALLBACK_GAS` budget, causing the callback receipt to fail with `GasExceeded`.
4. Because the receipt fails, `state_update.rollback()` discards the `self.has_in_flight_tx = false` write from step 3, per `runtime/runtime/src/lib.rs:1078-1088`.
5. Any subsequent `rlp_execute` call against this account now unconditionally returns `"transaction already in progress"` forever, since nothing else in the contract clears `has_in_flight_tx` [12](#0-11) . The account's NEAR balance is now permanently unreachable through the only authorization path available for that address.

### Citations

**File:** runtime/runtime/AGENTS.md (L63-64)
```markdown

To modify the shard's state, the runtime uses `TrieUpdate`. This struct applies changes on top of the chunk's pre-state. It allows to rollback or commit recent changes. When a receipt fails, its state changes are rolled back using `TrieUpdate`.
```

**File:** runtime/runtime/src/lib.rs (L1078-1088)
```rust
        // Committing or rolling back state.
        match &result.result {
            Ok(_) => {
                state_update.commit(StateChangeCause::ReceiptProcessing {
                    receipt_hash: receipt.get_hash(),
                });
            }
            Err(_) => {
                state_update.rollback();
            }
        };
```

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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L116-128)
```rust
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L140-140)
```rust
        self.has_in_flight_tx = false;
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L280-280)
```rust
        self.has_in_flight_tx = false;
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L320-322)
```rust
    pub fn ban_relayer(&mut self) -> ExecuteResponse {
        self.has_in_flight_tx = false;
        ExecuteResponse {
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
