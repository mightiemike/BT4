### Title
Excess deposit sent to `AddressRegistrar::register()` on success is permanently locked with no refund - (File: `runtime/near-wallet-contract/implementation/address-registrar/src/lib.rs`)

### Summary
`AddressRegistrar::register()` is a `#[payable]` method that any unprivileged account can call via a single `FunctionCall` action. It requires the caller to attach at least `required_deposit` (the storage cost of the new entry), but on the success path (`Entry::Vacant`) it never refunds any deposit in excess of `required_deposit`. That surplus silently becomes part of the contract's balance with no mechanism in the contract to recover or reclaim it, i.e. it is permanently frozen. This mirrors the D3Proxy `buyTokens()`/`refundETH()` bug class in spirit: a user overpays for an action whose exact required amount is only knowable after the call, and the excess is not returned to them — except here nearcore's analog is worse in one respect (no way to reclaim at all, rather than being reclaimable by anyone).

### Finding Description
`register()` computes the exact storage cost required to add the new `address -> account_id` mapping: [1](#0-0) 

It only validates that `given_deposit >= required_deposit` and panics otherwise; it never checks equality or computes/returns any excess. In the success branch (`Entry::Vacant`), the mapping is inserted and the function returns without creating any refund promise for the difference `given_deposit - required_deposit`: [2](#0-1) 

Contrast this with the `Entry::Occupied` (collision) branch, which explicitly creates a transfer promise refunding the entire `given_deposit` back to `env::predecessor_account_id()` because no storage was written: [3](#0-2) 

The asymmetry shows the refund logic exists in the codebase but was omitted for the success path. Since `#[payable]` deposits are captured by the runtime into the contract's account balance before execution starts (per the deposit/attached_deposit semantics documented in `docs/RuntimeSpec/Components/BindingsSpec/EconomicsAPI.md`), any excess over `required_deposit` becomes indistinguishable extra balance on the `AddressRegistrar` account. There is no other method on this contract (`lookup`, `get_address`, `new`) that transfers balance out, so the excess is unrecoverable by the depositor — a permanently frozen-funds condition triggered entirely by a normal, unprivileged transaction.

### Impact Explanation
Any account calling `register()` with more NEAR attached than the exact computed `required_deposit` (e.g., due to client-side estimation error, rounding, or simply attaching a round number like 1 NEAR for safety) permanently loses the difference. This is directly analogous to the reported medium-severity issue where users routinely overestimate the exact payment needed and lose the surplus — the impact category (permanently frozen funds from an ordinary user transaction) is the same, even though the underlying mechanism (locked in contract vs. stolen by a third party) differs slightly.

### Likelihood Explanation
`storage_byte_cost` and the exact number of bytes needed (`20 + account_id.len()`) are computable off-chain, but any caller that rounds up their attached deposit (which is the natural safe behavior to avoid the `Insufficient deposit` panic) will trigger this loss on every successful `register()` call. This makes the likelihood high for any real-world usage pattern that doesn't compute the byte-exact deposit.

### Recommendation
In the `Entry::Vacant` success branch of `register()`, compute `given_deposit.checked_sub(required_deposit)` and, if positive, create a transfer promise refunding the excess to `env::predecessor_account_id()`, mirroring the refund already implemented in the `Entry::Occupied` branch.

### Proof of Concept
1. Deploy `AddressRegistrar` and call `new()`.
2. Compute `required_deposit` for a target `account_id` (e.g., `alice.near`, `bytes_to_store = 20 + 10 = 30`), giving `required_deposit = storage_byte_cost * 30`.
3. Call `register({"account_id": "alice.near"})` attaching `required_deposit + 1 NEAR`.
4. Observe: the call succeeds (`Some(address)` returned, mapping stored), but the contract's account balance permanently retains the extra `1 NEAR` — no receipt/promise is generated to return it, and no other contract method can move it out. [4](#0-3)

### Citations

**File:** runtime/near-wallet-contract/implementation/address-registrar/src/lib.rs (L36-86)
```rust
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
```
