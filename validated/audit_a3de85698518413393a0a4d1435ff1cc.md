Given the scope explicitly calls out "the NEAR wallet contract" as a valid reachable target, I focused there and found a concrete analog to the Compound "partially-fixed re-entrancy guard" issue.

### Title
Reentrancy-style `has_in_flight_tx` guard in `WalletContract` can become permanently stuck, freezing the account - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
`WalletContract` (the NEAR "eth-wallet"/meta-transaction contract) uses a boolean flag `has_in_flight_tx` as a reentrancy-style lock to guarantee only one Ethereum-emulated transaction is "in flight" (i.e. has unresolved cross-contract promises) at a time. This mirrors exactly the kind of ad-hoc reentrancy guard that the Compound-derived report warns about being only *partially* fixed: the flag is set to `true` before scheduling a promise chain and is only reset to `false` at the very top of the corresponding `#[private]` callback [1](#0-0) . Because NEAR only persists contract state changes when the function call returns successfully (any panic/host error discards all state mutations for that receipt), any failure path in the callback that occurs *before* reaching the point where `has_in_flight_tx` reset is committed leaves the flag permanently `true`, with no recovery function anywhere in the contract.

### Finding Description
The lock is armed in `rlp_execute`: if `has_in_flight_tx` is already `true` the call is rejected; otherwise a promise chain is built and `self.has_in_flight_tx = true` is set right before returning `PromiseOrValue::Promise(promise)` [2](#0-1) .

The lock is supposed to be released inside each `#[private]` continuation: `address_check_callback`, `nep_141_storage_balance_callback`, `rlp_execute_callback`, and `ban_relayer` all start with `self.has_in_flight_tx = false;` [3](#0-2) [4](#0-3) [5](#0-4) .

The problem is that these callbacks then perform further logic — including forwarding an attacker/user-influenced cross-contract call target (`token_id`/`target`) and deserializing its response with `serde_json::from_slice` — all metered against a **fixed static gas budget** (`ADDRESS_CHECK_CALLBACK_GAS`, `NEP_141_STORAGE_BALANCE_CALLBACK_GAS`, `RLP_EXECUTE_CALLBACK_GAS`) plus the caller-supplied `action.gas()` [6](#0-5) . If that callback execution runs out of prepaid gas, or panics for any other host-level reason (e.g. index-out-of-range on `env::promise_result`), the entire function-call outcome is a failure and — per NEAR's execution model — **no state changes are committed**, including the `has_in_flight_tx = false` write that happened at the very start of the function. Since `rlp_execute` unconditionally refuses to start a new transaction whenever `has_in_flight_tx == true` [7](#0-6) , and there is no other method in the contract capable of clearing this flag, the wallet account becomes permanently unable to process any further Ethereum-emulated transaction.

This is the direct structural analog of the report: a reentrancy/concurrency guard was added, but the fix only covers the "happy path" reset and does not defensively guarantee the guard is released under all failure/edge-case executions — an incomplete mitigation of the same bug class.

### Impact Explanation
Once `has_in_flight_tx` gets stuck at `true`, `rlp_execute` — the sole entry point for the account owner to authorize any NEAR action (transfers, function calls, key management) via their Ethereum signature — will always short-circuit with "transaction already in progress" [8](#0-7) . For an eth-implicit account whose only authorization path is this contract, this permanently freezes the account's ability to move its $NEAR balance or any tokens/assets tied to it — satisfying the "permanently frozen funds" impact criterion.

### Likelihood Explanation
Triggering this requires only a single crafted `rlp_execute` transaction whose emulated action targets a contract (e.g. an ERC-20 `token_id`) that returns a response large/expensive enough, or a NEP-141/registrar call whose downstream deserialization/execution cost exceeds the fixed static callback gas budget, causing the callback receipt to fail with an out-of-gas or host error before the `has_in_flight_tx = false` write is committed. Because `target`/`token_id` and the forwarded `action.gas()` come from the signed Ethereum transaction, an attacker who can get their own or another party's malicious/oversized-response contract used as the transfer target can reliably reproduce the stuck state without needing any special privilege beyond submitting a transaction.

### Recommendation
- Add an explicit `#[private]` "unlock"/recovery method (or a self-callback with `.function_call_weight` guaranteed minimal gas) that can always reset `has_in_flight_tx` regardless of upstream failures, or
- Restructure the flow so the guard is released via a dedicated final catch-all callback attached with a minimal, gas-independent budget that cannot itself fail due to downstream response size, and
- Bound/validate the size of cross-contract responses (e.g. `storage_balance_of`, registrar `lookup`) before deserialization so gas usage in the callback is deterministic and cannot exceed the statically reserved gas.

### Proof of Concept
1. Wallet owner (or a relayer acting on their signed data) submits an `rlp_execute` transaction whose emulated action is an `ERC20Transfer` targeting a contract that responds to `storage_balance_of` with an oversized/adversarial JSON payload.
2. `inner_rlp_execute` schedules `storage_balance_of` -> `nep_141_storage_balance_callback`, setting `has_in_flight_tx = true` [9](#0-8) .
3. `nep_141_storage_balance_callback` executes, resets the in-memory flag to `false`, then attempts to deserialize/process the oversized response and exhausts `NEP_141_STORAGE_BALANCE_CALLBACK_GAS + action.gas()` before returning.
4. The receipt fails with an out-of-gas `FunctionCallError`; per NEAR semantics no state change from this call (including the `false` reset) is persisted, so `has_in_flight_tx` remains `true` in the trie.
5. Any subsequent `rlp_execute` call from the legitimate owner is rejected forever with "transaction already in progress", permanently freezing the account.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L36-41)
```rust
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L134-140)
```rust
    pub fn address_check_callback(
        &mut self,
        target: AccountId,
        action: near_action::Action,
        caller_deposit: Option<CallerDeposit>,
    ) -> PromiseOrValue<ExecuteResponse> {
        self.has_in_flight_tx = false;
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L194-202)
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
