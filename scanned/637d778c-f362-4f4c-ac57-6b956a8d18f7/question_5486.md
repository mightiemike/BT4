# Q5486: epoch_rewards_calculation::get_epoch_params_for_recalculation - recalculation after restart yields different rewards (deactivating and reactivating the stake across)

## Question
Can an unprivileged attacker who creates and delegates its own stake accounts to a vote account it controls, then waits for the epoch boundary, deactivating and reactivating the stake across two consecutive epochs, drive `epoch_rewards_calculation::get_epoch_params_for_recalculation` to make recalculate_partitioned_rewards_if_active or recalculate_stake_rewards produce different values than the original calculation, so that the invariant that reward recalculation reproduces the original result exactly is broken and the outcome is Consensus/Safety Violation (bank hash divergence or fork)?

## Target
- File/function: `runtime/src/bank/partitioned_epoch_rewards/calculation.rs` -> `get_epoch_params_for_recalculation`
- Entrypoint: creates and delegates its own stake accounts to a vote account it controls, then waits for the epoch boundary, deactivating and reactivating the stake across two consecutive epochs
- Attacker controls: stake amounts, delegation and deactivation timing, vote account commission, and credit history
- Exploit idea: Make recalculate_partitioned_rewards_if_active or recalculate_stake_rewards produce different values than the original calculation.
- Invariant to test: Reward recalculation reproduces the original result exactly.
- Expected Immunefi impact: Critical - Consensus/Safety Violation (bank hash divergence or fork)
- Fast validation: bank test warping across the epoch boundary with the crafted stake set and asserting rewards equal the point-based expectation
