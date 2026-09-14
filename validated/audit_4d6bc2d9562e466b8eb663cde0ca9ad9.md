Based on the investigation, the closest reachable analog in `nearcore` to the EigenLayer "third-party transfer forbidden causes withdrawal to permanently revert" bug class is a stuck-state/liveness bug in the **Wallet Contract** (`near-wallet-contract`), the account contract that gates all activity of ETH-implicit accounts, similar in spirit to the access-gated withdrawal path in EigenLayer.

### Title
Insufficient reserved gas for `rlp_execute_callback` permanently freezes ETH-implicit wallet funds - ([File: runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs])

### Summary
The `WalletContract` that governs every ETH-implicit account enforces a single in-flight transaction invariant via the `has_in_flight_tx` boolean, which is only cleared inside callback methods (`rlp_execute_callback`, `address_check_callback`, `nep_141_storage_balance_callback`, `ban_relayer`). If the callback receipt fails before completing (e.g. running out of its statically reserved gas), the entire receipt state change — including the reset of `has_in_flight_tx` — is rolled back, permanently locking the wallet and preventing the account owner from ever transacting again.

### Finding Description
`rlp_execute` refuses to process any request while `has_in_flight_tx` is `true`, and only the paired callback clears it: [1](#0-0) 

The default action path chains the target action to a **fixed** `RLP_EXECUTE_CALLBACK_GAS` (5 Tgas) callback, independent of what the action itself does inside the callback (e.g., issuing a refund `Promise` when the action fails): [2](#0-1) 

The callback itself resets the flag as its very first statement, then may perform additional gas-consuming work (deserializing the promise result, and — on failure — creating a refund `Promise` to `caller_deposit.account_id`): [3](#0-2) 

Because NEAR function-call execution is atomic per receipt (any panic, including `GasExceeded`, discards *all* state writes made during that call — not just the ones after the panic point), a callback that runs out of its narrowly reserved gas budget while performing the failure/refund branch will revert the `has_in_flight_tx = false` write along with everything else. The contract's own invariant comment even acknowledges this precondition must always hold: [4](#0-3) 

An attacker who can observe or is handed a validly-signed RLP-encoded Ethereum transaction from the account owner (a normal relayer role — the account owner necessarily hands raw signed bytes to *some* relayer to submit) can resubmit that same payload as the NEAR transaction's `rlp_execute` call while intentionally attaching just enough gas for the target action to fail during execution and enter the failure/refund branch of `rlp_execute_callback`, but not enough gas for that branch (deserialize failed result + create and dispatch the refund promise) to complete within the fixed 5 Tgas reservation. The callback then panics with `GasExceeded`, the receipt's state changes (including the `has_in_flight_tx` reset) are discarded, and `has_in_flight_tx` remains `true` forever.

### Impact Explanation
Once `has_in_flight_tx` is stuck `true`, every future call to `rlp_execute` on that account immediately short-circuits with "transaction already in progress" and never dispatches the user's intended action. Since ETH-implicit accounts cannot have a full access key added and cannot be deleted, this contract is the *only* way to move funds out of the account: [5](#0-4) 
This means all $NEAR and NEP-141 token balances held by the account become permanently unreachable — a direct analog to EigenLayer's forbidden-third-party-transfer scenario disabling withdrawals, except here the funds are frozen for the account's *legitimate owner* with no recovery path.

### Likelihood Explanation
The only capability required is the ability to submit any NEAR transaction calling `rlp_execute` with an attacker-chosen (low) gas value while relaying an already-signed valid Ethereum transaction from the victim — something any relayer (honest-looking but grief-capable), or anyone who has intercepted the raw signed bytes en route to a relayer, can do without needing to forge a signature or access the target's private key. No validator, sync, or privileged role is required — a single crafted transaction from an ordinary account suffices.

### Recommendation
Ensure the gas reserved for `rlp_execute_callback` (and the other callback variants) accounts for the worst-case gas cost of every branch inside the callback body, including the refund-promise-creation path, and/or restructure the contract so that the "in-flight" flag reset does not depend on the same failure-prone execution path that performs the refund (for example, always resetting the flag via a lower-cost dedicated final step, or performing the refund in a subsequent chained promise so the initial callback body — which clears the flag — is guaranteed to complete with a small fixed gas budget).

### Proof of Concept
1. Deploy/derive an ETH-implicit account with the Wallet Contract, funded with $NEAR.
2. Owner signs a valid Ethereum-encoded transaction (e.g., a `FunctionCall` action to a contract expected to fail, or any action whose failure triggers the `caller_deposit` refund branch), and hands the raw bytes to a relayer as normal.
3. Attacker/relayer submits `rlp_execute(target, tx_bytes_b64)` as the NEAR transaction, but with prepaid gas set just high enough for `inner_rlp_execute` to succeed and dispatch the action promise, yet leaving only just above/below the fixed `RLP_EXECUTE_CALLBACK_GAS` for the callback such that the callback's failure/refund branch (`env::promise_batch_create` + `env::promise_batch_action_transfer`) exceeds the reserved 5 Tgas and panics with `GasExceeded`.
4. Observe that the callback receipt fails, and `has_in_flight_tx` in the account's persisted state remains `true` (unchanged from before the call), confirmed via subsequent `rlp_execute` calls now always failing with "transaction already in progress".
5. No further `rlp_execute` call — from the true owner or anyone else — can ever succeed again, permanently freezing all funds held by that ETH-implicit account.

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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L89-128)
```rust
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L276-317)
```rust
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L459-471)
```rust
        TransactionKind::EthEmulation(EthEmulationKind::SelfBaseTokenTransfer) => {
            // Base token transfers to self are no-ops on Near, so we do not need to
            // schedule an additional call. We can simply go straight to `rlp_execute_callback`.
            let ext: WalletContractExt =
                WalletContract::ext(current_account_id).with_static_gas(RLP_EXECUTE_CALLBACK_GAS);
            ext.rlp_execute_callback(caller_deposit)
        }
        _ => {
            let ext =
                WalletContract::ext(current_account_id).with_static_gas(RLP_EXECUTE_CALLBACK_GAS);
            action_to_promise(target, action)?.then(ext.rlp_execute_callback(caller_deposit))
        }
    };
```

**File:** docs/DataStructures/Account.md (L119-122)
```markdown
Once a NEAR-implicit account is created it acts as a regular account until it's deleted.

An ETH-implicit account can only be used by calling the methods of the [Wallet Contract](#wallet-contract). It cannot be deleted, nor can a full access key be added.
The primary purpose of ETH-implicit accounts is to enable seamless integration of existing Ethereum tools (such as wallets) with the NEAR blockchain.
```
