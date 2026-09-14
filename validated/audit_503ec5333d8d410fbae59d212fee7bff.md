### Title
Wallet Contract `has_in_flight_tx` flag can become permanently stuck `true`, permanently freezing the ETH-implicit account - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
The `WalletContract::rlp_execute` entry point uses a persistent boolean flag, `has_in_flight_tx`, to serialize execution: it is set to `true` before dispatching a cross-contract promise chain and is only reset to `false` inside one of a fixed set of `#[private]` callback methods (`rlp_execute_callback`, `address_check_callback`, `nep_141_storage_balance_callback`, `ban_relayer`). All callback gas budgets are fixed static constants. Because NEAR receipt execution is atomic (a failed receipt rolls back *all* of its state writes), if the callback receipt itself fails — e.g. because its fixed gas budget is insufficient to finish executing — the `self.has_in_flight_tx = false` write made at the top of the callback is rolled back along with everything else, leaving the flag permanently `true`. Every subsequent `rlp_execute` call then unconditionally returns `"transaction already in progress"`, permanently bricking the account.

### Finding Description
`rlp_execute` guards against concurrent execution with: [1](#0-0) 

The contract's own doc comment states the invariant this code relies on: `has_in_flight_tx` must become `false` again once the dispatched promise resolves: [2](#0-1) 

The reset is only performed as the first statement of each callback, e.g.: [3](#0-2) [4](#0-3) 

These callbacks are always scheduled with a **fixed** static gas amount (`RLP_EXECUTE_CALLBACK_GAS`, `ADDRESS_CHECK_CALLBACK_GAS`, `NEP_141_STORAGE_BALANCE_CALLBACK_GAS`), analogous to the fixed `MIN_FALLBACK_RESERVE` gas stipend in the original Solidity report: [5](#0-4) 

Nearcore's runtime treats an entire action receipt atomically: if execution of the receipt fails (including running out of prepaid gas), *all* state changes from that receipt — including any `set_account`/contract-state writes already made — are discarded via `state_update.rollback()`: [6](#0-5) 

Consequently, if the scheduled callback receipt fails for any reason after the flag would have been reset (e.g., insufficient fixed gas to finish deserializing/processing a larger-than-expected cross-contract result, or any other execution error inside the callback body), the `has_in_flight_tx = false` write is rolled back together with the failure, exactly mirroring the Solidity bug class where the "fallback" step (here, the flag reset) is assumed to always occur but is not guaranteed to succeed given a fixed gas reservation.

### Impact Explanation
Once `has_in_flight_tx` is stuck at `true`, every future call to `rlp_execute` for that account immediately short-circuits and returns `"Error: transaction already in progress, please try again later."` without ever dispatching a new promise or providing any recovery path. Since `rlp_execute` is the sole entry point through which the ETH-implicit account can perform any NEAR action (transfers, function calls, key management), the account becomes permanently unable to move its funds or otherwise interact with the chain — a permanent freeze of funds/state, consistent with the "permanently frozen funds" acceptance criterion.

### Likelihood Explanation
This is reachable by any single relayer-submitted transaction that a legitimate user/relayer would send in the ordinary course of using the wallet contract (no privileged or malicious-validator access needed). Triggering the failure only requires a callback receipt to exhaust its fixed gas budget or otherwise fail during execution (e.g., a cross-contract call returning an unexpectedly large payload that the small fixed 5 Tgas callback budget cannot fully process) — plausible under normal operating conditions since the gas budgets are hard-coded constants rather than dynamically sized to the actual response.

### Recommendation
Do not rely on the callback body to reset `has_in_flight_tx` at the top of a fallible function whose later logic can still fail and roll back the earlier write. Instead:
- Reserve enough gas to guarantee the flag-reset write is committed independently of the rest of the callback logic (e.g., split the state-clearing write into its own always-succeeding step, or wrap the remaining logic so a failure there cannot roll back the flag reset).
- Alternatively, avoid storing `has_in_flight_tx` as ordinary contract state written inside the same receipt that can fail; use a mechanism (e.g., a scheduled recovery/timeout callback similar to NEAR's own `PromiseYield` timeout mechanism) that guarantees the lock is eventually released even if the primary callback receipt fails.

### Proof of Concept
1. A relayer submits an `rlp_execute` call whose emulated action routes through `nep_141_storage_balance_callback` → `action_to_promise(...).then(ext.rlp_execute_callback(...))`, with `NEP_141_STORAGE_BALANCE_CALLBACK_GAS` as the statically attached gas for the eventual `rlp_execute_callback`.
2. Craft/trigger a scenario where the inner cross-contract call chain returns a payload whose processing inside `rlp_execute_callback` needs more gas than the fixed budget allows (or otherwise induces a runtime execution failure in that receipt).
3. The `rlp_execute_callback` receipt fails; per NEAR's atomic-rollback semantics, its `self.has_in_flight_tx = false;` write is discarded along with the rest of the receipt's changes.
4. `has_in_flight_tx` remains `true` in the persisted contract state.
5. Any subsequent call to `rlp_execute` by the legitimate owner immediately returns `"Error: transaction already in progress, please try again later."`, permanently, with no way to reset the flag — freezing the account.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L37-41)
```rust
const RLP_EXECUTE_CALLBACK_GAS: Gas = Gas::from_tgas(5);
const ADDRESS_CHECK_CALLBACK_GAS: Gas = Gas::from_tgas(5).saturating_add(RLP_EXECUTE_CALLBACK_GAS);
const NEP_141_STORAGE_BALANCE_CALLBACK_GAS: Gas = Gas::from_tgas(5)
    .saturating_add(NEP_141_STORAGE_DEPOSIT_GAS)
    .saturating_add(RLP_EXECUTE_CALLBACK_GAS);
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L48-55)
```rust
    /// Tracks whether a transaction is currently being executed
    /// (i.e. has receipts that have not yet resolved).
    /// Invariant: `has_in_flight_tx` must be `true` when a mutable method
    /// of this contract returns a promise and `false` otherwise (except
    /// for the check if a transaction is already in flight at the beginning
    /// of `rlp_execute`).
    pub has_in_flight_tx: bool,
}
```

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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L276-281)
```rust
    pub fn rlp_execute_callback(
        &mut self,
        caller_deposit: Option<CallerDeposit>,
    ) -> ExecuteResponse {
        self.has_in_flight_tx = false;
        let n = env::promise_results_count();
```

**File:** protocol-model/spec/runtime-execution.md (L149-149)
```markdown
- **Failed receipt atomicity**: a receipt whose result is `Err` triggers `state_update.rollback()`, so no state changes persist except the outcome/gas accounting (`runtime/runtime/src/lib.rs:967`). `set_error` additionally clears queued receipts, proposals, and burnt/subsidized amounts (`runtime/runtime/src/lib.rs:487`).
```
