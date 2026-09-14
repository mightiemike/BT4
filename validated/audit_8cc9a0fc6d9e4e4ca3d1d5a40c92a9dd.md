The wallet contract (`near-wallet-contract`) exhibits a very similar bug class: a multi-step promise chain where an intermediate failure causes attached caller funds to be permanently stuck, because only one of the several failure branches actually refunds the deposit.

### Title
Caller deposits are permanently locked when the intermediate NEP-141/address-registrar check fails before reaching `rlp_execute_callback` - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
`WalletContract::rlp_execute` lets any external caller (relayer) attach a NEAR deposit (`caller_deposit`) on behalf of an ETH-implicit account owner, to be refunded if the underlying cross-contract action ultimately fails. For `ERC20Transfer` and `EOABaseTokenTransfer` (with address check) emulations, the contract inserts extra intermediate promises (`storage_balance_of` / registrar `lookup`) before the final action executes. If those intermediate calls fail or return unparsable data, the corresponding callbacks (`nep_141_storage_balance_callback`, `address_check_callback`) return a failure `ExecuteResponse` directly — without ever refunding `caller_deposit` — unlike the final-stage `rlp_execute_callback`, which explicitly refunds the caller on failure.

### Finding Description
`CallerDeposit::new` records the predecessor's attached deposit whenever the caller is not the wallet contract itself [1](#0-0) . This deposit is meant to be returned if the promise chain fails, and indeed `rlp_execute_callback` implements that refund on `PromiseResult::Failed` [2](#0-1) .

However, for `ERC20Transfer` emulation, `inner_rlp_execute` builds a two-step promise: first `storage_balance_of` on the token contract, then `nep_141_storage_balance_callback` [3](#0-2) . Inside that callback, both the "call failed" and "unexpected response" branches return a failure `ExecuteResponse` immediately, discarding `caller_deposit` with no refund promise created [4](#0-3) .

Similarly, for `EOABaseTokenTransfer` with an address check, `address_check_callback` performs the same pattern: on `PromiseResult::Failed` or bad deserialization from the address registrar, it returns failure without refunding `caller_deposit` [5](#0-4) .

In both cases the deposit has already been attached/transferred to the wallet contract account at call time via `#[payable]` `rlp_execute` [6](#0-5) , so once the intermediate call fails, those tokens remain in the wallet contract's balance with no code path returning them to the caller — they are permanently stuck (the only "recovery" would be an unrelated future transaction accidentally moving the wallet's general NEAR balance, which is not a refund to the original caller).

This mirrors the reported bug class: a two-phase/multi-step flow has a state-dependent branch (registrar/storage lookup result) inserted between "deposit taken" and "final settlement", and that branch's failure path was not updated to include the same refund logic as the final-callback failure path — funds get silently and permanently locked instead of returned.

### Impact Explanation
Any external relayer/caller who attaches a NEAR deposit while relaying an ERC-20 transfer or an address-checked base-token transfer for a NEAR wallet-contract-controlled ETH-implicit account can have that deposit permanently locked in the wallet contract if the intermediate `storage_balance_of` or registrar `lookup` call fails or returns malformed data (e.g., the token/registrar contract errors, is paused, runs out of gas, or returns unexpected data). This is a concrete, permanent loss of the caller's attached funds — no unauthorized value movement to a third party, but a genuine "permanently frozen funds" outcome for the depositor, reachable purely through normal `rlp_execute` transaction submission with no privileged access required.

### Likelihood Explanation
Likelihood is Medium: it requires an external caller to attach a deposit (fee) while relaying an ERC-20/address-checked transaction, and for the intermediate cross-contract call to fail (a plausible, non-adversarial event — e.g., the token contract being paused, panicking on `storage_balance_of`, exceeding gas, or the address registrar being briefly unavailable). No malicious validator/node behavior is needed; a single relayer submitting a normal transaction against a misbehaving/unavailable token or registrar contract triggers it.

### Recommendation
Add the same refund logic used in `rlp_execute_callback` to the failure branches of `nep_141_storage_balance_callback` and `address_check_callback`: when `PromiseResult::Failed` or deserialization fails, if `caller_deposit` is `Some`, create a refund promise transferring the recorded amount back to `caller_deposit.account_id` before returning the failure `ExecuteResponse`.

### Proof of Concept
1. Deploy the wallet contract as global contract for an ETH-implicit account and register some NEP-141 token contract as `target`.
2. An external relayer (not the wallet's own signer) calls `rlp_execute` with a signed ETH-emulated `ERC20Transfer` transaction and attaches a deposit (`fee` on top), so `CallerDeposit::new` records `Some(caller_deposit)` since predecessor != current account [1](#0-0) .
3. `inner_rlp_execute` issues `storage_balance_of` on the token contract, chained to `nep_141_storage_balance_callback` [7](#0-6) .
4. Cause the `storage_balance_of` call to fail (e.g. target a token contract that does not implement `storage_balance_of`, or one that panics for this account, or simply runs out of attached gas).
5. `nep_141_storage_balance_callback` hits the `PromiseResult::Failed` branch and returns `ExecuteResponse{success:false,...}` with no refund promise created [8](#0-7) .
6. Verify the relayer's attached deposit balance never returns to the relayer's account — it remains part of the wallet contract's balance indefinitely, unlike the case where the failure instead occurs inside `rlp_execute_callback`, where the same deposit would have been refunded.

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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L88-114)
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
        let current_account_id = env::current_account_id();
        let predecessor_account_id = env::predecessor_account_id();
        let result = inner_rlp_execute(
            current_account_id.clone(),
            predecessor_account_id,
            target,
            tx_bytes_b64,
            &mut self.nonce,
        );
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
