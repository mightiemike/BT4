# Q5619: epoch_rewards_distribution::build_updated_stake_reward - closed stake account still receives a reward (resizing the stake account during the)

## Question
Can an unprivileged attacker who holds stake accounts that receive partitioned epoch rewards over the distribution window, resizing the stake account during the distribution window, drive `epoch_rewards_distribution::build_updated_stake_reward` to close a stake account during the window and still have its reward credited to a resurrected account, so that the invariant that rewards are not credited to accounts that no longer exist is broken and the outcome is Loss of Funds (lamport inflation / supply corruption)?

## Target
- File/function: `runtime/src/bank/partitioned_epoch_rewards/distribution.rs` -> `build_updated_stake_reward`
- Entrypoint: holds stake accounts that receive partitioned epoch rewards over the distribution window, resizing the stake account during the distribution window
- Attacker controls: stake account sizes and rent state, delegation timing, and transactions submitted during the distribution window
- Exploit idea: Close a stake account during the window and still have its reward credited to a resurrected account.
- Invariant to test: Rewards are not credited to accounts that no longer exist.
- Expected Immunefi impact: Critical - Loss of Funds (lamport inflation / supply corruption)
- Fast validation: bank test running the full distribution window and asserting credited lamports equal the calculated rewards and capitalization balances
