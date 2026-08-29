# Q3352: interest-rate via supply-collateral-add: reuse one price and index snapshot across a batch that mut

## Question
Does `supply-collateral-add` (mainnet/contracts/market/v0-4-market.clar:1175) let an unprivileged attacker who controls the position state the final collateral-add is validated against reach `interest-rate` (mainnet/contracts/vault/v0-vault-stx.clar:371) in a state where it reuse one price and index snapshot across a batch that mutates state between entries? Given that it interpolates the packed curve at the current utilization, the invariant that every second of elapsed time is charged exactly once, to one index, in one direction breaks and the result is direct theft of another user's collateral.

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:371` -> `interest-rate`
- Entrypoint: `supply-collateral-add` (`mainnet/contracts/market/v0-4-market.clar:1175`), unprivileged and publicly callable
- Attacker controls: the position state the final collateral-add is validated against
- Exploit idea: `interest-rate` interpolates the packed curve at the current utilization. Reach it through `supply-collateral-add` and reuse one price and index snapshot across a batch that mutates state between entries.
- Invariant to test: every second of elapsed time is charged exactly once, to one index, in one direction
- Expected Immunefi impact: Critical - direct theft of another user's collateral
- Fast validation: Set up the position in simnet, call `supply-collateral-add` with the position state the final collateral-add is validated against, and assert on the printed event plus the post-state that collateral, debt and share totals still reconcile.
