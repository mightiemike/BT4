## Title
Address Registrar `register()` permanently locks excess attached deposit with no refund or recovery mechanism - (File: `runtime/near-wallet-contract/implementation/address-registrar/src/lib.rs`)

### Summary
The `AddressRegistrar::register` method is a `#[payable]` function that requires callers to attach a deposit covering the storage cost of a new address→account mapping. On the successful (vacant-entry) path, it consumes the *entire* attached deposit rather than only the `required_deposit`, and never refunds the surplus. Any caller who overpays gets that overpayment permanently trapped in the contract, with no withdraw/rescue function available to recover it — the same bug class as the reported Optimism `donateETH`/no-rescue issue.

### Finding Description
`register` computes the minimal storage cost needed (`required_deposit`) and checks `given_deposit >= required_deposit`, panicking if insufficient: [1](#0-0) 

When the entry is vacant (i.e., registration succeeds), it inserts the mapping and returns the address, but at no point compares `given_deposit` to `required_deposit` to refund the difference: [2](#0-1) 

Notably, the contract *does* implement a refund path — but only for the collision (`Entry::Occupied`) case, where it transfers back the full `given_deposit` since no storage was written: [3](#0-2) 

This asymmetry shows the developers were aware refunds are needed when the deposit isn't fully "spent," but omitted the equivalent excess-refund logic for the success path. The contract exposes only `new`, `register`, `lookup`, and `get_address` — there is no owner-only or public withdrawal function to recover stuck balance: [4](#0-3) 

The existing test suite only verifies that the *minimum* required deposit is charged and that duplicate registrations don't retake funds; it never exercises or asserts refund behavior for an over-attached deposit on first (successful) registration: [5](#0-4) 

### Impact Explanation
Any account — an ordinary transaction signer, or the NEAR Wallet Contract acting for an Ethereum-emulated user via `rlp_execute` calling into `register` — can attach more NEAR than `required_deposit` when registering an address mapping. The excess amount is silently absorbed into the `AddressRegistrar` contract's balance and becomes permanently unrecoverable, since no function exists to withdraw arbitrary/excess balance from the contract. This is a concrete, permanent loss of user funds triggered by a single transaction, matching the "permanently frozen funds" acceptance criterion.

### Likelihood Explanation
`register` is a public, `#[payable]` method with no restriction on the caller. Users (or relayers acting through the Wallet Contract's Ethereum-transaction emulation path) commonly attach round, conservative deposit amounts (e.g., "0.001 NEAR") rather than computing the exact per-byte storage cost, making overpayment a realistic, easy-to-trigger scenario rather than a contrived edge case.

### Recommendation
In the `Entry::Vacant` branch of `register`, compute `given_deposit.checked_sub(required_deposit)` and, if positive, issue a `promise_batch_action_transfer` refund of the surplus back to `env::predecessor_account_id()`, mirroring the refund logic already used in the `Entry::Occupied` branch. Alternatively, add an owner-restricted `withdraw_excess` method to rescue balance beyond what's required for tracked storage.

### Proof of Concept
1. Deploy/initialize `AddressRegistrar` (`new`).
2. Call `register({"account_id": "alice.near"})` attaching e.g. `1 NEAR` while `required_deposit` (based on `storage_byte_cost * (20 + len(account_id))`) is only a few hundred yoctoNEAR-equivalent (fractions of a millinear).
3. Registration succeeds (`Entry::Vacant` branch), returning the hex address.
4. Inspect the contract's account balance before/after — the entire `1 NEAR` deposit is retained, though only `required_deposit` was needed; the surplus (~`1 NEAR - required_deposit`) is now stuck, since there is no method in the contract to withdraw it back to the original caller or any account.

### Citations

**File:** runtime/near-wallet-contract/implementation/address-registrar/src/lib.rs (L16-112)
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/tests/sanity.rs (L249-296)
```rust
/// Test asserting the address registrar requires a deposit.
#[tokio::test]
async fn test_register_without_deposit() -> anyhow::Result<()> {
    let TestContext { worker, address_registrar, .. } = TestContext::new().await?;

    let method = "register";
    let args = br#"{"account_id": "birchmd.near"}"#;
    let result = address_registrar.call(method).args(args.to_vec()).transact().await?;
    assert!(result.is_failure(), "Call without deposit must fail");

    let pre_tx_account_balance = address_registrar.as_account().view_account().await?.balance;
    let deposit_amount = NearToken::from_yoctonear(320000000000000000000);
    let result = worker
        .root_account()?
        .call(address_registrar.id(), method)
        .args(args.to_vec())
        .deposit(deposit_amount)
        .transact()
        .await?;

    let output: Option<String> = result.json()?;
    assert_eq!(output.as_deref(), Some("0x4bfcff9a964925adf801c866f6ada98bd7ec40ca"));
    let post_tx_account_balance = address_registrar.as_account().view_account().await?.balance;
    assert!(
        post_tx_account_balance.as_yoctonear() - pre_tx_account_balance.as_yoctonear()
            >= deposit_amount.as_yoctonear()
    );

    // Sending a duplicate transaction does not take the deposit again.
    let pre_tx_account_balance = post_tx_account_balance;
    let result = worker
        .root_account()?
        .call(address_registrar.id(), method)
        .args(args.to_vec())
        .deposit(deposit_amount)
        .transact()
        .await?;

    let output: Option<String> = result.json()?;
    assert_eq!(output, None);
    let post_tx_account_balance = address_registrar.as_account().view_account().await?.balance;
    assert!(
        post_tx_account_balance.as_yoctonear() - pre_tx_account_balance.as_yoctonear()
            < deposit_amount.as_yoctonear()
    );

    Ok(())
}
```
