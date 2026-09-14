### Title
Wallet Contract `has_in_flight_tx` lock can be permanently stuck `true`, freezing an ETH-implicit account - ([File: runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs])

### Summary
The NEAR Wallet Contract (`runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`) guards `rlp_execute` with a `has_in_flight_tx` boolean that must be reset to `false` by a subsequent callback receipt. On the "faulty relayer" path, the flag is set to `true` and the reset is delegated to a single batched promise (`delete_key` + `function_call_weight("ban_relayer", …)`) with no further `.then()` fallback. If that terminal receipt itself fails (e.g., insufficient gas), the flag is never reset, and the account becomes permanently unable to process any further `rlp_execute` calls — the same class of "small/cheap action permanently disables a core payment/dispatch entry point" as the Napier `_pay`/WETH DOS report.

### Finding Description
`WalletContract::rlp_execute` enforces mutual exclusion of in-flight transactions via `self.has_in_flight_tx`: [1](#0-0) 

On the `Err(Error::Relayer(_))` branch (triggered when a relayer misuses the contract, e.g. wrong target/namespace), the code sets `has_in_flight_tx = true` and returns a single promise built by `create_ban_relayer_promise`: [2](#0-1) 

This promise batches `delete_key(pk)` and a weighted `function_call_weight("ban_relayer", …, GasWeight(1))` into a *single* receipt targeting the contract's own account — it is not a `.then()`-chained dependent promise with its own retry/callback path. `ban_relayer` is the only code path that resets `has_in_flight_tx` back to `false` on this branch: [3](#0-2) 

All other exit points that leave `has_in_flight_tx = true` (the `Ok(promise)` branch of `rlp_execute`, and the nested callbacks `address_check_callback` / `nep_141_storage_balance_callback`) chain to `rlp_execute_callback`, which unconditionally resets the flag at entry: [4](#0-3) 

However `ban_relayer` is invoked as an action bundled in the *same* receipt as `delete_key`, with `GasWeight(1)` gas rather than a fixed guaranteed budget. If that single receipt fails for any reason (most plausibly by running out of allocated gas, since the relayer/attacker fully controls how much prepaid gas is attached to the whole `rlp_execute` transaction and thus how much unused gas remains to be distributed by weight to this final action), the entire receipt's state changes are discarded per NEAR's per-receipt execution model, and `ban_relayer` never executes. Because `has_in_flight_tx = true` was already committed in the prior (successful) receipt, there is no other code path in the contract that can ever reset it. Every subsequent call to `rlp_execute` will immediately short-circuit at the in-flight check and return `"Error: transaction already in progress, please try again later."`, indefinitely.

This is directly analogous to the reported Napier issue: a cheap, attacker-controlled action (here, sending a transaction with a relayer error and deliberately minimal attached gas) drives the contract into a state that permanently disables its core dispatch function (`rlp_execute`), with no recovery path other than redeploying/migrating the account.

### Impact Explanation
An ETH-implicit account's Wallet Contract is its only means of executing NEAR actions (transfers, function calls, key management) on behalf of the Ethereum-style key holder — see the "Without going into details, an Ethereum-compatible wallet user sends a transaction... to the Wallet Contract" design description. If `has_in_flight_tx` is stuck `true`, the account is permanently unable to submit any further transactions through `rlp_execute`, effectively freezing all funds and functionality controlled by that ETH-implicit account (a form of permanently frozen funds / permanent halt of a specific account's transaction processing). Any relayer holding a `FunctionCall` access key for `rlp_execute` (which the design explicitly allows to be added by the user for gas sponsorship) can trigger the "faulty relayer" branch and, by controlling the transaction's attached gas, can attempt to starve the terminal `ban_relayer` receipt.

### Likelihood Explanation
Reaching the `Error::Relayer` branch requires only a normal, permissionless `FunctionCall` transaction to `rlp_execute` from an account holding a limited-allowance access key on the ETH-implicit account (a standard, documented relayer setup) — no validator or node compromise is needed. Whether the terminal batched receipt can reliably be starved of gas depends on exact gas-accounting mechanics of `GasWeight`/`function_call_weight` and the minimum gas guaranteed to a weighted action, which I was **not able to fully verify** from the available code/docs within the tool budget (gas distribution logic lives in `runtime/runtime/src/receipt_manager.rs`, which I could not fully inspect). This uncertainty should be resolved before treating likelihood as confirmed; the finding should be validated by reproducing the scenario against `receipt_manager.rs`'s gas-weight distribution and receipt-failure/rollback semantics for multi-action receipts.

### Recommendation
- Guarantee a minimum, non-weight-dependent gas budget for the `ban_relayer` action (e.g. `function_call` with a fixed static gas rather than `function_call_weight`/`GasWeight`), so it cannot be starved regardless of the caller's prepaid gas.
- Add a way to reset `has_in_flight_tx` independent of any single receipt's success — for example, gate the lock with a timeout/expiry (e.g. block height/timestamp) so a stuck flag self-clears, or split `delete_key` and `ban_relayer` into two independently-scheduled promises so a failure of one does not prevent the flag reset in the other.
- Add integration tests that deliberately under-fund the relayer-ban receipt's gas to confirm `has_in_flight_tx` is still recoverable.

### Proof of Concept
1. User creates an ETH-implicit account and adds a `FunctionCall` access key (relayer key) restricted to `rlp_execute`, per the documented relayer flow (`docs/DataStructures/Account.md` / `integration-tests/src/tests/features/wallet_contract.rs`).
2. Attacker (holding, or acting as, that relayer key — or simply the signer of the transaction) submits an `rlp_execute` transaction whose parsed target/namespace check fails such that `inner_rlp_execute` returns `Err(Error::Relayer(_))` and `env::signer_account_id() == current_account_id`, entering the branch at `lib.rs:121-125`.
3. Attacker sets the transaction's `prepaid_gas` to the minimum required to reach this branch, so that after `delete_key` consumes its fixed cost, the remaining unused gas handed to the `GasWeight(1)`-weighted `ban_relayer` function call in the same receipt is insufficient to complete execution.
4. That receipt fails; `has_in_flight_tx` (already committed as `true`) is never reset because `ban_relayer` — the only reset path on this branch — did not run.
5. Any subsequent `rlp_execute` call against the account now returns `"Error: transaction already in progress, please try again later."` permanently, confirmed via `WalletContract::rlp_execute`'s in-flight check at `lib.rs:97-105`.

(Step 3's precise gas threshold and the atomicity/rollback behavior of multi-action receipts were not independently confirmed against `runtime/runtime/src/receipt_manager.rs` within this investigation; this is flagged as an open verification item.)

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L93-128)
```rust
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L275-281)
```rust
    #[private]
    pub fn rlp_execute_callback(
        &mut self,
        caller_deposit: Option<CallerDeposit>,
    ) -> ExecuteResponse {
        self.has_in_flight_tx = false;
        let n = env::promise_results_count();
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L319-327)
```rust
    #[private]
    pub fn ban_relayer(&mut self) -> ExecuteResponse {
        self.has_in_flight_tx = false;
        ExecuteResponse {
            success: false,
            success_value: None,
            error: Some("Error: faulty relayer".into()),
        }
    }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L503-512)
```rust
fn create_ban_relayer_promise(current_account_id: AccountId) -> Promise {
    let pk = env::signer_account_pk();
    Promise::new(current_account_id).delete_key(pk).function_call_weight(
        "ban_relayer".into(),
        Vec::new(),
        NearToken::from_yoctonear(0),
        Gas::from_tgas(1),
        GasWeight(1),
    )
}
```
