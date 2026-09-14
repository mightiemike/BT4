## Finding [1](#0-0) [2](#0-1) [3](#0-2) 

### Title
Unrefunded caller deposit on failed intermediate cross-contract call in Wallet Contract - (File: runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs)

### Summary
The report's bug class — a cross-chain/cross-contract call being made without properly handling its failure, silently treating it as if nothing bad happened — maps onto the NEAR Wallet Contract's (`near-wallet-contract`) intermediate cross-contract calls in `address_check_callback` and `nep_141_storage_balance_callback`. Unlike the contract's own final callback (`rlp_execute_callback`), these two intermediate callbacks do not refund the external caller's attached deposit (`CallerDeposit`) when the intermediate cross-contract call fails (`PromiseResult::Failed`) or returns unparsable data.

### Finding Description
The wallet contract's `rlp_execute` entry point [4](#0-3)  accepts an attached deposit from an external caller (not necessarily the wallet owner) and tracks it in a `CallerDeposit` struct [5](#0-4)  so the deposit can be refunded if the eventual cross-contract action fails.

For two transaction kinds — an `EOABaseTokenTransfer` with an `address_check`, and an emulated `ERC20Transfer` — `inner_rlp_execute` first issues an intermediate cross-contract call (to the address registrar's `lookup`, or to the token contract's `storage_balance_of`) before performing the user's actual action: [6](#0-5) 

The corresponding callbacks (`address_check_callback`, `nep_141_storage_balance_callback`) handle a `PromiseResult::Failed` from this *intermediate* call by simply returning an `ExecuteResponse { success: false, ... }` — they never touch `caller_deposit`: [7](#0-6) [8](#0-7) 

This is inconsistent with the contract's own documented invariant ("An external caller gets its deposit back if the cross-contract call fails", tested in `test_caller_refunds`) and with the final callback `rlp_execute_callback`, which *does* explicitly refund `caller_deposit` on `PromiseResult::Failed`: [9](#0-8) [10](#0-9) 

Because the intermediate calls are unrelated to the value the caller actually asked to move (they are only lookups: "is this address a registered account", "does the receiver have NEP-141 storage"), a failure here has nothing to do with whether the caller's intended action should proceed or fail — yet its failure silently causes the caller's attached NEAR to be swallowed by the wallet contract instead of returned, exactly as in the reported pattern where a cross-chain/cross-contract call's failure is not checked/handled and downstream state proceeds as if all was fine (here: the deposit is dropped rather than tracked through to a refund).

### Impact Explanation
An external, unprivileged relayer/caller who attaches NEAR tokens to `rlp_execute` when submitting an ERC-20 transfer or an EOA transfer with `address_check` loses that deposit permanently whenever the address-registrar lookup or the token's `storage_balance_of` call fails (e.g., the registrar or token contract is temporarily out of gas, paused, deleted, or the call simply runs out of attached gas). This is concrete, unauthorized value movement: NEAR that should be refunded to the caller (per the contract's own stated invariant and test coverage) instead remains stuck in the wallet contract's balance with no code path to reclaim it, since `caller_deposit` is dropped on the `Failed`/deserialization-error branches of `address_check_callback` and `nep_141_storage_balance_callback`.

### Likelihood Explanation
Reachable by any account (not requiring the wallet owner's key) that calls `rlp_execute` with a deposit on an `EOABaseTokenTransfer` targeting an eth-implicit-looking address (triggering `address_check`) or an `ERC20Transfer`, and can be triggered any time the target registrar/token contract call fails — including gas exhaustion of the attached `REGISTRAR_LOOKUP_GAS` / `NEP_141_STORAGE_BALANCE_OF_GAS`, which is entirely controllable/observable by the caller, or the target contract being unresponsive. This makes the failure condition attacker/caller-triggerable rather than a rare edge case.

### Recommendation
In both `address_check_callback` and `nep_141_storage_balance_callback`, mirror the refund logic in `rlp_execute_callback`: on `PromiseResult::Failed` (and on the JSON-deserialization error branch), if `caller_deposit` is `Some`, issue a `promise_batch_action_transfer` refunding `caller_deposit.yocto_near` back to `caller_deposit.account_id` before returning the failed `ExecuteResponse`.

### Proof of Concept
1. Deploy a `WalletContract` and an `AddressRegistrar`/mock NEP-141 token that can be made to fail a specific call (e.g., point `storage_balance_of`/`lookup` gas so low it always fails, or point the target account to a nonexistent/unresponsive account).
2. As an external account (not the wallet owner), call `rlp_execute` with an RLP-encoded `ERC20Transfer` (or `EOABaseTokenTransfer` with `address_check`) transaction and attach a NEAR deposit.
3. Ensure the intermediate `storage_balance_of` (or registrar `lookup`) call fails, e.g. by targeting a non-existent token/registrar account or under-provisioning `NEP_141_STORAGE_BALANCE_OF_GAS`/`REGISTRAR_LOOKUP_GAS`.
4. Observe `nep_141_storage_balance_callback`/`address_check_callback` return `success: false` while `env::predecessor_account_id()`'s balance does not increase back by the attached deposit — the deposit remains on the wallet contract account, unlike the behavior verified for `rlp_execute_callback` failures in `test_caller_refunds`.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L88-128)
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L130-159)
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
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L194-220)
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
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L296-317)
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
