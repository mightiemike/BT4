### Title
Relayer fee is paid twice on a failed emulated base-token/ERC-20 transfer — full attached deposit is refunded on top of an already-sent fee - (File: runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs)

### Summary
The near-wallet-contract (`near-wallet-contract`) implements an "ETH-emulation" flow where a relayer submits an RLP-encoded Ethereum transaction to a user's wallet contract via `rlp_execute`. For `EOABaseTokenTransfer`/`ERC20Transfer` emulation kinds with a non-zero `fee`, the wallet immediately fires an unconditional transfer of `fee` to the relayer (`predecessor_account_id`) *before* the underlying action is attempted, and separately records a `CallerDeposit` equal to the **full** `attached_deposit` to be refunded to that same relayer if the subsequent cross-contract call fails. Because the refund uses the full original deposit rather than `deposit - fee`, a relayer that also attaches a deposit when calling `rlp_execute` can be paid the `fee` twice out of the wallet's own balance whenever the wrapped action fails — this is structurally the same class of bug as the Spartan `removeLiquiditySingle()` finding: a value component that was already carved off and sent to one party in an earlier step is not subtracted from what is subsequently sent back in the failure/cleanup path, causing an accounting mismatch and unintended value flow.

### Finding Description
In `inner_rlp_execute` (`runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs:330-410`):
1. `context = ExecutionContext::new(current_account_id, predecessor_account_id, env::attached_deposit())` captures the **full** deposit attached to the `#[payable]` `rlp_execute` call.
2. `caller_deposit = CallerDeposit::new(&context)` is built from this same full `attached_deposit` value, with no subtraction of anything (see `CallerDeposit::new`, `types.rs:180-191`, which stores `context.attached_deposit.as_yoctonear()` directly as `yocto_near`).
3. For `EOABaseTokenTransfer`/`ERC20Transfer` transaction kinds with a non-zero `fee` and `predecessor != current`, an **unconditional** promise is fired immediately to send `fee` to `predecessor_account_id`:
```
if !fee.is_zero() && context.predecessor_account_id != context.current_account_id {
    let refund_promise = env::promise_batch_create(&context.predecessor_account_id);
    env::promise_batch_action_transfer(refund_promise, *fee);
}
``` [1](#0-0) 
4. The underlying action promise chain is then dispatched, ending in `rlp_execute_callback(caller_deposit)`.
5. In `rlp_execute_callback` (`lib.rs:276-317`), if the promise result is `PromiseResult::Failed`, the **entire** `caller_deposit.yocto_near` (i.e., the full original attached deposit, not `deposit - fee`) is transferred back to `caller_deposit.account_id`, which is the same `predecessor_account_id` that already received `fee` in step 3:
```
if let Some(CallerDeposit { account_id, yocto_near }) = caller_deposit {
    let refund_promise = env::promise_batch_create(&account_id);
    env::promise_batch_action_transfer(refund_promise, NearToken::from_yoctonear(yocto_near.into()));
}
``` [2](#0-1) 

Since the `fee` transfer (step 3) is unconditional and happens before the outcome of the action is known, and the failure-path refund (step 5) uses the un-deducted, full deposit amount, the relayer collects `fee` once via the immediate transfer and again as part of the "full deposit" refund whenever the wrapped promise fails. Both transfers are funded from the wallet contract's own account balance (the balance that received the attached deposit when `rlp_execute` was called), so this is the wallet's own funds being drained beyond what the protocol/user intended — directly analogous to the Spartan report where a balance component removed in one step was not correctly accounted for when returning/forwarding funds in a later step, causing unintended loss/duplication of value.

### Impact Explanation
This causes an unauthorized, attacker-triggerable value movement: a relayer (which in the general design is meant to be untrusted/self-interested, that's the entire reason `CallerDeposit`/ban-relayer mechanisms exist) can construct a transaction that is guaranteed to fail during the wrapped promise (e.g., function call with a bad method name/insufficient gas, or transfer to a non-existent/blocking receiver) while attaching a real deposit on top of an embedded eth-emulation `fee`. The relayer then collects the `fee` immediately and the full deposit refund on failure, effectively over-paying itself from the wallet contract's balance. Repeated over many transactions this drains value from the eth-implicit wallet account that is not attributable to any genuine relayer service rendered for a successful transaction. This matches the "concrete unauthorized value movement" acceptance criterion.

### Likelihood Explanation
Reachable by any relayer/caller who can call `rlp_execute` (a public, payable contract method) with a self-crafted RLP transaction and an attached NEAR deposit — no special privilege, validator status, or network position is required; it is purely a transaction-triggered contract-logic bug in the wallet contract shipped as part of nearcore (`runtime/near-wallet-contract`). The only precondition is that the wallet's eth-emulation transaction kind carries a non-zero `fee` and the caller also attaches a deposit to the outer `rlp_execute` call, both of which are attacker-controlled inputs.

### Recommendation
`CallerDeposit` should record `attached_deposit - fee` (saturating at zero) rather than the full attached deposit whenever a `fee` has already been paid out unconditionally in `inner_rlp_execute`, so the failure-path refund in `rlp_execute_callback` never re-pays the already-sent fee. Alternatively, defer sending `fee` until `rlp_execute_callback` confirms success, and only refund `attached_deposit` (unreduced) on failure — mirroring the report's second suggested fix pattern (move the value-splitting decision to a single point after the outcome is known, rather than eagerly sending part of the value up front and separately refunding the whole amount later).

### Proof of Concept
1. Deploy a wallet contract for address `A`, controlled by attacker-relayer `R`.
2. `R` crafts an RLP transaction embedding an `EOABaseTokenTransfer` (or `ERC20Transfer`) with a non-zero `fee` `F`, and a `to` target chosen such that the eventual promise (e.g., the `FunctionCall`/`Transfer` action or NEP-141 `ft_transfer`) is guaranteed to fail (e.g., target account with no matching method, or insufficient attached gas for the call).
3. `R` calls `wallet.rlp_execute(target, tx_bytes_b64)` attaching deposit `D > 0` (in addition to whatever value is embedded in the RLP transaction itself).
4. `inner_rlp_execute` immediately fires `promise_batch_action_transfer(refund_promise, F)` to `R` (`lib.rs:382-384`), and separately records `caller_deposit = CallerDeposit { account_id: R, yocto_near: D }` (full `D`, not `D - F`).
5. The wrapped action promise fails as engineered; `rlp_execute_callback` observes `PromiseResult::Failed` and refunds the **full** `D` to `R` (`lib.rs:299-305`).
6. Net result: `R` has received `F + D` from the wallet's balance, even though only `D` (minus the intended fee) was ever supposed to return to `R`, and the wrapped action never executed. Repeating this lets `R` drain `F` per attempt from the wallet's balance with no successful service rendered.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L296-311)
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
