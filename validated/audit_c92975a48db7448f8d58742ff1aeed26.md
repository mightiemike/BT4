### Title
Address Registrar accepts NEAR deposits with no withdrawal path, permanently locking user funds - (File: runtime/near-wallet-contract/implementation/address-registrar/src/lib.rs)

### Summary
The `AddressRegistrar::register` method, part of the NEAR Wallet Contract system (NEP-518, reachable via any RPC caller / unprivileged transaction signer), is `#[payable]` and accepts an attached deposit to cover storage costs when registering an `account_id -> address` mapping. On a successful registration (the `Entry::Vacant` branch), the entire `given_deposit` is retained by the contract with no check that it equals (rather than merely exceeds) `required_deposit`, and no refund of any excess is issued. Unlike the collision path, which explicitly refunds the caller (`env::promise_batch_action_transfer(refund_promise, given_deposit)`), the success path performs no such transfer.

### Finding Description
In `register` (`runtime/near-wallet-contract/implementation/address-registrar/src/lib.rs:37-86`), the contract computes a `required_deposit` based on storage bytes and validates only that `given_deposit >= required_deposit` (`:50-61`). Any amount attached above `required_deposit` is silently absorbed by the contract when the entry is successfully inserted (`:65-72`), because the vacant-entry branch never issues a refund promise — only the occupied/collision branch does (`:73-84`). [1](#0-0) 

The `AddressRegistrar` contract exposes no method to withdraw any NEAR balance held by the contract account (`register`, `lookup`, `get_address` are the only public methods), so any surplus deposit sent by a caller — whether by mistake or because a relayer/wallet integration miscalculates `required_deposit` — becomes permanently stuck in the contract with no code path for the depositor, the contract, or anyone else to reclaim it. [2](#0-1) 

This is structurally identical to the reported bug class: a payable entry point collects a fee/deposit from a caller, and the contract provides no interface to withdraw those funds afterward.

### Impact Explanation
Impact is Medium: any excess deposit attached by a caller to `register` — for example, from a relayer or wallet-integration bug that over-estimates the required storage deposit, or a user who intentionally overpays out of caution — is irrecoverably locked in the `AddressRegistrar` contract's balance. There is no supply inflation or cross-account theft, but it is a genuine permanent loss of the caller's own funds with no operator or protocol-level recovery path, matching "permanently frozen funds" in the validation criteria.

### Likelihood Explanation
Likelihood is Medium: `register` is payable and callable by any account (it is intended to be called through wallet-contract relayer flows as well as directly), and the required-deposit calculation is a simple byte-cost estimate that callers/integrators must reproduce exactly to avoid overpaying; any mismatch (e.g., rounding, a slightly different account_id length calculation, or a caller intentionally sending extra margin) results in funds being trapped with no way to retrieve them.

### Recommendation
Modify `register` so that, on successful insertion (the `Entry::Vacant` branch), any amount attached above `required_deposit` is refunded to the caller via a `promise_batch_action_transfer`, symmetric to what is already done in the `Entry::Occupied` collision branch. Alternatively, add an explicit withdrawal/refund interface so any accidentally over-attached deposits can be reclaimed by the original depositor.

### Proof of Concept
1. A relayer or user calls `AddressRegistrar::register(account_id)` with `attached_deposit = required_deposit + X` (any `X > 0`), where `required_deposit` is the exact storage cost for `account_id`.
2. Since `given_deposit >= required_deposit`, the check at `runtime/near-wallet-contract/implementation/address-registrar/src/lib.rs:54-61` passes.
3. The `address` is not already registered (fresh account), so execution takes the `Entry::Vacant` branch at `:66-72`, which inserts the mapping and returns `Some(address)` — no refund transfer is created.
4. The `X` surplus yoctoNEAR remains part of the `AddressRegistrar` contract's balance permanently; no method exists on the contract (`register`, `lookup`, `get_address`) to move that balance back out to the original caller.

### Citations

**File:** runtime/near-wallet-contract/implementation/address-registrar/src/lib.rs (L23-112)
```rust
#[near_bindgen]
impl AddressRegistrar {
    #[init]
    pub fn new() -> Self {
        Self { addresses: LookupMap::new(StorageKey::Addresses) }
    }

    /// Computes the address associated with the given `account_id` and
    /// attempts to store the mapping `address -> account_id`. If there is
    /// a collision where the given `account_id` has the same address as a
    /// previously registered one then the mapping is NOT updated and `None`
    /// is returned. Otherwise, the mapping is stored and the address is
    /// returned as a hex-encoded string with `0x` prefix.
    #[payable]
    pub fn register(&mut self, account_id: AccountId) -> Option<String> {
        // It is not allowed to register eth-implicit accounts because the purpose
        // of the registry is to allow looking up the named account associated with
        // an address obtained via hashing, but eth-implicit accounts are already
        // parsable as addresses.
        if is_eth_implicit(&account_id) {
            let log_message = format!("Refuse to register eth-implicit account {account_id}");
            env::log_str(&log_message);
            return None;
        }

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

        let address = account_id_to_address(&account_id);

        match self.addresses.entry(address) {
            Entry::Vacant(entry) => {
                let address = format!("0x{}", hex::encode(address));
                let log_message = format!("Added entry {} -> {}", address, account_id);
                entry.insert(account_id);
                env::log_str(&log_message);
                Some(address)
            }
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
    }

    /// Attempt to look up the account ID associated with the given address.
    /// If an entry for that address is found then the associated account id
    /// is returned, otherwise `None` is returned. Use the `register` method
    /// to add entries to the map.
    /// This function will panic if the given address is not the hex-encoding
    /// of a 20-byte array. The `0x` prefix is optional.
    pub fn lookup(&self, address: String) -> Option<AccountId> {
        let address = {
            let mut buf = [0u8; 20];
            hex::decode_to_slice(address.strip_prefix("0x").unwrap_or(&address), &mut buf)
                .unwrap_or_else(|_| env::panic_str("Invalid hex encoding"));
            buf
        };
        self.addresses.get(&address).cloned()
    }

    /// Computes the address associated with the given `account_id` and
    /// returns it as a hex-encoded string with `0x` prefix. This function
    /// does not update the mapping stored in this contract. If you want
    /// to register an account ID use the `register` method.
    pub fn get_address(&self, account_id: AccountId) -> String {
        let address = account_id_to_address(&account_id);
        format!("0x{}", hex::encode(address))
    }
}
```
