# Q5517: epoch_rewards_calculation::recalculate_partitioned_rewards_if_active - recalculation after restart yields different rewards (setting the vote account commission to)

## Question
Can an unprivileged attacker who creates and delegates its own stake accounts to a vote account it controls, then waits for the epoch boundary, setting the vote account commission to its maximum immediately before the boundary, drive `epoch_rewards_calculation::recalculate_partitioned_rewards_if_active` to make recalculate_partitioned_rewards_if_active or recalculate_stake_rewards produce different values than the original calculation, so that the invariant that reward recalculation reproduces the original result exactly is broken and the outcome is Consensus/Safety Violation (bank hash divergence or fork)?

## Target
- File/function: `runtime/src/bank/partitioned_epoch_rewards/calculation.rs` -> `recalculate_partitioned_rewards_if_active`
- Entrypoint: creates and delegates its own stake accounts to a vote account it controls, then waits for the epoch boundary, setting the vote account commission to its maximum immediately before the boundary
- Attacker controls: stake amounts, delegation and deactivation timing, vote account commission, and credit history
- Exploit idea: Make recalculate_partitioned_rewards_if_active or recalculate_stake_rewards produce different values than the original calculation.
- Invariant to test: Reward recalculation reproduces the original result exactly.
- Expected Immunefi impact: Critical - Consensus/Safety Violation (bank hash divergence or fork)
- Fast validation: bank test warping across the epoch boundary with the crafted stake set and asserting rewards equal the point-based expectation
