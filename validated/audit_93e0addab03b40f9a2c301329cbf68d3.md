## Analog Reentrancy-Class Finding in nearcore

The external report describes a reentrancy pattern where persistent state is read/acted upon inconsistently around an external/cross-contract call, letting funds be mishandled. The closest reachable analog in nearcore's transaction-triggered logic is in the **NEAR wallet contract**'s asynchronous callback chain, which is explicitly in scope (the multi-step promise flow used to emulate Ethereum transactions).

### Title
Caller deposit permanently lost when intermediate cross-contract calls fail in `WalletContract::rlp_execute` promise chain - ([File: runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs])

### Summary
`WalletContract::rlp_execute` attaches an external caller's deposit (`CallerDeposit`) intending to refund it if the underlying cross-contract action ultimately fails. This refund logic is only implemented in the terminal callback, `rlp_execute_callback`. The two intermediate callbacks in the multi-step promise chain — `address_check_callback` and `nep_141_storage_balance_callback` — do not perform the refund when their own `PromiseResult::Failed` branch is hit, silently dropping `caller_deposit` and leaving the attached NEAR permanently stuck in the wallet contract's balance.

### Finding Description
`CallerDeposit::new` records the predecessor's attached deposit for later refund whenever the predecessor differs from the contract itself [1](#0-0) . This value is threaded through the promise chain to be refunded on failure, but only `rlp_execute_callback` actually issues a transfer back to the caller on `PromiseResult::Failed`: [2](#0-1) 

However, when the transaction target is an eth-implicit account requiring an address-registrar lookup, or a NEP-141 transfer requiring a `storage_balance_of` check, `inner_rlp_execute` first schedules a separate cross-contract call (`address_registrar.lookup(...)` or `token_id.storage_balance_of(...)`) whose callback is `address_check_callback` / `nep_141_storage_balance_callback`, not `rlp_execute_callback`: [3](#0-2) 

Both of these intermediate callbacks receive `caller_deposit` as a parameter, but on their own `PromiseResult::Failed` arm they simply return an error response and drop `caller_deposit` without ever scheduling a refund transfer: [4](#0-3) [5](#0-4) 

Because these are `#[private]` NEAR callbacks (not Solidity reentrancy), there is no cross-call re-entry within a single receipt execution; the actual bug is a missing refund branch that exists in the terminal callback but was not replicated in the intermediate callbacks of the same promise DAG — functionally the same class of "state mismanaged relative to the external call outcome" that the source report flags.

### Impact Explanation
Any relayer/user submitting an `rlp_execute` transaction with a non-zero attached deposit whose target is an eth-implicit account (triggering the address-registrar path) or a NEP-141 token transfer (triggering the storage-balance check path) will have that deposit permanently locked in the wallet contract's account balance if the registrar contract or the token's `storage_balance_of` call fails (e.g., registrar temporarily unavailable, out of gas, token contract paused/misbehaving, or an attacker-controlled registrar/token deliberately failing the call to grief victims). This is a concrete, transaction-triggered loss of funds reachable by any predecessor account calling `rlp_execute` with attached deposit — no privileged role required.

### Likelihood Explanation
Likelihood is moderate-to-high: any relayer routing an eth-implicit transfer or ERC-20-emulated transfer through the wallet contract is subject to this path whenever the registrar or the target token contract's `storage_balance_of` call fails for any reason (congestion, gas exhaustion, contract panics, or an adversarial token/registrar deliberately failing the call). The existing test suite (`test_caller_refunds`) only exercises the direct `rlp_execute_callback` failure path and does not cover the intermediate `address_check_callback` / `nep_141_storage_balance_callback` failure paths, indicating this gap was not caught by existing tests.

### Recommendation
Add the same `caller_deposit` refund logic (creating a `promise_batch_create` + `promise_batch_action_transfer` back to `caller_deposit.account_id`) inside the `PromiseResult::Failed` arms of `address_check_callback` and `nep_141_storage_balance_callback`, mirroring the logic already present in `rlp_execute_callback`, ideally by factoring it into a shared helper to avoid future divergence.

### Proof of Concept
1. A relayer submits an `rlp_execute` transaction on behalf of a user, targeting an eth-implicit account, with a non-zero attached deposit (`caller_deposit` is recorded since predecessor ≠ current account) [6](#0-5) .
2. `inner_rlp_execute` schedules `address_registrar.lookup(address).then(address_check_callback(...))`.
3. The address-registrar cross-contract call fails (e.g., registrar contract deployed at `ADDRESS_REGISTRAR_ACCOUNT_ID` is unreachable, out of gas, or panics).
4. `address_check_callback` executes its `PromiseResult::Failed` branch, returning an error `ExecuteResponse` and dropping `caller_deposit` without any transfer back to the caller [7](#0-6) .
5. The attached deposit remains credited to the wallet contract account permanently; the caller has no way to reclaim it through this contract's interface.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/types.rs (L180-191)
```rust
impl CallerDeposit {
    pub fn new(context: &ExecutionContext) -> Option<Self> {
        // Only track for external (non-self) callers
        if context.current_account_id == context.predecessor_account_id {
            return None;
        }

        NonZeroU128::new(context.attached_deposit.as_yoctonear()).map(|yocto_near| Self {
            account_id: context.predecessor_account_id.clone(),
            yocto_near,
        })
    }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L140-159)
```rust
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
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L202-221)
```rust
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L412-458)
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
```
