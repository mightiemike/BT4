### Title
Unconditional relayer fee payout in `inner_rlp_execute` before the target action is confirmed to execute - ([File: runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs])

### Summary
The NEAR Wallet Contract's `inner_rlp_execute` dispatches the relayer's fee-refund promise via `env::promise_batch_create`/`promise_batch_action_transfer` *before* the corresponding user action promise (`action_to_promise`) is constructed. Because the enclosing NEAR function call only returns a `Result`/`Value` (it does not panic) on a later conversion error, an already-dispatched fee-transfer promise is not rolled back even when the actual intended action is never created. This is analogous to the Little Boy Plus root cause: a privileged/valuable side effect (minting to the pool / here, paying the relayer) is triggered from an internal hook that runs ahead of, and independently from, the validation of the primary operation it is supposed to be conditioned on.

### Finding Description
`inner_rlp_execute` (`runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs:330-472`) processes a parsed RLP Ethereum transaction and, for `EOABaseTokenTransfer`/`ERC20Transfer` emulation kinds with a non-zero `fee`, immediately creates and dispatches a transfer promise to the relayer:

```
if !fee.is_zero() && context.predecessor_account_id != context.current_account_id {
    let refund_promise = env::promise_batch_create(&context.predecessor_account_id);
    env::promise_batch_action_transfer(refund_promise, *fee);
}
``` [1](#0-0) 

This happens *before* the match on `transaction_kind` that later calls `action_to_promise(target, action)?` to build the promise for the user's actual intended action (transfer/function call/ERC-20 transfer): [2](#0-1) 

If `action_to_promise(...)` returns an `Err` (via the `?` operator), `inner_rlp_execute` returns `Err(e)`, and the caller `rlp_execute` converts this into `PromiseOrValue::Value(e.into())`: [3](#0-2) 

Because the function call itself completes with a `Value` (a normal successful execution outcome, not a panicking/failed execution), any promise batches already created earlier in the same function invocation via `promise_batch_create`/`promise_batch_action_transfer` are still emitted as outgoing receipts — NEAR only discards/rolls back promises produced by a function call whose execution *fails* (panics or errors out entirely), not when the call returns a value describing an application-level failure. The nonce increment for these cases also occurs before the fee dispatch (`*nonce = nonce.saturating_add(1)` at line 364), so state is committed on this path as well.

The result: the relayer is paid `fee` even though the wallet's intended action was never dispatched. This mirrors the LBP root cause pattern — a value-moving side effect fires from a path that is supposed to be gated on (but is not actually coupled to) the success of the primary operation.

### Impact Explanation
This allows unauthorized/unaccounted value movement out of an ETH-implicit account: the relayer collects a fee for a transaction whose real effect (transfer, function call, or ERC-20 transfer) never occurred. While the fee amount is bounded by what the user signed in their Ethereum transaction (`max_fee_per_gas * gas_limit`, capped by `VALUE_MAX`), a malicious relayer that can force `action_to_promise` to fail after the fee dispatch (e.g., by supplying a transaction/action combination that fails during promise construction, such as malformed function-call arguments that pass EVM/ABI decoding but fail Near action construction) can repeatedly extract the fee component while never completing the user's requested operation, directly reducing the user's account balance without delivering the corresponding service. This is a concrete unauthorized balance drain from the wallet account, consistent with Medium severity given the fee is bounded per-transaction but still represents value extracted without correct authorization/completion.

### Likelihood Explanation
Exploitability depends on the relayer being untrusted/malicious (the wallet contract's entire design assumes potentially "faulty" relayers, with a ban mechanism for relayer errors) and on being able to find or construct an action whose `parse_rlp_tx_to_action` succeeds with a non-zero fee EthEmulation kind, but whose later `action_to_promise` conversion fails. This is a narrower, action-construction-time failure window rather than an execution-time failure (which is already correctly refunded in `rlp_execute_callback`), so likelihood is Medium — it requires a specific relayer-side sequencing but doesn't require any signature forgery, key compromise, or privileged access; a single malicious/self-relaying account is a legitimate unprivileged caller of `rlp_execute`.

### Recommendation
Move the relayer fee-refund promise dispatch so it is only created after `action_to_promise` has successfully constructed the target action's promise (i.e., chain the fee-transfer as part of, or conditioned on, the same promise batch/receipt that carries out the user's action), or defer/guard the fee promise creation until immediately before `.then(ext.rlp_execute_callback(...))` is attached, ensuring the fee is only paid when the intended action promise is actually created and its eventual success/failure is handled consistently with the existing `caller_deposit` refund-on-failure logic in `rlp_execute_callback`.

### Proof of Concept
1. A malicious relayer account calls `rlp_execute` on a target ETH-implicit wallet account, submitting an RLP-encoded transaction that is parsed by `parse_rlp_tx_to_action` as an `EthEmulationKind::ERC20Transfer` (or `EOABaseTokenTransfer`) with `fee` > 0.
2. `inner_rlp_execute` reaches the fee branch, dispatches `promise_batch_create`/`promise_batch_action_transfer(fee)` to the relayer's account, and increments the nonce.
3. Immediately after, the `match transaction_kind` arm that eventually calls `action_to_promise(target, action)?` (for the non-`EOABaseTokenTransfer`/non-`ERC20Transfer`/non-`SelfBaseTokenTransfer` default arm, line 466-470) fails to build the promise (returns `Err`).
4. `inner_rlp_execute` returns `Err(e)`; `rlp_execute` converts this to `PromiseOrValue::Value(ExecuteResponse{success:false,...})` — a normal, non-panicking function outcome.
5. On-chain, the receipt for `rlp_execute` succeeds (from the runtime's perspective), so the fee-transfer promise created in step 2 is emitted as a real outgoing receipt and the relayer receives `fee`, while the user's intended action never executed and the `has_in_flight_tx` flag remains available for the next attempt.

Note: I was not able to construct or run an end-to-end test transaction within this session to definitively confirm which specific action/ABI-decoding failure modes trigger `action_to_promise` errors after the fee dispatch point (e.g., exact `Error::User`/`Error::Relayer` variants reachable only after the fee condition is met); this would need to be validated with an actual RLP-encoded transaction and the `action_to_promise`/`near_action` module (not shown in the excerpts I could retrieve) to confirm reachability in production configurations.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L89-127)
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
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L374-385)
```rust
            if let TransactionKind::EthEmulation(EthEmulationKind::EOABaseTokenTransfer {
                fee,
                ..
            })
            | TransactionKind::EthEmulation(EthEmulationKind::ERC20Transfer { fee, .. }) =
                &transaction_kind
            {
                if !fee.is_zero() && context.predecessor_account_id != context.current_account_id {
                    let refund_promise = env::promise_batch_create(&context.predecessor_account_id);
                    env::promise_batch_action_transfer(refund_promise, *fee);
                }
            }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L466-471)
```rust
        _ => {
            let ext =
                WalletContract::ext(current_account_id).with_static_gas(RLP_EXECUTE_CALLBACK_GAS);
            action_to_promise(target, action)?.then(ext.rlp_execute_callback(caller_deposit))
        }
    };
```
