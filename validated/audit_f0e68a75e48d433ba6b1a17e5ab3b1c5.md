### Title
Wallet Contract fails to refund caller's attached deposit when the registrar lookup or NEP-141 `storage_balance_of` cross-contract call fails or returns unexpected data - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
The `near-wallet-contract` tracks a caller's attached deposit (`CallerDeposit`) so it can be refunded if a cross-contract call made on the caller's behalf fails. This refund logic is implemented in `rlp_execute_callback`, but the two earlier callbacks in the promise chain — `address_check_callback` and `nep_141_storage_balance_callback` — do not perform this refund when the promise they depend on fails or returns data that cannot be deserialized into the expected type. In those cases the attached deposit is silently kept by the contract instead of being returned to the caller.

### Finding Description
`CallerDeposit::new` records the `predecessor_account_id` and `attached_deposit` of an external (non-self) caller of `rlp_execute` so the deposit can be refunded if the downstream action fails [1](#0-0) .

For `EOABaseTokenTransfer` with an address check, and for `ERC20Transfer`, `inner_rlp_execute` schedules a cross-contract call (`address_registrar.lookup` or `storage_balance_of`) before the actual action, passing `caller_deposit` through to the corresponding callback [2](#0-1) .

`address_check_callback` handles the result of the registrar lookup: on `PromiseResult::Failed` or on a JSON deserialization error, it returns a failed `ExecuteResponse` and simply drops `caller_deposit` — no refund promise is created: [3](#0-2) 

Likewise, `nep_141_storage_balance_callback` handles the result of `storage_balance_of`: on `PromiseResult::Failed` or a deserialization error it returns failure without refunding `caller_deposit`: [4](#0-3) 

This is inconsistent with `rlp_execute_callback`, which explicitly refunds `caller_deposit` to the original caller when the final action's promise fails: [5](#0-4) 

Because the attached deposit is transferred into the Wallet Contract's own account balance as soon as `rlp_execute` (marked `#[payable]`) is called [6](#0-5) , any deposit that is not explicitly refunded via a `Promise::transfer` remains permanently in the contract's balance. Since `address_check_callback` and `nep_141_storage_balance_callback` never issue such a transfer on their failure paths, the caller's funds are stuck in the wallet contract with no code path to reclaim them.

This mirrors the reported bug class: code assumes a specific "successful" response shape/status from an external call (a boolean return in the Solidity report; here, a well-formed `Option<AccountId>` / `Option<StorageBalance>` JSON or `PromiseResult::Successful`) and treats any deviation as an unrecoverable failure, but — unlike the Solidity case which just reverts the whole transaction — here the failure path is only partially handled: the overall call still "succeeds" (returns `ExecuteResponse{success:false,...}`) while the attached deposit that should have been refunded is dropped, permanently freezing those funds in the contract.

### Impact Explanation
Any relayer or self-relaying user who attaches a deposit when calling `rlp_execute` for a base-token transfer with an address check, or for an emulated ERC-20 transfer, loses that deposit permanently if:
- the address registrar contract call fails or returns unexpected data, or
- the target NEP-141 token's `storage_balance_of` call fails, times out, or returns a payload that does not deserialize to `Option<StorageBalance>` (e.g., non-standard token implementations, gas exhaustion causing a failed promise, or a token contract without that method).

This results in permanently frozen funds for an unprivileged transaction signer/relayer, reachable purely through a normal `rlp_execute` call with an attached deposit — no privileged access needed.

### Likelihood Explanation
This is triggered any time the address registrar or the target NEP-141 token's `storage_balance_of` promise fails or returns non-conforming data while a non-zero deposit was attached by an external caller. Since relayers are expected to attach deposits/fees to compensate themselves, and any external contract call can fail (out of gas, non-standard implementation, contract paused, etc.), this is a realistically reachable condition, not merely a low-probability edge case.

### Recommendation
Add the same refund-on-failure logic used in `rlp_execute_callback` to `address_check_callback` and `nep_141_storage_balance_callback`: whenever these callbacks return an `ExecuteResponse{success:false,...}` due to `PromiseResult::Failed` or a deserialization error, create a `promise_batch_create`/`promise_batch_action_transfer` (or `Promise::new(...).transfer(...)`) to refund `caller_deposit.yocto_near` back to `caller_deposit.account_id`, exactly as done at [7](#0-6) .

### Proof of Concept
1. Deploy a Wallet Contract instance for an eth-implicit account.
2. As an external relayer, call `rlp_execute` with a non-zero attached deposit, targeting an ERC-20 transfer (`EthEmulationKind::ERC20Transfer`) to a token contract account that either doesn't implement `storage_balance_of` or returns a JSON payload that isn't `Option<StorageBalance>` (or simply make that call run out of gas so the promise resolves as `PromiseResult::Failed`, e.g. by using a token contract with a `storage_balance_of` implementation that consumes more gas than `NEP_141_STORAGE_BALANCE_OF_GAS` [8](#0-7) ).
3. Observe that `nep_141_storage_balance_callback` returns `ExecuteResponse{success:false,...}` at [9](#0-8)  without creating any refund promise.
4. Query the Wallet Contract's account balance: the deposit attached in step 2 is retained by the contract and there is no subsequent call path that returns it to the relayer/caller.

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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L33-36)
```rust
const NEP_141_STORAGE_DEPOSIT_AMOUNT: NearToken = NearToken::from_yoctonear(1_250 * MICRO_NEAR);
const NEP_141_STORAGE_DEPOSIT_GAS: Gas = Gas::from_tgas(5);
const NEP_141_STORAGE_BALANCE_OF_GAS: Gas = Gas::from_tgas(5);
const REGISTRAR_LOOKUP_GAS: Gas = Gas::from_tgas(5);
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L88-105)
```rust
    #[payable]
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
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L141-159)
```rust
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L203-221)
```rust
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L296-316)
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
