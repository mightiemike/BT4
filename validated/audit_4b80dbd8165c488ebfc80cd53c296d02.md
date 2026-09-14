### Title
Account-creation charge silently drops to zero when the burn-time gas price meets or exceeds the purchase price - ([File: runtime/runtime/src/lib.rs])

### Summary
`refund_unspent_gas_and_deposits` funds the mandatory `account_creation_charge` exclusively out of `burned_gas_refund` (the gas-price "surplus"), which is itself computed as `gas_purchase_price - gas_burn_price`. Because `gas_burn_price` is capped by `min(gas_purchase_price, apply_state.gas_price)`, this difference collapses to zero whenever the chunk's live gas price is at or above the price the gas was purchased at. In that (legitimate, attacker-reachable) condition, `amount_actually_charged = min(amount_to_charge, burned_gas_refund)` evaluates to `min(amount_to_charge, 0) = 0`, and the account-creation surcharge is bypassed entirely, mirroring the reported pattern of a mandatory fee computed as `A - B` that can legitimately equal zero.

### Finding Description
`refund_unspent_gas_and_deposits` (`runtime/runtime/src/lib.rs`, around lines 1338-1398) computes the price surplus/deficit for the gas actually burnt: [1](#0-0) 

Under `AccountCostIncrease`, gas is burnt at `gas_burn_price = min(gas_purchase_price, apply_state.gas_price)` (documented at `runtime/runtime/src/lib.rs:920`, referenced in `protocol-model/spec/economics.md:61`). Because `gas_burn_price` can never exceed `gas_purchase_price` under this feature, the `gas_burn_price > gas_purchase_price` branch (which would set `price_deficit`) is effectively unreachable, and the code always falls into the `price_surplus` branch, computed as `gas_purchase_price.checked_sub(gas_burn_price)`. When the chunk's live gas price (`apply_state.gas_price`) is at or above `gas_purchase_price`, `gas_burn_price == gas_purchase_price`, so `price_surplus == 0`.

`burned_gas_refund` is set directly from `price_surplus` when `AccountCostIncrease` is enabled: [2](#0-1) 

The account-creation charge is then capped at `burned_gas_refund`, with only a `debug_assert!` (compiled out in release builds) guarding the "should always be enough" invariant: [3](#0-2) 

When `burned_gas_refund == 0` (i.e., no positive price surplus at burn time), `amount_actually_charged = std::cmp::min(amount_to_charge, 0) = 0`, so `gas_refund_result.create_account_charge` is `0` and the entire `account_creation_charge` (mainnet `0.007 NEAR`, per `core/parameters/res/runtime_configs/85.yaml`, referenced in `protocol-model/spec/economics.md:84`) is skipped — with no fallback path that instead debits the newly created account's own balance directly for the missing amount.

### Impact Explanation
This is a protocol-level fee-bypass: any account-creating receipt (e.g., `CreateAccount`, `DeterministicStateInitAction`) executed in a chunk whose current gas price is at or above the gas-purchase price for that receipt entirely avoids paying the anti-spam `account_creation_charge`. Since account creation is one of the primary vectors of state growth targeted by this NEP-536/AccountCostIncrease surcharge, systematically triggering this condition lets an attacker mass-create accounts without paying the intended fee, undermining the economic deterrent the charge exists to enforce and shifting state-growth cost away from the actor who caused it, matching the "fee bypass" acceptance criterion.

### Likelihood Explanation
The condition (`apply_state.gas_price >= gas_purchase_price`) is not a contrived edge case: `gas_purchase_price` for typical low-congestion transactions is pinned at `min_gas_purchase_price` (1e9 yoctoNEAR per the res config), while the network's live gas price can rise up to `min(genesis_max_gas_price, min_gas_price * 20)` under congestion. Any period of elevated block gas price relative to the purchase-time floor makes this path trivially and repeatedly reachable by an ordinary unprivileged transaction signer, with no special privileges required — they simply need to submit account-creation transactions while the chunk gas price is not below their gas-purchase price.

### Recommendation
Do not silently cap the `account_creation_charge` to the available `burned_gas_refund`. When `burned_gas_refund < amount_to_charge`, the shortfall should either be charged separately from the created account's balance (failing account creation with insufficient balance if it cannot be covered), or the charge should be sourced from a mechanism independent of the gas-price-surplus refund so it cannot be reduced to zero by network gas-price conditions. At minimum, replace the `debug_assert!` invariants with enforced (release-mode) checks so any accounting gap is detected rather than silently discarded.

### Proof of Concept
1. Submit an account-creation transaction (`CreateAccount`, `DeterministicStateInitAction`) at a time/chunk where the current network gas price is low, so `gas_purchase_price = max(current_gas_price, min_gas_purchase_price) = min_gas_purchase_price` (1e9 yN).
2. Ensure (or wait until) the transaction's wrapped receipt executes in a chunk whose `apply_state.gas_price` has since risen to be `>= min_gas_purchase_price` (e.g., due to normal network congestion pushing gas price up, bounded by `MAX_GAS_MULTIPLIER = 20`).
3. At `runtime/runtime/src/lib.rs`, `gas_burn_price = min(gas_purchase_price, apply_state.gas_price) = gas_purchase_price`, making `price_surplus = gas_purchase_price - gas_burn_price = 0` (lines 1344-1349).
4. `burned_gas_refund = price_surplus = 0` (lines 1352-1358).
5. In the account-creation charge block (lines 1360-1397), `amount_actually_charged = min(amount_to_charge, burned_gas_refund) = min(amount_to_charge, 0) = 0`.
6. `gas_refund_result.create_account_charge = 0`; the account is created successfully but the `account_creation_charge` (0.007 NEAR on mainnet) is never collected from anywhere — confirmable by comparing the signer's/creator's total balance delta against `tokens_burnt` for the transaction, which would show the charge missing relative to `account_creation_charge`.

Note: I was not able to execute this scenario in a live/test environment (no tool access to run `test-loop-tests`), so this analysis is based on static code review of the arithmetic and control flow; a Devin session with repository execution access would be needed to confirm the exact numeric conditions (e.g., precise gas-price bounds where this triggers) end-to-end.

### Citations

**File:** runtime/runtime/src/lib.rs (L1338-1350)
```rust
        if gas_burn_price > gas_purchase_price {
            // price increased, burning resulted in a deficit
            gas_refund_result.price_deficit = safe_gas_to_balance(
                gas_burn_price.checked_sub(gas_purchase_price).unwrap(),
                result.gas_burnt,
            )?;
        } else {
            // price decreased, burning resulted in a surplus
            gas_refund_result.price_surplus = safe_gas_to_balance(
                gas_purchase_price.checked_sub(gas_burn_price).unwrap(),
                result.gas_burnt,
            )?;
        };
```

**File:** runtime/runtime/src/lib.rs (L1352-1358)
```rust
        // Refund for the price difference between gas_purchase_price and gas_burn_price of the gas burned in this receipt.
        let mut burned_gas_refund =
            if ProtocolFeature::AccountCostIncrease.enabled(protocol_version) {
                gas_refund_result.price_surplus
            } else {
                Balance::ZERO
            };
```

**File:** runtime/runtime/src/lib.rs (L1360-1397)
```rust
        // If an account was created, charge more to cover its cost.
        if created_account && ProtocolFeature::AccountCostIncrease.enabled(protocol_version) {
            // This is how much creating an account should cost
            let desired_cost = config.account_creation_charge;

            let create_account_gas_cost =
                config.fees.fee(ActionCosts::create_account).exec_fee().gas;
            // The cost of the gas that was burned already
            let burned_cost = safe_gas_to_balance(gas_burn_price, create_account_gas_cost)?;

            // We would like to charge as much as needed to reach desired_cost
            let amount_to_charge = desired_cost.saturating_sub(burned_cost);

            // We can't charge more than `burned_gas_refund`.
            // `burned_gas_refund < amount_to_charge` could happen for receipts where the gas was
            // purchased in protocol versions before `ProtocolFeature::AccountCostIncrease`, at a lower
            // gas price that isn't enough to cover the cost of creating an account.
            let amount_actually_charged = std::cmp::min(amount_to_charge, burned_gas_refund);

            // sanity check: purchasing gas at `min_gas_purchase_price` should be enough to cover
            // the cost of creating an account.
            debug_assert!(
                safe_gas_to_balance(config.min_gas_purchase_price, create_account_gas_cost)
                    .unwrap()
                    >= desired_cost
            );

            // sanity check: as long as the purchase price is high enough, there should always be
            // enough refund balance to cover the cost of creating an account.
            if gas_purchase_price >= config.min_gas_purchase_price {
                debug_assert!(burned_gas_refund >= amount_to_charge);
            }

            // Subtract `amount_actually_charged` from the refund.
            gas_refund_result.create_account_charge = amount_actually_charged;
            burned_gas_refund = burned_gas_refund
                .checked_sub(amount_actually_charged)
                .expect("burned_gas_refund >= amount_actually_charged checked above");
```
