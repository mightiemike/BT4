### Title
Hardcoded `RLP_EXECUTE_CALLBACK_GAS` (and related NEP-141 gas constants) in the Wallet Contract can permanently brick a wallet if the callback runs out of gas - ([File: runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs])

### Summary
The `WalletContract` hardcodes fixed `Gas` budgets (`NEP_141_STORAGE_DEPOSIT_GAS`, `NEP_141_STORAGE_BALANCE_OF_GAS`, `REGISTRAR_LOOKUP_GAS`, `RLP_EXECUTE_CALLBACK_GAS`) for its internal cross-contract calls and callbacks, exactly like the hardcoded 50,000-gas ETH transfer in the reported Auction contract. [1](#0-0)  If the fixed budget for `rlp_execute_callback` ever proves insufficient (e.g. higher WASM/host-function costs, larger callback payloads, or a future contract upgrade that adds logic to the callback), the callback's `FunctionCall` fails with `GasExceeded`, and because NEAR reverts *all* state changes of a failed receipt, the `has_in_flight_tx = false` reset inside that callback never takes effect - leaving the flag permanently `true` and the wallet permanently unusable.

### Finding Description
`rlp_execute` refuses to start a new transaction whenever `has_in_flight_tx` is `true`: [2](#0-1) 

The flag is set to `true` right before scheduling the async chain, and the *only* code path that resets it back to `false` is inside the callback methods (`address_check_callback`, `nep_141_storage_balance_callback`, `rlp_execute_callback`, `ban_relayer`), always as the very first statement: [3](#0-2) [4](#0-3) 

All of these callbacks are scheduled with a hardcoded, fixed `Gas` value taken from the constants at the top of the file - e.g. `RLP_EXECUTE_CALLBACK_GAS = Gas::from_tgas(5)`, used directly with `.with_static_gas(RLP_EXECUTE_CALLBACK_GAS)`: [5](#0-4) [6](#0-5) 

If the callback's execution requires more gas than this fixed budget (analogous to the Auction contract's fixed 50,000 gas becoming insufficient after a destination-contract change or gas-cost repricing), the callback `FunctionCall` action fails with `GasExceeded`. Per NEAR's execution model, a failed `FunctionCall` receipt reverts *all* state changes performed during that execution — including the `self.has_in_flight_tx = false` line that must run to unlock the contract. Since setting the flag back to `false` is entirely dependent on that same execution succeeding, the flag is stuck at `true` forever, and every future call to `rlp_execute` from any signer/relayer will short-circuit with "transaction already in progress" before doing anything else. There is no other method in the contract (no admin reset, no timeout) that clears `has_in_flight_tx`. [7](#0-6) 

### Impact Explanation
This produces a permanent denial of service / fund lock on the affected eth-wallet-contract account: the account can never again process an Ethereum-emulated transaction (transfers, ERC-20 transfers, contract calls) once triggered, since `rlp_execute` is gated entirely by the stuck flag. Any value already deposited to or controlled through that wallet contract (native NEAR held by the account, or tokens it was in the middle of moving) becomes practically inaccessible via the wallet's normal interface, matching the "permanently frozen funds" acceptance criterion. The trigger is reachable via a single ordinary transaction (any relayer or the account owner submitting `rlp_execute`) combined with a gas-repricing or logic-growth event exactly analogous to the three triggers listed in the original report (contract logic update, third-party gas-profile change, protocol gas-cost change).

### Likelihood Explanation
The hardcoded budgets are small (5 Tgas), leaving little safety margin, and the callback logic branches into multiple paths (`Failed`, `Successful` decode error, promise-count mismatch, refund promise creation) whose combined cost is not dynamically bounded. Any future increase to per-op WASM costs, host-function costs (`promise_result` deserialization, `promise_batch_create`/`promise_batch_action_transfer`), or added logic in a contract upgrade could push actual usage above 5 Tgas for some execution paths, making this reachable without any attacker action — purely from routine protocol or contract evolution, the same class of trigger described in the source report.

### Recommendation
Avoid hardcoding a small fixed gas amount for callbacks that gate a critical unlock flag. Either:
- Attach the callback with a gas *weight* (`promise_batch_action_function_call_weight` / unspent-gas ratio) so it always receives all remaining gas rather than a small fixed slice, or
- Add a fail-safe/reset path independent of the callback succeeding (e.g., a time-based or predecessor-authorized `force_clear_in_flight` method), so a stuck `has_in_flight_tx` cannot permanently disable the wallet.

### Proof of Concept
1. A relayer submits a valid RLP-encoded transaction to `rlp_execute`, which schedules `nep_141_storage_balance_callback`/`rlp_execute_callback` with the fixed `RLP_EXECUTE_CALLBACK_GAS` (5 Tgas) static gas.
2. Suppose (due to a future protocol gas repricing, or contract upgrade adding logic to `rlp_execute_callback`) the actual gas needed to execute the callback (JSON/borsh decode of `promise_result`, `promise_batch_create` + `promise_batch_action_transfer` for the refund, `ExecuteResponse` serialization) exceeds 5 Tgas.
3. The callback's `FunctionCall` receipt fails with `GasExceeded`; the runtime reverts all state changes from that receipt, so `has_in_flight_tx` (which was set to `true` in the parent call) remains `true`.
4. Every subsequent call to `rlp_execute` on this account immediately returns `"Error: transaction already in progress, please try again later."` [8](#0-7)  forever, since no method exists to clear the flag without going through the same vulnerable callback path.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L33-41)
```rust
const NEP_141_STORAGE_DEPOSIT_AMOUNT: NearToken = NearToken::from_yoctonear(1_250 * MICRO_NEAR);
const NEP_141_STORAGE_DEPOSIT_GAS: Gas = Gas::from_tgas(5);
const NEP_141_STORAGE_BALANCE_OF_GAS: Gas = Gas::from_tgas(5);
const REGISTRAR_LOOKUP_GAS: Gas = Gas::from_tgas(5);
const RLP_EXECUTE_CALLBACK_GAS: Gas = Gas::from_tgas(5);
const ADDRESS_CHECK_CALLBACK_GAS: Gas = Gas::from_tgas(5).saturating_add(RLP_EXECUTE_CALLBACK_GAS);
const NEP_141_STORAGE_BALANCE_CALLBACK_GAS: Gas = Gas::from_tgas(5)
    .saturating_add(NEP_141_STORAGE_DEPOSIT_GAS)
    .saturating_add(RLP_EXECUTE_CALLBACK_GAS);
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L93-105)
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
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L116-128)
```rust
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L222-223)
```rust
        let current_account_id = env::current_account_id();
        let ext = WalletContract::ext(current_account_id).with_static_gas(RLP_EXECUTE_CALLBACK_GAS);
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L462-470)
```rust
            let ext: WalletContractExt =
                WalletContract::ext(current_account_id).with_static_gas(RLP_EXECUTE_CALLBACK_GAS);
            ext.rlp_execute_callback(caller_deposit)
        }
        _ => {
            let ext =
                WalletContract::ext(current_account_id).with_static_gas(RLP_EXECUTE_CALLBACK_GAS);
            action_to_promise(target, action)?.then(ext.rlp_execute_callback(caller_deposit))
        }
```
