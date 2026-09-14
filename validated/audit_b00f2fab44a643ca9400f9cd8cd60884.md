### Title
Excess `attached_deposit` sent to `AddressRegistrar::register()` is never refunded and becomes permanently stuck - (File: `runtime/near-wallet-contract/implementation/address-registrar/src/lib.rs`)

### Summary
`AddressRegistrar::register()` validates that the caller's `attached_deposit` is at least the storage cost required to persist the new `address -> account_id` mapping, but on the success path it never returns any excess deposit to the caller. The contract has no owner/withdraw function, so any amount sent above the exact storage requirement is permanently locked in the contract's account balance.

### Finding Description
The `register` function computes a `required_deposit` from the storage bytes needed and compares it against `given_deposit = env::attached_deposit()`: [1](#0-0) 

If `given_deposit < required_deposit` it panics (and the whole deposit is auto-refunded by the runtime because the receipt fails). However, when the deposit is sufficient (`given_deposit >= required_deposit`) and a *new* mapping is inserted (the `Entry::Vacant` branch), the function only inserts the entry and returns the address — it never computes or refunds `given_deposit - required_deposit`: [2](#0-1) 

Interestingly, the `Entry::Occupied` (collision) branch *does* refund the caller's full deposit via a transfer promise, showing the developers were aware refunds are needed, but omitted it for the success path: [3](#0-2) 

Because NEAR's runtime credits `attached_deposit` directly to the receiving account's balance before contract execution begins (per the deposit refund model), any amount not explicitly forwarded back to the caller stays on the `AddressRegistrar` contract's account. The contract exposes no `withdraw` or owner-controlled method to move that balance back out, so overpayment is unrecoverable by the original sender.

### Impact Explanation
Any unprivileged caller who overestimates the required storage deposit (e.g., attaches a round number like 1 NEAR when only a fraction is required) permanently loses the difference — it becomes indistinguishable from the contract's own locked storage-staking balance, with no method exposed to return it. This is a direct, transaction-triggered, guaranteed loss of user funds analogous to the reported Solidity issue of excess ETH being trapped in `mintPlayers()`.

### Likelihood Explanation
This triggers on every single `register()` call where the caller attaches more than the exact required deposit, which is the common case since callers typically cannot easily compute `storage_byte_cost * (20 + account_id.len())` off-chain and will round up. No special privileges, timing, or state are needed — it's a straightforward, single-transaction call.

### Recommendation
After successfully inserting the new mapping (`Entry::Vacant` branch), compute `let excess = given_deposit.saturating_sub(required_deposit);` and if `excess > 0`, issue a transfer promise back to `env::predecessor_account_id()`, mirroring the refund logic already implemented in the `Entry::Occupied` branch.

### Proof of Concept
1. Call `register(account_id)` with `attached_deposit` = 1 NEAR while the actual required storage deposit (`storage_byte_cost * (20 + account_id.len())`) is a small fraction of 1 NEAR (e.g., a few hundred microNEAR).
2. The call succeeds (`Entry::Vacant` path), the mapping is inserted, and `Some(address)` is returned.
3. Check the caller's account balance before/after: the caller is charged the full 1 NEAR, not just `required_deposit`.
4. Inspect the `AddressRegistrar` contract's account balance: it now holds the full 1 NEAR, with no method available to return the excess `1 NEAR - required_deposit` to the original caller.

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
