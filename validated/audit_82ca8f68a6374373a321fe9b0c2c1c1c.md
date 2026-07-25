### Title
Attached Deposit Absorbed Without Refund on Early-Return Paths in `WalletContract::rlp_execute`, `address_check_callback`, and `nep_141_storage_balance_callback` — (`runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

---

### Summary

`WalletContract::rlp_execute` is `#[payable]` but silently absorbs any attached deposit when it returns early because `has_in_flight_tx == true`. Two callback functions — `address_check_callback` and `nep_141_storage_balance_callback` — receive a `caller_deposit` argument that tracks an external caller's attached NEAR, but both functions have early-return error paths that drop `caller_deposit` without issuing a refund transfer. In all three cases the caller's NEAR is permanently credited to the wallet contract's account balance with no recovery path for the caller.

---

### Finding Description

**Path 1 — `rlp_execute` / `has_in_flight_tx` guard**

`rlp_execute` is marked `#[payable]`, so any caller may attach NEAR. The very first check is:

```rust
if self.has_in_flight_tx {
    return PromiseOrValue::Value(ExecuteResponse { success: false, … });
}
``` [1](#0-0) 

Returning `PromiseOrValue::Value(…)` is a *successful* function exit in the NEAR SDK. The runtime does **not** auto-refund deposits on success — only on panic. No refund promise is created before the return, so `env::attached_deposit()` is silently credited to the wallet contract's account.

**Path 2 — `address_check_callback` early returns**

When the address-registrar cross-contract call fails or returns unparseable data, the callback returns immediately:

```rust
PromiseResult::Failed => {
    return PromiseOrValue::Value(ExecuteResponse { … });
}
``` [2](#0-1) 

The `caller_deposit: Option<CallerDeposit>` argument — which holds the external caller's NEAR — is dropped without any `promise_batch_action_transfer` call. Compare this with the *only* place a refund is issued: inside `rlp_execute_callback` on `PromiseResult::Failed`:

```rust
if let Some(CallerDeposit { account_id, yocto_near }) = caller_deposit {
    let refund_promise = env::promise_batch_create(&account_id);
    env::promise_batch_action_transfer(refund_promise, …);
}
``` [3](#0-2) 

The early-return paths in `address_check_callback` never reach this refund logic.

**Path 3 — `nep_141_storage_balance_callback` early returns**

The same pattern repeats for the NEP-141 storage-balance callback:

```rust
PromiseResult::Failed => {
    return PromiseOrValue::Value(ExecuteResponse { … });
}
…
Err(_) => {
    return PromiseOrValue::Value(ExecuteResponse { … });
}
``` [4](#0-3) 

Both early exits drop `caller_deposit` without a refund. A wallet owner can specify a malicious or non-existent token contract whose `storage_balance_of` always fails, reliably triggering this path whenever a relayer attaches a deposit.

**`CallerDeposit` type — what is being lost**

`CallerDeposit` is constructed from `env::attached_deposit()` only when the caller is external and the deposit is non-zero:

```rust
NonZeroU128::new(context.attached_deposit.as_yoctonear()).map(|yocto_near| Self {
    account_id: context.predecessor_account_id.clone(),
    yocto_near,
})
``` [5](#0-4) 

The lost value is exactly `yocto_near` — the full deposit the relayer attached.

---

### Impact Explanation

The deposit is credited to the ETH-implicit account's balance (the wallet contract's account). The wallet owner can spend it freely. The relayer (caller) has no mechanism to recover it: there is no admin `withdraw`, no refund receipt, and no panic that would trigger the runtime's automatic deposit-refund path. This is a direct, permanent transfer of the relayer's NEAR to the wallet owner — matching the "stealing or loss of funds" and "balance manipulation" impact categories.

---

### Likelihood Explanation

- **Path 1** is reachable whenever a relayer attaches a deposit (required for base-token transfers) and a concurrent transaction is in flight. The `has_in_flight_tx` flag is publicly readable, but there is a race window between the relayer's view call and the transaction landing.
- **Path 3** is reliably triggerable by the wallet owner: sign an Ethereum ERC-20 transfer pointing at a contract whose `storage_balance_of` always panics or returns garbage. Any relayer who attaches a deposit to that call loses it. No privileged access is required.
- **Path 2** requires the address registrar to fail, which is not directly controllable by the wallet owner, making it lower likelihood but still possible under network stress.

---

### Recommendation

1. **`rlp_execute`**: Before the early return, check `env::attached_deposit()` and issue a `Promise::new(env::predecessor_account_id()).transfer(env::attached_deposit())` if non-zero.

2. **`address_check_callback` and `nep_141_storage_balance_callback`**: On every early-return error path, mirror the refund logic already present in `rlp_execute_callback`:

```rust
if let Some(CallerDeposit { account_id, yocto_near }) = caller_deposit {
    let refund_promise = env::promise_batch_create(&account_id);
    env::promise_batch_action_transfer(
        refund_promise,
        NearToken::from_yoctonear(yocto_near.into()),
    );
}
``` [3](#0-2) 

---

### Proof of Concept

**Path 3 (most reliable):**

1. Deploy a contract `evil.near` whose `storage_balance_of` method always panics.
2. Wallet owner signs an Ethereum ERC-20 transfer transaction with `to = evil.near` and `value = 0` (standard ERC-20), but the relayer is expected to attach `N` yoctoNEAR as a deposit (e.g., to cover storage).
3. Relayer calls `rlp_execute("evil.near", tx_bytes_b64)` with `N` yoctoNEAR attached.
4. `inner_rlp_execute` creates `CallerDeposit { account_id: relayer, yocto_near: N }`.
5. `storage_balance_of` is called on `evil.near`; it panics → `PromiseResult::Failed`.
6. `nep_141_storage_balance_callback` hits the `PromiseResult::Failed` arm at line 204–209 and returns `PromiseOrValue::Value(…)` — dropping `caller_deposit` without a refund.
7. The wallet contract's account balance increases by `N` yoctoNEAR; the relayer's balance decreases by `N` yoctoNEAR with no recovery path. [6](#0-5) [7](#0-6)

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L88-105)
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
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L141-158)
```rust
        let maybe_account_id: Option<AccountId> = match env::promise_result(0) {
            PromiseResult::Failed => {
                return PromiseOrValue::Value(ExecuteResponse {
                    success: false,
                    success_value: None,
                    error: Some("Call to Address Registrar contract failed".into()),
                });
            }
            PromiseResult::Successful(value) => match serde_json::from_slice(&value) {
                Ok(x) => x,
                Err(_) => {
                    return PromiseOrValue::Value(ExecuteResponse {
                        success: false,
                        success_value: None,
                        error: Some("Unexpected response from account registrar".into()),
                    });
                }
            },
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L194-220)
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
        let maybe_storage_balance: Option<StorageBalance> = match env::promise_result(0) {
            PromiseResult::Failed => {
                return PromiseOrValue::Value(ExecuteResponse {
                    success: false,
                    success_value: None,
                    error: Some(format!("Call to NEP-141 {token_id}::storage_balance_of failed")),
                });
            }
            PromiseResult::Successful(value) => match serde_json::from_slice(&value) {
                Ok(x) => x,
                Err(_) => {
                    return PromiseOrValue::Value(ExecuteResponse {
                        success: false,
                        success_value: None,
                        error: Some("Unexpected response from NEP-141 storage_balance_of".into()),
                    });
                }
            },
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L297-305)
```rust
            PromiseResult::Failed => {
                // The cross-contract call failed, refund the caller if needed
                if let Some(CallerDeposit { account_id, yocto_near }) = caller_deposit {
                    let refund_promise = env::promise_batch_create(&account_id);
                    env::promise_batch_action_transfer(
                        refund_promise,
                        NearToken::from_yoctonear(yocto_near.into()),
                    );
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/types.rs (L181-191)
```rust
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
