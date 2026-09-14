### Title
Wallet Contract loses the caller's attached deposit when the address-registrar lookup fails, instead of refunding it - ([File: runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs])

### Summary
This is the strongest reachable analog to the ERC4626 report's bug class ("the contract trusts a return value/outcome from an external call to drive its accounting, instead of verifying/handling the real outcome for every code path, causing unfair loss of a user's funds"). In the NEAR Wallet Contract, `rlp_execute` accepts an attached deposit (`caller_deposit`) from an external relayer/caller as compensation, and the accounting for whether that deposit should be refunded is decided asynchronously across several promise callbacks based on `env::promise_result(...)`. One of those callback paths (`address_check_callback`) drops the `caller_deposit` bookkeeping on a specific outcome instead of forwarding it into the refund path that all the other failure branches use.

### Finding Description
`rlp_execute` (`runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs:89-128`) computes a `CallerDeposit` from the attached deposit and, for the `EOABaseTokenTransfer` case with `address_check: Some(_)`, calls the external Address Registrar contract and attaches `address_check_callback(target, action, caller_deposit)` as its `.then()` callback [1](#0-0) .

`address_check_callback` reads `env::promise_result(0)` to learn the outcome of the registrar lookup:
- On `PromiseResult::Failed`, it immediately returns a failure `ExecuteResponse` **without ever creating a refund promise for `caller_deposit`** [2](#0-1) .
- On successful deserialization failure (`serde_json::from_slice` error) it likewise returns failure directly, again dropping `caller_deposit` [3](#0-2) .
- Only when the flow proceeds to chain another promise (`.then(ext.rlp_execute_callback(caller_deposit))`) does `caller_deposit` get forwarded into `rlp_execute_callback`, which is the *only* place that actually issues the refund transfer on `PromiseResult::Failed` [4](#0-3) .

In other words, the contract's accounting of "should this deposit be refunded" is entirely driven by which promise-result branch is taken in each callback, exactly analogous to the ERC4626 finding where the vault's return value (rather than an independent, verified balance check) determines share/asset accounting. Here, the `Failed`/error branches of `address_check_callback` never call the shared refund logic, so the deposit's fate diverges from the caller's real economic expectation (get a refund on any failure) purely because of which control-flow branch executed — there is no invariant/assertion tying `caller_deposit`'s presence to an actual refund receipt, and no code path recovers it once this callback returns a `Value` instead of chaining to `rlp_execute_callback`.

### Impact Explanation
A user/relayer who attaches a deposit while calling `rlp_execute` for an `EOABaseTokenTransfer` with an address check can have that deposit permanently lost (never refunded, never spent on the intended transfer) if the address-registrar cross-contract call fails or returns an undecodable response. This is a concrete "unfair" state-transition outcome — funds attached by an unprivileged caller become permanently unrecoverable through the normal contract API, matching the "permanently frozen funds" impact category.

### Likelihood Explanation
Any external, unprivileged caller invoking `rlp_execute` on an eth-implicit account, with `EOABaseTokenTransfer` targeting a non-implicit "named" account (`address_check: Some(_)`) and a non-zero `caller_deposit`/fee, can reach this path. The registrar lookup can fail for mundane reasons (target registrar contract missing, out of gas, deserialization mismatch, or registrar returning something unexpected) — not solely a malicious-actor prerequisite — making this reachable during normal operation, not just under adversarial conditions.

### Recommendation
Ensure every early-return branch of `address_check_callback` that does not continue the promise chain into `rlp_execute_callback` explicitly issues the same refund transfer used in `rlp_execute_callback`'s `PromiseResult::Failed` arm, so refund logic is centralized and invariant regardless of which callback observed the failure. Alternatively, always route through `rlp_execute_callback` (passing a synthetic failure) so `caller_deposit` handling has a single, provably-exhaustive code path.

### Proof of Concept
1. Deploy the Wallet Contract for an eth-implicit account and fund the Address Registrar dependency such that it is reachable but will fail (e.g., point `target` at a registrar/account that causes the registrar's `lookup` cross-contract call to fail, or have it return a payload that fails `serde_json::from_slice::<Option<AccountId>>`).
2. As an external relayer/caller, submit an `rlp_execute` transaction whose decoded action is `EthEmulationKind::EOABaseTokenTransfer` with `address_check: Some(address)` and a non-zero fee, attaching a deposit (`caller_deposit`) per `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs:340-345`.
3. Observe that `address_check_callback` is invoked with `caller_deposit` set, that the registrar promise fails or its response fails to deserialize, and that the function returns `PromiseOrValue::Value(...)` directly (lines 141-158) — no refund promise batch action is created anywhere in this branch.
4. Compare the caller's balance before and after: the deposit is neither refunded nor used for the intended action, unlike the `rlp_execute_callback::PromiseResult::Failed` branch (lines 296-311), which does refund `caller_deposit` under the same "the underlying operation failed" semantics.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L141-148)
```rust
        let maybe_account_id: Option<AccountId> = match env::promise_result(0) {
            PromiseResult::Failed => {
                return PromiseOrValue::Value(ExecuteResponse {
                    success: false,
                    success_value: None,
                    error: Some("Call to Address Registrar contract failed".into()),
                });
            }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L149-158)
```rust
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L296-311)
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
