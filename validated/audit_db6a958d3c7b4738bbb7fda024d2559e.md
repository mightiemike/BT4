### Title
Wallet Contract fails to refund caller's attached deposit when the NEP-141 `storage_balance_of` or address-registrar cross-contract call fails, permanently stranding user funds - ([File: runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs])

### Summary
The `near-wallet-contract` emulates Ethereum ERC-20 transfers by translating them into NEP-141 `ft_transfer` calls. Any external caller may invoke `rlp_execute` with an attached deposit, which is tracked as `CallerDeposit` and is supposed to be refunded to the caller if the emulated transaction ultimately fails. However, this refund logic is implemented only in the terminal callback `rlp_execute_callback`, and is missing from two intermediate callbacks — `address_check_callback` and `nep_141_storage_balance_callback` — that can also terminate the flow with a `PromiseResult::Failed` outcome.

### Finding Description
`rlp_execute` is a `#[payable]` entry point reachable by any unprivileged account (the "caller"/relayer) that attaches a `NearToken` deposit intended to pay the eventual receiver via a `FunctionCall`/`Transfer` action: [1](#0-0) 

Internally, `inner_rlp_execute` computes `caller_deposit` from the execution context and dispatches to different multi-step promise chains depending on the parsed `TransactionKind`: [2](#0-1) 

For an `EOABaseTokenTransfer` targeting another wallet contract, the flow first calls the address registrar and only then proceeds to `action_to_promise`, ending in `rlp_execute_callback`: [3](#0-2) 

For an `ERC20Transfer`, the flow first calls `storage_balance_of` on the target NEP-141 token, then proceeds to `ft_transfer`/`storage_deposit`, again ending in `rlp_execute_callback`: [4](#0-3) 

Only the final `rlp_execute_callback` refunds the caller's deposit on failure: [5](#0-4) 

But `address_check_callback`, which is invoked directly after the *first* promise (the registrar lookup) resolves, has no such refund when that first promise fails: [6](#0-5) 

Similarly, `nep_141_storage_balance_callback`, invoked after the `storage_balance_of` promise resolves, has no refund when that promise fails: [7](#0-6) 

This is structurally the same bug class as the reported issue: a code path that is supposed to be "type-aware" / "state-aware" about how to unwind a failed operation (there, ERC-721 vs ERC-20 deposit accounting; here, "deposit belongs to an external caller and must be refunded on failure" vs "deposit belongs to the account itself") omits the necessary handling for one of the two cases, so the asset (there, the NFT; here, the attached $NEAR deposit) becomes permanently unrecoverable by its rightful owner. The wallet contract test suite explicitly documents and tests the caller-refund invariant (`test_caller_refunds`), confirming this refund behavior is an intended, security-relevant guarantee — but the tests only cover the single-hop `action_to_promise` path, not the two multi-hop ERC20/address-check paths where the refund is missing: [8](#0-7) 

### Impact Explanation
Any unprivileged external account can call `rlp_execute` on someone else's wallet contract with an attached NEAR deposit (this is the documented "relayer compensation" mechanism — relayers routinely attach deposits that should be refunded on failure per the code's own comments and tests). If the target of an `EOABaseTokenTransfer` to another eth-implicit wallet fails the address-registrar lookup, or if the target of an `ERC20Transfer` fails to respond correctly to `storage_balance_of` (e.g., non-existent account, non-NEP-141 contract, or any contract call failure), the deposit attached by the caller is silently absorbed into the wallet contract's own balance with no mechanism for the original depositor to reclaim it. This is a concrete "permanently frozen funds" outcome for the caller, triggered purely by a single transaction from an unprivileged account.

### Likelihood Explanation
High likelihood of accidental triggering (e.g., any relayer/caller sending a transaction with an incorrect target, a token contract that is temporarily unavailable, or targeting an unregistered/nonexistent eth-implicit account) and is also trivially triggerable intentionally by any account wanting to grief a caller's deposit or by a caller who mistakenly targets a bad token contract. No special privileges, races, or validator collusion are required — a single `FunctionCall` transaction to `rlp_execute` with a non-zero attached deposit and a target that fails registrar lookup or `storage_balance_of` is sufficient.

### Recommendation
Add the same caller-deposit refund logic present in `rlp_execute_callback`'s `PromiseResult::Failed` branch to both `address_check_callback` and `nep_141_storage_balance_callback` for their respective `PromiseResult::Failed` branches, ensuring `caller_deposit` is refunded to `account_id` whenever the flow terminates in failure at any callback stage, not only at the final one.

### Proof of Concept
1. Deploy the wallet contract for an eth-implicit account `wallet.near`.
2. As an external account `caller.near` (distinct from `wallet.near`), submit a NEAR transaction calling `wallet.near::rlp_execute(target, tx_bytes_b64)` with a non-zero attached deposit, where the RLP-encoded Ethereum transaction is an ERC-20 `transfer` call (`ERC20_TRANSFER_SELECTOR`) targeting a `target` account that does not implement NEP-141's `storage_balance_of` (e.g., a plain, non-contract account or one that panics).
3. Observe: `inner_rlp_execute` builds the `ERC20Transfer` promise chain, which calls `storage_balance_of` on `target`; this call fails.
4. `nep_141_storage_balance_callback` executes its `PromiseResult::Failed` branch, returning an `ExecuteResponse{success: false, ...}` — without issuing any `promise_batch_action_transfer` to refund `caller.near`.
5. `caller.near`'s attached deposit remains part of `wallet.near`'s account balance permanently; `caller.near` has no method or key to recover it (analogous to the referenced report's ERC-721 deposit becoming permanently unrefundable due to the erroneous accounting/branch omission).

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L88-127)
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
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L134-148)
```rust
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
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L194-221)
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/tests/sanity.rs (L170-227)
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

```
