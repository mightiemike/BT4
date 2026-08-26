# Q5479: epoch_rewards_calculation::load_and_reward_commission_accounts - commission split favours the attacker's vote account (deactivating and reactivating the stake across)

## Question
Can an unprivileged attacker who creates and delegates its own stake accounts to a vote account it controls, then waits for the epoch boundary, deactivating and reactivating the stake across two consecutive epochs, drive `epoch_rewards_calculation::load_and_reward_commission_accounts` to make distribute_reward_commissions or update_reward_commissions pay more commission than the configured rate, so that the invariant that commission paid equals the configured rate applied to earned rewards is broken and the outcome is Loss of Funds (lamport inflation / supply corruption)?

## Target
- File/function: `runtime/src/bank/partitioned_epoch_rewards/calculation.rs` -> `load_and_reward_commission_accounts`
- Entrypoint: creates and delegates its own stake accounts to a vote account it controls, then waits for the epoch boundary, deactivating and reactivating the stake across two consecutive epochs
- Attacker controls: stake amounts, delegation and deactivation timing, vote account commission, and credit history
- Exploit idea: Make distribute_reward_commissions or update_reward_commissions pay more commission than the configured rate.
- Invariant to test: Commission paid equals the configured rate applied to earned rewards.
- Expected Immunefi impact: Critical - Loss of Funds (lamport inflation / supply corruption)
- Fast validation: bank test warping across the epoch boundary with the crafted stake set and asserting rewards equal the point-based expectation
