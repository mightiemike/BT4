Confirmed: no owner, withdraw, or excess-refund mechanism exists anywhere in `address-registrar`, so any excess deposit accepted by `register()` on the `Vacant` path is permanently locked in the contract with no code path to reclaim it.

### Title
`register()` in the Address Registrar accepts a `#[payable]` deposit but never refunds any excess above the required storage cost, permanently trapping user funds - (File: `runtime/near-wallet-contract/implementation/address-registrar/src/lib.rs`)

### Summary
The `AddressRegistrar::register` function is marked `#[payable]` and only validates that `attached_deposit >= required_deposit` [1](#0-0)  . When registration succeeds (the `Vacant` branch), it consumes the entire `given_deposit` implicitly by never issuing any transfer back to the caller, storing only the mapping and returning the computed address [2](#0-1)  . Only the collision (`Occupied`) branch refunds the caller, and even then it refunds the *entire* `given_deposit`, not the excess-over-required amount [3](#0-2)  . There is no owner-only withdrawal method or any other mechanism anywhere in the crate to move the contract's accumulated NEAR balance back out, confirmed by inspecting the full file — only `new`, `register`, `lookup`, and `get_address` are exposed [4](#0-3) .

### Finding Description
Any unprivileged caller invokes `register(account_id)` and attaches a NEAR deposit via a standard `FunctionCall` action with `deposit > 0`. The contract computes `required_deposit` from the storage bytes needed (`20 + account_id.len()`) and only checks that the attached amount is *at least* that much [1](#0-0) . If the caller attaches more than `required_deposit` — which is easy to do accidentally since the exact byte-cost formula is an implementation detail most callers won't compute precisely, or because callers conservatively over-fund to be safe — and the address slot is still `Vacant`, the excess deposit is neither transferred back nor tracked anywhere; it simply becomes part of the contract account's balance with no code path capable of later moving it out. This exactly mirrors the reported bug class: a payable entry point accepts value it has no logic to use or return, permanently locking user funds in the contract.

### Impact Explanation
This is a direct, protocol-level "permanently frozen funds" outcome triggerable by a single ordinary transaction/RPC call from any signer with no special privileges, satisfying the medium/high severity bar for unauthorized value loss (funds become irrecoverable by their original owner and unreachable by anyone, since the contract has no withdrawal function). The impact scales with every over-funded `register` call, and there is no self-service or admin recovery path in the current contract code.

### Likelihood Explanation
Likelihood is high: `register` is a public, payable method reachable directly via a standard NEAR `FunctionCall` action from any account; no special permission or race condition is required, and over-attaching a deposit is a very ordinary and even encouraged behavior when a caller isn't certain of the exact required storage byte cost (e.g., wanting to be safe against `LackBalanceForState`-style panics from under-paying).

### Recommendation
In the `Vacant` branch of `register`, compute and refund `given_deposit.checked_sub(required_deposit)` (any positive excess) back to `env::predecessor_account_id()` via `promise_batch_action_transfer`, mirroring the pattern already used in the `Occupied` branch. Alternatively, require an exact-match deposit (panic if `given_deposit != required_deposit`) so no excess can ever be attached, consistent with the "remove ability to receive un-refundable value" recommendation from the analogous report.

### Proof of Concept
1. Caller submits a `SignedTransaction` with a `FunctionCall` action targeting `AddressRegistrar::register`, `account_id = "alice.near"`, and `deposit` set to, e.g., `10x` the actual required storage cost (or simply any amount strictly greater than `required_deposit`).
2. Since `given_deposit >= required_deposit`, the check at [5](#0-4)  passes.
3. The address slot for `alice.near` is `Vacant`, so execution enters the branch at [6](#0-5) , which inserts the mapping and returns `Some(address)` without issuing any transfer back to the caller.
4. The caller's account balance has decreased by the full `given_deposit`, but the contract only "needed" `required_deposit`; the excess is now part of the `AddressRegistrar` account's NEAR balance with no method in the contract that can ever move it back out — verified by the complete absence of any `withdraw`/owner-only balance-moving function in the crate.

### Citations

**File:** runtime/near-wallet-contract/implementation/address-registrar/src/lib.rs (L1-124)
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

fn account_id_to_address(account_id: &AccountId) -> Address {
    let hash = near_sdk::env::keccak256_array(account_id.as_bytes());
    let mut result = [0u8; 20];
    result.copy_from_slice(&hash[12..32]);
    result
}

fn is_eth_implicit(account_id: &AccountId) -> bool {
    let id = account_id.as_str();
    id.len() == 42 && id.starts_with("0x") && id[2..].chars().all(|c| c.is_ascii_hexdigit())
}
```
