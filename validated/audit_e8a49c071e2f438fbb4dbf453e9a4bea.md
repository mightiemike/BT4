### Title
Wallet Contract's `rlp_execute` accepts arbitrary attached NEAR deposit without validating or fully accounting for it, allowing funds to become permanently stuck when the wrapped action succeeds - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
`WalletContract::rlp_execute` is marked `#[payable]`, so any unprivileged transaction signer/relayer calling it can attach an arbitrary NEAR deposit. That deposit is only tracked for potential refund via `CallerDeposit` and is only ever refunded when the resulting cross-contract promise **fails**. For every other outcome the deposit's value is silently absorbed into the wallet's account balance instead of being validated (e.g. required to be `0` unless consumed by the action) or returned, and the contract exposes no generic withdrawal path for a non-owner depositor to reclaim it.

### Finding Description
`rlp_execute` (`runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs:88-128`) is `#[payable]` and immediately builds an `ExecutionContext` from `env::attached_deposit()` (`lib.rs:340-344`), then computes an optional `CallerDeposit` via `CallerDeposit::new(&context)` (`types.rs:180-192`).

`CallerDeposit::new` only records the deposit when the caller is external (`predecessor_account_id != current_account_id`); it stores the attached amount for later refund purposes: [1](#0-0) 

This `caller_deposit` is threaded through every callback (`address_check_callback`, `nep_141_storage_balance_callback`, `rlp_execute_callback`), but it is used **only** on the `PromiseResult::Failed` branch of `rlp_execute_callback`: [2](#0-1) 

Crucially, the attached NEAR deposit is completely decoupled from the value of the Near action being emulated. The action's own value comes from the parsed Ethereum transaction's `yocto_near` field or `tx.value` (`internal.rs:159-165`), not from `env::attached_deposit()`. There is no check anywhere in `rlp_execute`/`inner_rlp_execute`/`internal.rs` that the attached deposit is `0`, or that it matches/exceeds the amount actually needed by the action. For the default (`NearNativeAction`, `AddKey`, `DeleteKey`, `SelfBaseTokenTransfer`) branches in `inner_rlp_execute` (`lib.rs:412-471`), the attached deposit is not forwarded into the constructed promise at all — it is simply left on the wallet's own account balance if the inner action succeeds.

Because the deposit was credited to the contract's account balance as soon as the `FunctionCall` receipt executed (standard NEAR semantics for attached deposits), and `WalletContract` exposes no generic "withdraw excess balance" method, any NEAR value an external caller (a relayer, or any RPC-visible transaction sender interacting with this contract) attaches beyond what is strictly consumed by the wrapped action becomes permanently non-refundable to that caller once the wrapped action succeeds — mirroring the reported Solidity pattern of `allocate()` accepting `msg.value` without validating/accounting it and lacking any withdrawal method.

### Impact Explanation
Any unprivileged account that calls `rlp_execute` with an attached deposit (accidental over-attachment, a naive relayer implementation, or a malformed client) permanently loses that value to the wallet-contract's account once the wrapped promise succeeds, with no protocol-level mechanism to reclaim it. This is a direct, transaction-triggered, unauthorized-appearing loss of NEAR (frozen funds) for the caller, even though no explicit validation error is raised by the contract. This matches the "permanently frozen funds" acceptance criterion.

### Likelihood Explanation
Reachable directly and trivially by any transaction signer/RPC caller submitting a `FunctionCall` to `rlp_execute` with a non-zero attached deposit while the RLP-encoded action does not require (or requires less than) that deposit. No special privileges, timing, or validator behavior are required — it is a straightforward misuse/no-validation path exposed to any caller.

### Recommendation
In `inner_rlp_execute` (`runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs:330-410`), explicitly validate that `env::attached_deposit()` is consumed entirely by the resulting action (or is `0` when the action doesn't require a deposit), rejecting/refunding any attached deposit that does not correspond to a value the contract will actually use, similar to the suggested Solidity fix of requiring `msg.value == 0` unless deposits are explicitly expected. Alternatively, always refund any unused/excess portion of `attached_deposit` back to the caller regardless of whether the wrapped promise succeeds or fails, not only on failure as `rlp_execute_callback` currently does (`lib.rs:296-316`).

### Proof of Concept
1. A relayer (unprivileged caller, `predecessor_account_id != current_account_id`) submits a transaction calling `rlp_execute(target, tx_bytes_b64)` on a deployed `WalletContract`, attaching e.g. `1 NEAR` as `env::attached_deposit()`, where the RLP-encoded Ethereum transaction encodes an `AddKey` or `DeleteKey` action (`ParsableTransactionKind::SelfNearNativeAction`, `internal.rs:272-304`) that requires no NEAR value at all.
2. `CallerDeposit::new` records the 1 NEAR against the relayer (`types.rs:181-191`) since predecessor != current.
3. `inner_rlp_execute`'s default branch (`lib.rs:466-470`) builds `action_to_promise(target, action)` for `AddKey`/`DeleteKey`, which does not use or forward the attached deposit at all, then chains to `rlp_execute_callback(caller_deposit)`.
4. The `AddKey`/`DeleteKey` promise succeeds → `rlp_execute_callback` hits `PromiseResult::Successful` (`lib.rs:313-315`) and simply returns success; `caller_deposit` is discarded, no refund is issued.
5. The 1 NEAR attached by the relayer remains permanently on the wallet contract's account balance; the relayer has no method exposed by `WalletContract` to reclaim it.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/types.rs (L180-191)
```rust
impl CallerDeposit {
    pub fn new(context: &ExecutionContext) -> Option<Self> {
        // Only track for external (non-self) callers
        if context.current_account_id == context.predecessor_account_id {
            return None;
        }

        NonZeroU128::new(context.attached_deposit.as_yoctonear()).map(|yocto_near| Self {
            account_id: context.predecessor_account_id.clone(),
            yocto_near,
        })
    }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L296-316)
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
            PromiseResult::Successful(value) => {
                ExecuteResponse { success: true, success_value: Some(value), error: None }
            }
        }
```
