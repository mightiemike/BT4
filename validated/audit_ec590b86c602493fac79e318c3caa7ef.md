### Title
Wallet Contract's `has_in_flight_tx` mutex can be permanently stuck at `true`, freezing all future transactions and any funds routed through the account - ([File: runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs])

### Summary
The NEAR Wallet Contract (`near-wallet-contract`, used to let Ethereum-signed transactions drive an eth-implicit NEAR account, functionally the "bridge/relay" entry point for that account) enforces that only one logical transaction can be "in flight" at a time via a boolean flag `has_in_flight_tx`. This flag is set to `true` when `rlp_execute` spawns a cross-contract promise chain, and is only reset to `false` inside the corresponding callback methods (`rlp_execute_callback`, `address_check_callback`, `nep_141_storage_balance_callback`). If the receipt executing any of these callbacks fails wholesale (e.g., runs out of the statically hard-coded gas budget, or panics for any other unhandled reason) before it can execute the line that resets the flag, NEAR's per-receipt state rollback semantics discard that state change together with everything else the callback receipt did. Because the nonce increment and `has_in_flight_tx = true` write happened in an earlier, already-committed receipt, they are not rolled back. The contract is left permanently in the `has_in_flight_tx == true` state with no other method able to clear it, so every subsequent `rlp_execute` call is unconditionally rejected with `"transaction already in progress, please try again later"`.

### Finding Description
`WalletContract::rlp_execute` is the sole entry point that lets an external relayer (an unprivileged transaction submitter) drive actions on behalf of the wallet's eth-implicit account: [1](#0-0) 

