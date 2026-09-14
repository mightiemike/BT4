## Finding: Address Registrar `register()` Never Refunds Excess Attached Deposit — Permanently Locked User Funds

### Title
Overpaid deposit in `AddressRegistrar::register()` is permanently locked when the address slot is vacant — ([File: runtime/near-wallet-contract/implementation/address-registrar/src/lib.rs])

### Summary
`AddressRegistrar::register()` is a `#[payable]` method that only validates a *lower bound* on the attached deposit (`given_deposit < required_deposit`) but never checks or refunds the *excess* when `given_deposit > required_deposit` and the address slot is vacant. Any caller who attaches more NEAR than the exact storage cost permanently loses the difference, with no method in the contract to recover it — mirroring the reported `receiveFunds()` pattern where a missing require/refund check on a value transfer causes silent fund loss.

### Finding Description
In `register()`, the required deposit is computed from the exact number of bytes needed to store the `Address -> AccountId` mapping: [1](#0-0) 

The check only guards against *under*-payment (`given_deposit < required_deposit` → panic, which correctly refunds the whole deposit since a panic reverts and refunds `attached_deposit` to `predecessor_account_id`). There is no corresponding branch for over-payment.

When the address slot is vacant, the contract stores the entry and returns, without ever transferring back `given_deposit - required_deposit`: [2](#0-1) 

Contrast this with the `Entry::Occupied` (collision) branch, which explicitly demonstrates that the authors know how to refund a deposit via `promise_batch_action_transfer`, but only does so for the *entire* deposit in the collision case — not for the excess in the success case: [3](#0-2) 

The contract exposes no `withdraw`, `refund`, or owner-controlled extraction method anywhere in the file, so any yoctoNEAR retained beyond `required_deposit` becomes permanently and irrecoverably part of the contract's balance, unreachable by the depositor or anyone else. [4](#0-3) 

This is structurally the same bug class as the reported `receiveFunds()` issue: a function accepts a value transfer alongside other logic, validates only one side of the expected equality/bound, and silently swallows the mismatched portion instead of requiring an exact match or refunding the difference.

### Impact Explanation
Any unprivileged account that calls `register(account_id)` with `attached_deposit` even slightly larger than the exact computed storage cost (e.g. due to using a generous manual estimate, a wallet UI rounding up, or simply reusing a deposit amount from a previous, longer `account_id`) permanently forfeits the excess NEAR to the contract. Because there is no admin/owner and no withdrawal path, these funds are locked forever — this matches the "permanently frozen funds" impact category. This affects every unique, non-colliding registration call, so funds loss can accumulate across many callers who overestimate their storage deposit.

### Likelihood Explanation
Likelihood is high: the register method is a normal, un-privileged, `#[payable]` public entry point reachable directly by any transaction signer via a function-call action; no special conditions are needed beyond attaching more than the minimal required deposit, which is a very natural and easy mistake for callers/wallet integrations to make (e.g. attaching a round number like 1 NEAR "to be safe").

### Recommendation
After successfully inserting a new entry (the `Entry::Vacant` branch), compute `excess = given_deposit - required_deposit` and, if `excess > 0`, issue a `promise_batch_action_transfer` refund to `env::predecessor_account_id()`, exactly as already done in the `Entry::Occupied` collision branch. Alternatively, require an exact deposit match (`given_deposit != required_deposit` → panic) so wallets/clients are forced to compute and send the precise amount.

### Proof of Concept
1. Caller `alice.near` calls `register("bob.near")` on `AddressRegistrar`, attaching `1 NEAR` as `given_deposit`.
2. `required_deposit` is computed as `storage_byte_cost * (20 + len("bob.near"))`, which is far less than `1 NEAR` (a few hundred bytes of storage cost, i.e. a tiny fraction of a NEAR).
3. `given_deposit >= required_deposit`, so the under-payment check passes.
4. The address slot for `bob.near`'s derived address is vacant, so the `Entry::Vacant` branch executes: the mapping is inserted, `Some(address)` is returned, and the function ends.
5. No promise transferring `given_deposit - required_deposit` back to `alice.near` is ever created.
6. `alice.near` has permanently lost `1 NEAR - required_deposit` yoctoNEAR, now stuck in the `AddressRegistrar` contract's balance with no method in the contract to retrieve it.

### Citations

**File:** runtime/near-wallet-contract/implementation/address-registrar/src/lib.rs (L1-21)
```rust
use near_sdk::{
    borsh::{BorshDeserialize, BorshSerialize},
    env, near_bindgen,
    store::{lookup_map::Entry, LookupMap},
    AccountId, BorshStorageKey, NearToken, PanicOnDefault,
};

type Address = [u8; 20];

#[derive(BorshSerialize, BorshStorageKey)]
#[borsh(crate = "near_sdk::borsh")]
enum StorageKey {
    Addresses,
}

#[near_bindgen]
#[derive(PanicOnDefault, BorshDeserialize, BorshSerialize)]
#[borsh(crate = "near_sdk::borsh")]
pub struct AddressRegistrar {
    pub addresses: LookupMap<Address, AccountId>,
}
```

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
