# Q5602: epoch_rewards_distribution::build_updated_stake_reward - credited lamports differ from the calculated reward (resizing the stake account during the)

## Question
Can an unprivileged attacker who holds stake accounts that receive partitioned epoch rewards over the distribution window, resizing the stake account during the distribution window, drive `epoch_rewards_distribution::build_updated_stake_reward` to make distribute_epoch_rewards_in_partition credit an amount other than the calculated reward, so that the invariant that credited lamports equal the reward computed at the epoch boundary is broken and the outcome is Loss of Funds (lamport inflation / supply corruption)?

## Target
- File/function: `runtime/src/bank/partitioned_epoch_rewards/distribution.rs` -> `build_updated_stake_reward`
- Entrypoint: holds stake accounts that receive partitioned epoch rewards over the distribution window, resizing the stake account during the distribution window
- Attacker controls: stake account sizes and rent state, delegation timing, and transactions submitted during the distribution window
- Exploit idea: Make distribute_epoch_rewards_in_partition credit an amount other than the calculated reward.
- Invariant to test: Credited lamports equal the reward computed at the epoch boundary.
- Expected Immunefi impact: Critical - Loss of Funds (lamport inflation / supply corruption)
- Fast validation: bank test running the full distribution window and asserting credited lamports equal the calculated rewards and capitalization balances
