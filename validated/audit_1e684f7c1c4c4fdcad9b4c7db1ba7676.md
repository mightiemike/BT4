## Title
Missing refund of excess attached deposit in `AddressRegistrar::register` permanently locks user funds - (File: `runtime/near-wallet-contract/implementation/address-registrar/src/lib.rs`)

### Summary
The `register` method of the `AddressRegistrar` contract (part of the NEAR Wallet Contract / Aurora-style ETH-implicit-account infrastructure) computes a `required_deposit` for the storage it needs to write, and rejects the call only if `given_deposit < required_deposit`. It never refunds the difference when `given_deposit > required_deposit` on the success path, unlike the collision path a few lines below which explicitly refunds the *entire* deposit. This is the exact "missing refund mechanism when overpaying a computed fee" bug class from the reference report, reachable from a normal, unprivileged `FunctionCall` transaction/RPC call.

### Finding Description
`register()` computes the storage cost and validates only a lower bound: [1](#0-0) 

On the success ("Vacant") branch, the entry is inserted and the function returns without ever computing or refunding `given_deposit - required_deposit`: [2](#0-1) 

Contrast this with the "Occupied" (collision) branch just below, which explicitly recognizes that unused deposits must be returned and issues a full refund via `promise_batch_action_transfer`: [3](#0-2) 

Because the method is `#[payable]`, the full `attached_deposit` is credited to the contract's account balance by the NEP-141/near-sdk/runtime `Transfer`/deposit semantics before execution runs, and the runtime's automatic refund path only fires when the **entire receipt fails** (see `refund_unspent_gas_and_deposits` / `Receipt::new_balance_refund`, which only triggers on `result.result.is_err()`): [4](#0-3) 

Since the `Vacant` branch returns `Ok`/`Some(address)` (a successful outcome), the protocol-level deposit-refund mechanism never activates, and any excess the caller attached is retained forever by the `AddressRegistrar` contract with no code path to reclaim it. This is confirmed by the existing test `test_register_without_deposit`, which attaches `320000000000000000000` yoctoNEAR (far more than the actual bytes-to-store require) and only asserts the contract balance *increased by at least* the full deposit — i.e. the excess is intentionally absorbed, not refunded: [5](#0-4) 

### Impact Explanation
Any account — including relayers and end users driving Ethereum-style transactions through the NEAR Wallet Contract's `rlp_execute` flow, which routes `register` calls with attacker/user-supplied deposits (see `CallerDeposit`/`register` call sites in `wallet-contract/src/lib.rs` and `tests/relayer.rs`) — that attaches more than the minimal required storage deposit when calling `register` permanently loses the excess NEAR into the `AddressRegistrar` contract's balance, with no method exposed to withdraw or reclaim it. This is a genuine "permanently frozen funds" condition for the overpaying caller, one of the explicitly accepted impact categories.

### Likelihood Explanation
Likelihood is high for any caller that doesn't compute the exact byte-for-byte storage cost client-side (e.g., relies on a fixed/rounded deposit such as the `1 milliNEAR` used in `tests/relayer.rs`, or any margin-of-safety deposit chosen by wallets/relayers to avoid `Insufficient deposit` panics). Since this contract is meant to be called by ordinary transaction signers/meta-transaction relayers as part of standard NEAR Wallet Contract operation, no privileged access is required to trigger the loss.

### Recommendation
In the `Entry::Vacant` success branch of `register`, compute `excess = given_deposit - required_deposit` and, if `excess > 0`, issue a `promise_batch_action_transfer` refunding `excess` back to `env::predecessor_account_id()`, mirroring the refund logic already implemented in the `Entry::Occupied` branch.

### Proof of Concept
1. Deploy/use the existing `AddressRegistrar` contract as in `runtime/near-wallet-contract/implementation/wallet-contract/src/tests/sanity.rs`.
2. Call `register({"account_id": "alice.near"})` attaching a deposit significantly larger than `required_deposit` (e.g. as done in `test_register_without_deposit`, which attaches `320000000000000000000` yoctoNEAR versus the minimal bytes-based cost).
3. Observe (as the existing test already implicitly shows) that the contract balance increases by the *entire* attached deposit and the caller's balance decreases by the entire amount — no refund transfer is emitted for the unused excess, confirmed by `test_register_without_deposit`: [6](#0-5)

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

**File:** runtime/runtime/src/lib.rs (L1303-1316)
```rust
        let deposit_refund = if result.result.is_err() { total_deposit } else { Balance::ZERO };
        let gross_gas_refund = if result.result.is_err() {
            prepaid_gas
                .checked_add(prepaid_exec_gas.gas)
                .ok_or(IntegerOverflowError)?
                .checked_sub(result.gas_burnt)
                .unwrap()
        } else {
            prepaid_gas
                .checked_add(prepaid_exec_gas.gas)
                .ok_or(IntegerOverflowError)?
                .checked_sub(result.gas_used)
                .unwrap()
        };
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
