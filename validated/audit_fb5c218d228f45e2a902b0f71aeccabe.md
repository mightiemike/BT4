The exact analog is in the NEAR Wallet Contract's `rlp_execute` entry point. This is a `#[payable]` method, meaning any NEAR attached as `attached_deposit` is credited to the contract's account balance by the protocol *before* the method body runs, regardless of what the method subsequently does. If the caller attempts to call `rlp_execute` while another transaction is already in flight, the function returns immediately with `PromiseOrValue::Value(...)` — an ordinary (non-panicking) return — without ever spending, forwarding, or refunding the deposit: [1](#0-0) 

Because the call succeeds (it doesn't panic), NEAR's protocol-level automatic deposit refund (which only triggers on receipt failure) never fires, so the yoctoNEAR the caller attached is permanently absorbed into the wallet contract's balance. The contract's only path for reclaiming an attached deposit is the explicit `CallerDeposit` bookkeeping used inside promise callbacks, and that mechanism is only invoked on `PromiseResult::Failed` after a promise chain has actually been created: [2](#0-1) [3](#0-2) 

The `has_in_flight_tx` early return at the top of `rlp_execute`, and the `Err(e) => PromiseOrValue::Value(e.into())` branch for `Error::User`/`Error::AccountId` variants (which occur before `CallerDeposit` tracking or any promise is created), both return a plain `Value` without creating any promise chain, so no refund logic runs at all: [4](#0-3) 

The `WalletContract` struct exposes no generic withdraw/sweep method — only `get_nonce`, `rlp_execute`, its three private callbacks, and `ban_relayer` — so there is no way for the eth-implicit account owner or anyone else to retrieve a deposit that lands in this "stuck" state: [5](#0-4) 

### Title
Attached NEAR deposit is permanently locked when `rlp_execute` returns via an early non-promise `Value` path - (File: runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs)

### Summary
`WalletContract::rlp_execute` is a `#[payable]` method. Any `attached_deposit` sent with the call is credited to the wallet contract's account balance by the NEAR runtime before the method body executes. Several code paths in `rlp_execute` (and its callbacks) return `PromiseOrValue::Value(...)` directly — i.e., a successful, non-panicking return — without creating any promise or issuing a `Transfer` action to refund the deposit. Because NEAR's protocol-level deposit refund only occurs automatically when the receipt fails (panics), a deposit attached to one of these "early success-with-error-payload" branches is never returned and never spent, leaving it permanently stuck in the contract's balance with no exposed method to reclaim it.

### Finding Description
The `has_in_flight_tx` guard at the top of `rlp_execute` returns `PromiseOrValue::Value(ExecuteResponse{success:false,...})` immediately if another transaction is in flight, before `inner_rlp_execute` (and thus `CallerDeposit` tracking) is ever invoked [6](#0-5) . Similarly, the `Err(e) => PromiseOrValue::Value(e.into())` fallback for `Error::User`/`Error::AccountId` results also returns a `Value` with no promise created [7](#0-6) . The only deposit-refund mechanism the contract implements is the `CallerDeposit` struct, which is refunded solely inside `rlp_execute_callback` when a downstream cross-contract promise fails [8](#0-7) . None of the immediate `Value`-returning branches route through this mechanism, so the deposit attached to those calls is neither refunded nor spent — it simply becomes stranded in the contract's balance. The contract's public surface (`get_nonce`, `rlp_execute`, `address_check_callback`, `nep_141_storage_balance_callback`, `rlp_execute_callback`, `ban_relayer`) contains no sweep/withdraw method that could later recover this balance [5](#0-4) .

### Impact Explanation
Any unprivileged caller (the relayer or any third party, since `rlp_execute` is a public, payable method reachable directly from a signed NEAR transaction) who attaches a deposit while another transaction happens to be in flight for that wallet, or whose transaction data triggers a `User`/`AccountId` parsing error, permanently loses that NEAR. There is no path in the contract to move that balance back out, resulting in concrete, permanent loss of funds for the caller (self-inflicted or attacker-induced by front-running with a colliding in-flight transaction).

### Likelihood Explanation
This requires no privileged access — merely attaching a deposit to an `rlp_execute` call at a moment when `has_in_flight_tx` is already true (which any relayer/attacker can engineer by submitting two overlapping calls, as demonstrated by the existing `test_simultaneous_transactions` test) or crafting an RLP payload that triggers a `User`/`AccountId` error while a deposit is attached [9](#0-8) .

### Recommendation
Every non-promise `Value` return path in `rlp_execute` and its callbacks should either (a) reject calls that attach a nonzero deposit before any promise is guaranteed to be created, or (b) explicitly issue a `Promise::transfer` refunding `env::attached_deposit()` back to `env::predecessor_account_id()` before returning `PromiseOrValue::Value(...)`.

### Proof of Concept
1. Caller A calls `rlp_execute` on a wallet contract instance with a valid signed transaction and a large attached NEAR deposit, and the call is still pending (has_in_flight_tx=true, e.g. via the pattern shown in `test_simultaneous_transactions`) [10](#0-9) .
2. Caller B immediately calls `rlp_execute` on the same wallet contract with an attached deposit.
3. Because `has_in_flight_tx` is `true`, Caller B's call hits the early-return branch at lines 97-105 of `lib.rs`, returning `PromiseOrValue::Value(...)` without refunding.
4. Caller B's attached deposit is now merged into the wallet contract's balance permanently; no method exists to withdraw it back to Caller B.

### Citations

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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L106-128)
```rust
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L296-317)
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
    }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/types.rs (L180-192)
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
}
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/tests/sanity.rs (L121-168)
```rust
/// Only one transaction can be in flight at a time.
#[tokio::test]
async fn test_simultaneous_transactions() -> anyhow::Result<()> {
    let TestContext { worker, wallet_contract, wallet_sk, .. } = TestContext::new().await?;

    let receiver_account = worker.root_account().unwrap();

    let initial_receiver_balance = receiver_account.view_account().await.unwrap().balance;

    let receiver_id = receiver_account.id().as_str().into();
    let action = Action::Transfer { receiver_id, yocto_near: 1 };
    let signed_transaction =
        utils::create_signed_transaction(0, receiver_account.id(), Wei::zero(), action, &wallet_sk);
    let wallet_method_call_1 = near_workspaces::operations::Function::new("rlp_execute")
        .args_json(serde_json::json!({
            "target": receiver_account.id(),
            "tx_bytes_b64": codec::encode_b64(&codec::rlp_encode(&signed_transaction))
        }))
        .gas(near_workspaces::types::Gas::from_tgas(100));
    let wallet_method_call_2 = near_workspaces::operations::Function::new("rlp_execute")
        .args_json(serde_json::json!({
            "target": receiver_account.id(),
            "tx_bytes_b64": codec::encode_b64(&codec::rlp_encode(&signed_transaction))
        }))
        .gas(near_workspaces::types::Gas::from_tgas(100));

    let near_transaction = wallet_contract
        .inner
        .as_account()
        .batch(wallet_contract.inner.id())
        .call(wallet_method_call_1)
        .call(wallet_method_call_2)
        .transact()
        .await?;

    let result: ExecuteResponse = near_transaction.json()?;

    // The second transaction in the batch fails and this is returned as the
    // result of the Near transaction. But the first transaction in the batch
    // spawns promises that resolve, so the transfer was will successful.
    assert!(!result.success);
    assert!(result.error.unwrap().contains("transaction already in progress"));

    let final_receiver_balance = receiver_account.view_account().await.unwrap().balance;
    assert_eq!(final_receiver_balance.as_yoctonear() - initial_receiver_balance.as_yoctonear(), 1,);

    Ok(())
}
```
