### Title
Wallet Contract cross-contract call consumes attached NEAR deposit with no refund even when the deposit amount is unrelated to the relayed action's declared value - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
The `near-wallet-contract` (the eth-implicit account "Wallet Contract" that emulates Ethereum accounts on NEAR) exposes a `#[payable]` entry point `rlp_execute` that lets an arbitrary caller attach a NEAR deposit alongside a relayed, RLP-encoded Ethereum transaction. [1](#0-0)  That attached deposit is tracked via `CallerDeposit` and is only refunded to the external caller if the resulting cross-contract promise **fails**; if the promise succeeds, the deposit is permanently absorbed regardless of whether it bears any relationship to the value actually specified in the (already-signed, immutable) Ethereum transaction. [2](#0-1) [3](#0-2) 

### Finding Description
`ExecutionContext::new` records `env::attached_deposit()` as-is, and `CallerDeposit::new` only stores it for later refund purposes — it never validates that the deposit matches anything derived from the actual signed Ethereum transaction (nonce, `value`, gas price, or fee). [4](#0-3) [5](#0-4) 

The actual value moved by the relayed action comes from the RLP-decoded, ABI-encoded `yocto_near`/`value` fields embedded in the Ethereum transaction itself (`parse_rlp_tx_to_action` → `try_into_near_action`), completely independent of whatever NEAR deposit the caller of `rlp_execute` chose to attach. [6](#0-5) 

Once the target promise resolves, `rlp_execute_callback` only issues a refund of the caller's tracked deposit on `PromiseResult::Failed`; on `PromiseResult::Successful`, no refund at all is generated, and the caller's deposit is simply merged into the wallet contract's account balance — this is confirmed directly by the project's own test, `test_caller_refunds`, whose comment states: "External caller does not get a refund when their tokens are spent" for the success path. [2](#0-1) [7](#0-6) 

This is the same bug class as the Gitcoin `RoundImplementation.vote` finding: a payable entry point forwards `msg.value`/attached deposit to an underlying operation whose success/failure is decoupled from whether the deposit was actually "used" for its intended purpose. If the caller (a third party funding/tipping a relayed transaction, or a relayer that mis-estimates/attaches an incorrect amount) attaches a deposit that has no relation to what the eth-tx's `value`/fee fields specify, and the downstream promise happens to succeed (e.g., a trivial method call, a `register` call to the address registrar as shown in the test, or any successful-but-cheap cross-contract call), the entire attached deposit is retained by the target account with no on-chain signal that it was "wasted," and with the "no-op & no-revert" characteristic flagged in the original report: success is reported (`success: true`) even though the deposit had no bearing on the outcome.

### Impact Explanation
Any account permanently able to attach and lose NEAR tokens to a misconfigured/mismatched deposit constitutes unauthorized value movement/permanently frozen (to the depositor) funds, moved to the wallet's/target's balance without an enforced correspondence to the intended transaction value. Because `rlp_execute` is `#[payable]` and callable by any signer (not just the wallet's own owner or a designated relayer), an external caller — a naive or automated relayer, an integrator, or a user experimenting with fee estimation — can lose real funds attaching an incorrect deposit whenever the underlying cross-contract call happens to succeed, which is the common case, not the exceptional one. This matches the Medium severity of the original finding: real economic loss for an unprivileged caller due to insufficiently validated payable-call semantics, though it requires the caller to misjudge/misconfigure the deposit relative to the actual action being relayed (a "round misconfiguration"-equivalent condition), which the project's own tests explicitly acknowledge as expected behavior.

### Likelihood Explanation
Likelihood is moderate: it requires a caller other than the wallet's own account (external caller / relayer) to attach a NEAR deposit to `rlp_execute` that is not equal to what is actually needed/expected by the embedded Ethereum transaction, and for the resulting promise to succeed. Because `attached_deposit` is fully caller-controlled and untied to the signed transaction's fields, this is easy to trigger accidentally (fee/gas estimation errors, tooling bugs, or a relayer over-funding out of caution) and requires no attacker sophistication or privileged position — exactly the "misconfiguration by a well-meaning but careless caller" scenario in the original report.

### Recommendation
- Validate that any attached deposit passed to `rlp_execute` is consistent with the value actually required by the parsed transaction (e.g., equal to `tx.value` converted to yoctoNEAR plus any expected fee), and reject/refund any excess deposit unconditionally rather than only on promise failure.
- In `rlp_execute_callback`, refund any deposit amount that was not consumed by the successful action (e.g., compute the delta between `CallerDeposit.yocto_near` and the amount actually forwarded to the target action) instead of refunding only on `PromiseResult::Failed`.
- Add an explicit invariant/test asserting that unrelated or excess deposits are always returned to the caller, regardless of whether the underlying promise succeeds, mirroring the recommendation in the source report to avoid "no-op & no-revert" loss of funds.

### Proof of Concept
1. Deploy a wallet contract instance for address `A` (eth-implicit account) as in `TestContext::new`. [8](#0-7) 
2. As an external caller distinct from the wallet account, call `rlp_execute(target=address_registrar, tx_bytes_b64=<signed "register" tx>)` while attaching `deposit_amount = 3 NEAR`, an amount unrelated to what the underlying `register` call actually needs. [9](#0-8) 
3. Because the call to the address registrar succeeds, `rlp_execute_callback` returns `success: true` and issues no refund of the caller's attached deposit. [10](#0-9) 
4. The caller's balance decreases by at least the full `deposit_amount`, confirmed by the existing test assertion `pre_tx_account_balance - post_tx_account_balance >= deposit_amount.as_yoctonear()`, with no relationship enforced between the deposit and what the relayed transaction actually specified as its value/fee. [11](#0-10)

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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/types.rs (L79-102)
```rust
impl ExecutionContext {
    pub fn new(
        current_account_id: AccountId,
        predecessor_account_id: AccountId,
        attached_deposit: NearToken,
    ) -> Result<Self, Error> {
        let current_address = crate::internal::extract_address(&current_account_id)?;
        Ok(Self { current_address, attached_deposit, predecessor_account_id, current_account_id })
    }

    /// In production eth-implicit accounts are top-level, so this suffix will
    /// always be empty. The purpose of finding a suffix is that it allows for
    /// testing environments where the wallet contract is deployed to an address
    /// that is a sub-account. For example, this allows testing on Near testnet
    /// before the eth-implicit accounts feature is stabilized.
    /// The suffix is only needed in testing.
    pub fn current_account_suffix(&self) -> &str {
        self.current_account_id
            .as_str()
            .find('.')
            .map(|index| &self.current_account_id.as_str()[index..])
            .unwrap_or("")
    }
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/internal.rs (L159-166)
```rust
    validate_tx_value(&tx)?;

    // Call to `low_u128` here is safe because of the validation done in `validate_tx_value`
    let near_action = action
        .try_into_near_action(tx.value.raw().low_u128().saturating_mul(MAX_YOCTO_NEAR.into()))?;

    Ok((near_action, transaction_kind))
}
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/tests/sanity.rs (L172-196)
```rust
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

```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/tests/sanity.rs (L215-227)
```rust
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
