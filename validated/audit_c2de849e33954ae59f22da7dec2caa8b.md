### Title
Payable `rlp_execute` entry point strands attached NEAR deposit on early error returns - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
`WalletContract::rlp_execute` is marked `#[payable]`, so a caller (owner-relayer, external relayer, or arbitrary RPC caller) can attach a NEAR deposit to the call. [1](#0-0)  That deposit is credited to the wallet contract's account balance by the runtime before the function body runs, per the standard Economics API semantics. [2](#0-1)  However, several error paths inside `inner_rlp_execute` return an `Err(e)` synchronously without creating any promise, meaning the attached deposit is never spent, transferred, or refunded — it is permanently absorbed into the contract's balance. This mirrors the reported Solidity pattern where a `payable` function accepts `msg.value` but never interacts with it, leaving funds stuck.

### Finding Description
`rlp_execute` computes a `CallerDeposit` from the attached deposit purely for later refund bookkeeping in case a *downstream promise* fails: [3](#0-2) [4](#0-3) 

But `CallerDeposit::new` only tracks a refund when `predecessor_account_id != current_account_id` (i.e., only for genuinely external non-owner callers), and even then the refund is only executed later inside `rlp_execute_callback` when a *promise* actually fails: [5](#0-4) 

The problem is that many error conditions in `inner_rlp_execute` never reach that callback at all — they return immediately from `inner_rlp_execute`/`rlp_execute` with `Err(e)` before any promise is created:
- `Error::User(_)` errors (malformed ABI data, unsupported action, excess yocto-near, unknown selector, etc.) increment the nonce and return `Err(err)` directly. [6](#0-5) 
- `Error::AccountId(_)`, `Error::AccountNonceExhausted`, and `Error::Relayer(_)` (when the caller is not itself, i.e. `env::signer_account_id() != current_account_id`) also return `Err(e)` directly. [7](#0-6) 
- Back in `rlp_execute`, any such `Err(e)` is converted straight into `PromiseOrValue::Value(e.into())` with no promise scheduled: [8](#0-7) 

In all of these branches, whatever NEAR was attached to the `rlp_execute` call via `#[payable]` was already deposited into the contract's account balance by the runtime before the function ran, and the function body performs no `Promise::transfer` or other action to return it. The value is simply left sitting on the wallet contract's balance permanently, exactly like the described `LiquidityZap.standardAdd()` / `BalancerPoolHelper.zapTokens()` issue where a `payable` function never interacts with `msg.value`.

### Impact Explanation
Any unprivileged caller (an external relayer without an access key, or any RPC caller submitting a `FunctionCall` to `rlp_execute` with a `deposit`) who supplies a malformed transaction, an unsupported action, an already-exhausted nonce, or any of the other error conditions above will have their attached NEAR deposit permanently and unrecoverably stuck in the wallet contract's balance. This is a direct, concrete "frozen funds" loss for the caller — no protocol invariant prevents attaching a deposit to this payable call while hitting one of these early-error branches, and there is no compensating refund logic for them (refund logic only exists for the narrower promise-failure path in `rlp_execute_callback`, and even that is skipped entirely when `predecessor_account_id == current_account_id`, i.e. self-relaying).

### Likelihood Explanation
Likelihood is high: no privileged role is required. Any account (a malicious or careless relayer, a buggy front-end, or any RPC caller) can call `rlp_execute` with a non-zero deposit and pass malformed/rejected transaction data (e.g., invalid ABI encoding, unsupported action like `AddFullAccessKey`/`Stake`/`DeployContract`, excess yocto-near, or an already-used/invalid nonce) — all deterministic, easily-triggered conditions that don't depend on race conditions or validator behavior. A user front-end that naively attaches a "just in case" deposit alongside gas, or a relayer compensating itself via deposit rather than the intended fee mechanism, would trigger this unintentionally as well.

### Recommendation
Ensure the attached deposit is refunded to the caller (`predecessor_account_id`) on every early-error return path of `rlp_execute`/`inner_rlp_execute`, not just when a scheduled promise later fails. Concretely:
- Before returning `Err(e)` from `inner_rlp_execute` (for `Error::User`, `Error::AccountId`, `Error::AccountNonceExhausted`, and the non-self `Error::Relayer` case), issue a `Promise::new(predecessor_account_id).transfer(attached_deposit)` (or equivalent `promise_batch_action_transfer`) whenever `attached_deposit > 0`, mirroring the logic already present in `rlp_execute_callback`.
- Alternatively, reject the call outright with a deposit-must-be-zero check for all paths that cannot use the deposit, so a non-zero deposit fails fast (panics/aborts, causing the runtime's own deposit-refund-on-failure mechanism to return funds to the sender) instead of silently stranding it.

### Proof of Concept
1. An external account `caller.near` (not the wallet's owner and without any access key on the wallet contract) submits a `FunctionCall` to `wallet.near::rlp_execute` with `target` and `tx_bytes_b64` encoding an Ethereum-style transaction whose ABI-encoded action data is malformed (e.g., truncated/invalid bytes for an `AddKey`/`FunctionCall` selector), and attaches a deposit of `1 NEAR`.
2. `env::attached_deposit()` is credited to `wallet.near`'s balance by the runtime as part of receipt processing, matching the standard Economics API semantics. [2](#0-1) 
3. `internal::parse_rlp_tx_to_action` fails with `Error::User(UserError::InvalidAbiEncodedData)` (or similar); `inner_rlp_execute` increments the nonce and returns `Err(err)` immediately. [6](#0-5) 
4. `rlp_execute` converts this into `PromiseOrValue::Value(e.into())`, returning an `ExecuteResponse{success:false, ...}` without ever creating a promise back to `caller.near`. [8](#0-7) 
5. `caller.near`'s balance is now permanently reduced by `1 NEAR`, and `wallet.near`'s balance increased by that amount, with no code path ever moving it back — the deposit is stuck on the contract exactly as in the reported `standardAdd()`/`zapTokens()` bug.

Note: I was not able to fully trace `internal::parse_rlp_tx_to_action` and the `ExcessYoctoNear` check (only grep hits were available, full file contents were truncated by index limits), so the precise set of `UserError` variants reachable purely from attacker-controlled RLP bytes could not be exhaustively confirmed from the index; however, the structural bug — early `Err` returns bypassing any deposit refund — is directly confirmed in `lib.rs`. If exact reachability of each `UserError` variant needs to be verified line-by-line, a full checkout of `internal.rs` would be required.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L88-93)
```rust
    #[payable]
    pub fn rlp_execute(
        &mut self,
        target: AccountId,
        tx_bytes_b64: String,
    ) -> PromiseOrValue<ExecuteResponse> {
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L116-127)
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L340-345)
```rust
    let context = ExecutionContext::new(
        current_account_id.clone(),
        predecessor_account_id,
        env::attached_deposit(),
    )?;
    let caller_deposit = CallerDeposit::new(&context);
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L389-393)
```rust
        Err(err @ Error::User(_)) => {
            // Increment nonce on all user errors to prevent replay.
            *nonce = nonce.saturating_add(1);
            return Err(err);
        }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L394-409)
```rust
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

**File:** docs/RuntimeSpec/Components/BindingsSpec/EconomicsAPI.md (L7-10)
```markdown
- `account_balance` -- the balance attached to the given account. This includes the `attached_deposit` that was attached
  to the transaction;
- `attached_deposit` -- the balance that was attached to the call that will be immediately deposited before
  the contract execution starts;
```

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
