## Root cause confirmed

In `rlp_execute` (`runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs:88-128`), the method is `#[payable]`, so any `attached_deposit` from an external caller (relayer) is credited to the Wallet Contract account's balance by the runtime **before** the method body runs, regardless of what the method subsequently does.

`inner_rlp_execute` (`lib.rs:330-410`) constructs a `CallerDeposit` (`types.rs:180-191`) only to be used for a refund **if a cross-contract promise later fails** — that refund is issued exclusively inside `rlp_execute_callback` on `PromiseResult::Failed` (`lib.rs:296-312`) and inside `address_check_callback`/`nep_141_storage_balance_callback` failure branches.

But `inner_rlp_execute` returns `Err(...)` synchronously (parse errors, nonce exhaustion, relayer errors, user errors, `ExecutionContext::new` failures) *before any promise is created* (`lib.rs:337-409`). In `rlp_execute`, this is handled as:
```rust
Err(e) => PromiseOrValue::Value(e.into()),
``` [1](#0-0) 

This returns an `Ok`/successful `ExecuteResponse{success:false,...}` value — it does **not** panic. Because the Wallet Contract's method call succeeds at the protocol level (no `ActionError`), the runtime's standard unspent-deposit refund path (`refund_unspent_gas_and_deposits`, `runtime/runtime/src/lib.rs:943`) does not trigger, since that path only refunds deposits for a *failed* receipt. No manual refund `Promise` is constructed for the caller in this branch either, since `caller_deposit`/refund logic hasn't been reached yet.

The result: an external caller (relayer) who attaches a deposit to `rlp_execute` and whose call fails validation synchronously (e.g. malformed RLP, nonce exhausted (`nonce == u64::MAX`), a `Error::User` variant, or an `ExecutionContext::new` failure) has their attached deposit silently absorbed into the Wallet Contract account's balance with **no code path that ever returns it** — matching the "funds forced into a contract and permanently stuck because internal accounting/refund logic doesn't account for it" bug class from the report.

### Title
Attached deposit is permanently absorbed by the Wallet Contract when `rlp_execute` fails synchronously before a promise is created - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
`WalletContract::rlp_execute` is `#[payable]`; any `attached_deposit` is credited to the contract's account balance by the protocol before contract logic executes. `inner_rlp_execute` only arranges a refund of the caller's deposit through `CallerDeposit`/`rlp_execute_callback` when a *cross-contract promise* subsequently fails. When `inner_rlp_execute` instead returns `Err(...)` synchronously (before any promise is scheduled), `rlp_execute` converts this into a normal (non-panicking) `ExecuteResponse` value with no refund, so the deposit is neither returned by the protocol-level refund mechanism (because the receipt did not fail) nor by the contract's manual refund logic.

### Finding Description
- `rlp_execute` is annotated `#[payable]`, so the attached NEAR deposit becomes part of the wallet account's balance immediately upon receipt execution. [2](#0-1) 
- `inner_rlp_execute` is called and, on failure, simply propagates `Err(e)` without creating any promise or refund for the depositor: nonce exhaustion, `ExecutionContext::new` errors, RLP parse errors, `Error::User`, and `Error::Relayer`/`Error::AccountId` all take this path. [3](#0-2) 
- In `rlp_execute`, this `Err(e)` is turned into `PromiseOrValue::Value(e.into())`, i.e. the method call *succeeds* and simply returns an `ExecuteResponse{success:false}` value — it does not panic. [4](#0-3) 
- Because the receipt is not marked as `Failure`, the protocol's automatic refund of unspent attached deposit (which only fires on failed receipts) does not apply.
- The only place the contract explicitly refunds a caller's deposit is inside `rlp_execute_callback` on `PromiseResult::Failed`, which is unreachable when no promise was ever created. [5](#0-4) 
- The contract test suite explicitly covers the case where a promise fails after being created (`test_caller_refunds`) but exercises only the "fake.near" receiver case where a promise *is* created and then fails; it does not cover synchronous, pre-promise `Err` paths (e.g. malformed rlp / nonce exhaustion) attaching a deposit. [6](#0-5) 

### Impact Explanation
Any relayer/caller who attaches NEAR to a call to `rlp_execute` that fails before a cross-contract promise is created loses that deposit permanently: it becomes indistinguishable general balance of the Wallet Contract account, with no function or access path to return it to the original depositor. Since ETH-implicit accounts across the network are deployed with this same global Wallet Contract code (`AccountContract::Global`, gated by `EthImplicitGlobalContract`), this is a systemic loss-of-funds bug reachable by any unprivileged relayer/caller who submits a bad or racing transaction with a non-zero deposit.

### Likelihood Explanation
Likelihood is Low-to-Medium: it requires an external (non-self) caller to attach a non-zero deposit while triggering one of the synchronous error paths in `inner_rlp_execute` (e.g. a stale/racing nonce because another relayer's transaction landed first, malformed transaction bytes, or `AccountNonceExhausted`). This is plausible in a competitive relayer environment where multiple relayers race to submit the same signed Ethereum transaction, and mis-attaching a deposit is an easy caller mistake or a hostile action by a griefing relayer trying to make legitimate relayers lose funds.

### Recommendation
In `rlp_execute`'s `Err(e) => PromiseOrValue::Value(e.into())` arm, when `env::predecessor_account_id() != env::current_account_id()` and `env::attached_deposit() > 0`, issue a transfer promise refunding the deposit to the predecessor before returning the `ExecuteResponse`, mirroring the refund already done in `rlp_execute_callback`'s `PromiseResult::Failed` branch. Equivalently, construct the `CallerDeposit` earlier (before any fallible step) and always refund it on any early-return `Err` path in `inner_rlp_execute`/`rlp_execute`.

### Proof of Concept
1. Deploy the global Wallet Contract for chain ETH-implicit accounts (as in `test_wallet_contract_interaction` / `TestContext`). [7](#0-6) 
2. As an external relayer account (not the wallet's own signer), call `rlp_execute(target, tx_bytes_b64)` attaching e.g. 3 NEAR, with `tx_bytes_b64` set so that `nonce == u64::MAX` on the target wallet (or any input causing `inner_rlp_execute` to return `Err` before scheduling a promise, e.g. malformed RLP bytes). [8](#0-7) 
3. Observe: the call returns `ExecuteResponse{success:false,...}` without panicking; the relayer's account balance decreases by the attached deposit (plus gas), and the wallet contract's account balance increases by the same deposit amount.
4. No subsequent call from the relayer (or anyone) can recover that specific deposit — it has been absorbed into the wallet's undifferentiated NEAR balance with no accounting trail (`CallerDeposit` was never even constructed for this path, since it's built inside `inner_rlp_execute` after the point at which several of these `Err` branches already return).

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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L116-126)
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L337-409)
```rust
    if *nonce == u64::MAX {
        return Err(Error::AccountNonceExhausted);
    }
    let context = ExecutionContext::new(
        current_account_id.clone(),
        predecessor_account_id,
        env::attached_deposit(),
    )?;
    let caller_deposit = CallerDeposit::new(&context);

    let parsing_result = internal::parse_rlp_tx_to_action(&tx_bytes_b64, &target, &context, *nonce);
    let (action, transaction_kind) = match parsing_result {
        Ok((action, transaction_kind)) => {
            // Increment nonce for all cases where the registrar contract is not needed
            // to prevent replay of those transactions. For transactions that go through
            // the registrar we still do not know if the transaction has a relayer error
            // or not, therefore we must delay incrementing the nonce.
            //
            // Note: relayers with access keys cannot use this delay to needlessly spend
            // the users tokens because only one transaction is allowed to be in-flight
            // at a time.
            if let TransactionKind::EthEmulation(EthEmulationKind::EOABaseTokenTransfer {
                address_check: Some(_),
                ..
            }) = &transaction_kind
            {
            } else {
                *nonce = nonce.saturating_add(1);
            }

            // If the action is an emulated base token or ERC-20 transfer with a non-zero fee then
            // create a promise to send the refund to the relayer. This allows any relayer
            // to safely serve base token transfers from any wallet without additional
            // on-boarding because the relayer will receive some compensation for sending
            // the transaction. Users should always verify the fee before signing a base token
            // transfer. Relayers should also verify the fee before sending to make sure the
            // user's signed transaction will refund enough to cover the relayer's gas costs.
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

            (action, transaction_kind)
        }
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/tests/sanity.rs (L170-229)
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

    Ok(())
}
```

**File:** integration-tests/src/tests/features/wallet_contract.rs (L245-256)
```rust
    // Deploy the wallet contract as a global contract for ETH implicit accounts.
    let magic_bytes = wallet_contract_magic_bytes(chain_id);
    let wallet_code = wallet_contract(*magic_bytes.hash()).unwrap();
    let deploy_tx = SignedTransaction::deploy_global_contract(
        1,
        relayer.clone(),
        wallet_code.code().to_vec(),
        &relayer_signer.signer,
        *genesis_block.hash(),
        GlobalContractDeployMode::CodeHash,
    );
    height = check_tx_processing(&mut env, deploy_tx, height, blocks_number);
```
