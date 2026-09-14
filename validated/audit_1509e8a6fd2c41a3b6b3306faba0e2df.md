## Analysis

The reported CVE describes a bug class where a contract's core execution entrypoint (`executeUcacTx`) can be driven into a permanently broken/DoS'd state because failure handling doesn't properly clean up state, blocking all future calls. The closest reachable analog in this nearcore codebase is the in-flight-transaction guard in the NEAR Wallet Contract (`near-wallet-contract`), which is reachable by any unprivileged relayer/RPC caller submitting an Ethereum-style transaction through `rlp_execute`.

### Title
Permanent denial-of-service of the Wallet Contract via a stuck `has_in_flight_tx` flag when a promise callback panics - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
`WalletContract::rlp_execute` is the sole entrypoint for executing user-signed Ethereum-style transactions on a NEAR "eth-implicit" account. It enforces a single-in-flight-transaction invariant using the `has_in_flight_tx` boolean field, which is set to `true` before dispatching a cross-contract promise chain and is supposed to be reset to `false` at the start of every promise callback (`address_check_callback`, `nep_141_storage_balance_callback`, `rlp_execute_callback`, `ban_relayer`). [1](#0-0) 

### Finding Description
`rlp_execute` rejects all calls while `has_in_flight_tx == true`: [2](#0-1) 

The flag is only ever cleared by the very first statement of each downstream promise callback, e.g. `address_check_callback` and `nep_141_storage_balance_callback`: [3](#0-2) [4](#0-3) 

The `has_in_flight_tx = true` write happens in a *separate receipt* from the callback (the initiating `rlp_execute` receipt), so it is committed to state permanently once that receipt completes successfully. On NEAR, a function-call execution's state mutations are only persisted if that specific WASM execution finishes without panicking; if the callback receipt itself panics for any reason (e.g., it runs out of the gas budget allocated to it before completing, or hits any other unhandled panic), *all* state writes made during that callback — including the `self.has_in_flight_tx = false` reset that was the very first line — are discarded, exactly like `executeUcacTx`'s failure to safely revert/clean up shared state on an unexpected failure path.

Because the callback's own gas budget (`ADDRESS_CHECK_CALLBACK_GAS`, `NEP_141_STORAGE_BALANCE_CALLBACK_GAS`, `RLP_EXECUTE_CALLBACK_GAS`) is a fixed static amount added on top of attacker/relayer-influenced `action.gas()`, and the callback logic path length/cost can vary with the parsed `Action` (JSON parsing of the registrar/storage-balance response, refund promise construction, nested `.then()` promise chaining), a submitter can craft an Ethereum transaction/action combination that causes the callback's own execution to exceed its statically reserved gas before it finishes, causing that receipt to fail with a panic rather than returning gracefully: [5](#0-4) [6](#0-5) 

Once this happens, `has_in_flight_tx` is permanently stuck at `true`, and `rlp_execute` will reject every subsequent transaction forever with "transaction already in progress" — there is no recovery path in the contract to clear the flag once its owning promise chain has already failed to reset it.

### Impact Explanation
This permanently freezes the affected eth-implicit account: the owner (and any relayer with access key) can never again submit a valid transaction through `rlp_execute`, because the guard check at the top of the function unconditionally short-circuits when `has_in_flight_tx` is `true`. Any funds and access controlled exclusively through this account become permanently inaccessible via the Wallet Contract's normal transaction path — this matches the "permanently frozen funds" / "transaction-triggered halt" acceptance criteria.

### Likelihood Explanation
The trigger is a single transaction/call reachable by any relayer or the account owner themselves (deliberately or accidentally) via the public `rlp_execute` method — no privileged role, validator, or network position is required. The exact gas margin needed to reliably force the callback receipt to panic (rather than complete) depends on runtime gas-cost parameters and the specific action payload size/shape (e.g., method names/args length in `FunctionCall`/`AddKey` actions, or crafting `target`/response sizes for the registrar/storage-balance lookups), but the code path and lack of any flag-recovery mechanism are concrete and directly inspectable in the contract logic itself.

### Recommendation
- Do not rely on an in-callback write as the sole mechanism to clear `has_in_flight_tx`; add a recovery mechanism (e.g., a privileged/self-only "unstick" method, or a timeout-based reset) so a partially-failed promise chain cannot permanently lock the contract.
- Ensure gas allocated to each callback (`ADDRESS_CHECK_CALLBACK_GAS`, `NEP_141_STORAGE_BALANCE_CALLBACK_GAS`, `RLP_EXECUTE_CALLBACK_GAS`) is provably sufficient for all code paths within that callback under worst-case attacker-controlled input sizes, independent of `action.gas()`.
- Consider persisting `has_in_flight_tx` optimistically at the same receipt/commit boundary as the promise dispatch using near_sdk primitives that guarantee atomic rollback of the flag alongside the promise creation, or gate the check on receipt/promise completion status queried from chain state rather than a mutable flag that can desync from the actual promise chain outcome.

### Proof of Concept
1. Deploy the Wallet Contract to an eth-implicit account and register a relayer key as in `register_relayer`.
2. Submit an RLP-encoded Ethereum transaction via `rlp_execute` whose parsed action routes to `address_check_callback` or `nep_141_storage_balance_callback` (e.g., an `EOABaseTokenTransfer` with `address_check: Some(address)`, or an `ERC20Transfer` to an unregistered receiver), while attaching the minimum total prepaid gas that still allows `rlp_execute` itself to succeed and dispatch the promise chain (satisfying `validate_tx_relayer_data`'s `InsufficientGas` check) but leaves the *fixed* callback-only gas margin (`ADDRESS_CHECK_CALLBACK_GAS` / `NEP_141_STORAGE_BALANCE_CALLBACK_GAS`) as tight as possible relative to the callback's actual execution cost (e.g., by maximizing `method_names`/`args` sizes in the encoded action so the callback's JSON parsing/promise-construction cost is inflated).
3. Observe that `rlp_execute`'s outer receipt succeeds and commits `has_in_flight_tx = true` (verifiable via `get_nonce`/contract state), while the dependent callback receipt fails with an out-of-gas/panic outcome rather than returning a graceful `ExecuteResponse`.
4. Call `rlp_execute` again with a valid, well-formed transaction and observe it is rejected with `"Error: transaction already in progress, please try again later."` indefinitely, confirming the contract is permanently locked. [7](#0-6)

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L46-55)
```rust
pub struct WalletContract {
    pub nonce: u64,
    /// Tracks whether a transaction is currently being executed
    /// (i.e. has receipts that have not yet resolved).
    /// Invariant: `has_in_flight_tx` must be `true` when a mutable method
    /// of this contract returns a promise and `false` otherwise (except
    /// for the check if a transaction is already in flight at the beginning
    /// of `rlp_execute`).
    pub has_in_flight_tx: bool,
}
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L94-106)
```rust
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
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L133-141)
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
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L194-203)
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
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L417-432)
```rust
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L439-458)
```rust
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/tests/sanity.rs (L121-168)
```rust
/// Only one transaction can be in flight at a time.
#[tokio::test]
async fn test_simultaneous_transactions() -> anyhow::Result<()> {
    let TestContext { worker, wallet_contract, wallet_sk, .. } = TestContext::new().await?;

    let receiver_account = worker.root_account().unwrap();

    let initial_receiver_balance = receiver_account.view_account().await.unwrap().balance;

    let receiver_id = receiver_account.id().as_str().into();
    let action = Action::Transfer { receiver_id, yocto_near: 1 };
    let signed_transaction =
        utils::create_signed_transaction(0, receiver_account.id(), Wei::zero(), action, &wallet_sk);
    let wallet_method_call_1 = near_workspaces::operations::Function::new("rlp_execute")
        .args_json(serde_json::json!({
            "target": receiver_account.id(),
            "tx_bytes_b64": codec::encode_b64(&codec::rlp_encode(&signed_transaction))
        }))
        .gas(near_workspaces::types::Gas::from_tgas(100));
    let wallet_method_call_2 = near_workspaces::operations::Function::new("rlp_execute")
        .args_json(serde_json::json!({
            "target": receiver_account.id(),
            "tx_bytes_b64": codec::encode_b64(&codec::rlp_encode(&signed_transaction))
        }))
        .gas(near_workspaces::types::Gas::from_tgas(100));

    let near_transaction = wallet_contract
        .inner
        .as_account()
        .batch(wallet_contract.inner.id())
        .call(wallet_method_call_1)
        .call(wallet_method_call_2)
        .transact()
        .await?;

    let result: ExecuteResponse = near_transaction.json()?;

    // The second transaction in the batch fails and this is returned as the
    // result of the Near transaction. But the first transaction in the batch
    // spawns promises that resolve, so the transfer was will successful.
    assert!(!result.success);
    assert!(result.error.unwrap().contains("transaction already in progress"));

    let final_receiver_balance = receiver_account.view_account().await.unwrap().balance;
    assert_eq!(final_receiver_balance.as_yoctonear() - initial_receiver_balance.as_yoctonear(), 1,);

    Ok(())
}
```
