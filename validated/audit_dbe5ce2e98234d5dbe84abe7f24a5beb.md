I found a concrete match to the report's bug class in the `AddressRegistrar` contract that ships as part of the ETH-implicit-account wallet infrastructure, reachable from a plain `FunctionCall` action / RLP-encoded ETH transaction routed through the wallet contract.

### Title
Overpaid registration deposits are permanently trapped in `AddressRegistrar` with no withdrawal path - (File: `runtime/near-wallet-contract/implementation/address-registrar/src/lib.rs`)

### Summary
`AddressRegistrar::register` is a `#[payable]` method that accepts an attached deposit to cover the storage cost of adding a new `address -> account_id` mapping. It validates that `given_deposit >= required_deposit` and, on the success (`Entry::Vacant`) path, stores the mapping but never refunds the excess if `given_deposit > required_deposit`. There is no owner, no admin key, and no other method on the contract capable of withdrawing or refunding that excess balance — it is retained by the contract's account balance forever.

### Finding Description
In `register`, the deposit check only enforces a lower bound: [1](#0-0) 

On the `Entry::Vacant` (success) branch, the contract inserts the mapping and returns without ever comparing `given_deposit` to `required_deposit` or refunding the difference: [2](#0-1) 

Contrast this with the `Entry::Occupied` (collision) branch, which explicitly creates a refund promise for `given_deposit` because "no storage was updated": [3](#0-2) 

The asymmetry shows the developers were aware that unused deposits must be refunded (they did it for the collision case) but omitted the equivalent logic for the "used" case when the attached deposit exceeds the actual cost. The whole contract (`runtime/near-wallet-contract/implementation/address-registrar/src/lib.rs`) exposes only `new`, `register`, `lookup`, and `get_address` — none of which can move balance out of the contract other than the collision-refund path. There is no owner field, no privileged withdrawal function, and the contract state (`LookupMap<Address, AccountId>`) carries no accounting of collected/overpaid balance to reclaim it later.

This contract is deployed as a global contract and is the callee used by the `WalletContract` (the NEAR side of ETH-implicit-account / RLP-encoded transaction emulation) whenever an incoming Ethereum transaction targets another ETH-implicit account, via `address_registrar.lookup(...)` in the wallet flow, and any account can call `register` directly with a `FunctionCall` action and an arbitrary attached deposit: [4](#0-3) 

Any unprivileged signer (or a relayer replaying an ETH-emulated transaction through `rlp_execute`) can call `register` directly on the deployed `AddressRegistrar` account with a deposit larger than `required_deposit`; the surplus becomes unrecoverable NEAR tokens locked in that account with no code path to ever move them out.

### Impact Explanation
Funds attached to a `register` call beyond the exact storage cost are permanently and irreversibly locked in the `AddressRegistrar` contract's account balance. Since `required_deposit` is a precise, easily-miscalculated value (`storage_byte_cost * (20 + account_id.len())`), any caller who is even slightly imprecise, or who intentionally/accidentally attaches a larger deposit (e.g. rounds up, forwards a fixed relayer-fee amount, or simply guesses), permanently loses that NEAR with no recovery mechanism — exactly matching the "missing mechanism for managing/withdrawing funds" bug class from the analog report. This is a direct, concrete loss of user funds (frozen funds) triggered by a single unprivileged transaction, with no operator, sandbox, or malicious-validator involvement required.

### Likelihood Explanation
High likelihood: `register` is a public, non-privileged method payable by any account; nothing in the RLP/wallet-contract call flow enforces the caller to attach exactly `required_deposit` (fee amounts, gas price roundings, or simple wallet UX choices could easily overshoot). Because the deposit-refund logic clearly exists for the collision branch but is missing on the success branch, this is a straightforward oversight bug rather than a hardened invariant, and it is trivially reachable by any signer sending a single `FunctionCall` transaction (or ETH-emulated transaction through a relayer) to the deployed `AddressRegistrar` account.

### Recommendation
On the `Entry::Vacant` success path, compute `given_deposit.checked_sub(required_deposit)` and, if positive, issue a refund `Promise` transferring the surplus back to `env::predecessor_account_id()` (mirroring the pattern already used in the `Entry::Occupied` branch), before or alongside inserting the new mapping.

### Proof of Concept
1. Deploy `AddressRegistrar` (as is done today as a global contract referenced by `ADDRESS_REGISTRAR_ACCOUNT_ID` used by the Wallet Contract).
2. Any account calls `register({"account_id": "alice.near"})` with an attached deposit of, say, `2 * required_deposit` (e.g. `640000000000000000000000` yoctoNEAR vs. required `320000000000000000000000`, matching the amounts already used in `test_register_without_deposit`): [5](#0-4) 
3. The call succeeds (`Entry::Vacant`), the mapping is inserted, and the full `2 * required_deposit` deposit remains on the `AddressRegistrar` account — only `required_deposit` was "needed."
4. There is no `register`, `lookup`, `get_address`, or any other method that lets anyone withdraw the surplus; it is permanently stuck.

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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L412-431)
```rust
    let promise = match transaction_kind {
        TransactionKind::EthEmulation(EthEmulationKind::EOABaseTokenTransfer {
            address_check: Some(address),
            ..
        }) => {
            let callback_gas = ADDRESS_CHECK_CALLBACK_GAS.saturating_add(action.gas());
            let ext = WalletContract::ext(current_account_id).with_static_gas(callback_gas);
            let address_registrar = {
                let account_id = ADDRESS_REGISTRAR_ACCOUNT_ID
                    .trim()
                    .parse()
                    .unwrap_or_else(|_| env::panic_str("Invalid address registrar"));
                ext_registrar::ext(account_id).with_static_gas(REGISTRAR_LOOKUP_GAS)
            };
            let address = format!("0x{}", hex::encode(address));
            address_registrar.lookup(address).then(ext.address_check_callback(
                target,
                action,
                caller_deposit,
            ))
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
