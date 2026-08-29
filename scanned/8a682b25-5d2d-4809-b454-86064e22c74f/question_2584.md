# Q2584: relevant via supply-collateral-add: leave the accrual clock stale so a later interval double-c

## Question
Does `supply-collateral-add` (mainnet/contracts/market/v0-4-market.clar:1175) let an unprivileged attacker who controls `amount` reach `relevant` (mainnet/contracts/market/v0-market-vault.clar:175) in a state where it leave the accrual clock stale so a later interval double-counts elapsed time? Given that it drops any position row whose bit is not present in the enabled mask, the invariant that every second of elapsed time is charged exactly once, to one index, in one direction breaks and the result is direct theft of user funds at rest or in motion.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:175` -> `relevant`
- Entrypoint: `supply-collateral-add` (`mainnet/contracts/market/v0-4-market.clar:1175`), unprivileged and publicly callable
- Attacker controls: `amount`
- Exploit idea: `relevant` drops any position row whose bit is not present in the enabled mask. Reach it through `supply-collateral-add` and leave the accrual clock stale so a later interval double-counts elapsed time.
- Invariant to test: every second of elapsed time is charged exactly once, to one index, in one direction
- Expected Immunefi impact: Critical - direct theft of user funds at rest or in motion
- Fast validation: Set up the position in simnet, call `supply-collateral-add` with `amount`, and assert on the printed event plus the post-state that collateral, debt and share totals still reconcile.
