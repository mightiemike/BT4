## Analysis

The Tokemak report describes a class of bug where a contract accepts a native-asset deposit (ETH) *and* separately pulls/consumes an equivalent wrapped-asset amount without reconciling the two, so any leftover value that wasn't actually needed for the operation is silently stranded in the contract instead of being returned to the depositor.

The closest reachable analog in this nearcore tree is in the **NEAR Wallet Contract** (`near-wallet-contract`), which is a `#[payable]`, unprivileged, publicly callable contract entry point (`rlp_execute`) that any transaction signer can invoke with an attached NEAR deposit on behalf of an eth‑implicit account.

### Root cause

`rlp_execute` accepts `env::attached_deposit()` from *any* predecessor (not just the wallet owner): [1](#0-0) 

The attached amount is wrapped once into a `CallerDeposit` purely so it can be refunded **only if the entire downstream promise chain fails**: [2](#0-1) 

But the actual on-chain deposit spent by the derived Near action (`Transfer`/`FunctionCall`) is computed completely independently from the Ethereum transaction's own `value`/`yocto_near` fields, not from `context.attached_deposit`: [3](#0-2) 

Because NEAR credits `attached_deposit` into the wallet contract's own account balance the moment the call executes, any part of it that the derived action doesn't explicitly re-transfer stays permanently in the wallet contract: [4](#0-3) 

The only refund path is the failure branch of `rlp_execute_callback`; there is no logic to return unused/excess deposit on success: [5](#0-4) 

The `nep_141_storage_balance_callback` path shows the same pattern concretely — a caller may attach `NEP_141_STORAGE_DEPOSIT_AMOUNT` expecting it to be needed, but if the receiver is already registered, the `storage_deposit` sub-call is skipped entirely and the attached NEAR is never returned: [6](#0-5) 

This is directly proven by the repository's own test, which documents that on success the caller loses their **entire** attached deposit even though the underlying `register` FunctionCall required essentially none of it: [7](#0-6) 

### Title
Unrefunded excess attached deposit permanently absorbed by Wallet Contract on successful `rlp_execute` - (`runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
Any unprivileged account can call the payable `rlp_execute` entry point of a deployed eth‑implicit Wallet Contract with an attached NEAR deposit intended to cover contingent on-chain costs (e.g., NEP‑141 `storage_deposit`). The contract only refunds this deposit via `CallerDeposit` when the entire downstream promise chain fails; on success, any portion of the deposit not explicitly consumed by the derived action is never returned and is permanently absorbed into the wallet contract's own balance.

### Finding Description
`rlp_execute` is `#[payable]` and callable by any predecessor, not just the wallet's owner (`lib.rs:88-127`). `inner_rlp_execute` records the caller's `attached_deposit` as a `CallerDeposit` solely to refund it on a failed cross-contract call (`lib.rs:330-345`, `types.rs:180-192`). The actual Near action's own deposit is computed independently from the signed Ethereum transaction's `value`/`yocto_near` fields (`internal.rs:159-166`), completely decoupled from `attached_deposit`. Since attached deposits are credited into the receiving account's balance before execution (`EconomicsAPI.md:7-10`), any amount not explicitly forwarded by a promise (e.g., the `storage_deposit` fallback path in `nep_141_storage_balance_callback`, `lib.rs:224-269`) simply remains as part of the wallet contract's balance with no accounting or return path on success (`rlp_execute_callback`, `lib.rs:275-317`).

### Impact Explanation
Any external, unprivileged relayer/caller that funds a wallet-contract call with a deposit sized to cover a *possible* NEP-141 storage registration (or any deposit larger than what the derived action strictly requires) forfeits that entire amount whenever the call succeeds and the contingency doesn't materialize (receiver already registered, action carries lower/zero deposit, etc.). This is a genuine unaccounted, involuntary value transfer from the caller to the wallet owner's account, reachable from a single signed transaction by any account, with no way for the caller to recover the unused portion.

### Likelihood Explanation
Medium-High. This is not an edge case requiring an adversarial setup — it is the default outcome any time a relayer conservatively over-attaches deposit to cover a contingent NEP-141 `storage_deposit` and the target happens to already be registered, or attaches any deposit exceeding the actual action cost. The scenario is explicitly exercised (and implicitly accepted as expected behavior) by the repository's own `test_caller_refunds` test.

### Recommendation
Track the exact amount of `attached_deposit` actually consumed by the generated promise chain (rather than only tracking success/failure), and issue a refund transfer of the unused remainder back to the original caller in `rlp_execute_callback` / `nep_141_storage_balance_callback` on the success path, mirroring the existing failure-refund logic in `CallerDeposit`.

### Proof of Concept
The existing integration test demonstrates the bug: an external `caller` attaches 3 NEAR to `rlp_execute_from`, targeting a `register` FunctionCall with `yocto_near: 0`. When the target call succeeds, the assertion confirms the caller's balance decreases by **at least** the full 3 NEAR deposit even though the underlying action required negligible deposit — none of the excess is returned. [7](#0-6)

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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L224-238)
```rust
        let promise = match maybe_storage_balance {
            Some(_) => {
                // receiver_id is registered so we can send the transfer
                // without additional actions. Note: in the standard NEP-141
                // implementation it is impossible to have `Some` storage balance,
                // but have it be insufficient to transact.
                match action_to_promise(token_id, action)
                    .map(|p| p.then(ext.rlp_execute_callback(caller_deposit)))
                {
                    Ok(p) => p,
                    Err(e) => {
                        return PromiseOrValue::Value(e.into());
                    }
                }
            }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L275-317)
```rust
    #[private]
    pub fn rlp_execute_callback(
        &mut self,
        caller_deposit: Option<CallerDeposit>,
    ) -> ExecuteResponse {
        self.has_in_flight_tx = false;
        let n = env::promise_results_count();

        if n == 0 {
            // `rlp_execute_callback` is called directly in the case of an emulated self-transfer.
            return ExecuteResponse { success: true, success_value: None, error: None };
        } else if n > 1 {
            return ExecuteResponse {
                success: false,
                success_value: None,
                error: Some(format!(
                    "Invariant violation: this callback comes after a single promise. n={n}"
                )),
            };
        }

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

**File:** docs/RuntimeSpec/Components/BindingsSpec/EconomicsAPI.md (L7-10)
```markdown
- `account_balance` -- the balance attached to the given account. This includes the `attached_deposit` that was attached
  to the transaction;
- `attached_deposit` -- the balance that was attached to the call that will be immediately deposited before
  the contract execution starts;
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/tests/sanity.rs (L215-226)
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
