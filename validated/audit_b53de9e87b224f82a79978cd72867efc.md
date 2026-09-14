Found a concrete analog. The reported bug class (a value-transfer helper that fails to properly finalize/refund when the recipient-side interaction cannot complete, permanently losing the depositor's assets) has a real match in the NEAR Wallet Contract's cross-contract callback chain.

### Title
Wallet Contract callbacks (`address_check_callback`, `nep_141_storage_balance_callback`) silently forfeit the caller's attached deposit when the intermediate cross-contract call fails - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
`CallerDeposit` is created to track a non-owner caller's `attached_deposit` on `rlp_execute` so that it can be refunded if the requested action ultimately fails [1](#0-0) . The refund is correctly issued in `rlp_execute_callback` when the final action's promise fails [2](#0-1) . However, two other callback paths that are executed *before* reaching `rlp_execute_callback` — `address_check_callback` and `nep_141_storage_balance_callback` — do not carry this refund logic when their own intermediate promise fails.

### Finding Description
`address_check_callback` handles the result of a registrar lookup used to validate transfers to other eth-implicit accounts. If that lookup call itself fails (`PromiseResult::Failed`), the function returns an `ExecuteResponse` with an error and resets `has_in_flight_tx`, but never creates a refund promise for `caller_deposit`, even though `caller_deposit` was passed into the function specifically for this purpose [3](#0-2) . The same omission occurs in the "target is an existing named account" branch of that callback [4](#0-3) .

Likewise, `nep_141_storage_balance_callback` handles the result of the `storage_balance_of` probe used before an emulated ERC-20 transfer. If that call fails, it returns a failure response without refunding `caller_deposit`, which was again explicitly passed in for that purpose [5](#0-4) .

In both cases, the attached deposit that the external caller sent along with their `rlp_execute` (or relayed) call is absorbed into the wallet account's own balance instead of being returned to the caller or used to perform the originally intended action. This mirrors the reported bug class: an asset-moving function has a failure/edge path where the intended recipient-side handling is skipped, and the caller's asset becomes permanently unrecoverable through the contract's own logic (comparable to `MeritDutchAuction`'s unsafe `mint` losing the caller's NFT when the receiver cannot process it).

### Impact Explanation
This causes permanent loss of the caller's attached $NEAR deposit whenever a downstream helper call (the address-registrar lookup, or the NEP-141 `storage_balance_of` probe) fails — for example due to the registrar or token contract running out of gas, panicking, or being temporarily broken. The depositor (which could be a relayer or any account invoking `rlp_execute` on someone else's eth-implicit wallet with a deposit) has no way to recover the funds through the contract, since the deposit is neither refunded nor consumed for its intended purpose.

### Likelihood Explanation
This is reachable by any account that calls `rlp_execute` on an eth-implicit account (wallet contract) with a non-zero attached deposit, targeting either another eth-implicit account (triggering the registrar lookup) or an ERC-20-emulated token transfer (triggering the storage-balance check). No privileged access is required — it only needs the intermediate cross-contract call to fail, which can happen due to ordinary conditions such as insufficient gas or a misbehaving/unregistered token or registrar contract.

### Recommendation
In both `address_check_callback` and `nep_141_storage_balance_callback`, mirror the refund logic used in `rlp_execute_callback`: when the intermediate promise result is `PromiseResult::Failed` (or when the flow terminates without proceeding to the final action), issue a `promise_batch_action_transfer` refund to `caller_deposit.account_id` for `caller_deposit.yocto_near` before returning the failure `ExecuteResponse`.

### Proof of Concept
1. Fund an eth-implicit wallet contract account and register a relayer key (as shown in `test_wallet_contract_interaction` / the `TestContext` helpers) [6](#0-5) .
2. As an external (non-owner) predecessor, call `rlp_execute` with a non-zero attached deposit and an RLP transaction whose parsed action is a `EOABaseTokenTransfer` requiring an `address_check` (i.e., `target` is another eth-implicit account with an unparsable/ambiguous emulated payload) — this routes into `address_check_callback` [7](#0-6) .
3. Cause the registrar lookup cross-contract call to fail (e.g., by pointing `ADDRESS_REGISTRAR_ACCOUNT_ID` at a non-existent/erroring account, or by starving the callback of gas).
4. Observe that `address_check_callback` returns `success: false` without issuing any refund transfer to the external caller, permanently absorbing the attached deposit into the wallet contract's balance [3](#0-2) .
5. The same can be reproduced for `nep_141_storage_balance_callback` by using an ERC-20-emulated transfer to a token contract whose `storage_balance_of` call fails.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/types.rs (L180-192)
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
}
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L141-148)
```rust
        let maybe_account_id: Option<AccountId> = match env::promise_result(0) {
            PromiseResult::Failed => {
                return PromiseOrValue::Value(ExecuteResponse {
                    success: false,
                    success_value: None,
                    error: Some("Call to Address Registrar contract failed".into()),
                });
            }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L161-173)
```rust
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L203-210)
```rust
        let maybe_storage_balance: Option<StorageBalance> = match env::promise_result(0) {
            PromiseResult::Failed => {
                return PromiseOrValue::Value(ExecuteResponse {
                    success: false,
                    success_value: None,
                    error: Some(format!("Call to NEP-141 {token_id}::storage_balance_of failed")),
                });
            }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L296-312)
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
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L412-432)
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
```

**File:** integration-tests/src/tests/features/wallet_contract.rs (L280-304)
```rust
    // The relayer adds its key to the eth implicit account so that
    // can sign Near transactions for the user.
    let relayer_pk = relayer_signer.signer.public_key();
    let action = Action::AddKey(Box::new(AddKeyAction {
        public_key: relayer_pk,
        access_key: AccessKey {
            nonce: 0,
            permission: AccessKeyPermission::FunctionCall(FunctionCallPermission {
                allowance: None,
                receiver_id: eth_implicit_account.to_string(),
                method_names: vec!["rlp_execute".into()],
            }),
        },
    }));
    let signed_transaction = create_rlp_execute_tx(
        &eth_implicit_account,
        action,
        0,
        &eth_implicit_account,
        &secret_key,
        &mut relayer_signer,
        &env,
    );
    let prepaid_gas = total_prepaid_gas(signed_transaction.transaction.actions()).unwrap();
    height = check_tx_processing(&mut env, signed_transaction, height, blocks_number);
```
