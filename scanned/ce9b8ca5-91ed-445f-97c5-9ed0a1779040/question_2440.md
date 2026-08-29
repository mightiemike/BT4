# Q2440: filter-u128 via collateral-remove: pass a health check and then change, in the same transacti

## Question
Does `collateral-remove` (mainnet/contracts/market/v0-4-market.clar:1107) let an unprivileged attacker who controls the set of assets held reach `filter-u128` (mainnet/contracts/registry/v0-egroup.clar:97) in a state where it pass a health check and then change, in the same transaction, the quantity it checked? Given that it filters a 128-entry bucket list, the invariant that when a multi-step entry point aborts, no value is stranded and no identifier survives breaks and the result is direct theft of user funds at rest or in motion.

## Target
- File/function: `mainnet/contracts/registry/v0-egroup.clar:97` -> `filter-u128`
- Entrypoint: `collateral-remove` (`mainnet/contracts/market/v0-4-market.clar:1107`), unprivileged and publicly callable
- Attacker controls: the set of assets held
- Exploit idea: `filter-u128` filters a 128-entry bucket list. Reach it through `collateral-remove` and pass a health check and then change, in the same transaction, the quantity it checked.
- Invariant to test: when a multi-step entry point aborts, no value is stranded and no identifier survives
- Expected Immunefi impact: Critical - direct theft of user funds at rest or in motion
- Fast validation: Set up the position in simnet, call `collateral-remove` with the set of assets held, and assert on the printed event plus the post-state that collateral, debt and share totals still reconcile.
