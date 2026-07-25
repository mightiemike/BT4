### Title
Inflationary Reward Dilution via Unstaked Validator Inclusion - ([File: chain/epoch-manager/src/reward_calculator.rs])

### Summary
A vulnerability exists in the `RewardCalculator::calculate_reward` function where the `total_stake` used for reward distribution includes the stake of validators who have unstaked (delegated 0 coins) during the current epoch. This leads to an inaccurate `total_stake` denominator, causing a dilution of rewards for active validators and a discrepancy between the calculated `epoch_total_reward` and the `epoch_actual_reward` minted.

### Finding Description
In `nearcore`, validator rewards are calculated at the end of each epoch in `finalize_epoch` [1](#0-0) . The process involves:
1. Identifying validators to be kicked out due to low performance (blocks/chunks/endorsements) [2](#0-1) .
2. Removing these poorly performing validators from the `validator_block_chunk_stats` map [3](#0-2) .
3. Calling `calculate_reward` with the remaining stats and the full `validator_stake` map [4](#0-3) .

The issue lies in `calculate_reward`:
```rust
let total_stake: Balance = validator_stake
    .values()
    .fold(Balance::ZERO, |sum, item| sum.checked_add(*item).unwrap());
``` [5](#0-4) 

This `total_stake` includes every account in the `validator_stake` map, which is derived from the current epoch's validator set [6](#0-5) . However, if a validator submitted an "unstake" proposal (stake set to 0) during epoch T, they are marked with `ValidatorKickoutReason::Unstaked` [7](#0-6) . Unlike performance-based kickouts, "Unstaked" validators are **not** removed from `validator_block_chunk_stats` before reward calculation [8](#0-7) .

Consequently, their stake (which was non-zero for the duration of the epoch they served) is included in the `total_stake` denominator [9](#0-8) , even though they are exiting. If the performance-based removal logic intended to exclude "inactive" validators from the reward pool to maintain yield for honest participants, the omission of "Unstaked" validators (who are also being kicked out) creates an inconsistency in the reward denominator.

### Impact Explanation
The inclusion of exiting (unstaked) validators in the `total_stake` denominator dilutes the rewards for all other honest validators. Since `validator_reward = epoch_validator_reward * multiplier * stake / total_stake` [10](#0-9) , an inflated `total_stake` reduces the payout for everyone. This mirrors the external report where "undelegated" (unstaked) coins are not properly handled in the distribution logic, leading to inaccurate reward yields.

### Likelihood Explanation
This occurs every time a validator unstakes. While the protocol eventually returns the stake to the account's liquid balance, the calculation for the final epoch of service uses a denominator that does not account for the immediate shift in the active validator set composition, leading to marginal but persistent reward inaccuracy.

### Recommendation
Update the filter logic in `finalize_epoch` to remove validators kicked out for the `Unstaked` reason from `validator_block_chunk_stats` before calling `calculate_reward`, ensuring they are treated consistently with performance-based kickouts. [2](#0-1) 

### Proof of Concept
1. A validator "test1" unstakes in Epoch T by sending a `StakeAction` with `stake: 0`.
2. At the end of Epoch T, `finalize_epoch` is called.
3. `collect_blocks_info` adds "test1" to `validator_kickout` with `Reason::Unstaked` [7](#0-6) .
4. The loop at `lib.rs:905` checks the kickout reasons. It only removes `NotEnoughBlocks`, `NotEnoughChunks`, and `NotEnoughChunkEndorsements`. It **skips** `Unstaked`.
5. `calculate_reward` is called. `total_stake` is calculated using all validators from Epoch T [5](#0-4) .
6. The rewards for remaining validators are calculated using this inflated `total_stake`, resulting in lower rewards than if the exiting validator's stake were excluded from the distribution pool.

### Citations

**File:** chain/epoch-manager/src/lib.rs (L687-687)
```rust
                validator_kickout.insert(account_id.clone(), ValidatorKickoutReason::Unstaked);
```

**File:** chain/epoch-manager/src/lib.rs (L873-873)
```rust
    fn finalize_epoch(
```

**File:** chain/epoch-manager/src/lib.rs (L884-885)
```rust
        let validator_stake =
            epoch_info.validators_iter().map(|r| r.account_and_stake()).collect::<HashMap<_, _>>();
```

**File:** chain/epoch-manager/src/lib.rs (L905-914)
```rust
            for (account_id, reason) in &validator_kickout {
                if matches!(
                    reason,
                    ValidatorKickoutReason::NotEnoughBlocks { .. }
                        | ValidatorKickoutReason::NotEnoughChunks { .. }
                        | ValidatorKickoutReason::NotEnoughChunkEndorsements { .. }
                ) {
                    validator_block_chunk_stats.remove(account_id);
                }
            }
```

**File:** chain/epoch-manager/src/lib.rs (L925-933)
```rust
            self.reward_calculator.calculate_reward(
                validator_block_chunk_stats,
                &validator_stake,
                *block_info.total_supply(),
                epoch_protocol_version,
                epoch_duration,
                online_thresholds,
                epoch_config.max_inflation_rate,
            )
```

**File:** chain/epoch-manager/src/reward_calculator.rs (L91-93)
```rust
        let total_stake: Balance = validator_stake
            .values()
            .fold(Balance::ZERO, |sum, item| sum.checked_add(*item).unwrap());
```

**File:** chain/epoch-manager/src/reward_calculator.rs (L134-140)
```rust
                    (U512::from(epoch_validator_reward.as_yoctonear())
                        * U512::from(uptime_numer)
                        * U512::from(stake.as_yoctonear())
                        / U512::from(uptime_denum)
                        / U512::from(total_stake.as_yoctonear()))
                    .as_u128(),
                )
```
