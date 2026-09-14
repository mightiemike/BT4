### Title
Wallet Contract's `has_in_flight_tx` guard can be permanently stuck `true`, freezing the account forever - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
`WalletContract` (the eth-implicit account "NEAR wallet contract") gates every `rlp_execute` call behind a single boolean, `has_in_flight_tx`, which is only cleared inside the promise callbacks that follow `rlp_execute`. Because the callback gas budget is a fixed constant while the data it must process (a cross-contract promise result) is attacker/target-controlled, a callback can run out of gas and panic, which reverts *all* state writes for that receipt — including the write that resets `has_in_flight_tx` to `false`. Once stuck `true`, `rlp_execute` unconditionally rejects every future call, permanently freezing the account's ability to move funds through the wallet contract, analogous to the Teller lender-group bug where a resettable/lockable guard is never cleared and permanently blocks a legitimate withdrawal.

### Finding Description
`WalletContract` maintains an explicit invariant documented in the struct itself: [1](#0-0) 

`rlp_execute` is the sole public entry point and immediately rejects the call if the flag is already set, with no recovery path: [2](#0-1) 

The flag is only cleared as the *first* statement of the `#[private]` callbacks (`address_check_callback`, `nep_141_storage_balance_callback`, `rlp_execute_callback`): [3](#0-2) 

These callbacks are invoked with a small, fixed static gas allocation (`RLP_EXECUTE_CALLBACK_GAS`, `ADDRESS_CHECK_CALLBACK_GAS`, `NEP_141_STORAGE_BALANCE_CALLBACK_GAS`), all constant regardless of the size of the data the callback must process: [4](#0-3) 

However, `rlp_execute_callback` and the intermediate callbacks copy the *promise result* returned by an arbitrary, attacker-controlled `target`/token contract into `ExecuteResponse.success_value` (e.g. an `ERC20Transfer` sends a promise to an arbitrary `token_id` account chosen by the RLP transaction, see `action_to_promise`/`inner_rlp_execute`): [5](#0-4) [6](#0-5) 

In NEAR, a WASM function-call receipt that fails (including `GasExceeded`) reverts **all** state changes made during that receipt's execution — it is not "partially applied." Because `self.has_in_flight_tx = false` is committed only if the whole callback function returns successfully, any deterministic way to make the callback exceed its fixed gas allocation (e.g. a malicious/compromised target/token contract returning a large payload as its promise result, which the callback must read via `env::promise_result`) causes the entire callback receipt to fail and the flag reset to be reverted, while `has_in_flight_tx` remains permanently `true` in the previously committed state. From that point on, `rlp_execute` immediately short-circuits on every future call (line 97-105), so the account can never execute another action (transfer, function call, add/delete key) through this contract again.

### Impact Explanation
Once `has_in_flight_tx` is stuck `true`, the eth-implicit account behind the wallet contract permanently loses the ability to sign/execute any NEAR action (including transferring out its own $NEAR or NEP-141 balances) via `rlp_execute`, since there is no method in the contract to force-reset the flag. This is a "permanently frozen funds" outcome directly reachable from a single (attacker-influenced) transaction targeting an attacker-controlled/malicious contract, matching the same root-cause pattern as the referenced report: a state guard meant to serialize operations is never properly reset because the code path that clears it can be prevented from completing, permanently denying the legitimate account holder access to their funds.

### Likelihood Explanation
This requires the wallet's RLP-signed transaction to target (or transfer through) a malicious/adversarial contract that returns an oversized promise result, or otherwise causes the fixed-gas callback to exceed its budget — a realistic scenario given users/relayers routinely interact with arbitrary, attacker-listed NEP-141 tokens or `FunctionCall` targets through this same mechanism (`ERC20Transfer`, generic `FunctionCall` action). No special privilege beyond being able to submit (or relay) a validly-RLP-signed transaction naming the malicious contract as target is required.

### Recommendation
- Do not gate the `has_in_flight_tx` reset behind a callback whose gas budget depends on attacker-controlled data size; size the callback's static gas based on the maximum possible returned payload (`max_length_returned_data`), or read/copy promise results in a gas-bounded, truncating way.
- Consider clearing `has_in_flight_tx` as the very first, cheap, unconditional operation isolated from any code that could OOG afterward, or add a self-call "unstick" recovery path (e.g., a timeout-based reset) so a stuck flag cannot permanently disable the account.

### Proof of Concept
1. Attacker deploys a NEP-141-like contract at some `token_id` whose `ft_transfer`/`storage_balance_of` (or any `FunctionCall` action) returns a maximal-size return value (bounded only by protocol's `max_length_returned_data`).
2. The wallet owner (or a relayer on their behalf) submits an RLP-encoded Ethereum transaction via `rlp_execute` with `target` set to the attacker's contract, entering `ERC20Transfer`/generic `FunctionCall` branch of `inner_rlp_execute`. [6](#0-5) 
3. `has_in_flight_tx` is set `true` (line 118/123/190/271) before the cross-contract promise executes.
4. The attacker's contract returns the oversized payload; `rlp_execute_callback` (or an intermediate callback) attempts to read/copy it via `env::promise_result` under the fixed `RLP_EXECUTE_CALLBACK_GAS` budget and runs out of gas, causing the whole callback receipt to fail.
5. Because the receipt failed, the `self.has_in_flight_tx = false` write at the top of the callback is never committed; the contract's persisted state retains `has_in_flight_tx = true`.
6. Every subsequent call to `rlp_execute` now immediately returns `"transaction already in progress, please try again later."` (lines 97-105) forever, permanently freezing the account's funds accessible only through this wallet contract.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L37-41)
```rust
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L275-317)
```rust
    #[private]
    pub fn rlp_execute_callback(
        &mut self,
        caller_deposit: Option<CallerDeposit>,
    ) -> ExecuteResponse {
        self.has_in_flight_tx = false;
        let n = env::promise_results_count();

        if n == 0 {
            // `rlp_execute_callback` is called directly in the case of an emulated self-transfer.
            return ExecuteResponse { success: true, success_value: None, error: None };
        } else if n > 1 {
            return ExecuteResponse {
                success: false,
                success_value: None,
                error: Some(format!(
                    "Invariant violation: this callback comes after a single promise. n={n}"
                )),
            };
        }

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
