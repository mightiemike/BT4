Confirmed: the `#[payable]` `rlp_execute` entry point in the NEAR Wallet Contract captures `env::attached_deposit()` into a `CallerDeposit` for later refunding, but only wires that refund logic into promise callbacks — several synchronous-error paths return an `ExecuteResponse` directly with no promise and no refund, permanently absorbing the caller's attached deposit into the contract balance.

### Title
Attached NEAR deposit permanently lost on synchronous `rlp_execute` error paths in the NEAR Wallet Contract - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
`WalletContract::rlp_execute` is marked `#[payable]`, so any external caller may attach a NEAR deposit (`msg.value` analog) when invoking it [1](#0-0) . The function tracks that deposit via `CallerDeposit::new`, whose only purpose is to allow refunding the caller if the resulting cross-contract call fails [2](#0-1) , and the refund is only issued from within `rlp_execute_callback` when a spawned `Promise` fails [3](#0-2) . However, `inner_rlp_execute` can return an `Err` synchronously — before any promise (and therefore before `caller_deposit` is ever attached to a callback) is created — for many parsing/relayer/user errors [4](#0-3) . In `rlp_execute`, this `Err` is converted directly into a value response with `PromiseOrValue::Value(e.into())`, with no refund transfer created at all [5](#0-4) .

### Finding Description
The vulnerability class matches the report: a caller can attach value (`attached_deposit`, NEAR's `msg.value` analog) to a call whose logic path does not actually consume or explicitly refund that value, resulting in silent fund loss. Concretely:
- `CallerDeposit::new` captures `context.attached_deposit` immediately when `inner_rlp_execute` starts, but only for external (non-self) callers [6](#0-5) .
- If RLP/ABI parsing of the Ethereum transaction fails with any `UserError` (e.g., `ExcessYoctoNear`, `UnknownFunctionSelector`, `InvalidAbiEncodedData`) or most `RelayerError`/`AccountIdError` variants, `inner_rlp_execute` returns `Err(err)` directly without ever using `caller_deposit` [4](#0-3) .
- Back in `rlp_execute`, this falls into `Err(e) => PromiseOrValue::Value(e.into())`, which just returns an `ExecuteResponse` value — no `Promise::new(...).transfer(...)` is created to send the deposit back to the predecessor [7](#0-6) .
- Because the method is `#[payable]`, any attached NEAR is deposited into the contract's balance as part of the function call receipt processing (the analog of NEAR's own deposit-refund machinery in `refund_unspent_gas_and_deposits`, which only fires when the whole *receipt* fails, not when a contract method logically "fails" while still returning `Ok`) [8](#0-7) . Since the wallet contract call itself succeeds at the protocol level (it returns a value, not an error), NEAR's own deposit-refund mechanism never engages — the deposit is retained by the wallet contract account, and the caller has no path to reclaim it.

This is directly analogous to the reported `BountyCore.receiveFunds` issue: value attached under one condition (external caller attaching a deposit, expecting a refund guarantee documented in `CallerDeposit`) is silently absorbed when a different code path (synchronous parse/validation error) is taken that was not designed to move or return that value.

### Impact Explanation
Any external, unprivileged caller (not using the wallet owner's access key) who attaches a NEAR deposit to `rlp_execute` and supplies transaction bytes/target that fail validation (bad ABI encoding, unsupported action, excess yoctoNear encoding, invalid base64, wrong chain id, etc.) permanently loses that attached deposit into the wallet contract's balance with no recovery path. This is a concrete, transaction-triggered, unauthorized-value-retention bug reachable by any signer calling the contract — matching the required "concrete unauthorized value movement / permanently frozen funds" bar. Given wallet contracts are deployed as global contracts backing ETH-implicit accounts and are a primary attack surface for relayers/external callers, this can cause real fund loss for users interacting through non-owner relayer paths or via `rlp_execute_from`-style direct calls with a deposit.

### Likelihood Explanation
Likelihood is meaningful but bounded: it requires an external caller (`predecessor_account_id != current_account_id`) to attach a deposit *and* supply a transaction that fails one of the synchronous validation branches (many of which are triggerable by a malformed or adversarial ABI-encoded action, e.g. `ExcessYoctoNear`, `UnknownFunctionSelector`, `InvalidAbiEncodedData`, `UnsupportedAction`). This is realistic for relayer-less/ untrusted external callers experimenting with `rlp_execute` directly and attaching a deposit speculatively (as shown to be an expected/tested scenario for the promise-failure case in `test_caller_refunds`), but the promise-failure refund path is well tested while the pre-promise synchronous error path is not covered by any test that attaches a deposit.

### Recommendation
In `rlp_execute`, before or when converting a synchronous `Err(e)` from `inner_rlp_execute` into `PromiseOrValue::Value(e.into())`, check whether a deposit was attached by the predecessor (mirroring the `CallerDeposit::new` non-self-caller condition) and if so, spawn a transfer promise refunding that deposit to `env::predecessor_account_id()`, similar to the refund already performed in `rlp_execute_callback` on `PromiseResult::Failed`. Alternatively, restructure `inner_rlp_execute` to always return the constructed `caller_deposit`/context alongside any early `Err`, so `rlp_execute` can uniformly issue a refund promise whenever an external caller's transaction fails, regardless of whether the failure is synchronous (parsing) or asynchronous (cross-contract call failure).

### Proof of Concept
1. Deploy the wallet contract as usual and note it accepts deposits on `rlp_execute` (`#[payable]`) [1](#0-0) .
2. As an external account (predecessor ≠ wallet contract's own account id, i.e. not the wallet owner using their own key), call `rlp_execute(target, tx_bytes_b64)` attaching a non-zero NEAR deposit, where `tx_bytes_b64` decodes to an RLP transaction that triggers e.g. `UserError::ExcessYoctoNear` or `UserError::InvalidAbiEncodedData` during `internal::parse_rlp_tx_to_action` (this happens before nonce increment logic even completes for most error kinds) [9](#0-8) .
3. `inner_rlp_execute` returns `Err(Error::User(...))` without ever consuming `caller_deposit`.
4. `rlp_execute` matches `Err(e) => PromiseOrValue::Value(e.into())`, returning the error response synchronously with no refund promise created.
5. Observe: the caller's account balance decreases by the full attached deposit; the wallet contract's balance increases by that deposit; no receipt refunding the caller is ever produced (contrast with `test_caller_refunds`, which only exercises the promise-failure refund path, not the synchronous-error path) [10](#0-9) .

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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L340-345)
```rust
    let context = ExecutionContext::new(
        current_account_id.clone(),
        predecessor_account_id,
        env::attached_deposit(),
    )?;
    let caller_deposit = CallerDeposit::new(&context);
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L347-409)
```rust
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/types.rs (L172-192)
```rust
/// A data type to keep track of the deposit given by an external caller.
/// This allows us to refund the caller's deposit if the cross-contract call fails.
#[derive(Debug, PartialEq, Eq, Clone, serde::Serialize, serde::Deserialize)]
pub struct CallerDeposit {
    pub account_id: AccountId,
    pub yocto_near: NonZeroU128,
}

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

**File:** runtime/runtime/src/lib.rs (L1284-1329)
```rust
    fn refund_unspent_gas_and_deposits(
        &self,
        gas_burn_price: Balance,
        gas_purchase_price: Balance,
        receipt: &Receipt,
        action_receipt: &VersionedActionReceipt,
        result: &mut ActionReceiptResult,
        config: &RuntimeConfig,
        created_account: bool,
        protocol_version: ProtocolVersion,
    ) -> Result<GasRefundResult, RuntimeError> {
        let total_deposit = total_deposit(&action_receipt.actions())?;
        let prepaid_gas = total_prepaid_gas(&action_receipt.actions())?
            .checked_add(total_prepaid_send_fees(config, &action_receipt.actions())?.gas)
            .ok_or(IntegerOverflowError)?;
        let prepaid_exec_gas =
            total_prepaid_exec_fees(config, &action_receipt.actions(), receipt.receiver_id())?
                .checked_add(config.fees.fee(ActionCosts::new_action_receipt).exec_fee())
                .ok_or(IntegerOverflowError)?;
        let deposit_refund = if result.result.is_err() { total_deposit } else { Balance::ZERO };
        let gross_gas_refund = if result.result.is_err() {
            prepaid_gas
                .checked_add(prepaid_exec_gas.gas)
                .ok_or(IntegerOverflowError)?
                .checked_sub(result.gas_burnt)
                .unwrap()
        } else {
            prepaid_gas
                .checked_add(prepaid_exec_gas.gas)
                .ok_or(IntegerOverflowError)?
                .checked_sub(result.gas_used)
                .unwrap()
        };

        // NEP-536 also adds a penalty to gas refund.
        let refund_penalty: Gas = config.fees.gas_penalty_for_gas_refund(gross_gas_refund);
        let penalty_gas_price = if ProtocolFeature::AccountCostIncrease.enabled(protocol_version) {
            gas_burn_price
        } else {
            gas_purchase_price
        };
        let refund_penalty_amount = safe_gas_to_balance(penalty_gas_price, refund_penalty)?;

        // Refund for the leftover gas that was not used by this receipt.
        let unused_gas_balance_refund = safe_gas_to_balance(gas_purchase_price, gross_gas_refund)?
            .saturating_sub(refund_penalty_amount);
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/tests/sanity.rs (L170-213)
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
```
