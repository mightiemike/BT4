# Q5521: epoch_rewards_calculation::calculate_rewards - rewards computed from post-boundary stake state (setting the vote account commission to)

## Question
Can an unprivileged attacker who creates and delegates its own stake accounts to a vote account it controls, then waits for the epoch boundary, setting the vote account commission to its maximum immediately before the boundary, drive `epoch_rewards_calculation::calculate_rewards` to change stake in the first slots of the new epoch and have it counted for the previous epoch's rewards, so that the invariant that rewards use the stake snapshot from the epoch boundary is broken and the outcome is Consensus/Safety Violation (bank hash divergence or fork)?

## Target
- File/function: `runtime/src/bank/partitioned_epoch_rewards/calculation.rs` -> `calculate_rewards`
- Entrypoint: creates and delegates its own stake accounts to a vote account it controls, then waits for the epoch boundary, setting the vote account commission to its maximum immediately before the boundary
- Attacker controls: stake amounts, delegation and deactivation timing, vote account commission, and credit history
- Exploit idea: Change stake in the first slots of the new epoch and have it counted for the previous epoch's rewards.
- Invariant to test: Rewards use the stake snapshot from the epoch boundary.
- Expected Immunefi impact: Critical - Consensus/Safety Violation (bank hash divergence or fork)
- Fast validation: bank test warping across the epoch boundary with the crafted stake set and asserting rewards equal the point-based expectation
