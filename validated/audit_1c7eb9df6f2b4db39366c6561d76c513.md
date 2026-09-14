## Analog Found

### Title
Excess attached deposit sent to `AddressRegistrar::register()` is permanently and unrecoverably locked in the contract on a successful registration - (File: `runtime/near-wallet-contract/implementation/address-registrar/src/lib.rs`)

### Summary
The `register()` method of the `AddressRegistrar` contract (used by the NEAR Wallet Contract system to map Ethereum-style addresses to NEAR account IDs) is `#[payable]` and only checks that the attached deposit is *at least* the storage cost, but never refunds the excess when a new mapping is successfully inserted. Any caller who overpays gets no refund in the success path, and the contract exposes no withdrawal/owner function to reclaim the surplus, so the excess balance is permanently stranded.

### Finding Description
In `register()`, the deposit check only enforces a lower bound: [1](#0-0) 

When the address is new (`Entry::Vacant`), the entry is inserted and the function returns without ever refunding the difference between `given_deposit` and `required_deposit`: [2](#0-1) 

By contrast, in the collision path (`Entry::Occupied`), the code explicitly refunds the caller's *entire* deposit via `promise_batch_action_transfer`, showing the developers were aware refunds are needed for balance correctness, but they only implemented it for the failure branch, not the success branch's excess-payment case: [3](#0-2) 

The `AddressRegistrar` struct exposes no admin/owner withdrawal method — only `new`, `register`, `lookup`, and `get_address`: [4](#0-3) [5](#0-4) 

This contract is directly reachable by any unprivileged caller: it is called both by ordinary `FunctionCall` transactions (any RPC caller with an account) and by the Wallet Contract's RLP-relayed cross-contract call flow used for meta-transaction-style execution, as seen in `WalletContract`'s `ext_registrar` binding and callback handling: [6](#0-5) 

Existing tests only assert that the balance increase is `>= deposit_amount` in the success case and never assert an upper bound / refund of the surplus, which is consistent with (and masks) this bug: [7](#0-6) 

This maps to the same bug class described in the external report: a payable function assumes/enforces only a lower-bound relationship between the attached payment and the fee/cost owed, and fails to refund (or, in the analog, silently absorbs) the excess rather than returning it to the caller, when a caller in good faith intentionally or accidentally overpays.

### Impact Explanation
Any unprivileged caller (a direct transaction signer, or a user relayed through the Wallet Contract's RLP execution path) who attaches more than the exact required storage deposit while registering a brand-new address mapping loses the excess permanently: it becomes part of the contract's balance with no code path to withdraw or reclaim it. This is a concrete, transaction-triggered, permanent loss of funds for the caller — matching the "permanently frozen funds" acceptance criterion.

### Likelihood Explanation
Likelihood is high in practice: attaching a slightly larger deposit than the exact expected minimum ("overpaying to be safe") is a common integration pattern for calling payable contract methods, and the caller has no way to know in advance the *exact* required deposit without querying `storage_byte_cost` and manually recomputing `bytes_to_store` off-chain. Any caller (human, relayer, or the Wallet Contract itself acting as a relayed intermediary) that attaches a deposit larger than the exact byte-cost requirement triggers the loss on every first-time registration of a given address.

### Recommendation
In the `Entry::Vacant` success branch of `register()`, compute the excess (`given_deposit - required_deposit`) and issue a `promise_batch_action_transfer` refund of that excess back to `env::predecessor_account_id()`, mirroring the refund logic already implemented in the `Entry::Occupied` branch, instead of silently retaining the full attached deposit.

### Proof of Concept
1. Deploy `AddressRegistrar` and call `new()`.
2. Call `register(account_id: "alice.near")` attaching a deposit significantly larger than the computed `required_deposit` (e.g. `NearToken::from_near(1)` when only a few hundred yoctoNEAR-per-byte × ~20-30 bytes is required).
3. Observe: the call succeeds (`Some(address)` is returned, matching `test_register_without_deposit`'s success assertions at [7](#0-6) ), and the registrar contract's account balance increases by the *entire* attached deposit rather than only by `required_deposit`.
4. There is no subsequent call (no owner/withdraw method exists) that can move the excess balance back to the original caller — it is permanently stuck in the `AddressRegistrar` contract's account.

### Citations

**File:** runtime/near-wallet-contract/implementation/address-registrar/src/lib.rs (L16-22)
```rust
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

**File:** runtime/near-wallet-contract/implementation/address-registrar/src/lib.rs (L88-112)
```rust
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L514-518)
```rust
#[near_sdk::ext_contract(ext_registrar)]
trait AddressRegistrar {
    fn lookup(&self, address: String) -> Option<AccountId>;
}

```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/tests/sanity.rs (L249-276)
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

```
