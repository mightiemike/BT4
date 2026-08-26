# Q5436: epoch_rewards_calculation::calculate_reward_points_partitioned - reward points inflated for a stake account

## Question
Can an unprivileged attacker who creates and delegates its own stake accounts to a vote account it controls, then waits for the epoch boundary, delegating stake in the final slot before the epoch boundary, drive `epoch_rewards_calculation::calculate_reward_points_partitioned` to make calculate_reward_points_partitioned attribute more points to the attacker's stake than its credits and stake justify, so that the invariant that reward points are a deterministic function of stake and earned credits is broken and the outcome is Loss of Funds (lamport inflation / supply corruption)?

## Target
- File/function: `runtime/src/bank/partitioned_epoch_rewards/calculation.rs` -> `calculate_reward_points_partitioned`
- Entrypoint: creates and delegates its own stake accounts to a vote account it controls, then waits for the epoch boundary, delegating stake in the final slot before the epoch boundary
- Attacker controls: stake amounts, delegation and deactivation timing, vote account commission, and credit history
- Exploit idea: Make calculate_reward_points_partitioned attribute more points to the attacker's stake than its credits and stake justify.
- Invariant to test: Reward points are a deterministic function of stake and earned credits.
- Expected Immunefi impact: Critical - Loss of Funds (lamport inflation / supply corruption)
- Fast validation: bank test warping across the epoch boundary with the crafted stake set and asserting rewards equal the point-based expectation
