Found a strong analog. The near-wallet-contract's `rlp_execute_callback` in [1](#0-0)  uses a fixed, hardcoded static gas budget (`RLP_EXECUTE_CALLBACK_GAS = Gas::from_tgas(5)`, and its derivatives `ADDRESS_CHECK_CALLBACK_GAS`, `NEP_141_STORAGE_BALANCE_CALLBACK_GAS`) attached to its own callback promise, mirroring the fixed-50k-gas pattern in the Solidity report. This is exactly the same bug class: a fixed gas stipend attached to a follow-up operation, with a fallback-transfer path executed when the primary operation fails.

### Title
Wallet-contract callback funded with hardcoded static gas can under-provision the caller-deposit refund, permanently burning attacker/relayer funds - (File: runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs)

### Summary
`near-wallet-contract` is a production NEAR contract shipped in this repo (`runtime/near-wallet-contract`) that lets Ethereum-style transactions be relayed through NEAR. When a relayed cross-contract call fails, `rlp_execute_callback` ( [2](#0-1) ) refunds the external caller's attached deposit via `env::promise_batch_action_transfer` to `caller_deposit.account_id`. This callback is always funded with a fixed static gas amount defined at compile time (`RLP_EXECUTE_CALLBACK_GAS = Gas::from_tgas(5)`, and composite constants `ADDRESS_CHECK_CALLBACK_GAS`, `NEP_141_STORAGE_BALANCE_CALLBACK_GAS` at [3](#0-2) ), exactly the same hardcoded-gas-for-external-effect pattern the Solidity report flags for `Auction._handleOutgoingTransfer`'s hardcoded `call(50000, ...)`.

### Finding Description
The relevant flow: an external, unprivileged relayer submits a NEAR `FunctionCall` action invoking `rlp_execute` on the wallet contract with an attached deposit (`CallerDeposit`, tracked at [4](#0-3) ). The wallet contract schedules the target action and chains a callback funded with `with_static_gas(callback_gas)`, where `callback_gas` is one of the hardcoded constants plus the forwarded action's own gas (see the dispatch in [5](#0-4) ). If the target promise fails, `rlp_execute_callback` runs and issues a `promise_batch_action_transfer` refund to the original caller ( [6](#0-5) ).

Because the gas figure is a fixed constant rather than dynamically computed/checked against the actual remaining gas budget of the outer transaction, a caller who crafts (or a relayer who forwards) a transaction with just enough total attached gas can make the outer call succeed up through scheduling the promise, but leave insufficient gas for the callback itself to execute its refund-transfer logic (`nep_141_storage_balance_callback` performs additional deserialization/promise construction before the refund can even fire; see [7](#0-6) ). If the callback receipt itself runs out of gas, `ActionErrorKind::FunctionCallError(FunctionCallError::ExecutionError("Exceeded the prepaid gas."))`-style failure results in the callback receipt failing outright — the deposit refund `Transfer` action never gets emitted at all (unlike the Solidity case, which at least falls back to WETH; here nothing is emitted), and since the refund logic lives inside application code that never ran, the deposit is not recoverable by any protocol-level mechanism (deposit refunds only trigger for the *outer* action receipt's own failure/deposit fields, not for value that a contract already decided to forward as a nested `Transfer` promise it never got to construct).

### Impact Explanation
This matches "permanently frozen funds": the caller's `CallerDeposit` (which is the NEAR-side wei-equivalent value backing an Ethereum-style transaction, up to whole NEAR tokens per `MAX_YOCTO_NEAR` scaling — see [8](#0-7)  for the expected refund-on-failure behavior this depends on) is transferred into the wallet contract's account balance at call time, and if the compensating refund promise never gets constructed due to gas starvation in the fixed-budget callback, those funds remain stuck in the wallet contract's balance with no code path left to return them to the caller.

### Likelihood Explanation
The `rlp_execute` and related entry points are explicitly permissionless — any account (including a malicious relayer with no privileges) can submit the outer `FunctionCall` and fully control the attached `gas` field, directly controlling how much gas remains for `rlp_execute_callback`'s execution once its own static portion is spent, mirroring the report's premise that `createBid()`/`settleAuction()` gas is attacker-controlled.

### Recommendation
Ensure gas attached to `rlp_execute_callback` (and its variants) is bounded not just from below by a fixed constant, but validated against a minimum safe threshold that accounts for worst-case deserialization/promise-construction cost inside the callback before the refund transfer is issued; alternatively, perform the refund transfer as the very first operation in the callback (before any other logic that could exhaust the static budget), or use `promise_batch_action_function_call_weight`/unspent-gas allocation (NEP-264) instead of a fixed `Gas::from_tgas(5)` to make the callback's available gas scale with what is actually left, and add explicit tests that starve the callback's gas budget to confirm the refund still executes or the whole outer receipt fails atomically (rather than the refund step being silently skipped).

### Proof of Concept
1. Relayer submits `rlp_execute` (or `rlp_execute_from`) with an attached deposit and a total transaction gas value tuned so that after scheduling the underlying promise (per the dispatch logic at [5](#0-4) ), the remaining gas is just above the check that triggers scheduling the callback but insufficient for the callback's actual logic once the underlying action fails.
2. The underlying promise resolves as `PromiseResult::Failed`.
3. `rlp_execute_callback` begins executing ( [6](#0-5) ) but runs out of the fixed `RLP_EXECUTE_CALLBACK_GAS` budget before or during `env::promise_batch_create`/`env::promise_batch_action_transfer`, causing the whole callback receipt to fail with a gas-exceeded error.
4. The caller's deposit, already held in the wallet contract's balance, is never returned — the compensating `Transfer` promise for the refund is never created, so no protocol-level refund mechanism recovers it.
5. Repeat against any relayer-operated wallet-contract instance to strand deposits at will.

### Citations

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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L275-316)
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/tests/sanity.rs (L170-213)
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
```
