### Title
`AddressRegistrar::register` allows overpayment of storage deposit that is permanently stuck with no refund path - (File: `runtime/near-wallet-contract/implementation/address-registrar/src/lib.rs`)

### Summary
The `AddressRegistrar` contract, part of the NEAR Wallet Contract infrastructure, exposes a `#[payable]` `register` method that any transaction signer or the Wallet Contract itself can invoke with an attached deposit. The deposit sufficiency check uses `given_deposit < required_deposit` (i.e., accepts `>=`), but on the success path (new address registered) the contract keeps the *entire* `given_deposit` instead of refunding the difference between what was sent and the exact storage cost. There is no owner/withdraw function in the contract, so any excess deposit becomes permanently locked NEAR with no path to recovery — the exact bug class described in the reference report (overpayment allowed by a `>=`-style check, but only the exact required amount is used/returned, stranding the rest).

### Finding Description
`register` computes the exact storage cost required for the new entry and validates the caller sent enough: [1](#0-0) 

If the check passes and the address slot is vacant (the common, successful case), the code inserts the mapping and returns — without ever using the excess portion of `given_deposit` or refunding it to the caller: [2](#0-1) 

Note that the *only* place the contract creates a refund promise is the `Entry::Occupied` (collision) branch, and even there it refunds the *entire* `given_deposit`, not the excess over the required amount: [3](#0-2) 

There is no owner, admin, or withdraw method anywhere in this contract (confirmed by inspecting the entire file — the struct only holds the `LookupMap`, and the only public methods are `register`, `lookup`, and `get_address`). Consequently, any yoctoNEAR sent above the exact `required_deposit` on a successful registration is permanently and irrecoverably locked in the `AddressRegistrar` account's balance. This mirrors the reported Solidity bug precisely: a `>=`-style deposit check combined with only the exact fee being consumed/distributed, with no excess-refund logic on the success path.

This contract is reachable via a plain, unprivileged NEAR transaction: `register` is a public `#[payable]` method with no access restriction, callable directly by any account (transaction signer or RPC caller submitting a `FunctionCall` action), and is also invoked internally by the Wallet Contract's cross-contract-call flow (`address_check_callback` in `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`) when resolving eth-implicit target addresses during RLP-emulated transaction execution.

### Impact Explanation
Any accidental or intentional overpayment (e.g., a caller passing a deposit rounded up, or a relayer/wallet-contract flow forwarding a deposit that is not computed to the exact yoctoNEAR) results in permanently frozen funds with no possibility of restitution to the depositor or recovery by anyone (no owner-only sweep function exists). This satisfies the "permanently frozen funds" impact category defined in scope. The magnitude is bounded by the difference between attached deposit and the exact per-entry storage cost, but because there is no cap on how much a caller can overpay, the amount at risk per call is unbounded from the protocol's perspective and cannot be recovered by governance or the depositor.

### Likelihood Explanation
Moderate. This does not require a malicious validator, network condition, or adversarial timing (unlike the referenced report's Ethereum fee-reduction race). Any regular NEAR account can trigger the loss with a single `FunctionCall` transaction attaching more than `storage_byte_cost * (20 + account_id.len())` yoctoNEAR to `register`. Rounding, UI-side deposit estimation errors, or slight miscalculation of `account_id.len()` byte-cost by an integrating wallet/relayer are realistic real-world triggers, making this a plausible unintentional-user-loss scenario reachable purely by a single unprivileged transaction.

### Recommendation
In the `Entry::Vacant` success branch of `register`, compute `excess = given_deposit.checked_sub(required_deposit)` and, if non-zero, issue a `promise_batch_action_transfer` refund of `excess` to `env::predecessor_account_id()`, mirroring what is already done (though not correctly limited to the excess) in the `Entry::Occupied` branch. Alternatively, require an exact-match deposit and panic on overpayment, forcing callers to compute the exact fee.

### Proof of Concept
1. Deploy `AddressRegistrar` (as done in the existing test harness, e.g. `TestContext::new()` in `runtime/near-wallet-contract/implementation/wallet-contract/src/tests/sanity.rs`).
2. From any account, call:
   ```
   address_registrar.register({ account_id: "alice.near" })
   ```
   attaching a deposit deliberately larger than the exact required storage cost, e.g. `required_deposit + 1 NEAR`.
3. Observe: the call succeeds (`Some(address)` returned, entry inserted).
4. Query the `AddressRegistrar` account's balance before and after the call — it increased by the *full* attached deposit, not just `required_deposit`.
5. There is no method on the contract (owner-only or otherwise) to withdraw the surplus; the excess 1 NEAR is permanently stuck.

This is directly analogous to the PoC in the reference report demonstrating that `EntityForging`'s `forgeWithListed` accepts `msg.value >= forgingFee` but only forwards `forgingFee`, stranding the difference — here `register` accepts `given_deposit >= required_deposit` but only "uses" `required_deposit`, stranding the difference with zero recovery path.

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

**File:** runtime/near-wallet-contract/implementation/address-registrar/src/lib.rs (L63-72)
```rust
        let address = account_id_to_address(&account_id);

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
