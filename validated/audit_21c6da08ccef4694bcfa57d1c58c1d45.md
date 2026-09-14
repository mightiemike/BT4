### Title
Excessive attached deposit permanently lost in `AddressRegistrar::register` - (File: `runtime/near-wallet-contract/implementation/address-registrar/src/lib.rs`)

### Summary
`AddressRegistrar::register` only validates that `attached_deposit >= required_deposit` and, on the successful registration path, never refunds the excess. Any caller that attaches more NEAR than the exact storage cost permanently loses the difference, since the contract exposes no withdrawal mechanism for its own balance.

### Finding Description
`register` computes the exact storage cost needed (`required_deposit`) and only guards against under-payment:

```rust
let given_deposit = env::attached_deposit();
// The caller must pay for the storage cost of registering.
if given_deposit < required_deposit {
    ...
    env::panic_str(&message);
}
``` [1](#0-0) 

When a new address is registered successfully (`Entry::Vacant`), the entire `given_deposit` is retained by the contract with no refund logic at all:

```rust
Entry::Vacant(entry) => {
    let address = format!("0x{}", hex::encode(address));
    let log_message = format!("Added entry {} -> {}", address, account_id);
    entry.insert(account_id);
    env::log_str(&log_message);
    Some(address)
}
``` [2](#0-1) 

Notably, the contract *does* refund the full deposit in the collision (`Entry::Occupied`) branch since no storage is used there, proving the developers were aware refund logic was needed — but they only implemented it for the "no storage used" case, not for "more than needed storage was paid for":

```rust
Entry::Occupied(entry) => {
    ...
    // Transfer the deposit back to the caller since no storage was updated.
    let refund_promise = env::promise_batch_create(&env::predecessor_account_id());
    env::promise_batch_action_transfer(refund_promise, given_deposit);
    None
}
``` [3](#0-2) 

The contract exposes no owner/withdraw method to recover its accumulated NEAR balance, so any amount attached above `required_deposit` on a successful call is permanently inaccessible to the sender and effectively stuck in the contract account (`AddressRegistrar` struct only holds a `LookupMap`, no admin/withdraw API) [4](#0-3) .

This is directly analogous to the reported Solidity bug class: a payable/deposit-accepting entry point checks `msg.value`/deposit with `>=` instead of `==` (or without refunding the surplus), silently accepting and losing any excess sent by a mistaken caller.

The `register` method is reachable by any unprivileged RPC caller or transaction signer directly, e.g. as demonstrated in the existing test `test_register_without_deposit`, which attaches an arbitrary deposit amount and shows the balance increases by at least (not exactly) `deposit_amount`:

```rust
let deposit_amount = NearToken::from_yoctonear(320000000000000000000);
let result = worker
    .root_account()?
    .call(address_registrar.id(), method)
    .args(args.to_vec())
    .deposit(deposit_amount)
    .transact()
    .await?;
...
assert!(
    post_tx_account_balance.as_yoctonear() - pre_tx_account_balance.as_yoctonear()
        >= deposit_amount.as_yoctonear()
);
``` [5](#0-4) 

### Impact Explanation
Any excess NEAR attached above the exact storage cost on a successful `register` call is permanently absorbed by the contract with no way for the sender to recover it and no admin/withdraw entry point to redistribute it. This is a direct, concrete loss of user funds triggered by a single ordinary transaction/RPC call — satisfying the "permanently frozen/lost funds" impact class.

### Likelihood Explanation
Likelihood is low-to-moderate: it requires a caller (a user, or more commonly relaying/wrapper logic such as the Wallet Contract's NEP-141 flow that attaches deposits programmatically) to overestimate the required storage deposit when invoking `register`. Since `required_deposit` depends on `account_id.len()`, callers not computing the exact value precisely (e.g., using a conservative upper-bound constant) will systematically overpay and lose the difference on every successful registration.

### Recommendation
Refund the excess over `required_deposit` on the success path, mirroring the pattern already used in the collision branch:

```diff
 Entry::Vacant(entry) => {
     let address = format!("0x{}", hex::encode(address));
     let log_message = format!("Added entry {} -> {}", address, account_id);
     entry.insert(account_id);
     env::log_str(&log_message);
+    let excess = given_deposit.saturating_sub(required_deposit);
+    if excess > NearToken::from_yoctonear(0) {
+        let refund_promise = env::promise_batch_create(&env::predecessor_account_id());
+        env::promise_batch_action_transfer(refund_promise, excess);
+    }
     Some(address)
 }
```

### Proof of Concept
1. Call `AddressRegistrar::register("alice.near")` attaching a deposit strictly greater than `storage_byte_cost * (20 + len("alice.near"))`.
2. Observe the call succeeds and returns the derived address.
3. Query the caller's account balance before/after: the balance decreases by the full attached deposit (not just `required_deposit`), and there is no method on `AddressRegistrar` to reclaim the surplus — the excess is permanently stuck in the contract's balance, as already implicitly demonstrated by the existing `test_register_without_deposit` test at [6](#0-5) .

### Citations

**File:** runtime/near-wallet-contract/implementation/address-registrar/src/lib.rs (L16-28)
```rust
#[near_bindgen]
#[derive(PanicOnDefault, BorshDeserialize, BorshSerialize)]
#[borsh(crate = "near_sdk::borsh")]
pub struct AddressRegistrar {
    pub addresses: LookupMap<Address, AccountId>,
}

#[near_bindgen]
impl AddressRegistrar {
    #[init]
    pub fn new() -> Self {
        Self { addresses: LookupMap::new(StorageKey::Addresses) }
    }
```

**File:** runtime/near-wallet-contract/implementation/address-registrar/src/lib.rs (L49-61)
```rust
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

**File:** runtime/near-wallet-contract/implementation/address-registrar/src/lib.rs (L73-84)
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
```
