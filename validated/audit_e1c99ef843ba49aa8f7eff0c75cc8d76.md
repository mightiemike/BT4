Confirmed. Now I have enough to finalize the analog.

### Title
Excess attached deposit permanently lost on successful `register` call in the NEAR Address Registrar contract - ([File: runtime/near-wallet-contract/implementation/address-registrar/src/lib.rs])

### Summary
The `AddressRegistrar::register` method, part of the NEAR Wallet Contract implementation, validates that the caller's `attached_deposit` is *at least* the required storage cost, but only refunds the deposit back to the caller on the collision (failure) path. On the success (new registration) path, any excess deposit above the exact storage cost is silently kept by the contract with no refund and no withdrawal mechanism, permanently losing the caller's excess funds — the same overpayment-loss pattern described in the Allo.sol report.

### Finding Description
In [1](#0-0) , `register` computes the exact `required_deposit` needed to store the new `(address -> account_id)` entry, then only checks `given_deposit < required_deposit` to reject underpayment — it does not require or enforce an exact match, and does not compute/refund the difference when `given_deposit > required_deposit`.

On the success path, `Entry::Vacant`, shown at [2](#0-1) , the entry is inserted and the full `given_deposit` (attached NEAR balance transferred via `#[payable]`) is retained by the contract's account balance, with no logic to compute or send back `given_deposit - required_deposit`.

By contrast, on the collision path, `Entry::Occupied`, at [3](#0-2) , the code explicitly refunds the caller's *entire* `given_deposit` via a `Transfer` promise back to `predecessor_account_id`, because no storage was consumed. This asymmetry confirms the missing-refund on the success path is unintentional: the developer clearly intended deposits to only cover exact storage cost, but forgot to refund the excess on the success branch.

There is no `withdraw`, owner-only sweep, or any other method in this contract (verified by inspecting the full contract — only `new`, `register`, `lookup`, `get_address` exist) that would allow recovering these stranded tokens later. The excess balance simply accumulates on the contract's account, unreachable by any account.

This is directly analogous to the Allo.sol `_createPool` bug: a `#[payable]`/attached-deposit function checks `attached >= required` instead of `attached == required` or refunding the delta, and only refunds in the failure branch, not the success branch — causing user overpayment to be permanently lost.

### Impact Explanation
Any unprivileged account (including a NEAR Wallet Contract user or relayer acting on their behalf, or any generic RPC caller submitting a `FunctionCall` transaction) that calls `register` with an attached deposit larger than the exact per-byte storage cost will have the excess yoctoNEAR permanently and unrecoverably locked in the `AddressRegistrar` contract's balance. This is a concrete "permanently frozen funds" outcome from a single, unprivileged transaction — no malicious node, validator, or off-chain actor is required.

### Likelihood Explanation
Likelihood is high in practice: callers commonly attach round, conservative deposit amounts (e.g. `NearToken::from_near(1)`, or `deposit_amount` from a wallet UI) rather than computing the exact per-byte storage price, since the exact cost depends on the dynamic `env::storage_byte_cost()` and account-id length. Any caller who overestimates, even slightly, loses the difference with no recourse, on the very common success path (new address registration), which is the primary intended use of the contract.

### Recommendation
On the `Entry::Vacant` success path, compute `excess = given_deposit - required_deposit` and, if `excess > 0`, issue a `promise_batch_action_transfer` back to `env::predecessor_account_id()` refunding the excess, mirroring the refund logic already present in the `Entry::Occupied` branch. Alternatively, require `given_deposit == required_deposit` exactly (rejecting anything else) so callers must pre-compute the precise amount, or add an owner-only sweep/withdraw method as a fallback safety valve.

### Proof of Concept
1. A caller determines/estimates `account_id` of length `L`, and knows `storage_byte_cost()` at call time, but attaches a deposit of, say, `2x` the exact `required_deposit = storage_byte_cost() * (20 + L)` "to be safe."
2. Caller submits a `FunctionCall` transaction (or the NEAR Wallet Contract does so via `promise_batch_action_function_call` on the caller's behalf) invoking `register(account_id)` on the deployed `AddressRegistrar` account with the inflated attached deposit.
3. Since `address_to_address(account_id)` is not yet registered, `self.addresses.entry(address)` matches `Entry::Vacant`; the mapping is inserted and `register` returns `Some(address)` — see [2](#0-1) .
4. No refund receipt is generated for the difference between the attached deposit and `required_deposit`; the entire deposit becomes part of the `AddressRegistrar` account's NEAR balance.
5. Querying the contract's account balance afterward shows it increased by the *full* `given_deposit`, not just `required_deposit`; there is no method on the contract to withdraw or reclaim that surplus, so it is permanently stuck.

### Citations

**File:** runtime/near-wallet-contract/implementation/address-registrar/src/lib.rs (L48-61)
```rust
        // Must store the address and the account id
        let bytes_to_store = 20 + (account_id.len() as u128);
        let required_deposit =
            NearToken::from_yoctonear(env::storage_byte_cost().as_yoctonear() * bytes_to_store);
        let given_deposit = env::attached_deposit();
        // The caller must pay for the storage cost of registering.
        if given_deposit < required_deposit {
            let message = format!(
                "Insufficient deposit to cover storage cost. Given={} Expected={}",
                given_deposit.as_yoctonear(),
                required_deposit.as_yoctonear(),
            );
            env::panic_str(&message);
        }
```

**File:** runtime/near-wallet-contract/implementation/address-registrar/src/lib.rs (L65-72)
```rust
        match self.addresses.entry(address) {
            Entry::Vacant(entry) => {
                let address = format!("0x{}", hex::encode(address));
                let log_message = format!("Added entry {} -> {}", address, account_id);
                entry.insert(account_id);
                env::log_str(&log_message);
                Some(address)
            }
```

**File:** runtime/near-wallet-contract/implementation/address-registrar/src/lib.rs (L73-85)
```rust
            Entry::Occupied(entry) => {
                let log_message = format!(
                    "Address collision between {} and {}. Keeping the former.",
                    entry.get(),
                    account_id
                );
                env::log_str(&log_message);
                // Transfer the deposit back to the caller since no storage was updated.
                let refund_promise = env::promise_batch_create(&env::predecessor_account_id());
                env::promise_batch_action_transfer(refund_promise, given_deposit);
                None
            }
        }
```
