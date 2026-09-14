### Title
Excess attached deposit is not refunded on successful `register()`, permanently locking user funds - (File: `runtime/near-wallet-contract/implementation/address-registrar/src/lib.rs`)

### Summary
The `AddressRegistrar::register` method (used by the NEAR wallet contract's Ethereum-emulation address-lookup flow) validates that `attached_deposit >= required_deposit`, matching the same pattern flagged in the external report (`msg.value >= price`). On the success path it never refunds the difference between what the caller attached and what was actually required for storage — only the failure/collision path refunds the deposit. Any excess deposit sent by the caller is silently absorbed into the contract's balance and is unrecoverable by the caller.

### Finding Description
`register()` computes the exact storage cost needed to persist a 20-byte address mapping plus the given `account_id`, and only checks that the attached deposit is *at least* that amount: [1](#0-0) 

If the entry is new (`Entry::Vacant`), the mapping is inserted and the function returns without ever comparing `given_deposit` to `required_deposit` again or refunding the difference: [2](#0-1) 

By contrast, on the collision path (`Entry::Occupied`) the *entire* `given_deposit` is refunded via a transfer promise back to `predecessor_account_id`: [3](#0-2) 

This is structurally identical to the reported `HeroMarketplace.purchase` bug: a `>=` comparison against a required amount is used as the acceptance gate, but the full attached value—not just the required amount—is retained/used, with no refund path for the surplus on the success branch. Any predecessor (an ordinary NEAR account, or the Wallet Contract acting as predecessor when processing an eth-emulated `register` call) that attaches more than the minimal storage deposit permanently loses the excess to the `AddressRegistrar` contract's balance, with no code path to reclaim it.

### Impact Explanation
This results in permanent, unrecoverable loss of user funds for any account (or for the wallet-contract instance acting on behalf of its owner) that overpays the storage deposit when calling `register`. Because the excess deposit is added to the contract's `amount` with no accounting or refund mechanism, it is effectively frozen — the contract exposes no withdrawal method for arbitrary surplus balance. This satisfies the "permanently frozen funds" impact category from the validation criteria.

### Likelihood Explanation
Likelihood is high in practice: callers (including the Wallet Contract's internal flows that invoke `register` as part of eth-implicit address registration, see `runtime/near-wallet-contract/implementation/wallet-contract/src/tests/sanity.rs` `test_caller_refunds`) have no strong guidance to attach the exact minimal deposit, and the contract itself never computes/returns the exact required amount to the caller beforehand. Any caller attaching a round NEAR amount (e.g., 1 NEAR) instead of the exact yoctoNEAR-precise storage cost will lose the difference on every successful call.

### Recommendation
On the success (`Entry::Vacant`) path, compute `excess = given_deposit.checked_sub(required_deposit)` and, if non-zero, issue a transfer promise refunding `excess` back to `env::predecessor_account_id()`, mirroring the refund already implemented in the collision (`Entry::Occupied`) branch. This ensures only the exact storage cost is retained by the contract.

### Proof of Concept
1. An account (or the Wallet Contract acting as predecessor) calls `AddressRegistrar::register("alice.near")` attaching `1 NEAR` as deposit.
2. `required_deposit` is computed as `storage_byte_cost * (20 + len("alice.near"))`, which is on the order of `~10^-2` NEAR or less — far smaller than the attached `1 NEAR`.
3. `given_deposit (1 NEAR) >= required_deposit` passes the check at lines 54-61.
4. The mapping is inserted (`Entry::Vacant` branch, lines 65-72); no refund promise is created.
5. The caller's account balance is now permanently reduced by `1 NEAR`, of which only the small `required_deposit` was actually needed; the surplus remains stuck in the `AddressRegistrar` contract's balance with no way for the caller to reclaim it.

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
