## Title
Wallet Contract `has_in_flight_tx` lock can become permanently stuck, freezing all future `rlp_execute` transactions - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
The `near-wallet-contract` (a NEAR contract that emulates an Ethereum EOA and is the sole entry point for transacting from an ETH-implicit account via `rlp_execute`) uses a boolean re-entrancy guard, `has_in_flight_tx`, to serialize transactions. This guard is set to `true` before a cross-contract promise is dispatched and is only reset to `false` inside the corresponding `#[private]` callback. If the promise chain never reaches (or the runtime rolls back) one of those callbacks, the flag remains `true` forever and every subsequent call to `rlp_execute` is rejected with "transaction already in progress" - permanently freezing the wallet, analogous to the `EthRouter` approval state that gets stuck and blocks all future callers.

### Finding Description
`WalletContract::rlp_execute` checks the guard first and bails out if it is already set: [1](#0-0) 

On the success path it sets `self.has_in_flight_tx = true` and returns a `Promise`; the intended invariant is that a follow-up `#[private]` callback (`address_check_callback`, `nep_141_storage_balance_callback`, or `rlp_execute_callback`) will run and reset the flag to `false` as the very first statement: [2](#0-1) [3](#0-2) 

The comment on the field states the invariant explicitly: "`has_in_flight_tx` must be `true` when a mutable method of this contract returns a promise and `false` otherwise": [4](#0-3) 

The protocol/runtime guarantees that on failure of a receipt/function-call, **all state changes made during that call are rolled back** (only the outcome/gas accounting persists): [5](#0-4) [6](#0-5) 

This means that if a callback (`address_check_callback`, `nep_141_storage_balance_callback`, or `rlp_execute_callback`) panics or runs out of gas *after* setting `has_in_flight_tx = false` but before it finishes (e.g. while building the next promise, parsing a malformed cross-call result, or scheduling `action_to_promise`), the entire function call is rolled back atomically by the runtime - including the write that reset `has_in_flight_tx`. The flag reverts to (or stays) `true` in the account's persisted state, and there is no public method in the contract to reset it: the only mutators of the field are `rlp_execute` (guarded by the very flag it tries to set) and the four `#[private]` callbacks, all of which are unreachable once the lock is stuck (they can only be invoked by the contract itself via the promise chain that already failed).

The static gas budgets for these callbacks are simple constants added to attacker/relayer-supplied `action.gas()` values (`ADDRESS_CHECK_CALLBACK_GAS`, `NEP_141_STORAGE_BALANCE_CALLBACK_GAS`, `RLP_EXECUTE_CALLBACK_GAS`): [7](#0-6) [8](#0-7) 

A relayer only has to attach *at least* `gas_limit * GAS_MULTIPLIER` total prepaid gas per `validate_tx_relayer_data`, but nothing prevents a relayer/attacker from crafting a transaction whose `action.gas()` is borderline low enough that the fixed `..._CALLBACK_GAS` overhead is insufficient for the callback's own post-reset logic (JSON/RLP parsing of a large cross-call result, building `action_to_promise`, etc.) to complete before running out of gas, causing the callback itself to abort mid-execution and roll back its own `has_in_flight_tx = false` write.

### Impact Explanation
If the lock becomes permanently stuck at `true`, the ETH-implicit account behind the wallet contract can never again execute `rlp_execute`, which is its only transaction entry point. All $NEAR/$FT/ERC-20-emulated assets held by that account become permanently unusable/frozen (no owner-only "unlock" function exists), matching the allowed "permanently frozen funds" impact category. Because the wallet contract is deployed as a shared global contract used by every ETH-implicit account (per `EthImplicitGlobalContract`), the same bug class threatens any account using it, and the trigger is reachable by an ordinary relayer/transaction signer crafting the RLP payload and gas attached to a normal `rlp_execute` call - no privileged, validator, or network-layer access required.

### Likelihood Explanation
Reaching this requires crafting a specific combination of RLP-encoded action + attached gas such that a callback runs out of gas (or panics) strictly after other work but conceptually before/without persisting the `has_in_flight_tx = false` write being the last durable effect — in practice, gas is metered per instruction and NEAR SDK panics unwind the whole call, so any panic anywhere in the callback (not only after the reset write, but from any unexpected cross-call response, unicode/JSON error, `action_to_promise` construction error, etc.) rolls back the reset. Given the contract's threat model already anticipates relayers behaving adversarially with respect to gas (see `test_relayer_insufficient_gas`, "faulty relayer" ban path), a similarly adversarial relayer or even an honest relayer's misestimate of downstream gas needed for the second promise leg is a plausible trigger. This is not guaranteed to be trivially reproducible without deeper fuzzing of gas boundaries, but the underlying design flaw (single mutable flag reset only inside promise callbacks, no external recovery, and atomic rollback semantics on any failure inside those callbacks) is directly analogous to the referenced `EthRouter` approval-state DOS and is confirmed structurally by the code and by the documented atomic rollback behavior of the runtime.

### Recommendation
- Reset `has_in_flight_tx = false` using a mechanism that is robust to partial failure of the callback, e.g., perform the reset in a separate leading state commit isolated from the fallible logic that follows it, or restructure callbacks so that gas-sensitive/fallible work (parsing, promise construction) happens before the state mutation is finalized, with a safe fallback path if it fails.
- Add an explicit, permissionless "unlock"/recovery path (e.g., allow the flag to be cleared if no promise resolves within N blocks, similar to a timeout) so a stuck lock cannot permanently brick the wallet.
- Add fuzz/gas-boundary tests that specifically attach the minimum viable gas to each callback to verify `has_in_flight_tx` cannot desync from actual in-flight state under any panicking/gas-exhaustion scenario within the callback bodies.

### Proof of Concept
1. Relayer submits an `rlp_execute` transaction whose decoded action routes through, e.g., `nep_141_storage_balance_callback` (ERC-20 transfer path), attaching gas equal to `NEP_141_STORAGE_BALANCE_CALLBACK_GAS + action.gas()` computed from the user's signed gas_limit, satisfying the `validate_tx_relayer_data` gas check exactly at the boundary.
2. The initial `storage_balance_of` cross-call succeeds and schedules `nep_141_storage_balance_callback`; `has_in_flight_tx` is `true`.
3. Inside `nep_141_storage_balance_callback`, `self.has_in_flight_tx = false;` executes, then downstream logic (constructing the `storage_deposit` + transfer promise chain, or parsing an oversized/malformed `StorageBalance` JSON from a malicious/nonstandard NEP-141 token used as `target`) exhausts the remaining gas or panics.
4. Per NEAR runtime semantics, the entire `nep_141_storage_balance_callback` receipt fails and is rolled back atomically, including the `has_in_flight_tx = false` write, leaving `has_in_flight_tx == true` persisted on-chain.
5. Any subsequent `rlp_execute` call for this account now unconditionally returns `"Error: transaction already in progress, please try again later."` forever, with no available recovery method — the account's funds are permanently inaccessible via its intended interface.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L33-41)
```rust
const NEP_141_STORAGE_DEPOSIT_AMOUNT: NearToken = NearToken::from_yoctonear(1_250 * MICRO_NEAR);
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L89-128)
```rust
    pub fn rlp_execute(
        &mut self,
        target: AccountId,
        tx_bytes_b64: String,
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

**File:** protocol-model/spec/runtime-execution.md (L146-153)
```markdown
## Invariants & failure modes

- **Gas ordering**: `merge` asserts `gas_burnt_for_function_call <= gas_burnt <= gas_used` per action (`runtime/runtime/src/lib.rs:440`).
- **Failed receipt atomicity**: a receipt whose result is `Err` triggers `state_update.rollback()`, so no state changes persist except the outcome/gas accounting (`runtime/runtime/src/lib.rs:967`). `set_error` additionally clears queued receipts, proposals, and burnt/subsidized amounts (`runtime/runtime/src/lib.rs:487`).
- **Staking invariant**: `update_validator_accounts` returns a fatal `StorageInconsistentState` if `locked < max_of_stakes` (`runtime/runtime/src/lib.rs:1617`).
- **Invalid txs make progress, not failure**: a chunk with invalid transactions is not rejected; the offending txs are skipped during conversion, polluting the chain with junk but keeping the shard live (`runtime/runtime/src/lib.rs:1706` doc; skip sites at `:1994`, `:2199`).
- **Refund receipts are free**: system-predecessor receipts burn zero gas; a failed refund burns its deposit into `other_burnt_amount` rather than refunding (`runtime/runtime/src/lib.rs:929`, `:972`).
- **Delayed receipts must stay valid**: a delayed receipt that fails `validate_receipt` on dequeue is treated as `StorageInconsistentState` (`runtime/runtime/src/lib.rs:2500`).
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