It guards re-entrancy with `has_in_flight_tx`, set `true` right before returning a `Promise`, and is documented as an invariant that must be restored to `false` by the eventual callback: [2](#0-1) 

The three callback methods (`address_check_callback`, `nep_141_storage_balance_callback`, `rlp_execute_callback`) are the *only* code paths that flip the flag back to `false`, and each does so at the very top of the function body: [3](#0-2) [4](#0-3) [5](#0-4) 

The gas attached to each of these callbacks is a hard-coded constant, not derived from the actual size/cost of the work the callback will do (e.g. deserializing an NEP-141 `storage_balance_of` response, or the registrar lookup result): [6](#0-5) 

NEAR's runtime semantics for a `FunctionCall` action are "commit on success, rollback on failure" for that receipt only: any panic (including host-triggered ones such as gas exhaustion) discards *all* state changes made during that receipt's execution, per `apply_action_receipt`/`apply_action` in the runtime crate. Because the write of `has_in_flight_tx = false` happens inside the callback function itself (not before it), a callback that panics for any reason - most plausibly running out of its statically-budgeted gas when handling an unexpectedly large or expensive `promise_result` payload - rolls back that write along with everything else, while the earlier receipt that already set `has_in_flight_tx = true` remains committed. There is no other method in the contract (no admin function, no timeout, no "unstick" mechanism) that can reset the flag once this occurs.

This is structurally analogous to the reported bug: the `L2CrossDomainMessenger`'s `pause()`/`whenNotPaused` mechanism causes `relayMessage` to permanently revert for in-flight L1→L2 transfers with no automatic recovery path, freezing bridged funds. Here, the wallet contract's `has_in_flight_tx` mutex plays the role of the "pause" state - once stuck `true`, it unconditionally rejects (reverts) every future `rlp_execute` call the same way `relayMessage` unconditionally reverts while paused, and there is no built-in mechanism to retry or unstick it.

### Impact Explanation
Once `has_in_flight_tx` is stuck at `true`:
- Every future `rlp_execute` invocation for that account immediately short-circuits to the "transaction already in progress" error branch: [7](#0-6) 
- The account (and any Ethereum-emulated tokens, NEAR balance, or NEP-141 balances routed through it) becomes permanently unusable via its intended entry point, matching the "permanently frozen funds" impact class. This is a High-severity, unauthorized-value-lockup condition reachable by any relayer/user interacting with the contract, not just a privileged operator.

### Likelihood Explanation
Triggering the bug requires only that one of the three fixed-gas callback receipts fails outright (panics) instead of returning a graceful `Err`/`ExecuteResponse{success:false,...}`. The most direct way to force this is gas exhaustion in a callback: an attacker (acting as, or manipulating the behavior of, a relayer or a target contract queried via cross-contract call) can cause the NEP-141 `storage_balance_of` call or address-registrar lookup to return an oversized/deeply nested payload, or otherwise cause the callback's actual gas usage to exceed the hard-coded `NEP_141_STORAGE_BALANCE_CALLBACK_GAS` / `ADDRESS_CHECK_CALLBACK_GAS` / `RLP_EXECUTE_CALLBACK_GAS` budgets. Any unhandled panic path in the callback (not just gas exhaustion, but any bug in `action_to_promise`, deserialization, or SDK-level panics) has the same effect, since the flag reset always happens at the *top* of the callback but is *conditionally* undone if the ambient receipt aborts. This does not require a malicious validator, network partition, or leaked keys - a single crafted call from an ordinary transaction signer/RPC caller who controls the contract targeted by the wallet's cross-contract call (or the response size) is sufficient.

### Recommendation
- Move the `has_in_flight_tx = false` reset to the earliest possible point and make it panic-safe, or better, structure the state machine so it cannot be left inconsistent by a partial-execution rollback (e.g., use a value/counter that is reconciled independently of the callback's success, or budget callback gas dynamically/conservatively with a safety margin well above worst-case response sizes).
- Add an explicit "unstick" / recovery path: e.g., allow a full-access-key holder (the account owner) to reset `has_in_flight_tx` after a timeout, so a stuck flag does not permanently brick the account.
- Audit and increase the static gas constants (`RLP_EXECUTE_CALLBACK_GAS`, `ADDRESS_CHECK_CALLBACK_GAS`, `NEP_141_STORAGE_BALANCE_CALLBACK_GAS`) to bound the maximum possible response size from arbitrary NEP-141/registrar contracts, or validate/truncate cross-contract responses before they can cause unbounded gas usage in the callback.

### Proof of Concept
1. Register/derive an eth-implicit account with the Wallet Contract deployed (as in `test_context.rs`).
2. Craft (or control) a target NEP-141 token contract (or the address registrar) so that its `storage_balance_of` response is unusually large or otherwise expensive to deserialize/process.
3. Submit an `rlp_execute` call whose parsed action is an `ERC20Transfer`/emulated transfer to an unregistered receiver, forcing the flow through `nep_141_storage_balance_callback`: [8](#0-7) 
4. Because `NEP_141_STORAGE_BALANCE_CALLBACK_GAS` is a small fixed constant, the callback runs out of gas while handling the oversized response and the whole receipt reverts, discarding the `has_in_flight_tx = false` write while the earlier `has_in_flight_tx = true` write (from the initiating receipt) remains committed.
5. Any subsequent `rlp_execute` call against this account now unconditionally returns `success:false, error:"Error: transaction already in progress, please try again later."` forever, as demonstrated by the existing (intentionally transient) reentrancy test: [9](#0-8) 
which shows the exact error string and mechanism, but with no code path to ever unset it once the resetting callback itself fails to complete.

Note: I was unable to execute this scenario end-to-end in the current environment (no filesystem/terminal access) to empirically confirm that the specific gas constants can be exceeded in practice; this assessment is based on static analysis of the code paths and NEAR's documented per-receipt commit/rollback semantics. A Devin session with repository and sandbox access would be needed to build a concrete gas-exhaustion payload and confirm the panic actually occurs before the flag reset line executes.

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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L276-281)
```rust
    pub fn rlp_execute_callback(
        &mut self,
        caller_deposit: Option<CallerDeposit>,
    ) -> ExecuteResponse {
        self.has_in_flight_tx = false;
        let n = env::promise_results_count();
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
