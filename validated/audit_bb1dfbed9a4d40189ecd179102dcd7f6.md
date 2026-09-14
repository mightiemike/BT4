## Title
Protocol-treasury reward silently dropped when treasury account is also a block/chunk validator - (File: `chain/epoch-manager/src/reward_calculator.rs`)

### Summary
`RewardCalculator::calculate_reward` keys its per-account reward map by `AccountId`. It first inserts the protocol-treasury cut under `protocol_treasury_account`, then iterates all validators and re-inserts (overwriting) an entry for each `account_id`. If the treasury account is itself one of this epoch's block/chunk-producer validators, the second insert overwrites the first, discarding the treasury's inflation cut from the map that is later used to actually credit accounts — while the *total* minted amount returned to the chain still includes that cut. This is structurally the same class of bug as the Beefy finding: two logically distinct value flows are keyed by the same identifier, and the code that aggregates "total funds"/"total minted" does not account for the fact that one flow silently absorbs/overwrites the other.

### Finding Description
In `calculate_reward` (`chain/epoch-manager/src/reward_calculator.rs:51-146`):
1. The treasury cut is inserted first: `res.insert(self.protocol_treasury_account.clone(), epoch_protocol_treasury);` [1](#0-0) 
2. `epoch_actual_reward` (the second return value, later stored as `minted_amount` and added to `total_supply`) is independently accumulated as `epoch_protocol_treasury` plus every validator's production reward: [2](#0-1) 
3. For every validator (including the treasury account, if it is one), `res.insert(account_id, reward)` unconditionally overwrites whatever was previously stored under that key: [3](#0-2) 

If `account_id == protocol_treasury_account` (the treasury account is also a validator this epoch), step 3 replaces the treasury's `epoch_protocol_treasury` entry with just its own block/chunk-production `reward`, permanently losing the treasury cut from the returned map — even though `epoch_actual_reward` still includes `epoch_protocol_treasury` and is minted into `total_supply`.

Downstream, `Runtime::update_validator_accounts` (`runtime/runtime/src/lib.rs:1717-1835`) relies on this map. Because the treasury account is present in `stake_info` (it is a validator), the code takes the branch that assumes "If protocol treasury stakes, then the rewards was already distributed above" [4](#0-3)  and skips the explicit treasury credit at lines 1810-1832. The "distributed above" credit is only the (already-clobbered) `reward` value applied to `account.locked()` at lines 1749-1755 [5](#0-4)  — which no longer contains `epoch_protocol_treasury`.

### Impact Explanation
The protocol treasury's inflation share (`protocol_reward_rate` of the epoch reward, hardcoded to 1/10 for production-genesis chains) is silently never credited to any account for every epoch in which the treasury account is also selected as a validator, yet `total_supply` is still incremented by the full `epoch_actual_reward` including that share (`core/primitives/src/block.rs:193`, `new_total_supply = prev.total_supply + minted_amount - balance_burnt`). This breaks the fundamental invariant that minted tokens are backed by a corresponding account credit: tokens are permanently unaccounted for (effectively lost — no account ever receives them), while the chain's reported total supply is inflated by an amount not present in any account balance. This is a genuine, protocol-level broken-accounting / loss-of-funds bug, not an admin input-validation issue, since nothing in account creation, staking, or validator selection prevents the treasury account from staking and becoming a validator.

### Likelihood Explanation
Triggering this requires only that the `protocol_treasury_account` (e.g., mainnet's `near` account) submits a normal `Stake` action and is subsequently selected as a block or chunk producer for an epoch — an ordinary, permitted transaction, not any privileged/admin-only operation. Once triggered, the bug recurs every epoch the treasury remains an active validator, continuously leaking the treasury's inflation share.

### Recommendation
In `RewardCalculator::calculate_reward`, when inserting a validator's reward into `res`, add to any existing entry (`entry(account_id).and_modify(|r| *r = r.checked_add(reward)).or_insert(reward)`) instead of unconditionally overwriting it, so the treasury's `epoch_protocol_treasury` cut is preserved and summed with its own validator-production reward. Correspondingly review/simplify the special-case handling in `update_validator_accounts` that assumes disjointness between `stake_info` and the treasury account.

### Proof of Concept
1. Configure (or observe on a live chain) `protocol_treasury_account = "near"`.
2. Have the `near` account submit a `Stake` transaction with a nonzero stake and become selected as a block/chunk producer for epoch `T`.
3. At the epoch boundary, `calculate_reward` computes `epoch_protocol_treasury > 0` and inserts it for `"near"`, then in the validator loop re-inserts `"near" -> reward` (its own production reward), clobbering the treasury cut.
4. `epoch_actual_reward` (returned `minted_amount`) still equals `epoch_protocol_treasury + sum(validator rewards)`, and is added to `total_supply` at the next block.
5. In `update_validator_accounts`, because `"near"` is in `stake_info`, only `reward` (not `reward + epoch_protocol_treasury`) is added to its `locked` balance; the explicit treasury-credit block is skipped since `stake_info.contains_key("near")` is true.
6. Result: `total_supply` increased by `epoch_protocol_treasury` more than the sum of all account balance deltas for that epoch — the treasury's inflation share is unaccounted for/lost every such epoch.

### Citations

**File:** chain/epoch-manager/src/reward_calculator.rs (L78-84)
```rust
        let epoch_protocol_treasury = Balance::from_yoctonear(
            (U256::from(epoch_total_reward.as_yoctonear())
                * U256::from(*protocol_reward_rate.numer() as u64)
                / U256::from(*protocol_reward_rate.denom() as u64))
            .as_u128(),
        );
        res.insert(self.protocol_treasury_account.clone(), epoch_protocol_treasury);
```

**File:** chain/epoch-manager/src/reward_calculator.rs (L88-93)
```rust
        let epoch_validator_reward =
            epoch_total_reward.checked_sub(epoch_protocol_treasury).unwrap();
        let mut epoch_actual_reward = epoch_protocol_treasury;
        let total_stake: Balance = validator_stake
            .values()
            .fold(Balance::ZERO, |sum, item| sum.checked_add(*item).unwrap());
```

**File:** chain/epoch-manager/src/reward_calculator.rs (L141-144)
```rust
            };
            res.insert(account_id, reward);
            epoch_actual_reward = epoch_actual_reward.checked_add(reward).unwrap();
        }
```

**File:** runtime/runtime/src/lib.rs (L1748-1755)
```rust
            if let Some(mut account) = account {
                if let Some(reward) = validator_accounts_update.validator_rewards.get(account_id) {
                    tracing::debug!(target: "runtime", %account_id, %reward, locked = %account.locked(), "account adding reward to stake");
                    let locked = account.locked().checked_add(*reward).ok_or_else(|| {
                        RuntimeError::UnexpectedIntegerOverflow("update_validator_accounts".into())
                    })?;
                    account.set_locked(locked).or_inconsistent_state(account_id)?;
                }
```

**File:** runtime/runtime/src/lib.rs (L1807-1809)
```rust
        if let Some(account_id) = &validator_accounts_update.protocol_treasury_account_id {
            // If protocol treasury stakes, then the rewards was already distributed above.
            if !validator_accounts_update.stake_info.contains_key(account_id) {
```
