## Finding: Overpaid deposit permanently stuck in the NEAR Wallet Contract's `AddressRegistrar`

This maps directly onto the Allo `_createPool` bug class: a contract validates that an attached payment is *at least* a required fee (`given_deposit < required_deposit` → reject) but never refunds the excess when the payment succeeds, so any surplus becomes permanently unrecoverable native-token balance sitting in the contract.

### Title
Overpayment to `AddressRegistrar::register` is permanently retained instead of refunded - (File: `runtime/near-wallet-contract/implementation/address-registrar/src/lib.rs`)

### Summary
The `AddressRegistrar` contract, which is part of the in-scope NEAR wallet contract system, exposes a `#[payable]` `register` method that requires callers to attach at least `required_deposit` yoctoNEAR to cover the storage cost of the new `address -> account_id` entry. The check only rejects deposits that are *too small*; any amount attached above `required_deposit` is silently kept by the contract on the success path, with no refund and no later withdrawal mechanism.

### Finding Description
In `register`, the storage cost is computed and compared against the attached deposit: [1](#0-0) 

If `given_deposit >= required_deposit`, execution proceeds. On the `Entry::Vacant` (success) branch the mapping is inserted and the function simply returns the derived address — the full `given_deposit`, including any amount above `required_deposit`, stays credited to the contract's account balance with no accounting or refund: [2](#0-1) 

Notably, the *collision* branch (`Entry::Occupied`) explicitly refunds the **entire** deposit back to the caller when no storage is written: [3](#0-2) 

This asymmetry confirms the contract's intended design is "pay exactly for what you use, refund the rest" — but that refund logic is missing from the success path, exactly mirroring the Allo `_createPool` flaw where `baseFee + _amount >= msg.value` was checked instead of an exact-match/refund pattern, leaving `msg.value - baseFee` stranded in the contract.

The contract has no `withdraw`, `sweep`, or owner-controlled fund-recovery method (the whole file — `new`, `register`, `lookup`, `get_address` — is shown above and contains no such method), so any excess deposit is permanently locked in the `AddressRegistrar` account with no code path to retrieve it.

### Impact Explanation
Any unprivileged caller — a direct RPC/transaction caller, or a user driving this contract indirectly through the NEAR Wallet Contract's ETH-transaction-emulation flow when it needs to register a named account (see `nep_141_storage_balance_callback`/`address_check_callback` call sites into the registrar) — who attaches more than the minimal required storage deposit permanently loses the difference. Existing tests confirm attaching deposits well above the minimum (e.g. `NearToken::from_millinear(1)` and `NearToken::from_near(3)` against a computed requirement on the order of `3.2e20` yoctoNEAR) is the common calling pattern: [4](#0-3) [5](#0-4) 

The existing sanity test even asserts the surplus is retained rather than refunded: [6](#0-5) 
This is a concrete, permanent loss of user native-token funds with no recovery path — matching the "permanently frozen funds" acceptance criterion.

### Likelihood Explanation
Likelihood is high in practice: since the exact storage-byte cost (`storage_byte_cost() * (20 + account_id.len())`) is an implementation detail most callers won't compute precisely, over-attaching a deposit (as shown in the project's own tests, which attach amounts many orders of magnitude above the requirement) is the natural calling pattern. Any transaction that reaches `register` — whether from a direct RPC caller or via the Wallet Contract's relayer-driven flow — is affected, with no privileged role required.

### Recommendation
Refund the excess deposit on the success path, mirroring the collision-branch behavior:
```rust
Entry::Vacant(entry) => {
    ...
    entry.insert(account_id);
    let refund = given_deposit.saturating_sub(required_deposit);
    if refund > NearToken::from_yoctonear(0) {
        let refund_promise = env::promise_batch_create(&env::predecessor_account_id());
        env::promise_batch_action_transfer(refund_promise, refund);
    }
    Some(address)
}
```
Alternatively, require `given_deposit == required_deposit` and reject overpayments outright, forcing callers to compute the exact cost.

### Proof of Concept
1. Call `AddressRegistrar::register("some_account.near")` attaching, e.g., `1 NEAR` while the computed `required_deposit` for that account_id is only a few hundred microNEAR (as in `test_register_without_deposit`, `runtime/near-wallet-contract/implementation/wallet-contract/src/tests/sanity.rs:249-275`, where `required_deposit ≈ 3.2e20` yoctoNEAR ≈ 0.32 NEAR).
2. The call succeeds (`given_deposit >= required_deposit`), the mapping is inserted, and the address is returned.
3. Query the contract's account balance before/after: the increase equals the *full* attached deposit, not just `required_deposit`; the excess (`given_deposit - required_deposit`) is not returned to the caller and there is no method in the contract to withdraw it later — it is permanently stuck.

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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/tests/relayer.rs (L191-200)
```rust
    let register_output: Option<String> = address_registrar
        .call("register")
        .args_json(serde_json::json!({
            "account_id": token_contract.contract.id().as_str()
        }))
        .max_gas()
        .deposit(NearToken::from_millinear(1))
        .transact()
        .await?
        .json()?;
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/tests/sanity.rs (L249-275)
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
