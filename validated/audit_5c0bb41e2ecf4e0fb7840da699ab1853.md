### Title
Attached deposit permanently stuck in the Wallet Contract when `address_check_callback` rejects a transaction targeting a registered named account - ([File: runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs])

### Summary
The `WalletContract::address_check_callback` function drops the tracked `caller_deposit` without refunding it in the "Invalid target" error branch (lines 168-173), unlike every other failure path in this contract, which explicitly issues a `Transfer` promise back to the depositor.

### Finding Description
`rlp_execute` is `#[payable]` and, for `EOABaseTokenTransfer` transactions with an `address_check`, calls out to the address registrar and schedules `address_check_callback` [1](#0-0) . Before that, `CallerDeposit::new` captures the `attached_deposit` whenever the `predecessor_account_id` differs from `current_account_id` (i.e., an external caller/relayer funded the call directly, not merely via access key) [2](#0-1) . This `caller_deposit` is threaded through to `address_check_callback` for exactly the purpose of refunding the external caller if the transaction cannot proceed [3](#0-2) .

Inside `address_check_callback`, when the registrar reports that the target address already corresponds to an existing named account and the transaction was **not** submitted using the wallet's own access key (`env::signer_account_id() != current_account_id`), the function returns an error value directly without ever consuming `caller_deposit`: [4](#0-3) 

Because this is a `PromiseOrValue::Value` return with no promise created for the deposit, the NEAR runtime keeps the previously attached deposit as part of the Wallet Contract account's balance — it is never sent back to the caller. This is inconsistent with the sibling failure paths in the same contract, all of which explicitly refund `caller_deposit`:
- `rlp_execute_callback`'s `PromiseResult::Failed` branch explicitly creates a transfer back to `account_id` [5](#0-4) .
- The registrar-lookup-failure branches of `address_check_callback` itself (`PromiseResult::Failed`, malformed JSON) also return early *without* refunding — the same bug class exists there too [6](#0-5) .

There is no other mechanism in the Wallet Contract to later reclaim these funds: the only supported actions reachable via `rlp_execute`/`action_to_promise` are `FunctionCall`, `Transfer`, `AddKey`, `DeleteKey` signed by the ETH-implicit account's own key [7](#0-6)  — the depositing relayer/caller has no way to recover their attached deposit once it silently becomes part of the wallet account's balance. The only test exercising the refund invariant (`test_caller_refunds`) covers the `rlp_execute_from` failing at `Failed Near promise` in `rlp_execute_callback`, not this `address_check_callback` "Invalid target" path, so the missing-refund branch is untested [8](#0-7) .

### Impact Explanation
Funds attached by an external caller (e.g., a relayer paying a base-token-transfer fee or forwarding value on behalf of a user) are permanently absorbed into the Wallet Contract's balance whenever the address-registrar check determines the target resolves to an existing named account and the caller isn't using the wallet's own access key. This is a direct, protocol-reachable loss of funds triggerable purely by submitting a crafted `rlp_execute` transaction whose `to` address happens to collide with a registered named account — a condition fully controllable by any caller since address registration is a public, permissionless action (`register`) as shown in the sanity tests [9](#0-8) . This matches the report's bug class: value is deposited into a contract with no corresponding withdrawal path, permanently freezing/losing the funds.

### Likelihood Explanation
Reaching this path requires: (1) attaching a non-zero deposit on `rlp_execute` from a predecessor different than the wallet's own account (straightforward — any relayer forwarding user funds, or fee payments, naturally does this), (2) triggering the `address_check` sub-path (`EOABaseTokenTransfer` with an unregistered ETH address as target), and (3) that address being (or becoming, since registration is public/permissionless) present in the address registrar. Because address registration is a normal, expected operational state (not an edge case) and any caller can register any account_id/address pair themselves, this is readily and repeatedly triggerable without needing privileged access or timing races.

### Recommendation
In `address_check_callback`'s "Invalid target" branch (and equally in the earlier `PromiseResult::Failed` / malformed-JSON branches of the same function), explicitly refund `caller_deposit` the same way `rlp_execute_callback` does before returning `PromiseOrValue::Value`, e.g., issue a `promise_batch_action_transfer` back to `caller_deposit.account_id` for `caller_deposit.yocto_near` prior to returning the error response.

### Proof of Concept
1. A relayer account `R` (not equal to the wallet's `current_account_id`) submits `rlp_execute(target, tx_bytes_b64)` with attached deposit `D`, where the RLP transaction encodes an `EOABaseTokenTransfer` whose destination address requires an `address_check` (i.e., `target` looks like an eth-implicit account whose corresponding address needs registrar verification).
2. `inner_rlp_execute` computes `caller_deposit = Some(CallerDeposit { account_id: R, yocto_near: D })` since `predecessor_account_id (R) != current_account_id` [2](#0-1) , and schedules the registrar lookup followed by `address_check_callback(target, action, Some(caller_deposit))` [1](#0-0) .
3. Separately (or beforehand), any user has permissionlessly `register`ed a named account for the address computed from `target`, per `test_register_without_deposit` [9](#0-8) , so the registrar lookup returns `Some(account_id)`.
4. In `address_check_callback`, `maybe_account_id.is_some()` is true and `env::signer_account_id() != current_account_id` (R signed with its own key, not an access key on the wallet), so execution hits the `else` branch at lines 167-173 and returns `PromiseOrValue::Value(...)` directly — `caller_deposit` (`D` tokens) is never sent back to `R` [4](#0-3) .
5. `D` remains permanently part of the Wallet Contract account's balance, unrecoverable by `R`.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L130-139)
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/near_action.rs (L12-18)
```rust
#[derive(Debug, serde::Deserialize, serde::Serialize)]
pub enum Action {
    FunctionCall(FunctionCallAction),
    Transfer(TransferAction),
    AddKey(AddKeyAction),
    DeleteKey(DeleteKeyAction),
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/tests/sanity.rs (L249-296)
```rust
/// Test asserting the address registrar requires a deposit.
#[tokio::test]
async fn test_register_without_deposit() -> anyhow::Result<()> {
    let TestContext { worker, address_registrar, .. } = TestContext::new().await?;

    let method = "register";
    let args = br#"{"account_id": "birchmd.near"}"#;
    let result = address_registrar.call(method).args(args.to_vec()).transact().await?;
    assert!(result.is_failure(), "Call without deposit must fail");

    let pre_tx_account_balance = address_registrar.as_account().view_account().await?.balance;
    let deposit_amount = NearToken::from_yoctonear(320000000000000000000);
    let result = worker
        .root_account()?
        .call(address_registrar.id(), method)
        .args(args.to_vec())
        .deposit(deposit_amount)
        .transact()
        .await?;

    let output: Option<String> = result.json()?;
    assert_eq!(output.as_deref(), Some("0x4bfcff9a964925adf801c866f6ada98bd7ec40ca"));
    let post_tx_account_balance = address_registrar.as_account().view_account().await?.balance;
    assert!(
        post_tx_account_balance.as_yoctonear() - pre_tx_account_balance.as_yoctonear()
            >= deposit_amount.as_yoctonear()
    );

    // Sending a duplicate transaction does not take the deposit again.
    let pre_tx_account_balance = post_tx_account_balance;
    let result = worker
        .root_account()?
        .call(address_registrar.id(), method)
        .args(args.to_vec())
        .deposit(deposit_amount)
        .transact()
        .await?;

    let output: Option<String> = result.json()?;
    assert_eq!(output, None);
    let post_tx_account_balance = address_registrar.as_account().view_account().await?.balance;
    assert!(
        post_tx_account_balance.as_yoctonear() - pre_tx_account_balance.as_yoctonear()
            < deposit_amount.as_yoctonear()
    );

    Ok(())
}
```
