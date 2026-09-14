Based on my investigation, the caller-deposit refund path in the Wallet Contract (`near-wallet-contract`) confirms the analog to the reported bug class: promise results from a sub-call are checked in `rlp_execute_callback`, but the `CallerDeposit` refund logic is only wired into that single callback path — earlier callbacks (`address_check_callback`, `nep_141_storage_balance_callback`) return failure `ExecuteResponse`s on a failed sub-call without forwarding/refunding the tracked `CallerDeposit`.

### Title
Caller deposit is not refunded when intermediate cross-contract calls fail in `rlp_execute` flow - ([File: runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs])

### Summary
The Wallet Contract's `rlp_execute` flow tracks an external caller's attached deposit in a `CallerDeposit` struct so it can be refunded if the eventual action fails [1](#0-0) . The refund is only ever issued inside `rlp_execute_callback` when `PromiseResult::Failed` is observed [2](#0-1) . However, two earlier intermediate callbacks in the same call chain — `address_check_callback` and `nep_141_storage_balance_callback` — also inspect a `PromiseResult` and can terminate the flow with `success: false` on failure, but neither of them carries the `caller_deposit` forward into a refund transfer in that failure branch [3](#0-2) [4](#0-3) .

### Finding Description
This is the same bug class as the reported finding: a value-moving operation's outcome (a promise/call result) is not fully checked/propagated before the contract commits to a final state, leading to a mismatch between recorded state (`success:false`, no refund issued) and actual asset custody (the deposit remains locked in the wallet contract instead of being returned to the caller as the contract's own accounting model promises).

