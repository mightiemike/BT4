### Title
Wallet Contract drops `CallerDeposit` refund on intermediate-promise failure (address-registrar / NEP-141 storage checks), permanently absorbing an external caller's attached deposit - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
The bug report describes a class of issue where an internal accounting variable (`stored_balances`) is tracked through only some of the code paths a value can take, so on the path that is missed the accounting is silently wrong and funds become stuck/the contract becomes unusable. The NEAR Wallet Contract has the same structural flaw: `CallerDeposit` is the value meant to track and refund an external caller's `attached_deposit` if the eventual cross-contract action fails, but it is threaded through only the terminal `rlp_execute_callback`, not through the two other failure branches that occur earlier in the same promise chain (`address_check_callback` and `nep_141_storage_balance_callback`).

### Finding Description
`inner_rlp_execute` computes `caller_deposit` once via `CallerDeposit::new(&context)`, which is `Some` whenever the predecessor differs from the current (wallet) account, i.e. whenever an "external caller" (not the wallet's own owner/relayer key) attaches a deposit: [1](#0-0) 

That `caller_deposit` is threaded through to callbacks so the deposit can be returned if the action ultimately fails, and this refund is implemented only in `rlp_execute_callback`'s `PromiseResult::Failed` branch: [2](#0-1) 

However, for two `EthEmulationKind` variants the executed promise chain has an *extra* intermediate cross-contract call before reaching `rlp_execute_callback`:
- `EOABaseTokenTransfer { address_check: Some(_), .. }` first calls the address registrar, resolved in `address_check_callback`.
- `ERC20Transfer` first calls `storage_balance_of` on the token, resolved in `nep_141_storage_balance_callback`.

Both of these intermediate callbacks receive `caller_deposit: Option<CallerDeposit>` as a parameter (so the contract clearly intends to eventually refund it), but their `PromiseResult::Failed` branches return an error `ExecuteResponse` **without ever consuming or refunding `caller_deposit`**: [3](#0-2) [4](#0-3) 

This is structurally identical to the reported Curve bug: a balance/deposit-tracking value (`stored_balances` / `caller_deposit`) is correctly updated on the "normal" path but the code that forwards/propagates it omits the update on an alternate branch reachable from the same entry point, and the value is silently lost instead of raising an error or completing accounting.

### Impact Explanation
Any unprivileged account can call `rlp_execute` directly on a deployed Wallet Contract instance with an attached NEAR deposit (this is exactly what `test_caller_refunds` exercises, confirming the refund path is a supported, reachable feature for arbitrary external callers, not an operator-only path): [5](#0-4) 

If the transaction the caller submits happens to be an `ERC20Transfer` (any FT emulated via the wallet) whose `storage_balance_of` call to the token contract fails — e.g., the token account does not exist, is out of gas, the token contract panics, or simply is temporarily unavailable — the deposit attached by the caller is retained by the Wallet Contract and never returned. The same occurs for an `EOABaseTokenTransfer` sent to an address requiring an address-registrar lookup, if that lookup call fails. This is a concrete, transaction-triggered loss of funds for the caller (their deposit is absorbed into the wallet account's balance with no path to recovery through the contract's own logic), matching the "permanently frozen funds" / unauthorized value retention criteria.

### Likelihood Explanation
This is reachable by any account that calls `rlp_execute` with a non-zero attached deposit and constructs (or is given, e.g. by a malicious/careless relayer or simply due to normal network conditions) a transaction whose auxiliary lookup call fails — no privileged role, validator behavior, or malicious peer is required. The `storage_balance_of` / registrar-lookup call can fail for mundane reasons (non-existent account, insufficient gas, unrelated contract error), making the failure condition realistically triggerable, not merely theoretical.

### Recommendation
In both `address_check_callback` and `nep_141_storage_balance_callback`, the `PromiseResult::Failed` branches should refund `caller_deposit` exactly as `rlp_execute_callback` does, before returning the error `ExecuteResponse`. Alternatively, refactor so that `caller_deposit` refund logic lives in one shared helper invoked from every terminal error branch in the promise chain, eliminating the possibility of a code path silently dropping it.

### Proof of Concept
1. Deploy the Wallet Contract as a global contract, and derive/fund an eth-implicit account with it (as in `runtime/near-wallet-contract/implementation/wallet-contract/src/tests/emulation.rs` `test_erc20_emulation`).
2. As an external caller (an account distinct from the wallet's own key/relayer), call `rlp_execute` with a non-zero attached deposit, targeting an ERC-20 `transfer` (`EthEmulationKind::ERC20Transfer`) pointed at a token contract account that does not exist or will make `storage_balance_of` fail (e.g., un-deployed contract, or one deliberately reverting on that view call).
3. Observe that `nep_141_storage_balance_callback` is invoked, `env::promise_result(0)` is `PromiseResult::Failed`, and the function returns an error `ExecuteResponse` without creating any refund promise for `caller_deposit`.
4. Verify (via `view_account`) that the caller's attached deposit balance decrease is not returned to the caller and instead remains in the wallet contract's account balance — unlike the behavior verified for the `rlp_execute_callback` failure path in `test_caller_refunds`.

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
