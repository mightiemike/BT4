### Title
Attached NEAR deposit permanently stranded in the Wallet Contract on `rlp_execute` parse/validation failures - ([File: runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs])

### Summary
The `WalletContract::rlp_execute` method is `#[payable]`, meaning any caller (an external relayer, not necessarily one holding a function-call access key) can attach a NEAR deposit when invoking it. When the inner parsing/validation of the RLP-encoded Ethereum transaction fails with certain error variants, the function returns `PromiseOrValue::Value(e.into())` directly, without creating any promise that spends, transfers, or otherwise accounts for the attached deposit. This is the same bug class as the reported "not all calls forward `msg.value`" issue: a payable entry point that does not forward/refund the value it received on all code paths, permanently trapping the funds in the contract.

### Finding Description
`rlp_execute` is declared `#[payable]` and reads the attached deposit through `env::attached_deposit()` inside `inner_rlp_execute`: [1](#0-0) 

Inside `inner_rlp_execute`, the context (including the attached deposit) is built, and `caller_deposit` is computed from it — but this happens *before* the RLP transaction is parsed: [2](#0-1) 

If `internal::parse_rlp_tx_to_action` fails, the function returns `Err(err)` directly — for `Error::User(_)` variants, `Error::AccountId` variants, and `Error::Relayer` variants — without ever using `caller_deposit` to build a refund `Promise`: [3](#0-2) 

Back in `rlp_execute`, these `Err` results are handled as follows: [4](#0-3) 

Only the specific case `Err(Error::Relayer(_)) if env::signer_account_id() == current_account_id` produces a `Promise` (`create_ban_relayer_promise`) — and even that promise only calls `delete_key` + `ban_relayer` with a hard-coded `NearToken::from_yoctonear(0)` deposit, never forwarding the caller's attached deposit: [5](#0-4) 

Every other `Err(e)` variant (all `Error::User(_)` parse/validation errors, `Error::AccountId` errors, and `Error::Relayer` errors when the signer is not the wallet's own access key) falls into `PromiseOrValue::Value(e.into())`. This is a synchronous return with no promise batch created — the method call itself succeeds at the protocol level (it returns an `ExecuteResponse{success:false,...}` value), so NEAR's automatic deposit-refund mechanism, which only triggers for *failed* receipts (see `refund_unspent_gas_and_deposits`), never fires. Compare with `refund_unspent_gas_and_deposits`, which only issues a `Receipt::new_balance_refund` when `result.result.is_err()` at the receipt level: [6](#0-5) 

Because `rlp_execute` returns `Ok`-like success at the receipt level (a value, not an action error), no such automatic protocol refund happens, and the deposit the caller attached is absorbed into the Wallet Contract's balance with no code path to reclaim it. This directly mirrors the reported Solidity pattern where `msg.value` is not forwarded on certain code paths of a payable function and becomes permanently stuck.

By contrast, the contract *does* correctly handle refunds for later failure stages — e.g. `rlp_execute_callback` explicitly creates a refund promise back to `caller_deposit.account_id` when the downstream cross-contract call fails: [7](#0-6) 

This shows the developers were aware of the need to refund attached deposits, but the early parse/validation error paths in `inner_rlp_execute` were missed.

### Impact Explanation
Any unprivileged NEAR account can call `rlp_execute` on any deployed Wallet Contract (an ETH-implicit account contract used for EVM-transaction emulation) with an intentionally malformed or invalid RLP-encoded transaction (triggering `Error::User`/`Error::AccountId`/most `Error::Relayer` variants) while attaching a NEAR deposit. That deposit is never returned to the caller and is not spent on any action — it silently becomes part of the Wallet Contract account's balance. This is a permanent, unrecoverable loss of funds for the caller (a relayer or any third party who mistakenly or maliciously interacts with this entry point with attached NEAR), matching "permanently frozen funds" in the acceptance criteria. It does not require any privileged role — it is reachable by any account issuing a plain `FunctionCall` transaction to `rlp_execute`.

### Likelihood Explanation
This is trivially reachable: any account can send a NEAR transaction calling `rlp_execute` on any deployed Wallet Contract with (a) a deposit attached and (b) either malformed/unparsable transaction bytes or an action that produces a `UserError`/`AccountIdError` during `parse_rlp_tx_to_action`. Because `rlp_execute` is payable and does not require the deposit to be non-zero, an inattentive relayer/UI that attaches a NEAR deposit alongside an invalid or user-error-triggering RLP payload will lose funds with high likelihood in normal operational error conditions (not just adversarial ones).

### Recommendation
In `inner_rlp_execute` / `rlp_execute`, ensure the attached deposit is always accounted for on every return path:
- When `parse_rlp_tx_to_action` fails (all `Error::User`, `Error::AccountId`, and `Error::Relayer` variants that do not lead to a promise), and the predecessor is not the current account (i.e., a real caller attached a deposit), create a refund `Promise::new(predecessor_account_id).transfer(attached_deposit)` before returning the error value, mirroring the pattern already used in `rlp_execute_callback`.
- Alternatively, reject calls to `rlp_execute` with a non-zero attached deposit upfront (fail fast) unless the deposit can be tied to a promise that will resolve it, avoiding any path where a deposit is silently absorbed by the contract without an explicit forwarding/refund action.

### Proof of Concept
1. Deploy/derive an ETH-implicit account with the Wallet Contract's global code (as in `test_wallet_contract_interaction`, `integration-tests/src/tests/features/wallet_contract.rs`).
2. From any funded NEAR account (the "attacker"/careless relayer), submit a `FunctionCall` transaction targeting the wallet's `rlp_execute` method:
   - `target`: any valid-looking account id
   - `tx_bytes_b64`: a base64 string that fails RLP parsing or produces a `UserError`/`AccountIdError` inside `internal::parse_rlp_tx_to_action` (e.g., malformed RLP bytes, or an unsupported action like adding a full-access key which triggers `UnsupportedAction::AddFullAccessKey` inside `action_to_promise`/parsing).
   - attach a non-zero deposit, e.g. `NearToken::from_near(1)`.
3. Observe: the call returns `ExecuteResponse{success:false, error: Some(...)}` (a normal successful receipt at the protocol level, not an `ActionError`), so the runtime's automatic `refund_unspent_gas_and_deposits` deposit-refund path is never triggered (it only fires when `result.result.is_err()` at the action-receipt level).
4. Check the caller's balance before/after: the attached deposit is deducted from the caller and never returned — verify the Wallet Contract account's balance increased by the attached deposit amount with no corresponding transfer receipt back to the caller (unlike the pattern demonstrated for the later-stage failure in `test_caller_refunds`, `runtime/near-wallet-contract/implementation/wallet-contract/src/tests/sanity.rs:170-229`, which only covers failures *after* successful parsing).

**Note on limitations:** I was not able to fully inspect `runtime/near-wallet-contract/implementation/wallet-contract/src/error.rs` (only grep match counts, not contents, were returned) to enumerate every `Error::User`/`Error::AccountId`/`Error::Relayer` variant and confirm each is reachable purely from malformed/invalid input rather than internal-only conditions. If a full listing of `Error` variants is needed to fully enumerate all reachable no-refund branches, a Devin session with full file access should be used to confirm the exact enumeration in `error.rs` and `internal::parse_rlp_tx_to_action`.

### Citations

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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L336-346)
```rust
) -> Result<Promise, Error> {
    if *nonce == u64::MAX {
        return Err(Error::AccountNonceExhausted);
    }
    let context = ExecutionContext::new(
        current_account_id.clone(),
        predecessor_account_id,
        env::attached_deposit(),
    )?;
    let caller_deposit = CallerDeposit::new(&context);

```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L389-409)
```rust
        Err(err @ Error::User(_)) => {
            // Increment nonce on all user errors to prevent replay.
            *nonce = nonce.saturating_add(1);
            return Err(err);
        }
        Err(err) => {
            // Do not increment nonce on Relayer or AccountId errors.
            // The latter error is an issue in the deployment (so the nonce is meaningless).
            // The former arises from the relayer itself doing something wrong and thus the
            // user's transaction could still be valid and potentially submitted properly by
            // another relayer. To allow this we do not increment the nonce.
            //
            // Note: if a relayer is using an access key for this wallet then that key will
            // still be revoked (in the main logic of `rlp_execute`). This fact together with
            // the condition that there only be one in-flight transaction at a time implies
            // that a relayer cannot maliciously burn a large portion of the user's tokens.
            // If the relayer is not using an access key then they are spending their own
            // resources on the gas and therefore we do not care if the relayer submits
            // the same faulty transaction multiple times.
            return Err(err);
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

**File:** runtime/runtime/src/lib.rs (L1400-1407)
```rust
        let gas_balance_refund = safe_add_balance(unused_gas_balance_refund, burned_gas_refund)?;

        if deposit_refund > Balance::ZERO {
            result.new_receipts.push(Receipt::new_balance_refund(
                receipt.balance_refund_receiver(),
                deposit_refund,
            ));
        }
```
