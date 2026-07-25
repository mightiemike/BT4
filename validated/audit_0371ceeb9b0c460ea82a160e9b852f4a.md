### Title
Kicked-out Validators' Stakes Inflate `total_stake` Denominator in Reward Calculation, Underpaying Honest Validators - (File: `chain/epoch-manager/src/reward_calculator.rs`)

### Summary

`RewardCalculator::calculate_reward` computes each validator's share of epoch rewards as `uptime * stake / total_stake`. The `total_stake` denominator is derived from the full `validator_stake` map, which is built from **all** validators in the epoch — including those who are simultaneously being kicked out for `NotEnoughBlocks`, `NotEnoughChunks`, or `NotEnoughChunkEndorsements`. Those kicked-out validators are removed from the numerator side (`validator_block_chunk_stats`) but their stakes remain in the denominator. The result is a systematically inflated denominator that causes every honest, performing validator to receive fewer rewards than the protocol intends, and the unallocated reward tokens are simply never minted.

### Finding Description

In `finalize_epoch` (`chain/epoch-manager/src/lib.rs`), the `validator_stake` map is built from the full current-epoch validator set:

```rust
let validator_stake =
    epoch_info.validators_iter().map(|r| r.account_and_stake()).collect::<HashMap<_, _>>();
``` [1](#0-0) 

Immediately after, validators kicked out for performance reasons are removed from `validator_block_chunk_stats` (the stats map that drives who receives a reward):

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
``` [2](#0-1) 

But `validator_stake` is **not** filtered. Both maps are then passed to `calculate_reward`:

```rust
self.reward_calculator.calculate_reward(
    validator_block_chunk_stats,
    &validator_stake,          // ← still contains kicked-out validators
    *block_info.total_supply(),
    ...
)
``` [3](#0-2) 

Inside `calculate_reward`, `total_stake` is summed over **all** entries in `validator_stake`:

```rust
let total_stake: Balance = validator_stake
    .values()
    .fold(Balance::ZERO, |sum, item| sum.checked_add(*item).unwrap());
``` [4](#0-3) 

Each performing validator's reward is then:

```rust
Balance::from_yoctonear(
    (U512::from(epoch_validator_reward.as_yoctonear())
        * U512::from(uptime_numer)
        * U512::from(stake.as_yoctonear())
        / U512::from(uptime_denum)
        / U512::from(total_stake.as_yoctonear()))  // ← inflated by kicked-out stakes
    .as_u128(),
)
``` [5](#0-4) 

The iteration only covers validators present in `validator_block_chunk_stats` (the filtered map), so kicked-out validators receive zero reward — but their stakes still inflate `total_stake`. The unallocated portion of `epoch_validator_reward` is never minted.

### Impact Explanation

The invariant `sum(validator_rewards) + treasury_reward == epoch_total_reward` is broken. The actual minted amount is strictly less than `epoch_total_reward` whenever any validator is kicked out for performance reasons. Honest validators receive a reward proportional to `stake / total_stake_all` instead of the correct `stake / total_stake_rewarded`. The shortfall scales linearly with the fraction of total stake held by kicked-out validators. With `validator_max_kickout_stake_perc` permitting up to ~33% of total stake to be kicked out in a single epoch, honest validators can receive up to ~25% fewer rewards than the protocol specifies. The missing tokens are permanently unissued — a deflationary deviation from the intended monetary policy and a direct balance underpayment to every honest validator.

### Likelihood Explanation

This triggers in every epoch where at least one validator is kicked out for `NotEnoughBlocks`, `NotEnoughChunks`, or `NotEnoughChunkEndorsements` — a routine, expected protocol event. No attacker action is required; the bug manifests automatically. On mainnet, validator kickouts occur regularly, so the underpayment accumulates every epoch.

### Recommendation

Filter `validator_stake` to exclude validators whose kickout reason is `NotEnoughBlocks`, `NotEnoughChunks`, or `NotEnoughChunkEndorsements` before passing it to `calculate_reward`, mirroring the existing filter applied to `validator_block_chunk_stats`. Alternatively, compute `total_stake` inside `calculate_reward` only over the keys present in `validator_block_chunk_stats` rather than over the entire `validator_stake` map.

### Proof of Concept

Consider epoch T with three validators:
- `test1`: stake = 1 000 000, fully online
- `test2`: stake = 1 000 000, produces 0 blocks → kicked out for `NotEnoughBlocks`
- `test3`: stake = 1 000 000, fully online

`epoch_validator_reward` = 3 000 (simplified).

**Current behavior (buggy):**
- `total_stake` = 3 000 000 (includes test2)
- `test1` reward = 3 000 × 1 000 000 / 3 000 000 = **1 000**
- `test3` reward = 3 000 × 1 000 000 / 3 000 000 = **1 000**
- Total minted = 2 000 (1 000 tokens never issued)

**Correct behavior:**
- `total_stake` = 2 000 000 (excludes test2)
- `test1` reward = 3 000 × 1 000 000 / 2 000 000 = **1 500**
- `test3` reward = 3 000 × 1 000 000 / 2 000 000 = **1 500**
- Total minted = 3 000 (full epoch reward distributed)

The existing test `test_rewards_with_kickouts` at `chain/epoch-manager/src/tests/mod.rs:1133` confirms the current (buggy) behavior is baked in: it asserts that `test1` and `test3` each receive `2378` yoctoNEAR while `test2` (kicked for `NotEnoughBlocks`) receives zero, with `test2`'s stake silently inflating the denominator. [6](#0-5)

### Citations

**File:** chain/epoch-manager/src/lib.rs (L884-885)
```rust
        let validator_stake =
            epoch_info.validators_iter().map(|r| r.account_and_stake()).collect::<HashMap<_, _>>();
```

**File:** chain/epoch-manager/src/lib.rs (L905-913)
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

**File:** chain/epoch-manager/src/reward_calculator.rs (L133-140)
```rust
                Balance::from_yoctonear(
                    (U512::from(epoch_validator_reward.as_yoctonear())
                        * U512::from(uptime_numer)
                        * U512::from(stake.as_yoctonear())
                        / U512::from(uptime_denum)
                        / U512::from(total_stake.as_yoctonear()))
                    .as_u128(),
                )
```

**File:** chain/epoch-manager/src/tests/mod.rs (L1133-1158)
```rust
    let wanted_rewards = HashMap::from([
        (
            2,
            // test3 should still be rewarded even though it is in the kickouts for unstaking
            HashMap::from([
                ("near".parse().unwrap(), Balance::from_yoctonear(792)),
                ("test1".parse().unwrap(), Balance::from_yoctonear(2378)),
                ("test3".parse().unwrap(), Balance::from_yoctonear(2378)),
            ]),
        ),
        (
            3,
            HashMap::from([
                ("near".parse().unwrap(), Balance::from_yoctonear(792)),
                ("test1".parse().unwrap(), Balance::from_yoctonear(2378)),
                ("test3".parse().unwrap(), Balance::from_yoctonear(2378)),
            ]),
        ),
        (
            4,
            HashMap::from([
                ("near".parse().unwrap(), Balance::from_yoctonear(792)),
                ("test1".parse().unwrap(), Balance::from_yoctonear(7135)),
            ]),
        ),
    ]);
```
