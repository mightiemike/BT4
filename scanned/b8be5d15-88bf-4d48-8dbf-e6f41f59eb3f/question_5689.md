# Q5689: inflation_points::tower_epoch_credits_iter - points computed from credits the vote account did not earn (deactivating stake partway through the epoch)

## Question
Can an unprivileged attacker who delegates stake to a vote account it controls and accumulates epoch credits, deactivating stake partway through the epoch, drive `inflation_points::tower_epoch_credits_iter` to make calc_earned_credits or tower_epoch_credits_iter count credits outside the reward epoch, so that the invariant that points count only credits earned in the epoch being rewarded is broken and the outcome is Loss of Funds (lamport inflation / supply corruption)?

## Target
- File/function: `runtime/src/inflation_rewards/points.rs` -> `tower_epoch_credits_iter`
- Entrypoint: delegates stake to a vote account it controls and accumulates epoch credits, deactivating stake partway through the epoch
- Attacker controls: stake amounts, delegation activation timing, vote credit history and commission settings
- Exploit idea: Make calc_earned_credits or tower_epoch_credits_iter count credits outside the reward epoch.
- Invariant to test: Points count only credits earned in the epoch being rewarded.
- Expected Immunefi impact: Critical - Loss of Funds (lamport inflation / supply corruption)
- Fast validation: unit-test the point calculation against the crafted stake and credit history and assert points match the protocol formula
