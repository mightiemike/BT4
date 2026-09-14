## #ValidAnalog found

### Title
Attached deposit (`CallerDeposit`) is permanently lost when the address-registrar lookup fails in the NEAR Wallet Contract - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
The reported issue is about code that performs a token transfer and fails to check/act on the outcome of that operation, causing value to be treated as successfully moved when it was not. In `nearcore`'s Wallet Contract (the eth-implicit-account emulation contract, reachable by any relayer/caller submitting a signed Ethereum-style transaction through `rlp_execute`), the analogous failure mode is a cross-contract call result that *is* checked for its `Failed`/`Successful` variant, but the caller's forwarded deposit is silently dropped instead of refunded on one of the failure branches, permanently trapping funds in the contract.

### Finding Description
`CallerDeposit` exists specifically to track NEAR attached by an external (non-self) caller so that it can be refunded if a downstream cross-contract call fails: [1](#0-0) 

The "happy path" refund logic is implemented correctly in `rlp_execute_callback`, which refunds `caller_deposit` to the predecessor whenever the chained promise fails: [2](#0-1) 

However, `address_check_callback` — invoked whenever an `EOABaseTokenTransfer` targets another eth-implicit account and requires an address-registrar lookup — receives the same `caller_deposit` parameter but drops it entirely on the `PromiseResult::Failed` branch, returning an error response without ever scheduling a refund: [3](#0-2) 

The call path is set up in `inner_rlp_execute`, which builds the `caller_deposit` from the attached deposit of the external caller and routes the flow through `address_check_callback` for this specific transaction kind: [4](#0-3) 

If the address-registrar `lookup` cross-contract call fails for any reason (registrar not deployed/misconfigured, registrar contract errors, or any other failure of that specific promise), `address_check_callback` returns `success: false` but never creates the refund promise that `rlp_execute_callback` would have created in the equivalent failure scenario. The deposit that was attached by the predecessor account is neither used nor returned — it simply becomes part of the wallet contract's balance with no code path that ever reclaims or forwards it back.

### Impact Explanation
This results in unconditional, unrecoverable loss of the attached NEAR deposit for the calling account whenever the registrar lookup step of an `EOABaseTokenTransfer` fails. Because the design intent (proven by the identical, correctly-implemented refund logic in `rlp_execute_callback`) is that failed cross-contract calls must return the caller's deposit, this is a genuine deviation from intended behavior causing permanently frozen funds, triggerable by a single ordinary transaction through the wallet contract's public `rlp_execute` entry point — no privileged or adversarial-node access is required.

### Likelihood Explanation
Any relayer or caller sending an `EOABaseTokenTransfer` with an `address_check` to another eth-implicit account and attaching a non-zero deposit will hit this code path. Since the registrar lookup is an ordinary cross-contract call, it can fail from mundane conditions (mis-deployed/unset registrar account, registrar-side panics, etc.), making the loss reachable in normal operation, not just a contrived edge case.

### Recommendation
In the `PromiseResult::Failed` branch of `address_check_callback`, mirror the refund logic used in `rlp_execute_callback`: if `caller_deposit` is `Some`, create a `promise_batch_create`/`promise_batch_action_transfer` back to `caller_deposit.account_id` for `caller_deposit.yocto_near` before returning the failure `ExecuteResponse`.

### Proof of Concept
1. Deploy the Wallet Contract for an eth-implicit account `A`, with `ADDRESS_REGISTRAR_ACCOUNT_ID` pointing to an account that either does not exist or will make the `lookup` call fail (e.g. no contract deployed there).
2. A relayer submits `rlp_execute` to account `A` with a signed Ethereum transaction whose `to` is another eth-implicit account `B`, attaching a NEAR deposit (`attached_deposit > 0`) to the outer `FunctionCall`.
3. This is parsed as `EthEmulationKind::EOABaseTokenTransfer { address_check: Some(address), .. }`, and `inner_rlp_execute` schedules the registrar `lookup` call followed by `.then(address_check_callback(target, action, caller_deposit))`, where `caller_deposit = Some(CallerDeposit { account_id: relayer, yocto_near: attached_deposit })`.
4. Because the registrar account is unreachable, the `lookup` promise resolves as `PromiseResult::Failed`.
5. `address_check_callback` returns `ExecuteResponse { success: false, .. }` without ever creating a refund promise; `caller_deposit` is dropped.
6. The relayer's attached deposit remains permanently in account `A`'s balance; there is no subsequent transaction or code path to reclaim it.

### Citations

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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L130-159)
```rust
    /// Callback after checking if an address is contained in the registrar.
    /// This check happens when the target is another eth implicit account to
    /// confirm that the relayer really did check for a named account with that address.
    #[private]
    pub fn address_check_callback(
        &mut self,
        target: AccountId,
        action: near_action::Action,
        caller_deposit: Option<CallerDeposit>,
    ) -> PromiseOrValue<ExecuteResponse> {
        self.has_in_flight_tx = false;
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
        };
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L412-432)
```rust
    let promise = match transaction_kind {
        TransactionKind::EthEmulation(EthEmulationKind::EOABaseTokenTransfer {
            address_check: Some(address),
            ..
        }) => {
            let callback_gas = ADDRESS_CHECK_CALLBACK_GAS.saturating_add(action.gas());
            let ext = WalletContract::ext(current_account_id).with_static_gas(callback_gas);
            let address_registrar = {
                let account_id = ADDRESS_REGISTRAR_ACCOUNT_ID
                    .trim()
                    .parse()
                    .unwrap_or_else(|_| env::panic_str("Invalid address registrar"));
                ext_registrar::ext(account_id).with_static_gas(REGISTRAR_LOOKUP_GAS)
            };
            let address = format!("0x{}", hex::encode(address));
            address_registrar.lookup(address).then(ext.address_check_callback(
                target,
                action,
                caller_deposit,
            ))
        }
```
