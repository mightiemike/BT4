## Analysis

The Solidity report describes a function that branches on a token-type flag and silently drops native currency when the "wrong" branch is taken, with no refund path back to the sender. The closest reachable analog in this nearcore checkout is the **NEAR Wallet Contract** (`rlp_execute`), which is a payable, publicly callable contract method that an unprivileged caller (an ordinary account, another contract, or a relayer) can invoke with an attached native `$NEAR` deposit.

### Root cause

`WalletContract::rlp_execute` is `#[payable]`, so the attached deposit is added to the wallet contract's balance the moment the call starts executing: [1](#0-0) 

`inner_rlp_execute` builds a `CallerDeposit` up front to remember the attached deposit so it can be refunded to the external caller *later, if a scheduled cross-contract promise fails*: [2](#0-1) 

However, if RLP/ABI parsing or validation of the embedded Ethereum-style transaction fails with a `User` error (bad nonce, malformed calldata, excess `yocto_near`, unsupported action, etc.), the function returns `Err(err)` **before any promise is ever created**: [3](#0-2) 

Back in `rlp_execute`, this `Err` path returns a plain `PromiseOrValue::Value(e.into())` — a *successful* function-call return (just with `success: false` embedded in the JSON body), not a WASM panic/failure: [4](#0-3) 

Because the receipt itself succeeds (from the runtime's point of view), NEAR's automatic "deposit refund on failed receipt" mechanism never triggers — that mechanism only fires when the whole receipt execution fails: [5](#0-4) 

And the only other refund mechanism, `CallerDeposit`, is only consulted inside `rlp_execute_callback`, which is exclusively reached when a promise chain (address-check, NEP-141 storage lookup, or the actual action promise) was actually created and later fails: [6](#0-5) 

The `caller_deposit` variable computed in `inner_rlp_execute` is simply dropped/unused on the early-return `User` error path, so it is never wired into any refund. The existing test suite only exercises the refund-on-promise-failure case (`test_caller_refunds`), never the "parsing fails before any promise is scheduled" case with a nonzero attached deposit: [7](#0-6) 

### Title
Attached NEAR deposit is permanently retained by the Wallet Contract when `rlp_execute` fails during transaction parsing before any promise is scheduled - (`runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
`WalletContract::rlp_execute` is `#[payable]` and accepts a native `$NEAR` deposit from any external caller. `inner_rlp_execute` captures this deposit in a `CallerDeposit` intended to refund the caller if the eventual cross-contract promise fails. But when the embedded Ethereum-style transaction fails validation/parsing with a `UserError` (bad nonce, malformed ABI data, excess yocto amount, unsupported action, etc.), the function returns an `Err` **before scheduling any promise**. The caller-supplied deposit is never refunded in this path, and since the receipt itself completes successfully (it just returns an error payload inside `ExecuteResponse`), NEAR's automatic deposit-refund-on-failed-receipt mechanism also does not trigger.

### Finding Description
- `rlp_execute` is payable and immediately credits `env::attached_deposit()` to the wallet contract's balance.
- `CallerDeposit::new` records the deposit for refund purposes only when `predecessor_account_id != current_account_id` (i.e., some other account is calling on behalf of the wallet).
- `parse_rlp_tx_to_action` can fail with `Error::User(_)` for many reasons (bad nonce, unparsable data, `ExcessYoctoNear`, unsupported `AddFullAccessKey`, etc.), all reachable purely from user-controlled RLP-encoded transaction bytes.
- On this error, `inner_rlp_execute` returns `Err(err)` directly — `caller_deposit` is discarded without being used, and no promise/refund transfer is ever created.
- `rlp_execute` converts this `Err` into `PromiseOrValue::Value(e.into())`, i.e., a *successful* execution outcome carrying `ExecuteResponse{ success: false, ... }`.
- Because the receipt execution is not itself a `Failure`, the runtime's automatic deposit-refund path (`Receipt::new_balance_refund`, only generated `if result.result.is_err()` at the action-receipt level) never fires either, since from the runtime's perspective the `FunctionCall` action succeeded.
- Net effect: any deposit attached by an external caller to `rlp_execute` is absorbed into the wallet contract's balance and stuck there with no automated return path whenever the embedded transaction fails parsing/validation.

### Impact Explanation
An external caller (e.g. a relayer contract or any account) that attaches native `$NEAR` to a `rlp_execute` call loses that deposit permanently whenever the embedded transaction is malformed/invalid in a way that is caught by parsing/validation (a `UserError`), rather than by the downstream cross-contract call. This is a genuine unauthorized-loss-of-funds bug class matching the report ("excess native fund could be lost" due to inconsistent handling paths and missing refund of the "unexpected" branch), reachable directly from an unprivileged transaction signer or contract calling the publicly deployed Wallet Contract with no special privileges.

### Likelihood Explanation
Reachable by any account attaching a deposit to a normal `FunctionCall` action targeting `rlp_execute`, and triggerable with commonly occurring `UserError` conditions (e.g., stale/incorrect nonce due to a race with another submitted tx, or slightly malformed calldata) — these are realistic operational conditions for relayers automating submission on behalf of users, not a contrived edge case.

### Recommendation
In `inner_rlp_execute`, ensure that whenever the function returns an `Err` prior to creating any promise, and a `CallerDeposit` (or a nonzero `env::attached_deposit()`) exists, a refund transfer is scheduled back to the predecessor before returning the error — mirroring the refund already performed in `rlp_execute_callback` on promise failure. Alternatively, reject calls to `rlp_execute` with nonzero attached deposit until a promise is guaranteed to be created (e.g., validate/parse the transaction first, and only accept payable calls once the parsing succeeded), or explicitly assert `env::attached_deposit() == 0` at method entry unless deposit-forwarding logic is guaranteed to run in every return path.

### Proof of Concept
1. Deploy `WalletContract` behind an eth-implicit account, as in `TestContext`.
2. From an external account (`predecessor_account_id != current_account_id`), call `rlp_execute` with `deposit(NearToken::from_near(N))` attached and a `tx_bytes_b64` that is well-formed enough to reach nonce validation but uses an incorrect nonce (or any input triggering `Error::User(_)` in `parse_rlp_tx_to_action`), analogous to `wallet_contract.rlp_execute_from(&caller, receiver_id.as_str(), &create_tx(...), deposit_amount)` used in `test_caller_refunds`, but choosing an input that fails in `parse_rlp_tx_to_action` rather than in the downstream cross-contract call.
3. Observe the call returns successfully (`ExecuteResponse{ success: false, ... }`), and the caller's account balance decreases by `deposit_amount` while the wallet contract's balance permanently retains it — unlike the "fake.near" case in `test_caller_refunds` which *is* refunded because it fails only after a promise was scheduled.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L88-114)
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

**File:** docs/RuntimeSpec/Refunds.md (L15-18)
```markdown
## Deposit Refunds

Deposit refunds are generated when an action receipt fails to execute. All attached deposit amounts are summed together and
sent as a refund to a `predecessor_id` (because only the predecessor can attach deposits).
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/tests/sanity.rs (L170-227)
```rust
// An external caller gets its deposit back if the cross-contract call fails.
#[tokio::test]
async fn test_caller_refunds() -> anyhow::Result<()> {
    let TestContext { worker, wallet_contract, wallet_sk, address_registrar, .. } =
        TestContext::new().await?;

    let caller = worker.root_account()?;
    let deposit_amount = NearToken::from_near(3);
    let create_tx = |receiver_id: &AccountId, nonce: u64| {
        let method = "register";
        let args = br#"{"account_id": "birchmd.near"}"#;
        let action = Action::FunctionCall {
            receiver_id: receiver_id.to_string(),
            method_name: method.into(),
            args: args.to_vec(),
            gas: Gas::from_tgas(10).as_gas(),
            yocto_near: 0,
        };
        utils::create_signed_transaction(
            nonce,
            receiver_id,
            Wei::new_u128(deposit_amount.as_yoctonear() / (MAX_YOCTO_NEAR as u128)),
            action,
            &wallet_sk,
        )
    };

    // External caller gets a refund when the cross-contract call fails
    let pre_tx_account_balance = caller.view_account().await?.balance;
    let receiver_id: AccountId = "fake.near".parse()?;
    let result = wallet_contract
        .rlp_execute_from(
            &caller,
            receiver_id.as_str(),
            &create_tx(&receiver_id, 0),
            deposit_amount,
        )
        .await?;
    assert!(!result.success);
    let post_tx_account_balance = caller.view_account().await?.balance;
    assert!(
        pre_tx_account_balance.as_yoctonear() - post_tx_account_balance.as_yoctonear()
            < deposit_amount.as_yoctonear()
    );

    // External caller does not get a refund when their tokens are spent
    let pre_tx_account_balance = post_tx_account_balance;
    let receiver_id = address_registrar.id();
    let result = wallet_contract
        .rlp_execute_from(&caller, receiver_id.as_str(), &create_tx(receiver_id, 1), deposit_amount)
        .await?;
    assert!(result.success);
    let post_tx_account_balance = caller.view_account().await?.balance;
    assert!(
        pre_tx_account_balance.as_yoctonear() - post_tx_account_balance.as_yoctonear()
            >= deposit_amount.as_yoctonear()
    );

```
