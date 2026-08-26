# Q5515: epoch_rewards_calculation::calculate_rewards - stake account credited twice across partitions (setting the vote account commission to)

## Question
Can an unprivileged attacker who creates and delegates its own stake accounts to a vote account it controls, then waits for the epoch boundary, setting the vote account commission to its maximum immediately before the boundary, drive `epoch_rewards_calculation::calculate_rewards` to get one stake account included in two reward partitions, so that the invariant that each stake account is rewarded at most once per epoch is broken and the outcome is Loss of Funds (lamport inflation / supply corruption)?

## Target
- File/function: `runtime/src/bank/partitioned_epoch_rewards/calculation.rs` -> `calculate_rewards`
- Entrypoint: creates and delegates its own stake accounts to a vote account it controls, then waits for the epoch boundary, setting the vote account commission to its maximum immediately before the boundary
- Attacker controls: stake amounts, delegation and deactivation timing, vote account commission, and credit history
- Exploit idea: Get one stake account included in two reward partitions.
- Invariant to test: Each stake account is rewarded at most once per epoch.
- Expected Immunefi impact: Critical - Loss of Funds (lamport inflation / supply corruption)
- Fast validation: bank test warping across the epoch boundary with the crafted stake set and asserting rewards equal the point-based expectation
