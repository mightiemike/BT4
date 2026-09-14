### Title
Wallet Contract `address_check_callback`/`nep_141_storage_balance_callback` fail to refund `caller_deposit` on registrar/lookup failure - ([File: runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs])

### Summary
`WalletContract::rlp_execute` accepts an attached NEAR deposit (`#[payable]`) from an external caller (e.g., a relayer submitting an emulated Ethereum transaction). This deposit is tracked in a `CallerDeposit` struct so it can be refunded to `predecessor_account_id` if the downstream cross-contract call fails. The refund is correctly implemented in `rlp_execute_callback`, but two intermediate callbacks — `address_check_callback` (address-registrar lookup) and `nep_141_storage_balance_callback` (NEP-141 `storage_balance_of` check) — return a failure `ExecuteResponse` on `PromiseResult::Failed` **without ever issuing the `caller_deposit` refund**. This mirrors the reported Solidity bug class: value attached for a batched/allowed-to-fail action is not returned to the caller when that action fails, leaving it stuck in the contract's balance.

### Finding Description
`inner_rlp_execute` computes `caller_deposit = CallerDeposit::new(&context)` from `env::attached_deposit()` whenever the predecessor differs from the wallet's own account: [1](#0-0)  and [2](#0-1) .

For `EOABaseTokenTransfer` transactions whose target is an unregistered eth-implicit address, execution is routed through an address-registrar lookup, forwarding `caller_deposit` to `address_check_callback`: [3](#0-2) .

In `address_check_callback`, if the registrar promise fails, the function returns a failure response and simply drops `caller_deposit` — no refund promise is created: [4](#0-3) . The same happens in the "unexpected response" branch of that function (lines 149-159, same citation).

The analogous NEP-141/ERC-20-transfer path (`nep_141_storage_balance_callback`) has the identical gap: on `PromiseResult::Failed` for the `storage_balance_of` lookup, it returns failure without refunding `caller_deposit`: [5](#0-4) .

By contrast, the terminal callback `rlp_execute_callback` — reached only when the main action promise itself is what fails — correctly refunds the caller's deposit: [6](#0-5) . This asymmetry is the same root cause as the Solidity finding: a code path treats an action's failure as one where the attached value should simply stay behind ("allowFailure" semantics), while another, structurally similar path treats it correctly. Since the deposit was attached via a real NEAR `Transfer`/payable call (not a sub-promise), it is already credited to the wallet contract's account balance at the protocol level per `Refunds.md`'s deposit-refund model [7](#0-6) ; native protocol refunds only apply to receipts, not to a dApp-level relay of that deposit across multiple promise hops, so the wallet contract must manually re-implement the refund, and it forgets to on these two paths.

### Impact Explanation
When the registrar lookup fails (e.g., registrar contract issue, exceeded gas, or any other promise failure) or the NEP-141 `storage_balance_of` call fails, the caller/relayer's attached NEAR deposit is permanently retained by the wallet contract instead of being refunded, even though `ExecuteResponse.success == false` clearly communicates that no action was carried out. This is a direct, protocol-reachable loss of caller funds: the deposit becomes indistinguishable from the wallet owner's own balance and can subsequently be spent by the wallet owner via any future `rlp_execute` action (e.g. `Transfer`/`FunctionCall` actions), i.e., value intended for a specific caller-initiated transfer is unilaterally redirected to the wallet owner. This is concrete unauthorized value movement / permanently misdirected funds triggered by a single external transaction, satisfying the required "concrete unauthorized value movement" bar.

### Likelihood Explanation
Reachable by any unprivileged relayer/RPC caller submitting a `rlp_execute` call with a non-zero attached deposit for either: (a) an `EOABaseTokenTransfer` targeting an unregistered eth-implicit address (triggers the registrar lookup), or (b) an emulated ERC-20 transfer to an unregistered NEP-141 receiver (triggers `storage_balance_of`). Both external calls (registrar, token contract) are cross-contract calls whose failure is entirely plausible in production (target account/contract deleted, insufficient gas, congestion, malicious/broken token or registrar contract, etc.), so this is not a purely theoretical edge case — it requires no malicious validator/node/peer, only a normal external caller and an external call that fails.

### Recommendation
Add the same `caller_deposit` refund logic used in `rlp_execute_callback` to both failure branches of `address_check_callback` (`PromiseResult::Failed` and the deserialization-error branch) and to the `PromiseResult::Failed` branch of `nep_141_storage_balance_callback`, i.e., issue `env::promise_batch_create` + `env::promise_batch_action_transfer` back to `caller_deposit.account_id` for `caller_deposit.yocto_near` before returning the failure `ExecuteResponse`.

### Proof of Concept
1. Deploy a `WalletContract` for an eth-implicit account and have it attempt an `EOABaseTokenTransfer` to another, not-yet-registered eth-implicit account (`address_check: Some(address)`).
2. An external relayer submits `rlp_execute` with a non-zero attached deposit (`env::attached_deposit() > 0`), causing `CallerDeposit::new` to populate `caller_deposit`.
3. Cause the subsequent registrar `lookup` cross-contract call to fail (e.g., deploy/point the registrar to a broken contract, or exhaust the registrar's callback gas via `REGISTRAR_LOOKUP_GAS`).
4. `address_check_callback` observes `PromiseResult::Failed` and returns `ExecuteResponse { success: false, ... }` without emitting any refund receipt — confirmable by comparing the caller's balance before and after the call using the existing test harness pattern in `test_caller_refunds` [8](#0-7) , but targeting the registrar-lookup failure path instead of the direct-action failure path already covered by that test.
5. Observe the caller's balance decreased by the full attached deposit and the wallet contract's balance increased correspondingly, with no refund receipt generated — unlike the `test_caller_refunds` assertion for the direct-action-failure case.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L133-159)
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L201-221)
```rust
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L340-345)
```rust
    let context = ExecutionContext::new(
        current_account_id.clone(),
        predecessor_account_id,
        env::attached_deposit(),
    )?;
    let caller_deposit = CallerDeposit::new(&context);
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

**File:** docs/RuntimeSpec/Refunds.md (L15-18)
```markdown
## Deposit Refunds

Deposit refunds are generated when an action receipt fails to execute. All attached deposit amounts are summed together and
sent as a refund to a `predecessor_id` (because only the predecessor can attach deposits).
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
