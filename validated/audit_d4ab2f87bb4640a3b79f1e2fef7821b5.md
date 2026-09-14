I have enough information to write up this finding now.

### Title
Fixed-gas `rlp_execute_callback` can be forced out-of-gas by an oversized cross-contract return value, permanently bricking an ETH-implicit Wallet Contract account - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
The NEAR Wallet Contract (NEP-518) that backs every ETH-implicit account enforces a single-in-flight-transaction invariant via the `has_in_flight_tx` boolean, which is only reset back to `false` inside a `#[private]` promise callback. For the generic "call another contract" path, the callback (`rlp_execute_callback`) is scheduled with a fixed static gas budget (`RLP_EXECUTE_CALLBACK_GAS = 5 Tgas`) that does **not** scale with the size of the value the called contract can return. A callee contract fully controlled by whoever the wallet owner's Ethereum transaction targets can return an oversized value, causing the callback itself to run out of gas. Because a failed action receipt rolls back all of its state changes, the callback's write of `has_in_flight_tx = false` never lands, and the account is permanently locked out of `rlp_execute`, which short-circuits on `has_in_flight_tx == true` without creating any new promise.

### Finding Description
`WalletContract::rlp_execute` checks `has_in_flight_tx` and, if false, builds a promise chain via `inner_rlp_execute`, setting `has_in_flight_tx = true` before returning the promise. [1](#0-0) 

For the generic/default transaction kind (an arbitrary `FunctionCall` targeting any Near contract, decoded straight from the user's signed Ethereum transaction), the promise is built with a **fixed** static gas budget independent of what the target might return: [2](#0-1) 

`RLP_EXECUTE_CALLBACK_GAS` is a small constant (5 Tgas): [3](#0-2) 

The callback that is supposed to clear the lock reads the promise result, which costs gas proportional to the size of the returned payload: [4](#0-3) 

If the called contract returns a value large enough that decoding it inside `rlp_execute_callback` exceeds the fixed 5 Tgas allotted to that callback, the callback itself fails with an out-of-gas `FunctionCallError`. Per the runtime's action-receipt execution model, "a receipt whose result is `Err` triggers `state_update.rollback()`, so no state changes persist except the outcome/gas accounting," which means the write `self.has_in_flight_tx = false;` performed at the top of the callback is discarded along with everything else the callback attempted: [5](#0-4) 

Once `has_in_flight_tx` is stuck at `true`, every subsequent call to `rlp_execute` (the only entry point into an ETH-implicit account, since such accounts cannot receive `AddKey` with full access, cannot be deleted, and cannot have `CreateAccount` applied to them) returns immediately with an error and never issues a new promise, so the flag can never be cleared again: [6](#0-5) [7](#0-6) 

This is analogous to the reported reNFT `Reclaimer` issue: a step that is supposed to "unlock" state for a party is coupled atomically to an externally-influenced call whose outcome (revert / gas exhaustion) is controlled by the callee, and failure of that step causes the lock to persist rather than being isolated into its own always-succeeding step.

### Impact Explanation
Any account that is ETH-implicit (i.e., every user of the Wallet Contract / Ethereum-compatible tooling on NEAR) can have its account permanently frozen if the target of one of its `rlp_execute` calls returns an oversized value that exhausts the fixed 5 Tgas callback budget. Because ETH-implicit accounts have no other way to regain access (no full access key can ever be added, the account cannot be deleted, `rlp_execute` is the sole entry point), all NEAR balance and any other assets held by the account become permanently inaccessible — this satisfies "permanently frozen funds." The trigger requires only a single transaction interacting with a contract whose return-value size is attacker/target-controlled (e.g., a malicious or compromised dApp/contract that the wallet owner calls), matching the "reachable from a single submitted transaction, contract call" criterion.

### Likelihood Explanation
The user (or a relayer forwarding the user's already-signed Ethereum transaction) must target a contract whose `FunctionCall` return-value size is controlled by that contract (i.e., interacting with any untrusted/attacker-controlled contract as part of normal Wallet Contract usage — a realistic scenario since the Wallet Contract's whole purpose is to let ETH-tooling users call arbitrary NEAR contracts). No special privileges, validator collusion, or network-layer behavior is required; only crafting a contract that returns a value whose deserialization/read cost exceeds ~5 Tgas.

### Recommendation
Scale the static gas attached to `rlp_execute_callback` (in the default `action_to_promise(target, action)?.then(...)` branch) with the size of gas already granted to the target call (similar to how the NEP-141/ERC-20 emulation branch adds `action.gas()` to its callback budget), or bound/truncate the amount of returned-value data the callback is allowed to read before decoding it, so the lock-clearing logic cannot be starved of gas by an oversized return value. Alternatively, decouple resetting `has_in_flight_tx` from the outcome of interpreting the promise result, e.g. by resetting the flag in a step that does not depend on reading/processing attacker-sized data, or by giving the callback a minimum fixed gas reserve independent of any data-dependent processing.

### Proof of Concept
1. Deploy a NEAR contract `Evil` with a method `boom()` that returns a very large `Vec<u8>` (sized so that reading/copying it inside a 5 Tgas budget is infeasible), e.g., several hundred KB to a few MB, staying within `max_length_returned_data`.
2. Create/fund an ETH-implicit account and have its owner sign an Ethereum `FunctionCall`-style transaction (per `FUNCTION_CALL_SELECTOR` in `parse_tx_data`) with `receiver_id = Evil`, `method_name = "boom"`, and sufficient `gas` for `Evil::boom()` to succeed but relying on the wallet's fixed `RLP_EXECUTE_CALLBACK_GAS` for the callback.
3. Relay this via `rlp_execute(target = Evil, tx_bytes_b64)`. `Evil::boom()` succeeds and returns the oversized value; the chained `rlp_execute_callback` attempts to read `env::promise_result(0)` and fails with an out-of-gas `FunctionCallError`, causing its receipt to roll back (including the `has_in_flight_tx = false` write).
4. Call `rlp_execute` again with any subsequent valid nonce/action: it immediately returns `ExecuteResponse { success: false, error: Some("Error: transaction already in progress, please try again later.") }` and issues no promise, permanently locking the account. [8](#0-7)

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L37-37)
```rust
const RLP_EXECUTE_CALLBACK_GAS: Gas = Gas::from_tgas(5);
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L43-55)
```rust
#[near_bindgen]
#[derive(Default, BorshDeserialize, BorshSerialize)]
#[borsh(crate = "near_sdk::borsh")]
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L94-128)
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L466-470)
```rust
        _ => {
            let ext =
                WalletContract::ext(current_account_id).with_static_gas(RLP_EXECUTE_CALLBACK_GAS);
            action_to_promise(target, action)?.then(ext.rlp_execute_callback(caller_deposit))
        }
```

**File:** protocol-model/spec/runtime-execution.md (L149-149)
```markdown
- **Failed receipt atomicity**: a receipt whose result is `Err` triggers `state_update.rollback()`, so no state changes persist except the outcome/gas accounting (`runtime/runtime/src/lib.rs:967`). `set_error` additionally clears queued receipts, proposals, and burnt/subsidized amounts (`runtime/runtime/src/lib.rs:487`).
```

**File:** docs/DataStructures/Account.md (L121-122)
```markdown
An ETH-implicit account can only be used by calling the methods of the [Wallet Contract](#wallet-contract). It cannot be deleted, nor can a full access key be added.
The primary purpose of ETH-implicit accounts is to enable seamless integration of existing Ethereum tools (such as wallets) with the NEAR blockchain.
```