Specifically:
- `CallerDeposit::new` is constructed whenever an external (non-self) predecessor attaches a deposit [5](#0-4) .
- It is threaded through `inner_rlp_execute` into the various promise chains that ultimately call `rlp_execute_callback(caller_deposit)` [6](#0-5) .
- Only `rlp_execute_callback` checks `PromiseResult::Failed` and issues `promise_batch_action_transfer` back to `caller_deposit.account_id` [2](#0-1) .
- `address_check_callback` receives `caller_deposit` as a parameter but, on `PromiseResult::Failed` (the address-registrar lookup call failing) or on a deserialization error, returns `ExecuteResponse{success:false, ...}` directly without transferring `caller_deposit` back [3](#0-2) .
- `nep_141_storage_balance_callback` has the identical pattern: on failure of the `storage_balance_of` call it returns early without refunding `caller_deposit` [4](#0-3) .

The existing test suite (`test_caller_refunds`) only exercises the refund path through a failing final action (going through `rlp_execute_callback`), not through failures in the address-registrar or NEP-141 storage-balance intermediate calls [7](#0-6) , so this gap is not covered by tests I could find.

### Impact Explanation
If the address-registrar lookup (used for EOA base-token transfers to another eth-implicit account) or the NEP-141 `storage_balance_of` lookup (used for emulated ERC-20 transfers) fails for any reason — e.g., the registrar/token account temporarily lacking gas, being deleted, or returning malformed data — an external relayer's attached deposit (funding the transaction) is permanently retained by the wallet contract rather than refunded, even though the response reports the transaction as failed. This is a concrete instance of unauthorized value retention/frozen funds caused by not fully propagating a sub-call's failure into the deposit-accounting logic, matching the "unchecked transfer/call result leads to wrong state accounting" bug class from the report.

### Likelihood Explanation
Likelihood is moderate: it requires (a) an external (non-self) caller who attaches a deposit when calling `rlp_execute`, and (b) the specific two-hop flows (EOA transfer to another eth-implicit account with `address_check`, or ERC-20 transfer requiring a `storage_balance_of` check) to hit a failure at the intermediate hop rather than the final action hop. These are reachable directly by any relayer/caller submitting a crafted RLP transaction with a `target` account or token contract that fails/misbehaves at that intermediate call, which is fully within an unprivileged caller's control (no validator or node compromise needed).

### Recommendation
Propagate `caller_deposit` refunds uniformly at every point where a `PromiseResult::Failed` (or unexpected response) short-circuits the `rlp_execute` flow — i.e., add the same refund-transfer logic used in `rlp_execute_callback` to the failure branches of `address_check_callback` and `nep_141_storage_balance_callback`, or refactor these into a shared helper that always attempts the refund before returning a failed `ExecuteResponse`.

### Proof of Concept
1. An external (non-current-account) relayer submits `rlp_execute(target, tx_bytes_b64)` with a nonzero attached deposit, where the decoded transaction is either:
   - an `EOABaseTokenTransfer` with `address_check: Some(address)` targeting another eth-implicit account, or
   - an `ERC20Transfer` targeting a NEP-141 token contract.
2. `CallerDeposit::new` captures `(predecessor_account_id, attached_deposit)` since predecessor ≠ current account [5](#0-4) .
3. The corresponding promise chain calls the address-registrar's `lookup` or the token's `storage_balance_of` first [8](#0-7) .
4. That first-hop call fails (e.g., registrar/token account is deleted or reverts).
5. `address_check_callback`/`nep_141_storage_balance_callback` observes `PromiseResult::Failed`, and returns `ExecuteResponse{success:false, ...}` without issuing any transfer back to `caller_deposit.account_id` [3](#0-2) .
6. The attached deposit remains in the wallet contract's balance indefinitely, unlike the equivalent failure path through `rlp_execute_callback`, which does refund it [2](#0-1) .

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/types.rs (L172-192)
```rust
/// A data type to keep track of the deposit given by an external caller.
/// This allows us to refund the caller's deposit if the cross-contract call fails.
#[derive(Debug, PartialEq, Eq, Clone, serde::Serialize, serde::Deserialize)]
pub struct CallerDeposit {
    pub account_id: AccountId,
    pub yocto_near: NonZeroU128,
}

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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/tests/sanity.rs (L170-229)
```rust
// An external caller gets its deposit back if the cross-contract call fails.
#[tokio::test]
async fn test_caller_refunds() -> anyhow::Result<()> {
    let TestContext { worker, wallet_contract, wallet_sk, address_registrar, .. } =
        TestContext::new().await?;

    let caller = worker.root_account()?;
    let deposit_amount = NearToken::from_near(3);
    let create_tx = |receiver_id: &AccountId, nonce: u64| {
        let method = "register";
        let args = br#"{"account_id": "birchmd.near"}"#;
        let action = Action::FunctionCall {
            receiver_id: receiver_id.to_string(),
            method_name: method.into(),
            args: args.to_vec(),
            gas: Gas::from_tgas(10).as_gas(),
            yocto_near: 0,
        };
        utils::create_signed_transaction(
            nonce,
            receiver_id,
            Wei::new_u128(deposit_amount.as_yoctonear() / (MAX_YOCTO_NEAR as u128)),
            action,
            &wallet_sk,
        )
    };

    // External caller gets a refund when the cross-contract call fails
    let pre_tx_account_balance = caller.view_account().await?.balance;
    let receiver_id: AccountId = "fake.near".parse()?;
    let result = wallet_contract
        .rlp_execute_from(
            &caller,
            receiver_id.as_str(),
            &create_tx(&receiver_id, 0),
            deposit_amount,
        )
        .await?;
    assert!(!result.success);
    let post_tx_account_balance = caller.view_account().await?.balance;
    assert!(
        pre_tx_account_balance.as_yoctonear() - post_tx_account_balance.as_yoctonear()
            < deposit_amount.as_yoctonear()
    );

    // External caller does not get a refund when their tokens are spent
    let pre_tx_account_balance = post_tx_account_balance;
    let receiver_id = address_registrar.id();
    let result = wallet_contract
        .rlp_execute_from(&caller, receiver_id.as_str(), &create_tx(receiver_id, 1), deposit_amount)
        .await?;
    assert!(result.success);
    let post_tx_account_balance = caller.view_account().await?.balance;
    assert!(
        pre_tx_account_balance.as_yoctonear() - post_tx_account_balance.as_yoctonear()
            >= deposit_amount.as_yoctonear()
    );

    Ok(())
}
```
